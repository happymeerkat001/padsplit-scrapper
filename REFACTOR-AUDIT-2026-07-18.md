# padsplit-scraper — Refactor & Optimization Audit (2026-07-18)

Fable audit; Codex implements. This is a **live production system** (cron 6:00/14:00 daily, launchd thermostat enforcer, Slack alerts) — every item below is sequenced to avoid breaking a running pipeline. Verify after each phase with the existing tests (`test_padsplit_scraper.py`, `test_thermostat_*.py`) plus one full `./run_morning.sh` dry cycle.

## Verdict in one line

The code quality is better than expected for an organically-grown scraper (typed helpers, phase-error pattern, lock files, partial-success logic) — the real debt is **git-as-a-database** (83 MB `.git` and growing twice daily), **one 1,522-line monolith** doing five jobs, and **name/stack inconsistencies** that will confuse every future agent session.

## P0 — Repo bloat: git is being used as a time-series database

- 89 timestamped JSON snapshots tracked in `padsplit_scraper/output/` (117 on disk), plus `docs/data/*` re-committed twice daily by `run_morning.sh`/`run_afternoon.sh`. `.git` is already **83 MB** and grows every single day forever.
- This is the highest-value fix and the one requiring care, since cron pulls/rebases/pushes this repo.

**Plan (Codex):**
1. Stop the growth: in `run_*.sh` `commit_and_push`, keep committing only the rolling files (`latest.json`, `stats.json`, `monthly_history.json`, drafts) — already the case — and stop tracking timestamped snapshots: `git rm -r --cached padsplit_scraper/output/2026-*.json`, add `padsplit_scraper/output/2026-*` to `.gitignore`. Timestamped history stays on disk (and is already summarized into `monthly_history.json`).
2. Optional second step (only if Leon wants the 83 MB back): history rewrite with `git filter-repo` on the output paths. Coordinate: cron must be paused and the remote force-pushed once. Do NOT do this casually; step 1 alone stops the bleeding.
3. Move `master_cron.log` + the ~96 `thermostat/*.log` launchd logs into a `logs/` dir (adjust `schedule.py` `log_paths_for` and the cron line) and gitignore it. Working-dir clutter, zero risk.

## P1 — Split the 1,522-line `padsplit_scraper/scraper.py`

It currently holds five jobs: HTTP/auth session, GraphQL fetchers, KPI math, persistence/run-status, CLI orchestration. The function inventory is already clean, so this is a mechanical extraction with near-zero logic risk:

- `client.py` — `create_session`, `login`, `_authed_request`, `ScrapePhaseError`, `fetch_*` (≈450 lines)
- `kpis.py` — `compute_kpis`, `compute_monthly_kpis`, `_parse_iso`, `_to_num`, `_find_*`, `_extract_*` (≈400 lines; **pure functions — add unit tests here first, it's the highest-math-density, least-tested code**)
- `persist.py` — `_*_output_path`, `_write_json`, `_load_json_if_exists`, `_persist_latest_payload`, `_build_*_payload`, run-status helpers
- `scraper.py` stays as thin orchestrator: `run()`, `main()`, `_run_phase`
- Keep import paths working for the cron entry point (`python3 padsplit_scraper/scraper.py` must keep working unchanged).

Same pattern applies to `thermostat/schedule.py` (912 lines: slot parsing + plist generation + launchctl + alerting in one file) — but it's stable and launchd-critical; refactor only if actively developing it. Flag, not a must.

## P2 — Consistency debt (cheap, high agent-ergonomics value)

1. **Two different `slack_notifier.py`** — root one posts task digests, `padsplit_scraper/` one posts error alerts. Same name, different jobs. Rename root → `slack_task_digest.py`; update `run_*.sh` callers.
2. **Two LLM stacks:** `message_drafter.py` uses Anthropic `claude-3-5-haiku-latest`; `message_summarizer.py` uses `"MiniMax-M2.5"`. Pick one provider (or document why two), and hoist model names into `.env` for both (`ANTHROPIC_MODEL` already exists — summarizer should follow the same pattern).
3. **CLAUDE.md drift:** says "no linter config" and lists 3 test files — there are 6 now, plus `.benchmarks/`. Update the Tests section and add the `logs/` convention from P0.3 once done. Also document the cron + launchd surfaces (currently only discoverable via `crontab -l`), so future agents don't edit files a scheduler is mid-run on.
4. `test_*.py` at repo root while code is in packages — move tests to `tests/` with a `pytest.ini` (pytest is already in use per `.pytest_cache`). Low priority.

## P3 — Robustness nits (only if touching the files anyway)

- `run_morning.sh` line ~57: thermostat/padsplit scraper failures under `set -e` abort before `commit_and_push`, so a partial morning's data never lands — intentional? If not, wrap phases like the drafter (`|| true`) and let the run-status payload carry the failure (the Python side already supports partial success).
- `echo "exit code: $?"` after a `set -e` command always prints 0 — dead diagnostics, remove or capture properly.
- `.env` at root is correctly untracked — good; keep `.env*` in `.gitignore` (currently only `.env`? verify).

## Explicitly do NOT

- Do not migrate to a framework (scrapy/playwright/etc.) — the requests+GraphQL approach works and is auth-fragile; churn risk exceeds gain.
- Do not touch `thermostat/set_temps.py` / enforcer logic without a house occupied-schedule test window — it controls real HVAC.
- Do not rewrite git history (P0.2) without pausing cron first.

## Suggested Codex order

1. P0.1 + P0.3 (one commit, stops daily bloat) → run `./run_morning.sh` manually, confirm commit contains only rolling files.
2. P2.1 + P2.3 (rename + docs, one commit).
3. P1 kpis.py extraction **with new unit tests** (one commit), then client/persist split (one commit).
4. P3 + P2.2/P2.4 opportunistically.
