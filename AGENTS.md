# AGENTS.md — brother-tools

Guidance for coding agents working on this repository.

## What this is

Two low-maintenance tools for a non-technical Windows user:
- `transcriber/` — meeting audio → transcript (GLM-ASR), priorities/events/
  communication insights (DeepSeek), `.ics` output; `watch.py` watches `inbox/`
  for phone-synced recordings.
- `news/` — daily 4-category briefing; fetches the feed pool in
  `categories.json`, DeepSeek curates by candidate id only.

## Hard rules

- **Standalone.** No dependencies on any external private system, vault, or
  machine. Everything runs from this repo on the user's Windows PC.
- **Secrets only in `.env`** (git-ignored). Never commit keys, never hardcode
  them, never print them. `.env.example` documents the names.
- **News link integrity:** article titles/URLs must come from fetched
  candidates, never from model output. Keep the id-only selection contract.
- **Degrade visibly:** a failed feed/API shows a visible note on the page or
  a clear CLI error — never a silent fallback that pretends to work.
- **Stdlib-first:** `news/` uses only the stdlib + python-dotenv. Keep it that
  way. `transcriber/` adds only `requests`.
- Windows launchers are `.cmd` (try `python`, fall back to `py`).

## Verify before claiming done

- `python -m py_compile` every changed Python file.
- Exercise the real path: run the tool, check its outputs. For checkers and
  validators, prove they can fail once (negative control) — not just pass.
- Live API tests cost small amounts (ASR ~¥0.06/min, DeepSeek a few cents);
  prefer offline/stubbed tests for mechanics, live tests for contracts.

## Git

- Never push to `main` directly; work on `agent/*` branches and open PRs.
- Never merge your own PR — the repository owner merges.
