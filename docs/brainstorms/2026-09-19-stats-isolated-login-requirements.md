---
title: "Isolate Stats behind a stats-only login"
type: requirements
date: 2026-09-19
---

# Isolate Stats behind a stats-only login

## Summary

Vendors keep occupancy. Stats and KPI History stay behind their own login. Only `stats-only@padsplit-scraper.com` (UID `TXSU0LOpmDWNBbbv0x3uHHBnZb12`) can unlock money. An old Codes or dashboard session must never auto-open those pages.

## Problem Frame

The operator clicked Stats while already signed in elsewhere and saw money. Codes and Stats must not share a session. Past Stats viewers cannot be reconstructed; this product does not invent that history.

## Requirements

### Lock

- R1. Opening Stats or KPI History always shows a Stats-only auth screen first.
- R2. Only Firebase user `stats-only@padsplit-scraper.com` with UID `TXSU0LOpmDWNBbbv0x3uHHBnZb12` unlocks those pages.
- R3. A Codes, Dashboard, Vendors, or Templates session does not unlock Stats.

### Session

- R4. On arrival at Stats or KPI History, any prior Stats auth session is cleared so the visitor must sign in again with the stats-only account.
- R5. Clearing the Stats session does not sign the visitor out of Codes or the rest of the dashboard.

### Non-goals

- R6. Occupancy, tasks, and tenancy stay on the public dashboard.
- R7. Do not reconstruct who viewed Stats in the past. Firebase last-sign-in is not a Stats audit.

## Success Criteria

- A vendor or Codes user who clicks Stats sees only the lock screen.
- Signing in with the stats-only email and password reveals prices and payout.
- Signing in with the Codes password does not.
- After a prior Codes or old Stats session, a refresh of Stats still shows the lock screen until the stats-only password is entered.

## Scope Boundaries

### In scope

- Isolate Stats and KPI History to the confirmed stats-only account
- Kill leftover Stats sessions so money does not auto-unlock

### Out of scope

- Historical login reconstruction
- A going-forward audit log of every unlock
- Changing the Codes password or Codes allowlist

## Key Decisions

- D1. Stats-only identity is `stats-only@padsplit-scraper.com` / `TXSU0LOpmDWNBbbv0x3uHHBnZb12`.
- D2. Force re-login on the Stats surface only. Do not force-sign-out Codes or Dashboard.
- D3. No past-viewer report. That data was never collected.

## Acceptance Examples

- AE1. Operator is signed into Codes, clicks Stats, and sees the Stats auth screen with no prices.
- AE2. Operator signs in as `stats-only@padsplit-scraper.com` and sees the score card and listed prices.
- AE3. Operator opens KPI History while still signed into Codes only and sees the same Stats lock, not payout charts.
