import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const scope = {};
vm.runInNewContext(fs.readFileSync('docs/overview-insights.js', 'utf8'), scope);
const lists = {
  occupiedAfterMoveOut: [{address:'<img src=x>',room_number:'1'}],
  vacantNotReady: [{address:'Example Lane',room_number:'2'}],
  incoming: [{address:'Sample Road',room_number:'3',next_move_in:'2026-12-01'}]
};
const result = scope.buildOverviewInsights(lists);
assert.equal(result.attention.length,2);
assert.equal(result.attention[0].kind,'Occupied after listed move-out');
assert.equal(result.attention[1].kind,'Vacant, not rent-ready');
assert.equal(result.upcoming[0].date,'2026-12-01');
const html = scope.renderOverviewInsights(result);
assert.ok(html.attention.includes('&lt;img')); assert.ok(!html.attention.includes('<img'));
assert.ok(html.upcoming.includes('2026-12-01'));
assert.ok(scope.renderOverviewInsights(scope.buildOverviewInsights({})).attention.includes('No known'));
console.log('overview insights preserve occupancy categories and escape untrusted labels');
