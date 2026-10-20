"""The worker: a loop that only works when nobody is looking."""

import hashlib
import os
import re
import threading
import time

from . import clerk
from .idle import IdleGate
from .store import Store

SKIP_DIRS = {".git", "var", "slothd", "ui", "__pycache__", "node_modules", ".venv"}


def slugify(text, limit=48):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit] or "file").strip("-")


def scan(inbox, seen, limit=400):
    """New files in the inbox, oldest-looking first, deduped by content hash."""
    found = []
    for root, dirs, files in os.walk(inbox):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
                if st.st_size == 0 and name.endswith((".part", ".crdownload")):
                    continue
                with open(path, "rb") as f:
                    digest = hashlib.sha256(f.read(1 << 20)).hexdigest()[:16]
            except OSError:
                continue
            if digest in seen:
                continue
            found.append({"path": path, "name": name, "hash": digest,
                          "mtime": st.st_mtime, "size": st.st_size})
            if len(found) >= limit:
                return found
    return found


class Runner(threading.Thread):
    def __init__(self, inbox, var_dir, model=clerk.MODEL):
        super().__init__(daemon=True)
        self.inbox = os.path.abspath(inbox)
        self.outbox = os.path.join(var_dir, "sorted")
        self.store = Store(var_dir)
        self.model = model
        self.gate = IdleGate()
        self.stopping = False
        self._lock = threading.Lock()
        self.current = None          # file being worked on
        self.queue = []              # paths still to do in this pass
        self.status = "sleeping"     # sleeping | waiting_for_idle | working | paused | empty
        self.last_event = None
        self.started = time.time()
        self.run_id = None
        self.attempts = {}           # hash -> failed tries, so nothing wedges forever
        self.cancel = threading.Event()   # set when the human needs the machine back
        self.preemptions = 0
        self.idle_seconds_used = 0.0

    # ---- public api ------------------------------------------------------
    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "current": self.current,
                "queue_len": len(self.queue),
                "queue_preview": [os.path.basename(p["path"]) for p in self.queue[:8]],
                "model": self.model,
                "inbox": self.inbox,
                "outbox": self.outbox,
                "uptime_s": round(time.time() - self.started),
                "idle_seconds_used": round(self.idle_seconds_used, 1),
                "gate": self.gate.as_dict(),
                "last_event": self.last_event,
                "recent": self.store.entries(limit=12)[::-1],
                "stats": self.store.stats(),
                "digest": self.store.digest(),
                "paused": bool(self.store.state.get("paused")),
                "preemptions": self.preemptions,
            }

    def pause(self):
        self.store.set_paused(True)
        with self._lock:
            self.status = "paused"

    def resume(self):
        self.store.set_paused(False)
        with self._lock:
            self.status = "sleeping"

    def stop(self):
        self.stopping = True

    # ---- loop ------------------------------------------------------------
    def run(self):
        try:
            os.nice(19)  # the least important thing on this machine
        except OSError:
            pass
        while not self.stopping:
            try:
                self._tick()
            except Exception as exc:  # a chore worker must never die loudly
                self._event(f"error: {exc}")
                time.sleep(5)

    def _tick(self):
        if self.store.state.get("paused"):
            self._set(status="paused")
            time.sleep(1)
            return

        if not self.queue:
            self.queue = scan(self.inbox, self.store.seen_hashes())
            if not self.queue:
                self._set(status="empty")
                self.current = None
                time.sleep(4)
                return
            self.run_id = self.store.start_run()
            self._event(f"found {len(self.queue)} new files")

        if not self.gate.is_idle():
            self._set(status="waiting_for_idle")
            time.sleep(max(2.0, self.gate.min_window))
            return

        item = self.queue[0]
        self._set(status="working", current=os.path.basename(item["path"]))
        self.cancel.clear()
        done = threading.Event()
        watch = threading.Thread(target=self._watch, args=(done,), daemon=True)
        watch.start()
        try:
            self._file(item)
        except clerk.Yielded:
            # The human started using the machine mid-file. Give it back at once.
            self.preemptions += 1
            self._set(status="waiting_for_idle", current=None)
            self._event(f"gave the machine back mid-file: {item['name']}")
            time.sleep(1.0)
            return
        except Exception as exc:
            # A chore worker must not be able to wedge on one weird file.
            self.attempts[item["hash"]] = self.attempts.get(item["hash"], 0) + 1
            self._event(f"could not file {item['name']}: {exc}")
            if self.attempts[item["hash"]] >= 2:
                self.store.append({"ts": time.time(), "run": self.run_id,
                                   "hash": item["hash"], "src": item["path"], "dst": None,
                                   "name": item["name"], "category": "reference",
                                   "title": item["name"][:60], "summary": "left alone after two failed attempts",
                                   "confidence": 0, "source": "skipped", "seconds": 0,
                                   "error": str(exc)[:160]})
                self._event(f"gave up on {item['name']}, left it where it was")
        finally:
            done.set()
            watch.join(timeout=2)
        self.queue = self.queue[1:]

    def _file(self, item):
        path, name = item["path"], item["name"]
        text = clerk.read_text(path)
        t0 = time.time()
        verdict, meta = clerk.classify(text, name, model=self.model, cancel=self.cancel)
        took = time.time() - t0
        self.idle_seconds_used += took

        if not os.path.exists(path):
            return
        cat = verdict["category"]
        dest_dir = os.path.join(self.outbox, cat)
        os.makedirs(dest_dir, exist_ok=True)
        ext = os.path.splitext(name)[1].lower()
        dest = os.path.join(dest_dir, f"{slugify(verdict['title'])}-{item['hash'][:6]}{ext}")
        try:
            os.replace(path, dest)
        except OSError as exc:
            self._event(f"could not move {name}: {exc}")
            return

        entry = {
            "ts": time.time(),
            "run": self.run_id,
            "hash": item["hash"],
            "src": path,
            "dst": dest,
            "name": name,
            "title": verdict["title"],
            "category": cat,
            "summary": verdict["summary"],
            "confidence": verdict.get("confidence", 0.5),
            "source": meta.get("source", "model"),
            "model": verdict.get("model", self.model),
            "tokens": verdict.get("tokens", 0),
            "seconds": round(took, 1),
            "error": meta.get("error"),
        }
        self.store.append(entry)
        self.store.count_run(self.run_id, cat)
        self._event(f"filed {name} -> {cat}/")
        self.current = None

    def _watch(self, done):
        """While a file is in flight, keep asking whether the human came back."""
        while not done.wait(1.0):
            if not self.gate.is_idle():
                self.cancel.set()
                return

    # ---- helpers ---------------------------------------------------------
    def _set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _event(self, msg):
        self.last_event = {"at": time.time(), "msg": msg}
