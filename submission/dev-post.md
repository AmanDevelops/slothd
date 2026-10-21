---
title: I built a chore daemon that refuses to work while I'm using my laptop
published: false
tags: devchallenge, hf26challenge
---

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19)*

## What I Built

`slothd` is a chore daemon. You point it at the folder you keep meaning to deal with — mine is a downloads folder full of files called `Untitled document (3).txt` — and it reads each file with a local open-weight model, decides what the thing actually is, renames it, and files it under `sorted/<category>/`.

The interesting part is not the filing. It is the pace. It only works when the machine is genuinely idle, and the moment you start using the computer it **drops the file it is in the middle of** and gets out of the way. You never wait for it, you never watch it, and you never pay for it. You walk away, and the mess quietly shrinks.

In the morning you get two lines, not a dashboard:

> Filed 16 things while you were away (4 code, 4 money, 4 personal). That is ~5.3 minutes you did not spend.
> Only 4 want a look from you: money files. Everything else can wait.

That is the whole interface. The web UI exists mostly so you can confirm it is not lying to you.

## Demo

<!-- Drop in the GIF: slothd-demo.gif (sent in chat). It shows the daemon filing, then
     dropping an in-flight file the moment a build starts, then resuming. -->

What to watch for, in order:

1. it is filing a file, one core busy, status `working while you're away`;
2. I start a build on both cores — within ~2 seconds the status flips to `waiting for you to stop typing`, the in-flight file is abandoned mid-inference, and nothing is left half-filed;
3. the build finishes, it resumes the same file from the start, and the ledger keeps growing;
4. the digest at the end is two lines.

The dashboard's `times it gave the machine back` counter is not a mock: it increments every time the model call is killed mid-stream because someone touched the machine.

<!-- Optional: a 58s MP4 version is in the same place if you would rather host it and
     embed the link than upload a 4.8MB GIF. -->

## Code

