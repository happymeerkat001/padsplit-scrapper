const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {before,after,beforeEach,test} = require('node:test');
const {initializeTestEnvironment,assertFails,assertSucceeds} = require('@firebase/rules-unit-testing');
const {doc,collection,setDoc,getDoc,getDocs} = require('firebase/firestore');
const OWNER = '8jOJNgLoxpfyseZ0RY1PDZ1DXbi2';
let env;
before(async()=>{env=await initializeTestEnvironment({projectId:'demo-padsplit',firestore:{rules:readFileSync(resolve(__dirname,'../../firestore.rules'),'utf8')}})});
after(async()=>{if(env)await env.cleanup()});
beforeEach(async()=>{await env.clearFirestore();await env.withSecurityRulesDisabled(async ctx=>{
 const db=ctx.firestore();
 for(const [uid,role,active,properties] of [['manager','manager',true,['house_a']],['operator','operator',true,['house_a']],['viewer','viewer',true,['house_a']],['inactive','manager',false,['house_a']]])
  await setDoc(doc(db,'team_members',uid),{role,active,properties});
 await setDoc(doc(db,'team_invitations','viewer'),{status:'pending'});
 await setDoc(doc(db,'team_admin_audit','event'),{actor:'fixture',action:'invite',outcome:'success'});
 await setDoc(doc(db,'property_workspace','house_a','tasks','task'),{title:'fixture',status:'open'});
 await setDoc(doc(db,'property_workspace','house_b','tasks','task'),{title:'fixture',status:'open'});
})});
test('team records are private and never client writable, including by owner',async()=>{
 const owner=env.authenticatedContext(OWNER).firestore();
 const viewer=env.authenticatedContext('viewer').firestore();
 const other=env.authenticatedContext('other').firestore();
 await assertSucceeds(getDocs(collection(owner,'team_members')));
 await assertSucceeds(getDoc(doc(viewer,'team_members','viewer')));
 await assertFails(getDoc(doc(viewer,'team_members','manager')));
 await assertFails(getDocs(collection(viewer,'team_members')));
 await assertSucceeds(getDoc(doc(viewer,'team_invitations','viewer')));
 await assertFails(getDoc(doc(other,'team_invitations','viewer')));
 for(const db of [owner,viewer,other,env.unauthenticatedContext().firestore()]){
  await assertFails(setDoc(doc(db,'team_members','viewer'),{role:'owner',active:true,properties:['house_b']}));
  await assertFails(setDoc(doc(db,'team_invitations','viewer'),{status:'accepted'}));
  await assertFails(setDoc(doc(db,'team_admin_audit','forged'),{actor:OWNER}));
 }
 await assertSucceeds(getDocs(collection(owner,'team_admin_audit')));
 await assertFails(getDocs(collection(viewer,'team_admin_audit')));
});
test('scoped workspace reads require active assigned membership, and writes require trusted server',async()=>{
 for(const uid of ['manager','operator','viewer']){
  const db=env.authenticatedContext(uid).firestore();
  await assertSucceeds(getDoc(doc(db,'property_workspace','house_a','tasks','task')));
  await assertFails(getDoc(doc(db,'property_workspace','house_b','tasks','task')));
  await assertFails(setDoc(doc(db,'property_workspace','house_a','tasks','forged'),{title:'x'}));
 }
 for(const uid of ['inactive','other'])await assertFails(getDoc(doc(env.authenticatedContext(uid).firestore(),'property_workspace','house_a','tasks','task')));
 await assertFails(getDoc(doc(env.unauthenticatedContext().firestore(),'property_workspace','house_a','tasks','task')));
 await assertSucceeds(getDoc(doc(env.authenticatedContext(OWNER).firestore(),'property_workspace','house_b','tasks','task')));
});
