# slothd 🦥

**A chore daemon that only works while you are not using your machine.**

Point it at the folder of things you keep putting off — a downloads folder, a pile of
receipts, notes, logs, a mess of screenshots. It reads each file with a local
open-weight model, decides what the thing *is*, renames it, and files it into
`sorted/<category>/`. It does this at the lowest priority on the box: the moment you
start using the machine it drops the file it is holding and gets out of the way; when
you walk away, it picks up exactly where it left off.

At the end it hands you two lines:

> Filed 16 things while you were away (4 code, 4 money, 4 personal). That is ~5.3 minutes you did not spend.
> Only 4 want a look from you: money files. Everything else can wait.

No account, no API key, no upload, no bill.

[58-second demo](demo/slothd-demo.mp4) — filing, then dropping an in-flight file the
moment a build starts, then picking it back up.

---

## Why it is slow on purpose

Every other "AI assistant" wants to interrupt you at full speed. slothd inverts that: it
is a worker with no urgency whatsoever. It only touches a file when **you** are leaving
the machine alone — that is, when the CPU your processes are using is below 30% of the
box over the last sample window (tune with `SLOTHD_IDLE_PCT`).

The subtle part is that a self-throttling worker is itself load: with a local 4B model
one core is pinned for ~20 seconds per file, which looks exactly like "the human is
busy". A worker that watches its own footprint deadlocks. So the gate measures the
machine **minus itself** — it sums the CPU seconds of this process and of the Ollama
server from `/proc/<pid>/stat`, subtracts them from the cgroup's total CPU usage, and
judges only what is left:

```python
d_own   = this_process + model_server           # core-seconds we spent
d_total = cgroup_cpu_seconds_delta              # every process in the session
human   = max(0.0, d_total - d_own)
idle    = 100 * (1 - human / (elapsed * cores))
```

The dashboard shows both numbers (`your cpu, this window` / `its cpu, this window`) so
the arithmetic is never a mystery, and the bar can sit near 100% while Ollama chews,
because *you* are not the one being slowed down.

Two signals were tried and rejected: **load average** is a one-minute EWMA that counts
your own past work, so the worker throttles itself for a minute after every file; and
**`/proc/stat`** reports the host's CPUs inside a container, where the machine can read
as 99% busy while your slice is idle. cgroup v2 `cpu.stat` is per-slice and correct in
both places; `/proc/stat` remains as the fallback for systems without it.

## Preemption, because "idle" changes while you are typing

Sampling the gate between files is not enough: one file takes ~20 seconds of inference,
and a build that starts in the middle of a file would otherwise wait for a courtesy it
did not ask for.

The model call is streamed over a raw `http.client` connection, and every chunk is a
chance to change our mind. A watchdog thread samples the gate once a second while a file
is in flight; if the human comes back, it sets a cancel flag, the reader closes the
socket, and the generation dies mid-token. The file has not moved — the move happens only
after a complete verdict — so nothing is ever half-filed, and the ledger records the
yield instead of the file. The `times it gave the machine back` counter on the dashboard
is that event, live.

## Quickstart

```bash
# needs Ollama with an open-weight model pulled locally
ollama pull gemma3:4b

python3 -m slothd.server --inbox ~/Downloads --var ./var --model gemma3:4b --port 8010
# open http://127.0.0.1:8010
```

No dependencies. Python 3.11+, standard library only (`http.server`, `http.client`,
`hashlib`).

```bash
python3 -m slothd.server --digest     # the two lines, for a cron job at 08:00
python3 -m slothd.server --once       # file the queue now and exit (for cron)
python3 -m slothd.server --undo       # put the last run's files back
python3 -m slothd.server --undo-all   # put everything in the ledger back
```

Options: `--inbox`, `--var`, `--model`, `--port`. Env: `SLOTHD_IDLE_PCT` (default 70).

No dependencies, but it is also a normal package if you would rather install it:

```bash
pipx install .        # or: pip install --user .
slothd --inbox ~/Downloads --var ~/.local/state/slothd
```

### Run it like a daemon

Long-running, idle-gated: [`contrib/slothd.service`](contrib/slothd.service) is a
systemd user unit (`Nice=19`, `IOSchedulingClass=idle`, restarts on failure).

```bash
cp contrib/slothd.service ~/.config/systemd/user/
systemctl --user enable --now slothd && journalctl --user -u slothd -f
```

Or let cron pick the time instead — `--once` skips the gate because the scheduler
already decided:

```cron
30 2 * * *  cd ~/slothd && python3 -m slothd.server --once --inbox ~/Downloads >> ~/.local/state/slothd/cron.log 2>&1
0  8 * * *  cd ~/slothd && python3 -m slothd.server --digest | mail -s "slothd" you@example.com
```

## What it actually did

Measured on a 2-core ARM VM, `gemma3:4b` Q4_K_M via Ollama, 16 messy files in
`fixtures/inbox` (receipts, a traceback, a training script, a flight ticket, a bank
statement, a screenshot, four files called some variant of "notes" or "untitled"):

