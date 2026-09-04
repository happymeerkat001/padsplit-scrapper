#!/usr/bin/env node
/**
 * Structure-only renderer test. Uses a dummy house with empty/sentinel
 * values only — never loads DEFAULTS and never prints field values.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = join(dirname(fileURLToPath(import.meta.url)));
const html = readFileSync(join(root, "docs/codes.html"), "utf8");

const start = html.indexOf("    const OVERDUE_DAYS = {");
const end = html.indexOf("    async function initCodes()");
if (start < 0 || end < 0 || end <= start) {
  throw new Error("Could not extract codes dashboard renderer");
}
const rendererSrc = html.slice(start, end);

class FakeClassList {
  constructor(el) {
    this.el = el;
  }
  add(name) {
    this.el._classes.add(name);
  }
  remove(name) {
    this.el._classes.delete(name);
  }
}

class FakeEl {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.dataset = {};
    this._classes = new Set();
    this.classList = new FakeClassList(this);
    this.textContent = "";
    this.innerHTML = "";
    this.value = "";
    this.type = "text";
    this.placeholder = "";
    this.disabled = false;
  }
  set className(value) {
    this._classes = new Set(String(value).split(/\s+/).filter(Boolean));
  }
  get className() {
    return [...this._classes].join(" ");
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  addEventListener() {}
}

const fakeDocument = {
  createElement(tag) {
    return new FakeEl(tag);
  }
};

const fn = new Function("document", `${rendererSrc}\nreturn { renderProperty, lockboxCodeKey, roomAcFilterSizeKey, extraLockboxKeys, OVERDUE_DAYS, EXTRA_LOCKBOX_COUNT, ROOM_TABLE_HEADERS };`);
const api = fn(fakeDocument);

const dummy = {
  slug: "fixture_house",
  address: "Fixture House",
  sections: [
    {
      label: "Door Codes",
      fields: [{ key: "front_door", label: "Front", value: "" }]
    },
    {
      label: "Rooms",
      fields: [
        { key: "r1", label: "R1", value: "" },
        { key: "r2", label: "R2", value: "" }
      ]
    },
    {
      label: "Lockboxes",
      fields: [{ key: "lockbox_1", label: "1", value: "MAPPED" }]
    },
    {
      label: "Contact",
      fields: [
        { key: "responsible_tenant", label: "Responsible Tenant", value: "" },
        { key: "ac_filter_date", label: "AC filter date", value: "", type: "date" },
        { key: "ac_filter_size", label: "AC filter size", value: "", placeholder: "16x25x1" },
        { key: "dryer_lint_date", label: "Dryer lint date", value: "", type: "date" },
        { key: "dryer_lint_notes", label: "Dryer lint notes", value: "" }
      ]
    }
  ]
};

function walk(el, visit) {
  visit(el);
  el.children.forEach((child) => walk(child, visit));
}

const tree = api.renderProperty(dummy, {});
const inputs = [];
const texts = [];
walk(tree, (el) => {
  if (el.tagName === "INPUT") inputs.push(el);
  if (el.textContent) texts.push(el.textContent);
});

const fields = inputs.map((el) => el.dataset.field);
const fieldSet = new Set(fields);

assert.equal(fields.length, fieldSet.size, "duplicate data-field inputs");
assert.ok(fieldSet.has("r1") && fieldSet.has("lockbox_1"));
assert.notEqual("r1", "lockbox_1");
assert.ok(fieldSet.has("lockbox_2"), "every room gets a lockbox code field");
assert.ok(fieldSet.has("lockbox_1_location"));
assert.ok(fieldSet.has("lockbox_1_notes"));
assert.ok(fieldSet.has("lockbox_2_location"));
assert.ok(fieldSet.has("r1_ac_filter_size"));
assert.ok(fieldSet.has("r2_ac_filter_size"));
assert.ok(fieldSet.has("ac_filter_size"));
assert.ok(fieldSet.has("ac_filter_date"));
assert.ok(fieldSet.has("dryer_lint_date"));
assert.ok(fieldSet.has("dryer_lint_notes"));
assert.ok(fieldSet.has("extra_lockbox_1_name"));
assert.ok(fieldSet.has("extra_lockbox_1_code"));
assert.ok(fieldSet.has("extra_lockbox_1_location"));
assert.ok(fieldSet.has("extra_lockbox_1_notes"));
assert.ok(fieldSet.has("extra_lockbox_2_code"));
assert.ok(fieldSet.has("front_door"));
assert.equal(inputs.filter((el) => el.dataset.field === "lockbox_1").length, 1);

const lockbox1 = inputs.find((el) => el.dataset.field === "lockbox_1");
assert.equal(lockbox1.value, "MAPPED", "legacy lockbox_N still maps to the code column");

const room1 = inputs.find((el) => el.dataset.field === "r1");
assert.notEqual(room1, lockbox1);

for (const header of api.ROOM_TABLE_HEADERS) {
  assert.ok(texts.includes(header), `missing header ${header}`);
}
assert.ok(texts.includes("Extra Lockboxes"));
assert.ok(!texts.includes("Lockboxes") || texts.includes("Extra Lockboxes"));

assert.equal(api.lockboxCodeKey(3), "lockbox_3");
assert.equal(api.roomAcFilterSizeKey(4), "r4_ac_filter_size");
assert.equal(api.OVERDUE_DAYS.ac_filter_date, 90);
assert.equal(api.OVERDUE_DAYS.dryer_lint_date, 30);
assert.equal(api.EXTRA_LOCKBOX_COUNT, 2);

const payload = {};
inputs.forEach((input) => {
  payload[input.dataset.field] = String(input.value || "").trim();
});
for (const key of [
  "r1",
  "lockbox_1",
  "lockbox_1_location",
  "r1_ac_filter_size",
  "ac_filter_size",
  "dryer_lint_date",
  "extra_lockbox_1_code"
]) {
  assert.ok(Object.prototype.hasOwnProperty.call(payload, key), `payload missing ${key}`);
}

console.log("codes dashboard renderer structure ok");
console.log(`inputs=${inputs.length}`);
