import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const ctx = {};
vm.runInNewContext(readFileSync('docs/draft-guard.js', 'utf8'), ctx);
const input = {dataset:{}, value:'initial'};
assert.equal(ctx.canApplyRemote(input, null), true);
ctx.markDraft(input);
assert.equal(ctx.canApplyRemote(input, null), false, 'unsaved draft survives blur');
assert.equal(ctx.canApplyRemote(input, input), false, 'focused field is never overwritten');
input.value = 'newer edit';
ctx.markDraftSaved(input, 'initial');
assert.equal(input.dataset.dirty, 'true', 'late save cannot clear a newer edit');
ctx.markDraftSaved(input, 'newer edit');
assert.equal(ctx.canApplyRemote(input, null), true, 'successful save clears the matching draft');
const label = {dataset:{}, textContent:'label', isContentEditable:true};
ctx.markDraft(label);
ctx.markDraftSaved(label, 'label');
assert.equal(ctx.canApplyRemote(label, null), true);
console.log('secondary draft guard: ok');
for (const name of ['templates','private-messages']) {
  const html = readFileSync(`docs/${name}.html`, 'utf8');
  assert.match(html, /draft-guard\.js/, `${name} loads draft guard`);
  assert.match(html, /canApplyRemote\((?:notesTA|ta), document\.activeElement\)/, `${name} protects notes against snapshots`);
  assert.match(html, /markDraftSaved\((?:notesTA|ta), saved\)/, `${name} clears saved note only after success`);
}
for (const name of ['codes','stats']) {
  const html = readFileSync(`docs/${name}.html`, 'utf8');
  assert.match(html, /owner-notes\.js/, `${name} loads owner notes`);
  assert.match(html, /createOwnerNotesSession\(/, `${name} uses the owner notes session`);
}
const templates=readFileSync('docs/templates.html','utf8');
assert.match(templates, /canApplyRemote\(ta, document\.activeElement\)/);
assert.match(templates, /markDraftSaved\(textarea, saved\)/);
const vendors=readFileSync('docs/vendors.html','utf8');
assert.match(vendors, /canApplyRemote\(inp, document\.activeElement\)/);
assert.match(vendors, /markDraftSaved\(input, submitted\[key\]\)/);
assert.match(vendors, /if \(addVendorBtn\.disabled\) return;/, 'duplicate add is blocked while pending');
assert.match(vendors, /Could not add vendor/, 'add failure is visible');
assert.match(vendors, /Could not delete vendor/, 'delete failure is visible');
