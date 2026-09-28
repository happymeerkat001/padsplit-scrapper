/**
 * Notes-pad save failures. Extracts the same helper from every page that
 * writes notes/{doc}. Never prints note text.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import test from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)));
const PAGES = [
  "docs/templates.html",
  "docs/stats.html",
  "docs/codes.html",
  "docs/index.html",
  "docs/private-messages.html",
];
const TOO_LONG = "too long to save (20,000 character limit). nothing was saved.";
const SAVE_FAILED = "couldn't save. your text is still here. sign in again and retry.";

function normalize(block) {
  const lines = block.replace(/\r\n/g, "\n").split("\n").slice(1);
  const indents = lines.filter((line) => line.trim()).map((line) => line.match(/^ */)[0].length);
  const min = Math.min(...indents);
  return lines.map((line) => (line.trim() ? line.slice(min) : "")).join("\n").trim();
}

function extract(html) {
  const start = html.indexOf("// begin notes-save");
  const end = html.indexOf("// end notes-save");
  assert.ok(start >= 0 && end > start, "notes-save helper missing");
  return normalize(html.slice(start, end));
}

function loadPad(source) {
  const factory = new Function(
    `${source}\nreturn bindNotesPad;`
  );
  return factory();
}

function fakePad(bind, writeText) {
  const textarea = { value: "" };
  const errorEl = { textContent: "", hidden: true };
  const saveBtn = { textContent: "Save", disabled: false };
  const pad = bind(textarea, errorEl, saveBtn, writeText);
  return { textarea, errorEl, saveBtn, pad };
}

const htmlByPage = PAGES.map((file) => ({
  file,
  html: readFileSync(join(root, file), "utf8"),
}));
const helpers = htmlByPage.map((page) => extract(page.html));
const bind = loadPad(helpers[0]);

test("every notes page uses the same save helper", () => {
  for (const helper of helpers) {
    assert.equal(helper, helpers[0]);
  }
  for (const page of htmlByPage) {
    assert.equal(page.html.split('id="notes-save-error"').length, 2);
    assert.ok(page.html.includes("notesPad.save()"));
    assert.ok(page.html.includes("notesPad.applyServerText("));
    assert.ok(page.html.includes("notesPad.onListenerError()"));
    assert.equal(page.html.includes("maxlength"), false);
    assert.equal(page.html.includes("characters left"), false);
  }
});

test("a note at the limit is not written", async () => {
  let calls = 0;
  const ui = fakePad(bind, async () => { calls += 1; });
  ui.textarea.value = "x".repeat(20000);
  await ui.pad.save();
  assert.equal(calls, 0);
  assert.equal(ui.textarea.value.length, 20000);
  assert.equal(ui.errorEl.hidden, false);
  assert.equal(ui.errorEl.textContent, TOO_LONG);
  assert.equal(ui.saveBtn.textContent, "Save");
  assert.equal(ui.saveBtn.disabled, false);
});

test("a shorter note is written and shows saved only after the write resolves", async () => {
  let calls = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const ui = fakePad(bind, (text) => {
    calls += 1;
    assert.equal(text.length, 19999);
    return gate;
  });
  ui.errorEl.textContent = TOO_LONG;
  ui.errorEl.hidden = false;
  ui.textarea.value = "x".repeat(19999);
  const pending = ui.pad.save();
  assert.equal(ui.saveBtn.textContent, "Saving…");
  assert.equal(ui.pad.applyServerText("old", false), false);
  assert.equal(ui.textarea.value.length, 19999);
  release();
  await pending;
  assert.equal(calls, 1);
  assert.equal(ui.saveBtn.textContent, "Saved!");
  assert.equal(ui.errorEl.hidden, true);
  assert.equal(ui.errorEl.textContent, "");
  assert.equal(ui.pad.applyServerText("stored", false), true);
  assert.equal(ui.textarea.value, "stored");
});

test("a denied or failed write keeps the text and the error", async () => {
  for (const failure of [
    Object.assign(new Error("denied"), { code: "permission-denied" }),
    new Error("network"),
  ]) {
    const ui = fakePad(bind, async () => { throw failure; });
    ui.textarea.value = "draft";
    await ui.pad.save();
    assert.equal(ui.textarea.value, "draft");
    assert.equal(ui.errorEl.hidden, false);
    assert.equal(ui.errorEl.textContent, SAVE_FAILED);
    assert.equal(ui.saveBtn.textContent, "Save");
    assert.equal(ui.pad.applyServerText("server", false), false);
    assert.equal(ui.textarea.value, "draft");
    assert.equal(ui.pad.onListenerError(), false);
    assert.equal(ui.textarea.value, "draft");
  }
});

test("the next successful save clears the error", async () => {
  let fail = true;
  const ui = fakePad(bind, async () => {
    if (fail) throw new Error("network");
  });
  ui.textarea.value = "draft";
  await ui.pad.save();
  assert.equal(ui.errorEl.textContent, SAVE_FAILED);
  fail = false;
  await ui.pad.save();
  assert.equal(ui.errorEl.hidden, true);
  assert.equal(ui.errorEl.textContent, "");
  assert.equal(ui.saveBtn.textContent, "Saved!");
  assert.equal(ui.textarea.value, "draft");
});
