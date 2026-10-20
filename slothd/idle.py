"""Idle detection: only do chores when the human isn't using the machine.

A self-throttling worker is itself load, so the gate has to measure the machine
**minus itself**, or it concludes the human is always busy and stalls forever.

Two signals, in order of preference:

* **cgroup v2 CPU usage** (`/sys/fs/cgroup/cpu.stat`) covers every process in
  this container/session; the model server's and this process's own core-seconds
  are subtracted. This is the honest "how much CPU did the human actually use"
  number, and it still works inside a container, where `/proc/stat` reports the
  whole host instead of your slice of it.
* **`/proc/stat` machine idle**, minus our own ticks, as a fallback for systems
  without cgroup v2.

CPU pressure (PSI) and load average are shown as context but are not gates:
both count work that is not the human's, and both lag.
"""

import math
import os
import time


def _read_kv(path, wanted):
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if parts and parts[0] == wanted:
                    out = {}
                    for p in parts[1:]:
                        if "=" in p:
                            k, v = p.split("=", 1)
                            out[k] = float(v)
                        else:
                            out["value"] = float(p)   # "usage_usec 12345"
                    return out
    except OSError:
        return None
    return None


def _cgroup_cpu_seconds():
    """Total CPU seconds used by this cgroup, or None if unavailable."""
    kv = _read_kv("/sys/fs/cgroup/cpu.stat", "usage_usec")
    if kv and "value" in kv:
        return kv["value"] / 1e6
    return None


def _cpu_pressure():
    """PSI 'some avg10' for our cgroup, else the machine's. Display only."""
    kv = _read_kv("/sys/fs/cgroup/cpu.pressure", "some")
    if kv and "avg10" in kv:
        return kv["avg10"]
    kv = _read_kv("/proc/pressure/cpu", "some")
    return kv["avg10"] if kv else None


def _machine_times():
    """(idle_ticks, total_ticks) for the whole machine, from /proc/stat."""
    with open("/proc/stat") as f:
        fields = f.readline().split()[1:]
    vals = [int(v) for v in fields]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    return idle, sum(vals)


def _own_pids():
    """Pids of this process plus anything that looks like the model server."""
    pids = [os.getpid()]
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in pids:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().lower()
        except OSError:
            continue
        if b"ollama" in cmd:
            pids.append(pid)
    return pids


def _own_ticks(pids):
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                raw = f.read()
            after = raw.rsplit(")", 1)[1].split()
            total += int(after[11]) + int(after[12])  # utime + stime
        except (OSError, IndexError, ValueError):
            continue
    return total


class IdleGate:
    """Says yes only when the machine has room for a chore."""

    def __init__(self, idle_threshold=70.0, min_window=2.0):
        self.idle_threshold = float(os.environ.get("SLOTHD_IDLE_PCT", idle_threshold))
        self.min_window = min_window
        self.cores = os.cpu_count() or 1
        self.hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        self.use_cgroup = _cgroup_cpu_seconds() is not None

        self._t0 = time.time()
        self._cgroup0 = _cgroup_cpu_seconds()
        self._machine0 = _machine_times()
        self._own0 = _own_ticks(_own_pids())

        self.idle_pct = 100.0
        self.human_cores = 0.0
        self.own_cores = 0.0
        self.own_load = 0.0     # our own 1-minute load share, for display
        self.load1 = 0.0
        self.psi_some = _cpu_pressure()

    def sample(self, force=False):
        now = time.time()
        elapsed = now - self._t0
        if not force and elapsed < self.min_window:
            return self.idle_pct

        own_ticks = _own_ticks(_own_pids())
        d_own = max(0, own_ticks - self._own0) / self.hz  # our core-seconds

        if self.use_cgroup:
            cgroup = _cgroup_cpu_seconds()
            d_total = max(0.0, (cgroup or 0.0) - (self._cgroup0 or 0.0))
            used = d_total
            if elapsed > 0:
                busy_pct = 100.0 * max(0.0, d_total - d_own) / (elapsed * self.cores)
                self.idle_pct = round(max(0.0, min(100.0, 100.0 - busy_pct)), 1)
            self._cgroup0 = cgroup
        else:
            idle, total = _machine_times()
            d_idle = idle - self._machine0[0]
            d_total = total - self._machine0[1]
            used = d_total / self.hz
            if d_total > 0:
                busy = 100.0 * (d_total - d_idle) / d_total
                credit = min(100.0 * d_own / (elapsed * self.cores), busy)
                self.idle_pct = round(max(0.0, min(100.0, 100.0 - busy + credit)), 1)
            self._machine0 = (idle, total)

        if elapsed > 0:
            self.human_cores = round(max(0.0, used - d_own) / elapsed, 2)
            self.own_cores = round(d_own / elapsed, 2)
            alpha = 1.0 - math.exp(-elapsed / 60.0)
            self.own_load += (self.own_cores - self.own_load) * alpha

        self.psi_some = _cpu_pressure()
        try:
            self.load1 = os.getloadavg()[0]
        except OSError:
            self.load1 = 0.0

        self._own0 = own_ticks
        self._t0 = now
        return self.idle_pct

    def is_idle(self):
        self.sample()
        return self.idle_pct >= self.idle_threshold

    def as_dict(self):
        return {
            "idle_pct": round(self.idle_pct, 1),
            "human_cores": self.human_cores,
            "own_cores": self.own_cores,
            "own_load": round(self.own_load, 2),
            "load1": round(self.load1, 2),
            "psi_some": self.psi_some,
            "cores": self.cores,
            "idle_threshold": self.idle_threshold,
            "mode": "cgroup cpu.stat" if self.use_cgroup else "proc/stat",
        }
