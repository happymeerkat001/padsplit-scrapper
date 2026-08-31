---
title: "fix: Lower Leanna low-temp alert threshold to 74°F"
type: fix
date: 2026-07-18
depth: lightweight
---

# fix: Lower Leanna low-temp alert threshold to 74°F

## Summary

The thermostat schedule enforcer sends a Slack alert when Leanna's live temperature drops below `LOW_TEMP_THRESHOLD_F = 75`. User wants alerts only when the temperature is below 74°F, not 75. Change the constant from 75 to 74.

## Problem Frame

Alerts currently fire at 74.x°F readings (anything below 75). These are noise — only readings strictly below 74°F should notify.

## Requirements

- R1: Slack low-temp alert for Leanna fires only when live temp < 74°F.
- R2: No other alert behavior changes (debounce, target gating, message format stay as-is).

## Key Technical Decisions

- **Single-constant change.** The comparison at `thermostat/schedule.py:499` is `live_temp_f < LOW_TEMP_THRESHOLD_F` and the alert message interpolates the same constant, so changing `LOW_TEMP_THRESHOLD_F` from 75 to 74 updates both the trigger and the Slack message text consistently. "Below the 74 threshold" maps to strict `< 74`, which the existing comparison already implements.
- **No config surface added.** No repo-wide config pattern exists for this value; a per-property config would be scope creep for a one-line change.

## Implementation Units

### U1. Change LOW_TEMP_THRESHOLD_F from 75 to 74

**Goal:** Leanna low-temp alert fires only below 74°F.

**Requirements:** R1, R2

**Dependencies:** none

**Files:**
- `thermostat/schedule.py` (line 36 constant)

**Approach:** Edit `LOW_TEMP_THRESHOLD_F = 75` → `LOW_TEMP_THRESHOLD_F = 74`. The alert gate (`LOW_TEMP_ALERT_TARGET in norm_target`), hourly debounce (`_should_send_temp_alert`), and Slack message all remain unchanged; the message reads "below 74°F threshold" automatically via interpolation.

**Test scenarios:** No existing tests reference `LOW_TEMP_THRESHOLD_F` (verified via grep across the repo). `Test expectation: none -- pure constant change with no test harness covering the alert path; existing suite `test_thermostat_schedule.py` still passes as a regression check.`

**Verification:** `python3 test_thermostat_schedule.py` passes; grep confirms no other reference to the old threshold value in alert logic.

## Scope Boundaries

- Out of scope: making the threshold configurable per property, changing debounce interval, other properties' alerts (none exist).
