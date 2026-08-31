---
title: "feat: Pair room door codes and lockboxes"
type: feat
date: 2026-08-31
origin: docs/brainstorms/2026-08-31-codes-room-lockbox-columns-requirements.md
---

# feat: Pair room door codes and lockboxes

## Summary

Change the Codes tab so each room is one row with Door code and Lockbox side by side. Keep every value already on the site. Add the handwritten lockbox lists. Show all conflicting numbers until verification.

## Problem Frame

`docs/codes.html` rendered each section as its own label-plus-input table. Operators could not scan a room's door code and lockbox together. (see origin: `docs/brainstorms/2026-08-31-codes-room-lockbox-columns-requirements.md`)

## Requirements

Carried from origin: R1–R8.

## Key Technical Decisions

KTD1. One Rooms section with two value columns. Drop standalone Lockboxes / Other tables.
KTD2. Reuse field keys: `rN`, `lockbox_N`, Pioneer `keys_N`.
KTD3. Do not rewrite existing conflict strings.
KTD4. Omit empty-empty rows.
KTD5. Do not copy codes into occupancy or `docs/data/`.

## Implementation Units

### U1. Room row layout

Rooms use `{ label, door, lockbox }` and render `Room | Door code | Lockbox`. Blank sides stay keyed empty inputs.

### U2. Defaults union

DEFAULTS match the pairing table. `test_codes_room_lockbox.py` is the spec.
