import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync('docs/stats.html','utf8');
const start=source.indexOf('    function computeManualKpis('),end=source.indexOf('    function applyManualInputs()',start);
assert.ok(start>=0&&end>start,'legacy score formula must exist for parity test');
const legacy={};vm.createContext(legacy);
vm.runInContext('const HUSTLE_FIELDS=["daily_calls","todo_completion","ownership","other_hustle"]; function toNum(v){const n=Number(v);return Number.isFinite(n)?n:0;}\n'+source.slice(start,end),legacy);
const api={};vm.createContext(api);vm.runInContext(readFileSync('docs/stats-api.js','utf8'),api);
for(const occupancy of [0,69.9,70,74.9,75,79.9,80,89.9,90,100])for(const tenure of [0,139.9,140,151.9,152,159.9,160,179.9,180])for(const count of [0,1,9]){
 const v={occupancy_pct:occupancy,avg_tenure_days:tenure,rooms_over_14d:count,listed_count:count,rooms_over_21d:count,avg_flip_days:5,flip_rooms_over_5d:count,open_tickets:count,tickets_over_7d:count,tickets_over_14d:count,daily_calls:5,todo_completion:3,ownership:2,other_hustle:0};
 const old=legacy.computeManualKpis({},v),fresh=api.calculateManualScore(v);
 assert.equal(fresh.score,old.score,JSON.stringify(v));
 assert.equal(fresh.bonus,old.bonus_points,JSON.stringify(v));
 assert.equal(fresh.penalty,-old.penalty_points,JSON.stringify(v));
 const normalized=Math.max(0,Math.min(100,old.score));
 assert.equal(fresh.payout,normalized<=80?normalized*1000:80000+(normalized-80)*11000);
}
console.log('Manual score and payout match legacy thresholds across 270 boundary scenarios');
