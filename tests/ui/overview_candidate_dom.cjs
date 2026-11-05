const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {parseHTML}=require(path.join(process.env.PADSPLIT_UI_TEST_MODULES,'linkedom'));
const html=fs.readFileSync('deploy/frontend-api/index.html','utf8');
async function exercise(role,hasData=false,inflight=false,weatherError=false) {
 const {window}=parseHTML(html),{document}=window,store=new Map();
 const storage={getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)};
 const user={uid:'test-user'};let onAuth;const auth={currentUser:user};let weatherCalls=0, observedCalls=0,dataActive=hasData,address='Example property',occupancyWait=null,releaseOccupancy;const timers=[];
 const api={me:async()=>({uid:user.uid,role,properties:role==='owner'?[]:['example']}),
   occupancy:async()=>{if(occupancyWait)await occupancyWait;return {items:dataActive?[{room_id:'room-1',observation_id:'1',property_id:'example',observed_at:'2026-01-01T00:00:00Z',data:{address,room_number:'1',occupant_present:false,vacant:true,rent_ready:false}}]:[],ingestion:'staging_only'}},climate:async()=>({items:dataActive?[{device_key:'device-1',property_id:'example',observed_at:'2026-01-01T00:00:00Z',data:{location_name:'Example property',name:'Example device',temp:70}}]:[],ingestion:'staging_only'}),
   providerTasks:async()=>{observedCalls++;return {items:dataActive?[{task_key:'task-1',observation_id:'observation-1',source_id:'source-1',bucket:'Open',property_id:'example',observed_at:'2026-01-01T00:00:00Z',data:{status:'Open',details:'Example request'}}]:[],ingestion:'staging_only'}},roomTaskLinks:async()=>({items:[],coverage:'reviewed_links_only',ingestion:'staging_only'}),providerDiscussionLinks:async()=>({items:[{task_key:'task-1',task_observation_id:'observation-1',internal_task_id:'internal-1',property_id:'example'}],coverage:'reviewed_links_only',ingestion:'staging_only'}),
   weather:async()=>{weatherCalls++;if(weatherError)throw new Error('<img src=x onerror=alert(1)>');return {status:'not_configured',days:[]}},
   poll:()=>()=>{},clearSession:()=>{},note:async()=>({text:'',version:0}),saveNote:async()=>({version:1})};
 const context={window,document,console,URL,Intl,Date,Map,Set,localStorage:storage,sessionStorage:storage,location:{hash:''},navigator:{clipboard:{writeText:async()=>{}}},
  setInterval:()=>0,clearInterval:()=>{},setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length},clearTimeout:()=>{},
  fetch:async url=>{assert.doesNotMatch(String(url),/open-meteo/);return {ok:true,json:async()=>({scraped_at:null,tasks:{},private_content_omitted:true,run_status:{}})}},
  initializeApp:()=>({}),getAuth:()=>auth,onAuthStateChanged:(_auth,cb)=>{onAuth=cb},signOut:async()=>{auth.currentUser=null;await onAuth(null)}};
 context.globalThis=context;window.localStorage=storage;window.location=context.location;window.PADSPLIT_RUNTIME_CONFIG={dataMode:'api',environment:'preview',apiOrigin:'https://stage.example.com',firebase:{projectId:'demo-private',authDomain:'demo-private.firebaseapp.com',apiKey:'fake'}};
 vm.createContext(context);
 for(const match of html.matchAll(/<script(?: defer)? src="\.\/([^"/]+\.js)"/g)){
  const name=match[1];if(name==='runtime-config.js'||name==='navigation.js'||name==='overview-views.js')continue;
  vm.runInContext(fs.readFileSync('docs/'+name,'utf8'),context,{filename:name});
 }
 for(const name of ['resolveOperationsRuntime','createApiNoteBinding','createOverviewApiTasks'])context[name]=window[name];
 window.createOperationsApi=()=>api;context.createOperationsApi=()=>api;
 const module=html.split('<script type="module">')[1].split('</script>')[0].replace(/^\s*import .*;\s*$/gm,'');
 vm.runInContext(module,context,{filename:'overview-candidate-inline.js'});
 await onAuth(user);for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
 assert.equal(observedCalls,1);if(hasData){assert.match(document.getElementById('private-sync').textContent,/Provider tasks observed/);assert.match(document.getElementById('content').textContent,/Example request/);const link=[...document.querySelectorAll('a')].find(a=>a.textContent==='Open reviewed task discussion');assert.ok(link);assert.equal(link.getAttribute('href'),'./tasks.html#task=internal-1');assert.match(document.getElementById('thermostat-section').textContent,/70/);}else assert.match(document.getElementById('private-sync').textContent,/No verified provider task/);
 const section=document.getElementById('weather-section');assert.equal(weatherCalls,role==='owner'?1:0);assert.equal(!!section.querySelector('img'),false);assert.doesNotMatch(section.textContent,/<img/);assert.match(section.textContent,role!=='owner'?/owner only/:weatherError?/unavailable/:/not configured/);
 assert.equal(document.getElementById('overview-notes').hidden,role!=='owner');
 if(hasData)assert.match(document.getElementById('occupancy-content').textContent,/Example property/);else assert.match(document.getElementById('occupancy-content').textContent,/No verified occupancy/);
 if(hasData){
  const dialog=document.querySelector('.property-detail-dialog');
  Object.defineProperty(dialog,'open',{get:()=>dialog.hasAttribute('open')});
  dialog.showModal=()=>dialog.setAttribute('open','');dialog.close=()=>dialog.removeAttribute('open');
  if(inflight){
   occupancyWait=new Promise(resolve=>{releaseOccupancy=resolve});
   const refresh=timers.find(t=>t.ms===300000);assert.ok(refresh);const pending=refresh.fn();
   await new Promise(resolve=>setImmediate(resolve));
   document.querySelector('[data-room-detail]').click();assert.ok(dialog.hasAttribute('open'));
   assert.match(dialog.textContent,/Example property/);
   if(inflight==='updated')address='Updated example';else dataActive=false;
   releaseOccupancy();await pending;
   assert.doesNotMatch(dialog.textContent,/Example property/,'a changed or unavailable observation must clear an already open detail');
   if(inflight==='updated')assert.match(document.getElementById('occupancy-content').textContent,/Updated example/);
  }else{
  document.querySelector('[data-room-detail]').click();assert.ok(dialog.hasAttribute('open'));
  dataActive=false;const refresh=timers.find(t=>t.ms===300000);assert.ok(refresh);await refresh.fn();
  assert.equal(observedCalls,1,'refresh must defer while the property detail is open');
  assert.match(document.getElementById('content').textContent,/Example request/);
  dialog.close();const retry=timers.find(t=>t.ms===5000);assert.ok(retry);await retry.fn();
  for(let i=0;i<4;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.doesNotMatch(document.getElementById('content').textContent,/Example request/,'empty refreshed data must clear stale task records');
  }
 }
 auth.currentUser=null;await onAuth(null);assert.match(document.getElementById('occupancy-content').textContent,/Sign in/);
}
(async()=>{await exercise('owner');await exercise('viewer',true);await exercise('viewer',true,true);await exercise('viewer',true,'updated');await exercise('owner',false,false,true);console.log('Overview candidate handles owner/viewer, changed observations, safe error text, open details and sign-out.');})().catch(e=>{console.error(e);process.exit(1)});
