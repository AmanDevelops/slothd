"""Durable state: an append-only ledger, a small state file, and the digest."""

import json
import os
import threading
import time

# A deliberately conservative estimate used for the "time saved" number:
# open the file, read enough to know what it is, decide where it belongs,
# rename it, drag it. Twenty seconds is the low end of that.
SECONDS_PER_FILE = 20


class Store:
    def __init__(self, var_dir):
        self.var_dir = var_dir
        self.ledger_path = os.path.join(var_dir, "ledger.jsonl")
        self.state_path = os.path.join(var_dir, "state.json")
        self._lock = threading.Lock()
        os.makedirs(var_dir, exist_ok=True)
        self._state = self._load_state()

    # ---- state -----------------------------------------------------------
    def _load_state(self):
        default = {"paused": False, "runs": [], "created_at": time.time()}
        try:
            with open(self.state_path) as f:
                data = json.load(f)
            default.update(data)
        except (OSError, json.JSONDecodeError):
            pass
        return default

    def save_state(self):
        with self._lock:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._state, f, indent=1)
            os.replace(tmp, self.state_path)

    @property
    def state(self):
        return self._state

    def set_paused(self, paused):
        self._state["paused"] = bool(paused)
        self.save_state()

    # ---- ledger ----------------------------------------------------------
    def append(self, entry):
        with self._lock:
            with open(self.ledger_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

    def entries(self, limit=None):
        out = []
        try:
            with open(self.ledger_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return out[-limit:] if limit else out

    def seen_hashes(self):
        return {e.get("hash") for e in self.entries() if e.get("hash")}

    def last_run_entry(self):
        runs = self._state.get("runs", [])
        return runs[-1] if runs else None

    def start_run(self):
        run = {"id": int(time.time()), "started": time.time(), "filed": 0}
        self._state.setdefault("runs", []).append(run)
        self._state["runs"] = self._state["runs"][-50:]
        self.save_state()
        return run["id"]

    def count_run(self, run_id, by_category):
        for run in self._state.get("runs", []):
            if run["id"] == run_id:
                run["filed"] += 1
                run["paused_because"] = by_category
                run["ended"] = time.time()
        self.save_state()

    # ---- undo ------------------------------------------------------------
    def undo_last_run(self, everything=False):
        """Put the last run's files back where they came from.

        everything=True walks the whole ledger instead, which is what you want
        after a first sitting that spanned several daemon restarts.
        """
        run = self.last_run_entry()
        if not run and not everything:
            return 0
        run_id = run["id"] if run else None
        entries = self.entries()
        moved = 0
        keep = []
        for e in reversed(entries):
            mine = everything or e.get("run") == run_id
            if mine and e.get("dst") and os.path.exists(e["dst"]):
                src = e["src"]
                os.makedirs(os.path.dirname(src), exist_ok=True)
                try:
                    os.replace(e["dst"], src)
                    moved += 1
                    continue
                except OSError:
                    pass
            keep.append(e)
        keep.reverse()
        with self._lock:
            with open(self.ledger_path, "w") as f:
                for e in keep:
                    f.write(json.dumps(e) + "\n")
        if run:
            self._state["runs"] = [r for r in self._state.get("runs", []) if r["id"] != run["id"]]
        self.save_state()
        return moved

    # ---- derived ---------------------------------------------------------
    def stats(self):
        entries = [e for e in self.entries() if e.get("dst")]
        by_cat = {}
        for e in entries:
            cat = e.get("category", "reference")
            by_cat[cat] = by_cat.get(cat, 0) + 1
        filed = len(entries)
        fallbacks = sum(1 for e in entries if e.get("source") == "fallback")
        tokens = sum(e.get("tokens", 0) or 0 for e in entries)
        latency = sum(e.get("seconds", 0) or 0 for e in entries)
        return {
            "filed": filed,
            "by_category": by_cat,
            "fallback_files": fallbacks,
            "model_tokens": tokens,
            "model_seconds": round(latency, 1),
            "seconds_saved": filed * SECONDS_PER_FILE,
            "minutes_saved": round(filed * SECONDS_PER_FILE / 60.0, 1),
            "dollars_spent": 0.0,
            "bytes_uploaded": 0,
        }

    def digest(self):
        """The whole point: two lines in the morning."""
        run = self.last_run_entry()
        entries = [e for e in self.entries() if e.get("dst")]
        if not entries:
            return ["Nothing filed yet. Point me at a folder and go do something else."]
        by_cat = {}
        for e in entries:
            by_cat[e.get("category", "reference")] = by_cat.get(e.get("category", "reference"), 0) + 1
        order = sorted(by_cat.items(), key=lambda kv: -kv[1])
        top = ", ".join(f"{n} {c}" for c, n in order[:3])
        total = len(entries)
        mins = round(total * SECONDS_PER_FILE / 60.0, 1)
        needs_you = [e for e in entries if e.get("category") == "money" and e.get("confidence", 0) >= 0.6]
        line1 = f"Filed {total} things while you were away ({top}). That is ~{mins} minutes you did not spend."
        if needs_you:
            line2 = f"Only {len(needs_you)} want a look from you: money files. Everything else can wait."
        else:
            line2 = "Nothing needs you. Go back to being slow."
        return [line1, line2]
