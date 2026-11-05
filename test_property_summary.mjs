import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const ctx = {};
vm.runInNewContext(readFileSync('docs/property-summary.js','utf8'),ctx);
const result = ctx.summarizePropertyRooms([
  {property_id:1,address:'Example Lane',rent_ready:true,occupant_present:false},
  {property_id:1,address:'Example Lane',rent_ready:false,occupant_present:true},
  {property_id:2,address:'<img src=x onerror=alert(1)>',rent_ready:null,occupant_present:null},
  {address:'Unlinked',rent_ready:true}
]);
assert.equal(result.rows.length,2);
const example = result.rows.find(row => row.label === 'Example Lane');
assert.equal(example.id,'1');
assert.equal(example.rooms,2); assert.equal(example.ready,1);
assert.equal(example.present,1); assert.equal(example.presenceUnknown,0);
assert.equal(result.rows.find(row => row.label.includes('<img')).presenceUnknown,1);
assert.equal(result.rows.find(row => row.label.startsWith('<img')).unknown,1);
assert.equal(result.unlinked,1);
const html=ctx.renderPropertySummary(result);
assert.ok(html.includes('&lt;img')); assert.ok(!html.includes('<img'));
assert.ok(html.includes('1 room without a property identifier'));
assert.ok(html.includes('Present')); assert.ok(html.includes('Presence unknown'));
const selectable=ctx.renderPropertySummary(result,{selectable:true});
assert.ok(selectable.includes('data-property-scope="1"')); assert.ok(!html.includes('data-property-scope='));
assert.equal(ctx.matchesPropertyScope({property_id:1},'1'),true);
assert.equal(ctx.matchesPropertyScope({property_id:2},'1'),false);
assert.equal(ctx.matchesPropertyScope({},'1'),false);
assert.equal(ctx.matchesPropertyScope({},null),true);
assert.ok(ctx.renderPropertySummary(ctx.summarizePropertyRooms([])).includes('No property records'));
const conflicting = ctx.summarizePropertyRooms([{property_id:4,address:'A'}, {property_id:4,address:'B'}]);
assert.equal(conflicting.rows[0].label,'Property 4');
assert.equal(ctx.summarizePropertyRooms([{property_id:{}, address:'Invalid'}]).unlinked,1);
console.log('property overview uses identified rooms and escapes property labels');
