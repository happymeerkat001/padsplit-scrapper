// Integration tests use the Firestore emulator, fictional records, and mock auth contexts.
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const vm = require('node:vm');
const {before, after, beforeEach, test} = require('node:test');
const {initializeTestEnvironment, assertFails, assertSucceeds} = require('@firebase/rules-unit-testing');
const {doc, collection, setDoc, getDoc, getDocs, addDoc, deleteDoc, onSnapshot, query, orderBy, serverTimestamp} = require('firebase/firestore');
const OWNER = 'fictional-owner-uid';
const PROJECT = 'demo-padsplit';
const root = resolve(__dirname, '../..');
const helpers = vm.createContext({});
vm.runInContext(readFileSync(resolve(root, 'docs/private-snapshot.js'), 'utf8'), helpers);
vm.runInContext(readFileSync(resolve(root, 'docs/auth-listeners.js'), 'utf8'), helpers);
let env;
const generation = 'a'.repeat(32);
const stamp = '2026-01-01T00:00:00Z';
const privateData = {scraped_at:stamp,messages:[{id:'fictional-thread',lastMessage:{text:'fictional message'}}],tasks:{Requests:[{id:'fictional-task',details:'fictional request'}]},occupancy:{scraped_at:stamp,rooms:[{address:'Fictional House',room_number:'1'}]}};
const serialized = JSON.stringify(privateData);
const split = Math.floor(serialized.length / 2);
const pointer = {schema_version:1,run_id:generation,chunk_count:2,scraped_at:stamp};
const pathFor = i => `private_dashboard_runs/${generation}/chunks/${i}`;
const readFor = db => async path => { const result = await getDoc(doc(db,path)); return result.exists() ? result.data() : null; };
const promiseWithin = (promise, ms=4000) => Promise.race([promise,new Promise((_,reject)=>setTimeout(()=>reject(new Error('listener timed out')),ms))]);
before(async()=>{env=await initializeTestEnvironment({projectId:PROJECT,firestore:{rules:readFileSync(resolve(root,'firestore.rules'),'utf8')}});});
after(async()=>{if(env) await env.cleanup();});
beforeEach(async()=>{
 await env.clearFirestore();
 await env.withSecurityRulesDisabled(async ctx=>{const db=ctx.firestore();
   await setDoc(doc(db,'private_dashboard/current'),pointer);
   await setDoc(doc(db,pathFor(0)),{index:0,data:serialized.slice(0,split)});
   await setDoc(doc(db,pathFor(1)),{index:1,data:serialized.slice(split)});
 });
});
test('the actual private reader obtains all chunks for the owner and rejects anonymous and other users',async()=>{
 const owner=env.authenticatedContext(OWNER, {padsplit_legacy_owner:true}).firestore();
 const result=await helpers.readPrivateSnapshot(readFor(owner));
 assert.deepEqual(JSON.parse(JSON.stringify(result)),privateData);
 for(const db of [env.unauthenticatedContext().firestore(),env.authenticatedContext('other-user').firestore()])
   await assert.rejects(helpers.readPrivateSnapshot(readFor(db)),/permission|PERMISSION_DENIED/i);
});
test('the actual reader rejects an incomplete generation instead of returning partial private data',async()=>{
 await env.withSecurityRulesDisabled(async ctx=>deleteDoc(doc(ctx.firestore(),pathFor(1))));
 const owner=env.authenticatedContext(OWNER, {padsplit_legacy_owner:true}).firestore();
 await assert.rejects(helpers.readPrivateSnapshot(readFor(owner)),/incomplete/i);
});
test('internal task discussion and manual-task mutations require the operator',async()=>{
 const owner=env.authenticatedContext(OWNER, {padsplit_legacy_owner:true}).firestore();
 for(const db of [env.unauthenticatedContext().firestore(),env.authenticatedContext('other-user').firestore()]){
  await assertFails(addDoc(collection(db,'task_messages'),{taskId:'fictional-task',role:'Owner',status:'In Process',text:'fictional note',created:serverTimestamp()}));
  await assertFails(addDoc(collection(db,'manual_tasks'),{who:'Owner',source:'Other',notes:'fictional task',created:serverTimestamp()}));
 }
 const discussion=await assertSucceeds(addDoc(collection(owner,'task_messages'),{taskId:'fictional-task',role:'Owner',status:'In Process',text:'fictional note',created:serverTimestamp()}));
 const manual=await assertSucceeds(addDoc(collection(owner,'manual_tasks'),{who:'Owner',source:'Other',notes:'fictional task',created:serverTimestamp()}));
 const rows=await assertSucceeds(getDocs(query(collection(owner,'task_messages'),orderBy('created','asc'))));
 assert.equal(rows.size,1); assert.equal(rows.docs[0].data().text,'fictional note');
 await assertFails(deleteDoc(doc(env.unauthenticatedContext().firestore(),discussion.path)));
 await assertSucceeds(deleteDoc(discussion)); await assertSucceeds(deleteDoc(manual));
});
test('auth listener manager detaches on sign-out and reattaches for the next session',async()=>{
 const owner=env.authenticatedContext(OWNER, {padsplit_legacy_owner:true}).firestore();
 let initial, reattached; const first=new Promise(resolve=>{initial=resolve}); const second=new Promise(resolve=>{reattached=resolve});
 let notifications=0, starts=0;
 const subscriptions=helpers.createAuthListeners();
 subscriptions.add(()=>{starts++; return onSnapshot(query(collection(owner,'manual_tasks'),orderBy('created','asc')),()=>{notifications++; if(starts===1)initial(); else reattached();});});
 subscriptions.setUser({uid:OWNER}); await promiseWithin(first); assert.equal(starts,1);
 subscriptions.setUser(null); const countAtSignout=notifications;
 await setDoc(doc(owner,'manual_tasks/after-signout'),{who:'Owner',source:'Other',notes:'fictional',created:serverTimestamp()});
 await new Promise(resolve=>setTimeout(resolve,200)); assert.equal(notifications,countAtSignout);
 subscriptions.setUser({uid:OWNER}); await promiseWithin(second); assert.equal(starts,2); assert.ok(notifications>countAtSignout);
 subscriptions.setUser(null);
});