| | |
|---|---|
| files filed | **16** |
| total model time | **307 s** (~5.1 min), spent while the machine was unattended |
| average per file | **19 s** (one local 4B call, temperature 0) |
| model tokens generated | 823 |
| bytes uploaded | **0** |
| dollars spent | **$0.00** |
| human clicks | **0** |
| categories | 4 money · 4 code · 4 personal · 2 work · 2 junk |
| mis-filed on review | 0 flatly wrong, 2 arguable |

Filing quality is honest rather than magic. `gemma3:4b` at temperature 0 gets the obvious
ones right (`invoice` → `money`, `train.py` → `code`, `tmpasdf.txt` → `junk`, a Zomato
receipt → `money`) and gets vague ones wrong the way a tired human would (a page of
project musings called `Untitled document (3).txt` → `code` because it mentions a deploy;
a grocery list → `personal`, which you could argue for). Nothing is deleted, and
everything is reversible in one call.

The time-saved number in the UI uses a deliberately low **20 seconds per file** — open
it, read enough to know what it is, decide, rename, drag. If your own filing rate is
slower, the dashboard is understating you, which is the right direction to be wrong in.

### Swapping the brain is one flag

`scripts/compare_models.py` re-runs the same 16 files through another local model and
compares against the ledger:

```
$ python3 scripts/compare_models.py gemma3:1b
11/16 agree · baseline 307.3s vs gemma3:1b 161.9s
```

So the 1B model is **1.9× faster** on this box and disagrees on the five vaguest files,
nearly all of them by falling back to `reference`. That is the trade an open-weight model
lets you make per-machine, per-afternoon — and it is the reason the model name is a CLI
flag rather than a vendor relationship.

## The boring engineering that makes it safe to leave running

* **Never loses a file.** Every move is `os.replace` followed by one append to
  `var/ledger.jsonl`. Kill -9 mid-run and the in-flight file is still in the inbox,
  untouched, and is retried on the next pass.
* **Resumes, not restarts.** Files are deduped by content hash, so a restart skips
  everything already filed and continues with the rest. Verified by killing the daemon
  repeatedly during the demo run.
* **Undo.** `POST /api/undo` walks the last run backwards and moves every file to its
  original path; `--undo-all` walks the whole ledger.
* **Degrades instead of wedging.** Ollama down, model returns prose, model returns a
  category that was not on the list — the category is clamped, or a deterministic
  keyword classifier files the thing, and the ledger entry says `"source": "fallback"`.
  Two failed attempts on one file and it gives up and leaves the file alone. (My first
  version looped forever on one unreadable screenshot; that is why this exists.)
* **Strict output handling.** `format: json`, temperature 0, an enumerated category in
  the prompt, then re-parsing (fences, leading prose), length clamping and category
  clamping in `slothd/clerk.py::_clean`.
* **Lowest priority.** `os.nice(19)`. If it is ever running while you need the machine,
  it is the least important process on it.
* **No network by construction.** The only sockets are the UI on `127.0.0.1` and the
  model on `127.0.0.1:11434`.

## Layout

```
slothd/idle.py         the self-excluding idle gate (cgroup cpu.stat, /proc/<pid>/stat)
slothd/clerk.py        the only prompt in the project, streaming, preemption, fallback
slothd/runner.py       the loop: scan → wait for idle → one file → ledger → repeat
slothd/store.py        ledger.jsonl, state.json, stats, digest, undo
slothd/server.py       http.server on 127.0.0.1 + a small CLI
slothd/ui/index.html   the dashboard: one file, no build step, no CDN
scripts/               fixture generator, model comparison, demo recorder
tests/                 17 stdlib unittest cases: parsing, clamping, dedupe, undo, digest
contrib/               systemd unit for running it as a daemon
submission/            the write-up as submitted to the DEV challenge
.gitea/workflows/      CI: unit tests on every push
```

## Tests

```bash
python3 -m unittest discover -s tests -v      # 17 tests, no network, no model
```

CI runs the same suite on every push (`.gitea/workflows/tests.yml`).

## Get it

```bash
git clone https://gitea.amanpal.tech/admin/slothd.git
cd slothd && python3 -m unittest discover -s tests -v
```

## Limits, stated plainly

* Text-ish files only. A screenshot gets filed by its filename and size, not its pixels.
* Six fixed categories; no learning, no embeddings, no memory of what you renamed last
  time.
* One model call per file, no batching. On a 2-core box that is ~19 s per file — which
  is the point, but it does mean a 2,000-file folder is a multi-day chore.
* macOS and Windows have no cgroup v2 or `/proc`; the gate falls back to `/proc/stat` on
  Linux only. A macOS port needs `host_statistics` in place of both.
* Undo restores paths, not timestamps: `os.replace` preserves mtime, but the ledger's
  own ordering is what you lose if you undo and re-run.
