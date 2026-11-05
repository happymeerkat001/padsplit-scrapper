import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import vm from 'node:vm';
const ctx={};vm.runInNewContext(readFileSync('docs/system-health.js','utf8'),ctx);
assert.equal(ctx.healthLabel(undefined),'Not reported');
assert.equal(ctx.healthLabel({state:'ok'}),'Successful');
assert.equal(ctx.healthLabel({state:'degraded'}),'Degraded');
assert.equal(ctx.healthLabel({state:'failed'}),'Failed');
assert.equal(ctx.healthLabel({state:'unknown'}),'Not reported');
assert.equal(ctx.healthTime(undefined),'Not reported');
console.log('system health does not invent collection outcomes');
