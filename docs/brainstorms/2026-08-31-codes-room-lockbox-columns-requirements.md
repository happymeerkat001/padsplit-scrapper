---
date: 2026-08-31
topic: codes-room-lockbox-columns
---

# Codes tab: room door code and lockbox on one row

## Summary

The Codes tab shows each room as one row with Door code and Lockbox side by side. Every property uses that pairing. Existing website values stay. New lockbox numbers from the handwritten sheets are added. Conflicting numbers all remain visible until someone verifies.

## Problem Frame

Door codes and lockboxes live in separate sections, so a house often looks incomplete: five properties have room codes and no lockboxes; Pioneer stores lockboxes as Keys. Operators have to hunt two lists to match a room.

## Key Decisions

**Pair every property.** Room | Door code | Lockbox on one row, including Pioneer Keys as the Lockbox column.

**Union of rooms and lockboxes.** Extra rooms or extra lockboxes each get a row. The missing side is blank.

**Keep maximum data.** Values already on the website stay. Sheet values are added. When numbers disagree, keep every known value. Do not pick a winner.

**House-level fields stay as they are.** Front/back, masters, thermo, WiFi, and contact stay in their current sections.

## Requirements

**Layout**

- R1. Each room row shows Door code and Lockbox in two columns on the same row.
- R2. Every property uses that pairing. Pioneer Keys render as the Lockbox column.
- R3. The table is the union of room door codes and lockboxes. Extra items get a row with the missing side blank.

**Data**

- R4. Every value already on the Codes tab remains visible.
- R5. Handwritten lockbox lists are added for houses that lack lockboxes on the site.
- R6. When sources disagree, the field shows every known number until verification.

**Unchanged**

- R7. Front door, back door, masters, thermo, WiFi, contact, and notes stay in their current sections.
- R8. Lock-reset recipes, eviction process, and a WiFi/thermo cleanup are out of scope.

## Acceptance Examples

- AE1. **Covers R1, R5.** Given Leana has five room codes and no lockboxes on the site, when the page loads, each of R1–R5 shows its existing door code beside the sheet lockbox, and R6 shows a blank door code beside lockbox 4003.
- AE2. **Covers R2.** Given Pioneer has Keys 1–7, when the page loads, those values appear in the Lockbox column on the same rows as R1–R7 door codes.
- AE3. **Covers R4, R6.** Given Pebbleshores Front/Back is already `0961 (or 8338?)`, when defaults are updated, that field still shows both numbers.

## Scope Boundaries

- Do not resolve which conflicting number is current.
- Do not add reset instructions, eviction flow, or a WiFi/thermo audit.
- Do not write codes into `occupancy.json` or other public digest JSON.

## Assumptions

- The five-column lockbox sheet is the lockbox source for Leana, Sylvia, Ridge Oak, Greenhill, and Pebbleshores.
- An empty lockbox slot on the sheet does not create a blank-blank row.
