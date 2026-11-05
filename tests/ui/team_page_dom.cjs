const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {parseHTML}=require(require('node:path').join(process.env.PADSPLIT_UI_TEST_MODULES||'.','linkedom'));
const html=fs.readFileSync('docs/team.html','utf8'),tick=()=>new Promise(r=>setImmediate(r));
const moduleText=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^\s*import .*;\s*$/gm,'');
async function mount({role='owner',enabled=true}={}){
 const {window}=parseHTML(html),document=window.document,form=document.getElementById('team-form');form.reset=()=>{};
 let selectedRole='viewer';Object.defineProperty(document.getElementById('target-role'),'value',{get:()=>selectedRole,set:value=>{selectedRole=value}});
 const auth={currentUser:{uid:'fictional-owner',getIdToken:async()=>'token'}};let callback,rosterCalls=0,saves=[],memberCalls=0,failConflict=false,release=null,delay=false;
 const member={uid:'fictional-member',role:'operator',active:true,properties:['fixture-house'],version:3};
 const api={me:async()=>({uid:'fictional-owner',role}),properties:async()=>({items:[{id:'fixture-house',name:'Example house'}]}),
  teamMembers:async()=>{rosterCalls++;return {items:[member]}},teamMember:async()=>{memberCalls++;return member;},
  setTeamMember:async(uid,body)=>{saves.push({uid,body});if(failConflict){const e=new Error('conflict');e.status=409;throw e;}if(delay)return new Promise(resolve=>{release=resolve});return {ok:true,version:body.expected_version+1}},clearSession:()=>{}};
 const config={dataMode:'api',environment:'preview',apiOrigin:'https://stage.example.invalid',teamEnabled:enabled,firebase:{projectId:'fictional-stage',apiKey:'fictional',authDomain:'fictional-stage.firebaseapp.com'}};
 window.PADSPLIT_RUNTIME_CONFIG=config;window.confirm=()=>true;
 const context={window,document,URL,URLSearchParams,console,initializeApp:c=>({options:c}),getAuth:()=>auth,signInWithEmailAndPassword:async()=>{},signOut:async()=>{},onAuthStateChanged:(_,cb)=>{callback=cb},createOperationsApi:()=>api};context.globalThis=context;vm.createContext(context);
 vm.runInContext(fs.readFileSync('docs/vendors-api.js','utf8'),context);vm.runInContext(fs.readFileSync('docs/team-page.js','utf8'),context);
 context.resolveOperationsRuntime=window.resolveOperationsRuntime;context.createTeamWorkspace=window.createTeamWorkspace;
 try{vm.runInContext(moduleText,context)}catch(error){if(enabled)throw error;return {document,error,roster:()=>rosterCalls};}
 callback(auth.currentUser);await tick();await tick();
 return {window,document,auth,callback,api,saves,roster:()=>rosterCalls,members:()=>memberCalls,fail:()=>{failConflict=true},delay:()=>{delay=true},release:()=>release({ok:true,version:4})};
}
(async()=>{
 const disabled=await mount({enabled:false});assert.ok(disabled.error);assert.equal(disabled.roster(),0);assert.equal(disabled.document.getElementById('team-signin').hidden,true);
 const viewer=await mount({role:'viewer'});assert.equal(viewer.roster(),0);assert.equal(viewer.document.getElementById('team-workspace').hidden,true);
 const x=await mount();assert.equal(x.roster(),1);assert.equal(x.document.getElementById('team-workspace').hidden,false);
 x.document.querySelector('.team-row').click();await tick();assert.equal(x.members(),1);assert.equal(x.document.getElementById('target-uid').value,'fictional-member');
 const role=x.document.getElementById('target-role');role.value='manager';role.dispatchEvent(new x.window.Event('change'));x.fail();
 const form=x.document.getElementById('team-form');form.dispatchEvent(new x.window.Event('submit',{cancelable:true}));await tick();assert.equal(x.saves[0].body.expected_version,3);assert.equal(role.value,'manager');assert.match(x.document.getElementById('team-status').textContent,/draft is preserved/i);
 x.auth.currentUser=null;x.callback(null);await tick();assert.equal(x.document.getElementById('team-workspace').hidden,true);assert.equal(x.document.getElementById('team-roster').textContent,'');
 const y=await mount();y.document.querySelector('.team-row').click();await tick();y.delay();const yrole=y.document.getElementById('target-role');yrole.value='manager';yrole.dispatchEvent(new y.window.Event('change'));y.document.getElementById('team-form').dispatchEvent(new y.window.Event('submit',{cancelable:true}));await tick();yrole.value='viewer';yrole.dispatchEvent(new y.window.Event('change'));y.release();await tick();assert.equal(yrole.value,'viewer');assert.match(y.document.getElementById('team-status').textContent,/newer unsaved edits/i);
 console.log('Team page: disabled gate, owner check, scoped edit, version conflict, sign-out and in-flight draft passed');
})().catch(e=>{console.error(e);process.exit(1)});