[AmanDevelops/slothd](https://github.com/AmanDevelops/slothd) — standard library only, no dependencies, no API key. Runs on Linux (and WSL) today; macOS needs the gate ported.

```bash
git clone https://github.com/AmanDevelops/slothd.git
cd slothd
python3 scripts/make_fixtures.py                       # the 16 messy files from this post
python3 -m slothd.server --inbox fixtures/inbox --var ./var
```

17 stdlib tests (`python3 -m unittest discover -s tests`) cover the parsing, the
category clamping, hash dedupe, undo, and the digest. The gate itself is exercised by
watching the dashboard flip to `waiting for you to stop typing` the moment you start a build.

## How I Built It

### The only prompt in the project

One question per file, four fields out, temperature 0, JSON-constrained:

```text
You are a filing clerk with no sense of urgency. Read the file snippet and reply with
JSON only. Keys: title (max 6 words), category (exactly one of money, work, code,
personal, reference, junk), summary (max 12 words), confidence (0-1).
```

That is it. No agent loop, no tool calling, no planner. The job is narrow enough that a 4B model does it well, and every extra token is time I am spending on someone else's machine.

### The gate is the hard part

"It runs when you are idle" is easy to write and hard to get right, because **the worker is itself load**. My first version read `/proc/stat`, saw the CPU at 100% (from its own inference), concluded the human was busy, and stopped forever. A worker that watches its own footprint is a worker that deadlocks.

The fix is to measure the machine **minus itself**:

```python
d_own   = our_cpu_seconds + model_server_cpu_seconds   # from /proc/<pid>/stat
d_total = cgroup_cpu_seconds_delta                     # whole box, this session
human   = max(0, d_total - d_own)
idle    = 100 * (1 - human / (elapsed * cores))
```

So when Ollama pins one core for twenty seconds, the gate reads `human uses 0.0 cores` and keeps going, and when *I* pin both cores it reads `human uses 2.0 cores` and stops. The dashboard shows both numbers side by side (`your cpu, this window` / `its cpu, this window`) so the arithmetic is never a mystery.

Two things I got wrong and fixed, in case you are building something similar:

* **Load average is the wrong signal.** It is a one-minute EWMA that counts your own past work, so the worker throttles itself for a minute after every file. I switched to cgroup CPU usage and dropped the load check entirely.
* **`/proc/stat` lies inside containers.** It reports the host's CPUs, not your slice. On a shared VM the machine can read as 99% busy while your container is idle. cgroup v2 `cpu.stat` is per-slice and correct in both places; `/proc/stat` is the fallback for older systems.

### Preemption, because "idle" changes while you are typing

Sampling the gate between files is not enough: one file takes ~20 seconds of inference, so a build that starts in the middle of a file waits 20 seconds for a courtesy.

The model call is streamed over a raw `http.client` connection, and every chunk is an opportunity to change my mind. A watchdog thread samples the gate once a second while a file is in flight; if the human comes back, it sets a cancel flag, the reader closes the socket, and the generation dies mid-token. The file has not moved — the move happens only after a complete verdict — so nothing is half-filed, and the ledger records the yield instead of the file.

That is also why the answer to "what happens if I shut the laptop lid?" is boring: files move with `os.replace` and then one line is appended to `var/ledger.jsonl`. Kill -9 mid-run and the in-flight file is simply still in the inbox, to be retried on the next pass.

### The unglamorous parts that make it safe to leave running

* **Dedupe by content hash.** Restarting the daemon cannot file the same receipt twice.
* **Undo.** `POST /api/undo` walks the last run backwards and puts every file back where it came from. `--undo-all` does the ledger.
* **It cannot wedge.** Model down, model returns prose, model returns a category I did not ask for — it clamps the category, or falls back to a keyword classifier, and marks the ledger entry `source: fallback`. Two failed attempts on one file and it gives up and leaves the file alone, rather than looping forever (which my first version cheerfully did).
* **It yields rather than shares.** `os.nice(19)`. If it is ever running while you need the machine, it is the least important process on it.

## Why Does Open Innovation Matter?

Three things, all of them things a hosted API structurally cannot give me:

**It runs on the pile of personal files you would never upload.** A folder like this one has bank statements, invoices with your address on them, and a to-do list with your mother's birthday in it. A local 4B model means the answer to "who can read this" is "whoever can read my disk", full stop. The process opens exactly two sockets: `127.0.0.1:8010` for the UI and `127.0.0.1:11434` for the model. The dashboard's `bytes uploaded` counter is `0 B` because there is no code path that could make it anything else.

**The cost of a chore is zero, which changes what counts as worth doing.** Filing a file by hand costs me 20 seconds of attention. Filing it with a paid API costs a fraction of a cent plus a key to manage plus a service that can deprecate the model next quarter. When inference is free and local, "automate this boring thing" stops being a budget decision. I left it running for hours because there was nothing to meter.

**I can swap the brain.** The model is one string in `--model`. `gemma3:4b` gets the subtle ones right; `gemma3:1b` filed the same 16 files in 162 seconds against the 4B's 307 — **1.9x faster** on this 2-core box — and disagreed on the five vaguest ones, nearly all of them by falling back to `reference`. On a slow machine that is the difference between "overnight" and "over lunch", and it is my call to make, per machine, per afternoon. A closed endpoint lets me pick from whatever tier the vendor keeps in stock; an open-weight model lets me pick quantisation, size, and hardware — and lets me fine-tune it later on my own categorisation without asking permission.

### The numbers, since claims deserve them

| | |
|---|---|
| files filed | 16 |
| total model time | 307 s (~5.1 min), spent while I was not using the machine |
| average per file | 19 s (one local 4B call, temperature 0) |
| tokens generated | 823 |
| bytes uploaded | 0 |
| dollars spent | $0.00 |
| human clicks | 0 |
| mis-filed on review | 0 flatly wrong, 2 arguable |

The reproduction is two commands, and the second one re-derives the model comparison:

```bash
python3 scripts/make_fixtures.py          # writes the 16 messy files
python3 scripts/compare_models.py gemma3:1b
```

The run above is 16 files, so treat the accuracy as a spot check rather than a benchmark; it is a holdout the size of one evening's clutter.

## The Lazy Payoff

The person who gets to be lazier is me, at 11pm, with a folder of 16 files I keep avoiding.

**What it did, measured:** 16 files, unattended, on a 2-core ARM VM, with `gemma3:4b` Q4_K_M running locally through Ollama. Total model time ~5.1 minutes, spread across the evening while I was not using the machine. Human clicks: 0. Files uploaded: 0. Dollars: $0.00.

**What it saved:** the dashboard uses a deliberately conservative 20 seconds per file — open it, read enough to know what it is, decide, rename, move. 16 files × 20s ≈ **5.3 minutes** of the specific kind of attention that is hardest to schedule: small, boring, interruptible, and always deferred to tomorrow. The estimate is probably low, and I would rather understate it.

**What it got wrong:** a grocery list went to `personal` instead of `reference`, which is arguable, and a page of project musings in a file called `Untitled document (3).txt` went to `code` because the contents mention a deploy and a cache. No file landed somewhere I would call flatly wrong, nothing was deleted, and everything is reversible in one call. That is the bar I wanted: a tired intern, not an oracle.

**The honest bit:** the folder in this post is a synthetic one I put together to make the run reproducible (`scripts/make_fixtures.py`), because my real downloads folder is full of invoices with my address on them and I am not publishing those. The code does not care which folder it points at; `--inbox` is the only thing that changes.

When it finished I opened the folder once, saw `money/`, `work/`, `code/`, `personal/`, `reference/`, `junk/`, and closed it again. That is the entire product: it removes a decision I did not want to make, at a time I was not using the computer, for free.

Slow and steady. Mostly slow.

## Prize Categories

**Best Use of Gemma** — the whole project is built on Gemma running locally: `gemma3:4b` (Q4_K_M, via Ollama) does every classification, with `gemma3:1b` as the speed option in the same code path, and a deterministic keyword fallback when the model is unavailable. There is no other model provider in the project.

I am not entering any other category, because the build does not use any other partner technology — no hosting, no cloud inference, no auth, no database. The point of this one is that it runs on the machine it is sitting on.
