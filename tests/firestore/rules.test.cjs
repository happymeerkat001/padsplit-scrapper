const {readFileSync} = require('node:fs');
const assert = require('node:assert/strict');
const {resolve} = require('node:path');
const {before, after, beforeEach, test} = require('node:test');
const {initializeTestEnvironment, assertFails, assertSucceeds} = require('@firebase/rules-unit-testing');
const {doc, collection, setDoc, getDoc, getDocs, updateDoc, deleteDoc, query, limit, serverTimestamp, runTransaction} = require('firebase/firestore');
const OPERATOR = '8jOJNgLoxpfyseZ0RY1PDZ1DXbi2';
const STATS_USER = 'TXSU0LOpmDWNBbbv0x3uHHBnZb12';
let env;
const version = {fields:{front_door:'fictional'}, contentHash:'test-hash', createdAt:new Date(), expireAt:new Date('2030-01-01T00:00:00Z'), source:'test'};
const ref = (db,path) => doc(db,path);
const codes = 'property_codes/example';
const history = `${codes}/code_versions`;
before(async () => { env = await initializeTestEnvironment({projectId:'demo-padsplit',firestore:{rules:readFileSync(resolve(__dirname,'../../firestore.rules'),'utf8')}}); });
after(async () => { if(env) await env.cleanup(); });
beforeEach(async () => {
 await env.clearFirestore();
 await env.withSecurityRulesDisabled(async ctx => { const db=ctx.firestore();
  await setDoc(ref(db,codes),{front_door:'fictional'});
  await setDoc(ref(db,`${history}/v1`),version);
  await setDoc(ref(db,'notes/dashboard'),{text:'test'});
  await setDoc(ref(db,'stats/latest'),{json:'{}'});
  await setDoc(ref(db,'leak_alert_pending/test'),{status:'pending'});
 });
});
test('codes and immutable history: only designated operator can read',async()=>{
 for(const uid of [null,'other',STATS_USER]){ const db=uid?env.authenticatedContext(uid).firestore():env.unauthenticatedContext().firestore();
  await assertFails(getDoc(ref(db,codes))); await assertFails(getDoc(ref(db,`${history}/v1`))); await assertFails(getDocs(query(collection(db,history),limit(50)))); }
 const db=env.authenticatedContext(OPERATOR).firestore();
 await assertSucceeds(getDoc(ref(db,codes))); await assertSucceeds(getDoc(ref(db,`${history}/v1`)));
 await assertSucceeds(getDocs(query(collection(db,history),limit(50))));
 await assertFails(getDocs(query(collection(db,history),limit(51)))); await assertFails(getDocs(collection(db,history)));
});
test('history snapshots are create-only and reject extra fields',async()=>{
 const db=env.authenticatedContext(OPERATOR).firestore();
 await assertSucceeds(setDoc(ref(db,`${history}/v2`),version));
 await assertFails(setDoc(ref(db,`${history}/v3`),{...version,unexpected:'no'}));
 await assertFails(updateDoc(ref(db,`${history}/v1`),{source:'changed'}));
 await assertFails(deleteDoc(ref(db,`${history}/v1`)));
 await assertFails(setDoc(ref(env.authenticatedContext('other').firestore(),`${history}/v4`),version));
});
test('restore overwrites parent document but cannot modify history',async()=>{
 const db=env.authenticatedContext(OPERATOR).firestore();
 await assertSucceeds(setDoc(ref(db,codes),{front_door:'restored'}));
 await assertFails(setDoc(ref(db,`${history}/v1`),{...version,source:'changed'}));
 await assertFails(setDoc(ref(env.unauthenticatedContext().firestore(),codes),{front_door:'changed'}));
});
test('actual codes restore transaction requires owner and preserves conflicts',async()=>{
 const vm=require('node:vm');
 const restoreCodeFields=vm.runInThisContext(readFileSync(resolve(__dirname,'../../docs/codes-restore.js'),'utf8')+'\n;restoreCodeFields');
 const owner=env.authenticatedContext(OPERATOR).firestore();
 const options=db=>({run:cb=>runTransaction(db,cb),ref:ref(db,codes),expectedHash:'fictional',
  hash:async fields=>fields.front_door,fields:{front_door:'restored'},timestamp:serverTimestamp,canWrite:()=>true});
 await assert.rejects(()=>restoreCodeFields({...options(owner),expectedHash:'stale'}),e=>e.code==='codes/conflict');
 assert.equal((await getDoc(ref(owner,codes))).data().front_door,'fictional');
 await assertSucceeds(restoreCodeFields(options(owner)));
 assert.equal((await getDoc(ref(owner,codes))).data().front_door,'restored');
 const anon=env.unauthenticatedContext().firestore();
 await assertFails(restoreCodeFields(options(anon)));
});
test('operational collections are owner-only; stats have separate identity',async()=>{
 const anon=env.unauthenticatedContext().firestore(), operator=env.authenticatedContext(OPERATOR).firestore(), other=env.authenticatedContext('other').firestore(), stats=env.authenticatedContext(STATS_USER).firestore();
 const collections=['notes','task_messages','manual_tasks','vendors','templates'];
 for (const name of collections) {
  const path=`${name}/fictional`;
  await env.withSecurityRulesDisabled(async ctx=>setDoc(ref(ctx.firestore(),path),{text:'fictional'}));
  await assertSucceeds(getDoc(ref(operator,path))); await assertSucceeds(getDocs(collection(operator,name)));
  for (const db of [anon,other,stats]) { await assertFails(getDoc(ref(db,path))); await assertFails(getDocs(collection(db,name))); await assertFails(setDoc(ref(db,path),{text:'no'})); }
  await assertSucceeds(setDoc(ref(operator,path),{text:'operator'}));
 }
 await assertFails(getDoc(ref(operator,'stats/latest'))); await assertSucceeds(getDoc(ref(stats,'stats/latest')));
});
test('client access to leak-alert internals and unspecified paths is denied',async()=>{
 for(const db of [env.unauthenticatedContext().firestore(),env.authenticatedContext(OPERATOR).firestore()]){
  await assertFails(getDoc(ref(db,'leak_alert_pending/test'))); await assertFails(setDoc(ref(db,'leak_alert_result_queue/test'),{result:'test'})); await assertFails(getDoc(ref(db,'unspecified/test')));
 }
});

