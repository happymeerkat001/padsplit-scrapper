import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const context = {};
vm.runInNewContext(readFileSync('docs/codes-restore.js', 'utf8'), context);
const helper = context.restoreCodeFields;
let writes = [];
let current = {front: 'fixture-current'};
const ref = {};
const run = async callback => callback({
  get: async () => ({exists: () => true, data: () => current}),
  set: (_ref, value) => writes.push(value),
});
const options = {run, ref, expectedHash:'fixture-current', hash: async x => x.front,
  fields:{front:'fixture-restored'}, timestamp:() => 'server-time', canWrite:() => true};
current = {front:'other-writer'};
await assert.rejects(() => helper(options), error => error.code === 'codes/conflict');
assert.equal(writes.length, 0, 'conflicting live changes must not be overwritten');
current = {front:'fixture-current'};
await assert.rejects(() => helper({...options, canWrite:() => false}), error => error.code === 'codes/session-changed');
assert.equal(writes.length, 0, 'a changed session must not write');
await helper(options);
assert.equal(writes.length, 1);
assert.equal(writes[0].front, 'fixture-restored');
assert.equal(writes[0].updatedAt, 'server-time');
console.log('codes restore rejects conflicts and checks the owner session inside the transaction');
