import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const context={}; vm.runInNewContext(readFileSync('docs/overview-session.js','utf8'),context);
const calls=[], drafts={text:'draft'};
const session=context.createOverviewSession({
 canAccess:user=>user?.uid==='owner',
 stopListeners:()=>calls.push('stop'), captureDrafts:()=>{calls.push('capture');return drafts;},
 clearPrivate:()=>calls.push('clear'), restoreDrafts:state=>calls.push(state===drafts?'restore saved':'restore empty'),
 startListeners:()=>calls.push('start')
});
session.transition(null); assert.deepEqual(calls,['clear']); calls.length=0;
session.transition({uid:'owner'}); assert.deepEqual(calls,['clear','restore empty','start']); calls.length=0;
session.transition(null); assert.deepEqual(calls,['capture','stop','clear']); calls.length=0;
session.transition({uid:'other'}); assert.deepEqual(calls,[]); calls.length=0;
session.transition({uid:'owner'}); assert.deepEqual(calls,['clear','restore saved','start']); calls.length=0;
session.transition({uid:'owner'}); assert.deepEqual(calls,[]);
console.log('overview session clears private content and restores same-account drafts');
