---
title: "fix: Populate missing Discord GitHub Actions secrets"
type: fix
date: 2026-08-24
---

# fix: Populate missing Discord GitHub Actions secrets

## Summary

Messages and task digests never reach Discord because the GitHub Actions workflows still carry only the old `SLACK_*` secrets. `send_to_discord()` reads the unset `DISCORD_*` env vars, prints a skip message, and returns — so the job completes green with nothing actually sent. This plan sets the missing secrets and adds a preflight check so a missing secret fails the job loudly instead of silently.

---

## Problem Frame

The prior refactor (`docs/plans/2026-08-23-001-refactor-discord-migration-plan.md`) moved all notification code from Slack to Discord and updated `.github/workflows/*.yml` to read `DISCORD_*` secrets. `gh secret list` confirms the repository never received those secrets — only the original `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `SLACK_WEBHOOK_MESSAGES`, `SLACK_WEBHOOK_TASKS`, and `SLACK_WEBHOOK_URL` exist. `gh run list` shows the "Summarize Messages" workflow completing successfully at the exact time the user expected a Discord post — because `send_to_discord()` treats an unset webhook as "skip, don't error" (intentional for local ad-hoc runs, per the migration plan's U1). In CI, that same behavior hides a real misconfiguration behind a green checkmark.

---

## Requirements

- R1. Every `DISCORD_*` secret referenced by production code — `DISCORD_WEBHOOK_MESSAGES`, `DISCORD_WEBHOOK_TASKS`, `DISCORD_WEBHOOK_URL` (read by the four workflows), plus `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID` (read by `padsplit_scraper/discord_reply_monitor.py`, not currently invoked by any workflow) — is set to a working value in GitHub Actions.
- R2. If `DISCORD_WEBHOOK_MESSAGES` or `DISCORD_WEBHOOK_TASKS` is missing at run time, the "Summarize Messages" and "Discord Digests" jobs fail visibly instead of completing green with a silent no-op send.

---

## Key Technical Decisions

- **Preflight check lives in workflow YAML, not Python:** `send_to_discord()`'s graceful skip-if-unset behavior is intentional for local runs (documented in the prior migration plan). Changing that function to raise would also change local behavior. A CI-only preflight step is the narrower fix.
- **Preflight check scoped to the two primary-path workflows:** `summarize_messages.yml` and `slack_digest.yml` are the cases where a missing secret currently produces a misleadingly green job — that's the actual bug reported. `scrape.yml` and `thermostat.yml` only read a `DISCORD_*` secret in their `if: failure()` notify step; the underlying job already reports red from the scrape/thermostat step itself, so a missing notify secret is lower-severity (see Scope Boundaries).
- **Secret values are set via `gh secret set`, sourced from local `.env`, never written into this plan or committed to the repo.**

---

## Scope Boundaries

**In scope:** setting the 5 missing `DISCORD_*` secrets; adding a missing-secret preflight check to `summarize_messages.yml` and `slack_digest.yml`.

**Deferred to follow-up work:**
- The same preflight check for `scrape.yml` and `thermostat.yml`'s failure-notify step.
- Removing the now-unused `SLACK_*` secrets from GitHub (harmless to leave; pure cleanup).
- Wiring `message_summarizer.py` / `slack_task_digest.py` into `run_morning.sh` / `run_afternoon.sh`. The current split — GitHub Actions cron owns digest cadence, local launchd scripts own scrape/draft/thermostat — is the existing design and works once secrets exist; there's no evidence it needs to change.

---

### U1. Populate missing Discord GitHub Actions secrets

**Goal:** Give the already-migrated workflows real credentials to send with.

**Requirements:** R1

**Dependencies:** None

**Files:** None — operational change, no repo files modified.

**Approach:** For each of `DISCORD_WEBHOOK_MESSAGES`, `DISCORD_WEBHOOK_TASKS`, `DISCORD_WEBHOOK_URL`, `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`: read the value from the local `.env` and set it with `gh secret set <NAME>`, piping the value in rather than passing it as a CLI argument, so it never appears in shell history or process listings. Do not print secret values to terminal output. The first three are read by workflow YAML today; `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID` aren't consumed by any current workflow (only by `discord_reply_monitor.py`, run outside CI) — set for parity with the local `.env` so nothing is silently missing if that script is ever wired into a workflow.

**Test scenarios:**
- `Test expectation: none -- operational credential change, not code.`

**Verification:** `gh secret list` shows all 5 `DISCORD_*` names with a fresh `updatedAt` timestamp. Trigger the "Summarize Messages" and "Discord Digests" workflows via `workflow_dispatch` and confirm a message actually lands in the Discord channel — not just that the job reports success.

---

### U2. Add missing-secret preflight checks to the primary-path Discord workflows

**Goal:** Turn a silent no-op send into a visible job failure when a required Discord secret is absent.

**Requirements:** R2

**Dependencies:** U1 for the happy-path verification only (needs a real secret to confirm no regression). The preflight check's failure-path logic does not depend on U1 and can be written and tested for the empty-secret case first.

**Files:**
- `.github/workflows/summarize_messages.yml`
- `.github/workflows/slack_digest.yml`

**Approach:** Add a step before each job's send step that checks its required env var (`DISCORD_WEBHOOK_MESSAGES` in `summarize_messages.yml`, `DISCORD_WEBHOOK_TASKS` in `slack_digest.yml`'s `task-digest` job) is non-empty, and fails the job with a `::error::`-annotated message if not. Plain shell step, matching the existing step style in these files — no new marketplace action.

**Patterns to follow:** Existing step shape in these two files (`checkout` → `setup-python` → env-scoped `run` step).

**Test scenarios:**
- Secret present and non-empty → preflight step passes, send step runs normally. Exercise via `workflow_dispatch` after U1 sets the real secret.
- Secret absent or empty → job fails at the preflight step with an actionable error message; the send step does not run. Exercise on a throwaway branch: temporarily rename the `env:` key so it no longer matches the secret reference (e.g., `DISCORD_WEBHOOK_MESSAGES_TEST:` instead of `DISCORD_WEBHOOK_MESSAGES:`), run via `workflow_dispatch`, and confirm the job goes red at the preflight step rather than the send step. Revert the rename before merging.

**Verification:** Manually trigger both workflows once via `workflow_dispatch` with the secret present (confirms no regression), then once with the secret value blanked (confirms the job fails loudly at the preflight step with a clear message rather than completing green).

---

## Risks & Dependencies

- Requires `gh` CLI authenticated with secret-write access to the repo (already available in this environment, confirmed by `gh secret list` succeeding).
- Secret values must not be echoed to logs or written to any file in the repo, including this plan.
