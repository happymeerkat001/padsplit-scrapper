// Install linkedom@0.18.15 outside the checkout and pass its node_modules path.
const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const {parseHTML}=require(require('node:path').join(process.env.PADSPLIT_UI_TEST_MODULES || '.', 'linkedom'));
const html=fs.readFileSync(process.env.PADSPLIT_TEMPLATES_HTML || 'docs/templates.html','utf8');
const source=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^\s*import .*;\s*$/gm,'');
const tick=()=>new Promise(r=>setImmediate(r));
async function setup({role='owner',config=true}={}) {
 const {window}=parseHTML(html),document=window.document;
 document.querySelector('head').insertAdjacentHTML('beforeend','<meta name="padsplit-data-mode" content="api">');
 const auth={currentUser:{uid:'owner',getIdToken:async()=>'fictional'}};
 let callback,firestoreReads=0,apiReads=0,stops=0;const polls=[],updates=[],savedNotes=[];
 let items=[{id:'template_shared_0',name:'',body:'First body',version:1},{id:'template_shared_8',name:'Ninth',body:'Last body',version:1}], note={id:'templates',text:'Fictional note',version:1,exists:true};
 const api={me:async()=>({uid:'owner',role}),templates:async()=>{apiReads++;return {items}},note:async()=>note,clearSession:()=>{},
  updateTemplate:async(id,body)=>{updates.push({id,body});return {id,version:body.expected_version+1}},saveNote:async(id,text,v)=>{savedNotes.push({id,text,version:v});return {id,version:v+1}},
  poll:(load,onData,onError)=>{const p={load,onData,onError};polls.push(p);load(new AbortController().signal).then(onData);return()=>{stops++}}};
 const ctx={window,document,URL,URLSearchParams,AbortController,console,confirm:()=>true,navigator:{clipboard:{writeText:async()=>{}}},
  setTimeout:()=>1,clearTimeout:()=>{},initializeApp:c=>({options:c}),getAuth:()=>auth,getFirestore:()=>{firestoreReads++;throw Error('unexpected Firestore')},
  doc:()=>{throw Error('unexpected Firestore')},onSnapshot:()=>{throw Error('unexpected Firestore')},
  signInWithEmailAndPassword:async()=>{},sendPasswordResetEmail:async()=>{},signOut:async()=>{},onAuthStateChanged:(_,cb)=>{callback=cb},mountPasswordReset:()=>{},createOperationsApi:()=>api};
 window.PADSPLIT_RUNTIME_CONFIG=config?{dataMode:'api',environment:'preview',apiOrigin:'https://api.staging.example.invalid',firebase:{projectId:'fictional-stage',apiKey:'fictional-public-key',authDomain:'fictional-stage.firebaseapp.com'}}:undefined;
 ctx.globalThis=ctx;vm.createContext(ctx);
 for(const file of ['docs/draft-guard.js','docs/editable-search.js','docs/auth-listeners.js','docs/vendors-api.js','docs/templates-api.js'])vm.runInContext(fs.readFileSync(file,'utf8'),ctx);
 ctx.resolveOperationsRuntime=window.resolveOperationsRuntime;ctx.loadTemplateSlots=window.loadTemplateSlots;
 try{vm.runInContext(source,ctx)}catch(e){if(config)throw e;return {document,error:e,firestoreReads};}
 callback(auth.currentUser);await tick();await tick();
 return {document,window,auth,callback,api,polls,updates,savedNotes,firestore:()=>firestoreReads,reads:()=>apiReads,stops:()=>stops,setItems:x=>{items=x},setNote:x=>{note=x}};
}
(async()=>{
 const bad=await setup({config:false});assert.ok(bad.error);assert.equal(bad.firestoreReads,0);assert.equal(bad.document.querySelector('#gate-form button[type=submit]').disabled,true);
 const viewer=await setup({role:'viewer'});assert.equal(viewer.firestore(),0);assert.equal(viewer.reads(),0);assert.equal(viewer.document.querySelector('#templates-grid textarea').disabled,true);
 const blank=await setup();const blankCard=blank.document.querySelector('.template-card'),blankText=blankCard.querySelector('textarea');
 blankText.value='Edited body';blankText.dispatchEvent(new blank.window.Event('input'));blankCard.querySelector('.save-btn').click();await tick();assert.equal(blank.updates[0].body.name,'');
 const x=await setup();assert.equal(x.firestore(),0);const cards=x.document.querySelectorAll('.template-card');assert.equal(cards.length,9);
 const first=cards[0],text=first.querySelector('textarea');assert.equal(text.value,'First body');assert.equal(text.disabled,false);assert.equal(cards[1].querySelector('textarea').disabled,true);
 text.value='Unsaved draft';text.dispatchEvent(new x.window.Event('input'));
 x.setItems([{id:'template_shared_0',name:'Changed',body:'Remote body',version:2},{id:'template_shared_8',name:'Ninth',body:'Last body',version:1}]);
 x.polls[0].onData(await x.polls[0].load(new AbortController().signal));assert.equal(text.value,'Unsaved draft');assert.equal(first.querySelector('.template-refresh-btn').hidden,false);
 first.querySelector('.template-refresh-btn').click();await tick();await tick();assert.equal(text.value,'Remote body');
 text.value='New body';text.dispatchEvent(new x.window.Event('input'));first.querySelector('.save-btn').click();await tick();assert.equal(x.updates[0].body.expected_version,2);assert.equal(x.updates[0].body.name,'Changed');
 x.polls[0].onError(Object.assign(new Error('unmapped'),{code:'templates/unmapped'}));assert.equal(first.querySelector('.save-btn').disabled,true);assert.equal(text.value,'New body');
 x.setItems([{id:'template_shared_0',name:'Changed',body:'New body',version:3},{id:'template_shared_8',name:'Ninth',body:'Last body',version:1}]);x.polls[0].onData(await x.polls[0].load(new AbortController().signal));assert.equal(first.querySelector('.save-btn').disabled,false);
 const notes=x.document.getElementById('notes-textarea');assert.equal(notes.value,'Fictional note');notes.value='Notes draft';notes.dispatchEvent(new x.window.Event('input'));
 x.setNote({id:'templates',text:'Updated note',version:2,exists:true});x.polls[1].onData(await x.polls[1].load(new AbortController().signal));assert.equal(notes.value,'Notes draft');assert.equal(x.document.getElementById('notes-reload-btn').hidden,false);
 x.document.getElementById('notes-reload-btn').click();await tick();assert.equal(notes.value,'Updated note');
 notes.value='Edited note';notes.dispatchEvent(new x.window.Event('input'));x.document.getElementById('notes-save-btn').click();await tick();assert.equal(x.savedNotes[0].version,2);
 x.polls[0].onError(Object.assign(new Error('denied'),{status:403}));assert.equal(text.value,'');assert.equal(text.disabled,true);assert.equal(notes.value,'');assert.equal(notes.disabled,true);assert.ok(x.stops()>=2);
 console.log('Templates API mode: owner gate, nine-slot mapping, draft conflicts, notes and revocation passed');
})().catch(e=>{console.error(e);process.exit(1)});
