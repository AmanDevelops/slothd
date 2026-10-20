"""The filing clerk: a local open-weight model, asked one narrow question.

Two things matter here beyond "call a model":

* **Preemption.** The call is streamed, and every chunk is a chance to give up.
  If the human starts using the machine mid-file, the socket is closed and the
  file is left exactly where it was -- no half-filed state, no wasted verdict.
* **Never raise.** A chore queue that can block on a model is broken. Anything
  unreachable, unparseable or cancelled falls back to deterministic rules.
"""

import http.client
import json
import re
import socket
import urllib.error

HOST = "127.0.0.1"
PORT = 11434
MODEL = "gemma3:4b"
CATEGORIES = ["money", "work", "code", "personal", "reference", "junk"]

SYSTEM = (
    "You are a filing clerk with no sense of urgency. Read the file snippet and "
    "reply with JSON only, no prose, no code fences. Keys:\n"
    '  "title": short human title, max 6 words\n'
    f'  "category": exactly one of {", ".join(CATEGORIES)}\n'
    '  "summary": what it is, max 12 words\n'
    '  "confidence": number between 0 and 1\n'
    "Rules: money = invoices, receipts, bills, payments, statements. work = jobs, "
    "clients, deadlines, meetings, campaigns. code = source, configs, logs, scripts. "
    "personal = family, health, travel, plans, lists. reference = docs, notes, specs "
    "worth keeping. junk = duplicates, empty, temporary, screenshots of nothing.\n\n"
)

TEXTY = {
    ".md", ".txt", ".csv", ".log", ".json", ".yml", ".yaml", ".toml", ".ini",
    ".py", ".js", ".ts", ".sh", ".sql", ".html", ".css", ".rst", ".conf", ".env",
}


class Yielded(Exception):
    """The human needed the machine, so we gave it back mid-file."""


def _stream(payload, cancel=None):
    """POST to the local model, reading its NDJSON stream chunk by chunk."""
    conn = http.client.HTTPConnection(HOST, PORT, timeout=300)
    try:
        conn.request("POST", "/api/generate", body=json.dumps(payload),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            raise ConnectionError(f"model server returned {resp.status}")
        parts, meta = [], {}
        while True:
            if cancel is not None and cancel.is_set():
                raise Yielded()
            line = resp.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if chunk.get("response"):
                parts.append(chunk["response"])
            if chunk.get("done"):
                meta = chunk
                break
        meta["response"] = "".join(parts)
        return meta
    finally:
        conn.close()  # closing the socket abandons a generation we no longer want


def _parse(raw):
    """Pull a JSON object out of whatever the model actually said."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def _clean(obj):
    if not isinstance(obj, dict):
        return None
    cat = str(obj.get("category", "")).strip().lower()
    if cat not in CATEGORIES:
        cat = "reference"
    title = re.sub(r"\s+", " ", str(obj.get("title", "")).strip())[:60] or "Untitled"
    summary = re.sub(r"\s+", " ", str(obj.get("summary", "")).strip())[:120]
    try:
        conf = float(obj.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    return {"title": title, "category": cat, "summary": summary,
            "confidence": round(max(0.0, min(1.0, conf)), 2)}


def fallback(text, name):
    """Deterministic keyword filing, used when the model is unreachable.

    The point is not accuracy. The point is that a chore queue must never be
    able to wedge itself because a model is missing.
    """
    low = (text + " " + name).lower()
    rules = [
        ("money", ["invoice", "receipt", "paid", "inr", "usd", "bill", "gst",
                   "refund", "statement"]),
        ("work", ["deadline", "meeting", "client", "standup", "sprint", "ticket",
                  "okr", "campaign"]),
        ("code", ["def ", "import ", "function", "select ", "traceback", "error",
                  "class "]),
        ("personal", ["birthday", "doctor", "mom", "dad", "trip", "flight", "wedding",
                      "grocery"]),
        ("junk", ["tmp", "copy", "duplicate", "untitled", "asdf"]),
    ]
    for cat, words in rules:
        if any(w in low for w in words):
            return {"title": name[:60], "category": cat,
                    "summary": "Filed by keyword rules (model offline).",
                    "confidence": 0.3}
    return {"title": name[:60], "category": "reference",
            "summary": "Filed by keyword rules (model offline).", "confidence": 0.2}


def classify(text, name, model=MODEL, cancel=None):
    """Verdict + metadata. Raises Yielded only when `cancel` fires mid-stream."""
    prompt = SYSTEM + f"FILE NAME: {name}\nFILE TEXT:\n{text[:3000]}\n\nJSON:"
    payload = {
        "model": model, "prompt": prompt, "stream": True, "format": "json",
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 120, "num_ctx": 4096},
    }
    try:
        out = _stream(payload, cancel=cancel)
    except Yielded:
        raise
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError,
            ConnectionError) as exc:
        verdict = fallback(text, name)
        verdict["model"] = "keyword-fallback"
        return verdict, {"source": "fallback", "error": str(exc)[:120]}

    verdict = _clean(_parse(out.get("response", "")))
    if verdict is None:
        verdict = fallback(text, name)
        verdict["model"] = "keyword-fallback"
        return verdict, {"source": "fallback", "error": "unparseable model output"}

    verdict["model"] = out.get("model", model)
    verdict["tokens"] = out.get("eval_count", 0)
    return verdict, {"source": "model", "tokens": verdict["tokens"],
                     "model_load_ms": round(out.get("load_duration", 0) / 1e6)}


def read_text(path, limit=4000):
    """Read a snippet of a text-ish file. Binaries get a metadata-only view."""
    import os
    from pathlib import PurePath
    if PurePath(str(path)).suffix.lower() not in TEXTY:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return f"[binary or unsupported file, {size} bytes]"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""