test('Auth emulator SDK sign-in and sign-out propagate identity to Firestore without granting owner access',async()=>{
 const {initializeApp, deleteApp}=require('firebase/app');
 const {getAuth, connectAuthEmulator, setPersistence, inMemoryPersistence, createUserWithEmailAndPassword, signInWithEmailAndPassword, signOut, onAuthStateChanged}=require('firebase/auth');
 const {getFirestore, connectFirestoreEmulator}=require('firebase/firestore');
 const app=initializeApp({projectId:PROJECT,apiKey:'demo-test-key'},'auth-emulator-integration');
 const auth=getAuth(app);
 connectAuthEmulator(auth,'http://127.0.0.1:9198',{disableWarnings:true});
 await setPersistence(auth,inMemoryPersistence);
 const db=getFirestore(app);
 connectFirestoreEmulator(db,'127.0.0.1',8185);
 const email='fictional-operator@example.invalid', password='Fictional-Password-2026!';
 const states=[];
 const stop=onAuthStateChanged(auth,user=>states.push(user?.uid || null));
 try {
  await auth.authStateReady();
  assert.equal(auth.currentUser,null);
  const created=await createUserWithEmailAndPassword(auth,email,password);
  const uid=created.user.uid;
  assert.ok(uid && uid!==OWNER);
  assert.ok(await created.user.getIdToken());
  await assertFails(getDoc(doc(db,'private_dashboard/current')));
  await assertFails(addDoc(collection(db,'task_messages'),{taskId:'fictional-task',text:'must not write'}));
  await signOut(auth);
  assert.equal(auth.currentUser,null);
  await assertFails(getDoc(doc(db,'private_dashboard/current')));
  const restored=await signInWithEmailAndPassword(auth,email,password);
  assert.equal(restored.user.uid,uid);
  assert.equal(auth.currentUser.uid,uid);
  await assertFails(getDoc(doc(db,'private_dashboard/current')));
  await signOut(auth);
  assert.equal(auth.currentUser,null);
  assert.ok(states.includes(uid),'auth state observer received signed-in identity');
  assert.equal(states.at(-1),null);
 } finally {
  stop();
  await deleteApp(app);
 }
});
