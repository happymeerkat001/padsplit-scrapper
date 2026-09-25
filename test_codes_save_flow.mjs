#!/usr/bin/env node
/**
 * Save-flow helpers. Uses fixture keys only — never prints stored codes.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = join(dirname(fileURLToPath(import.meta.url)));
const html = readFileSync(join(root, "docs/codes.html"), "utf8");
const start = html.indexOf("    const LOCK_NOTE = ");
const end = html.indexOf("    // end save-flow helpers");
if (start < 0 || end < 0 || end <= start) {
  throw new Error("Could not extract save-flow helpers");
}
const api = new Function(
  `${html.slice(start, end)}\nreturn { LOCK_NOTE, SAVE_FAILURE, houseDisplayName, changeLabels, saveConfirmLine, formatSavedTime, sameUpdatedAt, conflictMessage, nextEditingSlug };`
)();

assert.equal(api.LOCK_NOTE, "this updates the record only. it doesn't change the lock.");
assert.equal(api.SAVE_FAILURE, "didn't save, nothing changed. try again.");
assert.equal(api.houseDisplayName("10235 Ridge Oak"), "ridge oak");
assert.equal(api.houseDisplayName("6623 Leana"), "leana");

const labels = api.changeLabels(
  { front_door: "WAS", r3: "ROOM", r1: "SAME" },
  { front_door: "NOW", r3: "", r1: "SAME" }
);
assert.deepEqual(labels, ["front_door", "clear r3"]);
assert.equal(
  api.saveConfirmLine("ridge oak", labels),
  "save 2 changes to ridge oak? front_door, clear r3"
);
assert.equal(
  api.saveConfirmLine("ridge oak", ["front_door"]),
  "save 1 change to ridge oak? front_door"
);
assert.deepEqual(api.changeLabels({ r3: "" }, { r3: "" }), []);
assert.deepEqual(api.changeLabels({ r3: "ROOM" }, { r3: "   " }), ["clear r3"]);

assert.equal(api.formatSavedTime(new Date(2026, 8, 25, 18, 12)), "saved 6:12pm");
assert.equal(api.formatSavedTime(new Date(2026, 8, 25, 9, 5)), "saved 9:05am");
assert.equal(api.formatSavedTime(new Date(2026, 8, 25, 0, 0)), "saved 12:00am");
assert.equal(api.formatSavedTime(new Date(2026, 8, 25, 12, 0)), "saved 12:00pm");

assert.equal(api.conflictMessage("ridge oak"), "ridge oak changed since you opened it. refresh first.");

assert.equal(api.sameUpdatedAt(null, null), true);
assert.equal(api.sameUpdatedAt(undefined, null), true);
assert.equal(api.sameUpdatedAt(null, "2026-01-01T00:00:00.000Z"), false);
const sameStamp = { toMillis() { return 10; } };
assert.equal(api.sameUpdatedAt(sameStamp, { toMillis() { return 10; } }), true);
assert.equal(api.sameUpdatedAt(sameStamp, { toMillis() { return 11; } }), false);
assert.equal(api.sameUpdatedAt({ seconds: 1, nanoseconds: 2 }, { seconds: 1, nanoseconds: 2 }), true);
assert.equal(api.sameUpdatedAt({ seconds: 1, nanoseconds: 2 }, { seconds: 1, nanoseconds: 3 }), false);
assert.equal(api.sameUpdatedAt("2026-01-01T00:00:00.000Z", { toMillis() { return 1; } }), false);

assert.equal(api.nextEditingSlug(null, "ridge_oak_10235", false, () => false), "ridge_oak_10235");
assert.equal(api.nextEditingSlug("ridge_oak_10235", "pioneer_1404", false, () => false), "pioneer_1404");
assert.equal(api.nextEditingSlug("ridge_oak_10235", "pioneer_1404", true, () => false), "ridge_oak_10235");
assert.equal(api.nextEditingSlug("ridge_oak_10235", "pioneer_1404", true, () => true), "pioneer_1404");
assert.equal(api.nextEditingSlug("ridge_oak_10235", "ridge_oak_10235", true, () => true), "ridge_oak_10235");

function sliceBetween(source, startMark, endMark) {
  const start = source.indexOf(startMark);
  const from = start + startMark.length;
  const end = source.indexOf(endMark, from);
  if (start < 0 || end < 0) throw new Error(`missing ${startMark} or ${endMark}`);
  return source.slice(from, end);
}

const commit = sliceBetween(html, "async function commitHouseSave", "function bindHouse");
assert.ok(commit.indexOf("codes-conflict") < commit.indexOf("transaction.set"), "conflict aborts before any write");
const failureAt = commit.indexOf("} catch (err)");
if (failureAt < 0) throw new Error("missing save failure catch");
const failure = commit.slice(failureAt);
assert.equal(failure.includes(".value"), false, "failure leaves the typed fields alone");
assert.equal(failure.includes("refreshHouse"), false);
assert.ok(failure.includes("SAVE_FAILURE"));
assert.ok(failure.includes("conflictMessage("));
assert.ok(commit.includes("source: 'page'"));
assert.ok(commit.includes("updatedAt: serverTimestamp()"));

const openEdit = sliceBetween(html, "function openHouseEdit", "function showSaveConfirm");
assert.ok(openEdit.includes("nextEditingSlug"));
assert.ok(openEdit.includes("restoreHouseFields"));
assert.ok(openEdit.includes("setHouseEditing(editingSlug, false)"));

console.log("codes save flow ok");