test('history creation requires all five fields with correct types',async()=>{
 const db=env.authenticatedContext(OPERATOR).firestore();
 await assertFails(setDoc(ref(db,`${history}/empty`),{}));
 for(const key of Object.keys(version)) {
  const missing={...version}; delete missing[key];
  await assertFails(setDoc(ref(db,`${history}/missing-${key}`),missing));
 }
 for(const [key,value] of Object.entries({fields:'not-a-map',contentHash:1,createdAt:'not-a-timestamp',expireAt:'not-a-timestamp',source:1})) {
  await assertFails(setDoc(ref(db,`${history}/invalid-${key}`),{...version,[key]:value}));
 }
 // Matches the browser's writeSnapshot payload, including a server timestamp.
 await assertSucceeds(setDoc(ref(db,`${history}/server-time`),{...version,createdAt:serverTimestamp()}));
});
test('private snapshots are readable only by the operator and never client writable', async()=>{
 const pointer='private_dashboard/current', chunk='private_dashboard_runs/0123456789abcdef0123456789abcdef/chunks/0';
 await env.withSecurityRulesDisabled(async ctx=>{
  await setDoc(ref(ctx.firestore(),pointer),{run_id:'0123456789abcdef0123456789abcdef',chunk_count:1,schema_version:1,scraped_at:'2026-01-01T00:00:00Z'});
  await setDoc(ref(ctx.firestore(),chunk),{index:0,data:'{}'});
 });
 const owner=env.authenticatedContext(OPERATOR).firestore();
 await assertSucceeds(getDoc(ref(owner,pointer))); await assertSucceeds(getDoc(ref(owner,chunk)));
 await assertFails(setDoc(ref(owner,pointer),{run_id:'bad'})); await assertFails(setDoc(ref(owner,chunk),{index:0,data:'bad'}));
 await assertFails(getDocs(collection(owner,'private_dashboard_runs/0123456789abcdef0123456789abcdef/chunks')));
 for (const db of [env.unauthenticatedContext().firestore(),env.authenticatedContext('other').firestore(),env.authenticatedContext(STATS_USER).firestore()]) {
  await assertFails(getDoc(ref(db,pointer))); await assertFails(getDoc(ref(db,chunk)));
 }
});

test('private codes schema is owner-readable and never client writable or listable', async()=>{
 const path='private_configuration/codes_schema';
 await env.withSecurityRulesDisabled(async ctx=>setDoc(ref(ctx.firestore(),path),{properties:[]}));
 const owner=env.authenticatedContext(OPERATOR).firestore();
 await assertSucceeds(getDoc(ref(owner,path)));
 await assertFails(setDoc(ref(owner,path),{properties:[]}));
 await assertFails(getDocs(collection(owner,'private_configuration')));
 for (const db of [env.unauthenticatedContext().firestore(),env.authenticatedContext('other').firestore(),env.authenticatedContext(STATS_USER).firestore()]) {
  await assertFails(getDoc(ref(db,path))); await assertFails(setDoc(ref(db,path),{properties:[]}));
 }
});
