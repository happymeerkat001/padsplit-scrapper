const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {parseHTML} = require(require('node:path').join(process.env.PADSPLIT_UI_TEST_MODULES || '.', 'linkedom'));
const html=fs.readFileSync('docs/settings.html','utf8');
assert.match(html,/src="\.\/runtime-config\.js"/);
assert.match(html,/PADSPLIT_RUNTIME_CONFIG/);
assert.match(html,/padsplit-data-mode/);
function setup({api=false,valid=true}={}) {
 const {window}=parseHTML(html),document=window.document,store=new Map();
 for (const select of document.querySelectorAll('select')) {
   let value='browser'; Object.defineProperty(select,'value',{get:()=>value,set:next=>{value=next}});
 }
 const localStorage={getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v)};
 const config=api&&valid?{dataMode:'api',environment:'preview',apiOrigin:'https://api.staging.example.com',firebase:{projectId:'fictional-stage',apiKey:'public-example',authDomain:'fictional-stage.firebaseapp.com'}}:null;
 window.PADSPLIT_SETTINGS_API_REQUIRED=api;window.PADSPLIT_SETTINGS_CONFIG=config;window.PADSPLIT_RUNTIME_CONFIG=config;
 const context={window,document,localStorage,URL,console};vm.createContext(context);
 vm.runInContext(fs.readFileSync('docs/vendors-api.js','utf8'),context);
 context.resolveOperationsRuntime=window.resolveOperationsRuntime;
 vm.runInContext(fs.readFileSync('docs/display-format.js','utf8'),context);
 context.setOperationsDensity=compact=>{localStorage.setItem('operations-density',compact?'compact':'comfortable');return true};
 vm.runInContext(fs.readFileSync('docs/settings.js','utf8'),context);
 return {window,document,store};
}
for(const options of [{},{api:true}]) {
 const {window,document,store}=setup(options);
 const density=document.getElementById('compact-density');density.checked=true;density.dispatchEvent(new window.Event('change'));
 assert.equal(store.get('operations-density'),'compact');
 const zone=document.getElementById('display-timezone'); zone.value='UTC';
 const format=document.getElementById('display-date-format');format.value='day-first';
 document.getElementById('save-display-preferences').click();
 assert.equal(store.get('operations-display-timezone'),'UTC');assert.equal(store.get('operations-display-date-format'),'day-first');
}
const bad=setup({api:true,valid:false});
for(const element of bad.document.querySelectorAll('input,select,button')) assert.equal(element.disabled,true);
assert.match(bad.document.getElementById('settings-status').textContent,/configuration/i);
assert.equal(bad.store.size,0);
console.log('Settings supports local preferences in legacy and API modes, and fails closed on invalid API configuration');
