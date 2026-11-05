const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
// Install linkedom@0.18.15 outside the checkout and pass its node_modules path.
const {parseHTML}=require(require('node:path').join(process.env.PADSPLIT_UI_TEST_MODULES || '.', 'linkedom'));
const html=fs.readFileSync(process.env.PADSPLIT_TEST_VENDOR_HTML || 'docs/vendors.html','utf8');
if(process.env.PADSPLIT_TEST_VENDOR_HTML) assert.doesNotMatch(html,/console\.error\(err\)/);
const source=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^\s*import .*;\s*$/gm,'');
const tick=()=>new Promise(r=>setImmediate(r));
async function setup({role='owner',config=true}={}) {
 const {window}=parseHTML(html);const document=window.document;
 document.querySelector('head').insertAdjacentHTML('beforeend','<meta name="padsplit-data-mode" content="api">');
 const form=document.getElementById('vendor-create-form');form.reset=()=>{for(const e of form.querySelectorAll('input'))e.value=''};
 form.elements={namedItem:name=>form.querySelector(`[name="${name}"]`)};
 const dialog=document.getElementById('vendor-dialog');dialog.showModal=()=>{dialog.open=true};dialog.close=()=>{dialog.open=false};
 const auth={currentUser:{uid:'owner',getIdToken:async()=>'fictional'}};
 let callback, firestoreReads=0, polling, stopCount=0, errorStatus=409, apiReads=0, creates=[], updates=[], delayUpdate=false, releaseUpdate;
 let items=[{id:'vendor-1',name:'Fictional vendor',data:{contact:null},version:1}];
 const api={me:async()=>({uid:'owner',role}),vendors:async()=>{apiReads++;return {items}},clearSession:()=>{},
   updateVendor:async(id,body)=>{updates.push({id,body});if(errorStatus){const e=new Error('conflict');e.status=errorStatus;throw e;}if(delayUpdate)return await new Promise(resolve=>{releaseUpdate=resolve});return {version:2}},
   deleteVendor:async()=>{}, createVendor:async(data,key)=>{creates.push({data,key});if(creates.length===1)throw Error('network');return {id:'new'}},
   poll:(load,onData,onError)=>{polling={load,onData,onError};load(new AbortController().signal).then(onData);return()=>{stopCount++}}};
 const ctx={window,document,URL,URLSearchParams,AbortController,console,confirm:()=>true,navigator:{clipboard:{writeText:async()=>{}}},
  setTimeout:()=>1,clearTimeout:()=>{},crypto:{randomUUID:()=>'fictional-request'},
  FormData:class {constructor(f){this.entries=[...f.querySelectorAll('input[name]')].map(e=>[e.name,e.value])}[Symbol.iterator](){return this.entries[Symbol.iterator]()}},
  initializeApp:c=>({options:c}),getAuth:()=>auth,getFirestore:()=>{firestoreReads++;throw Error('unexpected Firestore')},
  collection:()=>{throw Error('unexpected Firestore')},onSnapshot:()=>{throw Error('unexpected Firestore')},
  signInWithEmailAndPassword:async()=>{},sendPasswordResetEmail:async()=>{},signOut:async()=>{},
  onAuthStateChanged:(_,cb)=>{callback=cb},mountPasswordReset:()=>{},createOperationsApi:()=>api};
 window.PADSPLIT_RUNTIME_CONFIG=config?{dataMode:'api',environment:'preview',apiOrigin:'https://api.staging.example.invalid',firebase:{projectId:'fictional-stage',apiKey:'fictional-public-key',authDomain:'fictional-stage.firebaseapp.com'}}:undefined;
 ctx.globalThis=ctx;vm.createContext(ctx);
 for(const file of ['docs/draft-guard.js','docs/editable-search.js','docs/auth-listeners.js','docs/vendors-api.js'])vm.runInContext(fs.readFileSync(file,'utf8'),ctx);
 // The helper normally exports onto window; browser globals resolve as window properties.
 ctx.resolveVendorRuntime=window.resolveVendorRuntime;ctx.loadAllApiVendors=window.loadAllApiVendors;ctx.vendorRemoteDecision=window.vendorRemoteDecision;ctx.vendorChangedFields=window.vendorChangedFields;
 try{vm.runInContext(source,ctx)}catch(e){if(config)throw e;return {document,firestoreReads,error:e};}
 callback(auth.currentUser);await tick();await tick();
 return {document,window,auth,callback,api,form,creates,updates,poll:()=>polling,firestore:()=>firestoreReads,reads:()=>apiReads,stops:()=>stopCount,setItems:x=>{items=x},setError:x=>{errorStatus=x},delay:()=>{delayUpdate=true},release:()=>releaseUpdate({version:2})};
}
(async()=>{
 const bad=await setup({config:false});assert.ok(bad.error);assert.equal(bad.firestoreReads,0);assert.equal(bad.document.querySelector('#gate-form button[type=submit]').disabled,true);
 const viewer=await setup({role:'viewer'});assert.equal(viewer.firestore(),0);assert.equal(viewer.reads(),0);assert.equal(viewer.document.getElementById('add-vendor-btn').disabled,true);
 const partial=await setup();partial.setError(null);const partialCard=partial.document.querySelector('.vendor-card'),partialInput=partialCard.querySelector('.vendor-input');
 partialInput.value='Updated name';partialInput.dispatchEvent(new partial.window.Event('input'));partialCard.querySelector('.v-save-btn').click();await tick();
 assert.equal(partial.updates.length,1);assert.deepEqual(Object.keys(partial.updates[0].body).sort(),['expected_version','name']);
 const concurrent=await setup();concurrent.setError(null);concurrent.delay();const concurrentCard=concurrent.document.querySelector('.vendor-card'),fields=concurrentCard.querySelectorAll('.vendor-input');
 fields[0].value='Changed';fields[0].dispatchEvent(new concurrent.window.Event('input'));concurrentCard.querySelector('.v-save-btn').click();await tick();
 fields[2].value='';fields[2].dispatchEvent(new concurrent.window.Event('input'));concurrent.release();await tick();assert.equal(fields[2].dataset.dirty,'true');
 const x=await setup();assert.equal(x.firestore(),0);assert.equal(x.document.querySelectorAll('.vendor-card').length,1);
 const card=x.document.querySelector('.vendor-card');const input=card.querySelector('.vendor-input');input.value='unsaved draft';input.dispatchEvent(new x.window.Event('input'));
 x.setItems([{id:'vendor-1',name:'Changed elsewhere',data:{contact:'sample'},version:2}]);x.poll().onData(await x.poll().load(new AbortController().signal));
 assert.equal(input.value,'unsaved draft');assert.equal(card.dataset.version,'1');assert.equal(card.querySelector('.v-reload-btn').hidden,false);
 card.querySelector('.v-save-btn').click();await tick();assert.equal(input.value,'unsaved draft');assert.equal(card.querySelector('.v-reload-btn').hidden,false);
 card.querySelector('.v-reload-btn').click();await tick();await tick();assert.equal(input.value,'Changed elsewhere');assert.equal(card.dataset.version,'2');
 x.document.getElementById('add-vendor-btn').click();x.form.elements.namedItem('name').value='Fictional new vendor';
 x.form.dispatchEvent(new x.window.Event('submit',{cancelable:true}));await tick();assert.equal(x.creates.length,1);
 x.form.dispatchEvent(new x.window.Event('submit',{cancelable:true}));await tick();assert.equal(x.creates.length,2);assert.equal(x.creates[0].key,x.creates[1].key);
 x.poll().onError(Object.assign(new Error('denied'),{status:403}));assert.equal(x.document.querySelectorAll('.vendor-card').length,0);assert.equal(x.document.getElementById('add-vendor-btn').disabled,true);assert.ok(x.stops()>0);
 console.log('Vendor page API mode: owner gate, no Firestore, draft conflicts, idempotent retry, refresh, and revocation passed');
})().catch(e=>{console.error(e);process.exit(1)});
