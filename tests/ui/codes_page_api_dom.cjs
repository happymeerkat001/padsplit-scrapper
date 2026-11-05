const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {parseHTML}=require(require('node:path').join(process.env.PADSPLIT_UI_TEST_MODULES||'.','linkedom'));
const html=fs.readFileSync(process.env.PADSPLIT_CODES_HTML||'docs/codes.html','utf8');
const source=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^\s*import .*;\s*$/gm,'');
const tick=()=>new Promise(r=>setImmediate(r));
async function setup({role='owner',configured=true}={}){
 const {window}=parseHTML(html),document=window.document;document.querySelector('head').insertAdjacentHTML('beforeend','<meta name="padsplit-data-mode" content="api">');
 const auth={currentUser:{uid:'fictional-owner',getIdToken:async()=>'fictional'}};let callback,firestoreCalls=0,schemaCalls=0,reveals=0;
 const prop={slug:'fixture_house',address:'Example house',sections:[{label:'Contact',fields:[{key:'front_door',label:'Front',value:''}]}]};
 const api={me:async()=>({uid:'fictional-owner',role}),codesSchema:async()=>{schemaCalls++;return {properties:[prop],versions:{fixture_house:1}}},revealCodes:async()=>{reveals++;return {version:1,fields:{front_door:'fictional-secret',legacy_hidden:'keep'}}},clearSession:()=>{},note:async()=>({version:0,text:'',exists:false}),saveNote:async()=>({version:1}),poll:(load,onData)=>{load(new AbortController().signal).then(onData);return()=>{}},
 codesHistory:async()=>({items:[]}),saveCodes:async()=>({version:2}),restoreCodes:async()=>({version:3})};
 const ctx={window,document,URL,URLSearchParams,AbortController,console,navigator:{clipboard:{writeText:async()=>{}}},setTimeout:()=>1,clearTimeout:()=>{},formatDisplayTime:x=>String(x),
 initializeApp:c=>({options:c}),getAuth:()=>auth,getFirestore:()=>{firestoreCalls++;throw Error('unexpected Firestore')},doc:()=>{throw Error('unexpected Firestore')},
 onAuthStateChanged:(_,cb)=>{callback=cb},signInWithEmailAndPassword:async()=>{},sendPasswordResetEmail:async()=>{},signOut:async()=>{},mountPasswordReset:()=>{},createOperationsApi:()=>api};
 window.confirm=()=>true;window.PADSPLIT_RUNTIME_CONFIG=configured?{dataMode:'api',environment:'preview',apiOrigin:'https://stage.example.invalid',firebase:{projectId:'fictional-stage',apiKey:'fictional-key',authDomain:'fictional-stage.firebaseapp.com'}}:undefined;
 ctx.globalThis=ctx;vm.createContext(ctx);
 for(const file of ['docs/draft-guard.js','docs/owner-notes.js','docs/codes-restore.js','docs/codes-schema.js','docs/vendors-api.js','docs/api-note.js','docs/codes-api.js','docs/codes-api-workspace.js'])vm.runInContext(fs.readFileSync(file,'utf8'),ctx);
 for(const k of ['resolveOperationsRuntime','createApiNoteBinding','parseApiCodesSchema','mergeApiCodeDraft','createApiCodesWorkspace'])ctx[k]=window[k];
 try{vm.runInContext(source,ctx)}catch(error){if(configured)throw error;return {error,firestore:()=>firestoreCalls,document};}
 callback(auth.currentUser);await tick();await tick();await tick();
 return {document,window,auth,callback,api,firestore:()=>firestoreCalls,schemas:()=>schemaCalls,reveals:()=>reveals};
}
(async()=>{
 const missing=await setup({configured:false});assert.ok(missing.error);assert.equal(missing.firestore(),0);assert.equal(missing.document.querySelector('#gate-form button[type=submit]').disabled,true);
 const viewer=await setup({role:'viewer'});assert.equal(viewer.firestore(),0);assert.equal(viewer.schemas(),0);assert.equal(viewer.document.querySelector('#codes-list details'),null);
 const x=await setup();assert.equal(x.firestore(),0);assert.equal(x.schemas(),1);assert.equal(x.reveals(),0);
 let node=x.document.querySelector('#codes-list details');assert.ok(node);let input=node.querySelector('[data-field="front_door"]');assert.equal(input.value,'');assert.equal(input.disabled,true);
 node.querySelector('.api-codes-reveal').click();await tick();assert.equal(input.value,'fictional-secret');assert.equal(x.reveals(),1);
 input.value='draft-after-sign-in';input.dispatchEvent(new x.window.Event('input',{bubbles:true}));
 x.auth.currentUser=null;x.callback(null);await tick();assert.equal(x.document.querySelector('#codes-list details'),null);
 x.auth.currentUser={uid:'fictional-owner',getIdToken:async()=>'fictional'};x.callback(x.auth.currentUser);await tick();await tick();node=x.document.querySelector('#codes-list details');input=node.querySelector('[data-field="front_door"]');assert.equal(input.value,'');node.querySelector('.api-codes-reveal').click();await tick();assert.equal(input.value,'draft-after-sign-in');assert.equal(x.firestore(),0);
 console.log('Codes page API mode: runtime isolation, owner gate, hidden values, same-owner draft recovery passed');
})().catch(e=>{console.error(e);process.exit(1)});
