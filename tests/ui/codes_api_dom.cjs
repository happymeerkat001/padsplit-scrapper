const assert=require('node:assert/strict');const fs=require('node:fs');const vm=require('node:vm');
const {parseHTML}=require(require('node:path').join(process.env.PADSPLIT_UI_TEST_MODULES||'.','linkedom'));
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 const {window}=parseHTML('<main><button id="all">History</button><div id="panel"></div><div id="list"></div></main>');const document=window.document;
 const ctx={window,document,URL,URLSearchParams,AbortController,console};ctx.globalThis=ctx;vm.createContext(ctx);
 vm.runInContext(fs.readFileSync('docs/codes-schema.js','utf8'),ctx);vm.runInContext(fs.readFileSync('docs/codes-api.js','utf8'),ctx);
 ctx.parseApiCodesSchema=window.parseApiCodesSchema;ctx.mergeApiCodeDraft=window.mergeApiCodeDraft;
 vm.runInContext(fs.readFileSync('docs/codes-api-workspace.js','utf8'),ctx);
 const schema={slug:'fixture_house',address:'Example',sections:[{label:'Main',fields:[{key:'front',label:'Front',value:''}]}]};
 let version=1, fields={front:'fictional-secret',legacy_hidden:'keep'},revealCount=0,saved=null,history=[],failConflict=false;
 const api={codesSchema:async()=>({properties:[schema],versions:{fixture_house:version}}),revealCodes:async()=>{revealCount++;return {fields:{...fields},version}},
  saveCodes:async(id,value,expected)=>{saved={...value};if(failConflict){let e=new Error('conflict');e.status=409;throw e;}assert.equal(expected,version);fields={...value};version++;return {version}},
  codesHistory:async()=>({items:history}),revealCodeHistory:async()=>({fields:{front:'old-fictional-secret'}}),restoreCodes:async(id,hid,expected)=>{assert.equal(expected,version);version++;return {version}}};
 function render(property){const box=document.createElement('details');box.dataset.house=property.slug;box.innerHTML='<summary><div class="flex-between"><button class="save-btn">Save</button></div></summary><div class="property-body"><input data-prop="fixture_house" data-field="front" data-sensitive="true" type="password"></div>';return box;}
 let lost=0;const workspace=window.createApiCodesWorkspace({api,list:document.getElementById('list'),historyButton:document.getElementById('all'),historyPanel:document.getElementById('panel'),renderProperty:render,isCurrent:()=>true,onAccessLost:()=>lost++,confirmChange:()=>true,formatTime:()=> 'time'});
 await workspace.start();assert.equal(revealCount,0);let box=document.querySelector('details'),input=box.querySelector('input');assert.equal(input.value,'');assert.equal(input.disabled,true);assert.equal(box.querySelector('.save-btn').disabled,true);
 box.querySelector('.api-codes-reveal').click();await tick();assert.equal(revealCount,1);assert.equal(input.value,'fictional-secret');assert.equal(input.disabled,false);
 input.value='new-value';input.dispatchEvent(new window.Event('input',{bubbles:true}));box.querySelector('.save-btn').click();await tick();assert.equal(saved.front,'new-value');assert.equal(saved.legacy_hidden,'keep');
 input.value='draft';input.dispatchEvent(new window.Event('input',{bubbles:true}));failConflict=true;box.querySelector('.save-btn').click();await tick();assert.equal(input.value,'draft');assert.equal(box.querySelector('.api-codes-refresh').hidden,false);
 failConflict=false;box.querySelector('.api-codes-refresh').click();await tick();assert.equal(input.value,'new-value');
 history=[{id:'hist1',source_version:1,created_at:'2026-01-01T00:00:00Z'}];box.querySelector('.history-btn').click();await tick();assert.match(box.textContent,/Snapshot · time/);assert.doesNotMatch(box.textContent,/Snapshot v1|old-fictional-secret/);box.querySelector('.api-codes-history-reveal').click();await tick();assert.match(box.textContent,/old-fictional-secret/);box.querySelector('.api-codes-history-reveal').click();assert.doesNotMatch(box.textContent,/old-fictional-secret/);box.querySelector('.api-codes-history-reveal').click();await tick();box.querySelector('.history-btn').click();assert.doesNotMatch(box.textContent,/old-fictional-secret/);box.querySelector('.history-btn').click();await tick();box.querySelector('.api-codes-restore').click();await tick();assert.equal(input.value,'');assert.equal(input.disabled,true);assert.equal(lost,0);
 workspace.stop();assert.equal(document.querySelectorAll('details').length,0);
 const options={api,list:document.getElementById('list'),historyButton:document.getElementById('all'),historyPanel:document.getElementById('panel'),renderProperty:render,isCurrent:()=>true,onAccessLost:()=>lost++,confirmChange:()=>true,formatTime:()=> 'time'};
 const beforeSignout=window.createApiCodesWorkspace(options);await beforeSignout.start();let previous=document.querySelector('details');previous.querySelector('.api-codes-reveal').click();await tick();
 previous.querySelector('input').value='remembered draft';previous.querySelector('input').dispatchEvent(new window.Event('input',{bubbles:true}));const drafts=beforeSignout.captureDrafts();beforeSignout.stop();version++;
 const afterSignin=window.createApiCodesWorkspace({...options,initialDrafts:drafts});await afterSignin.start();let resumed=document.querySelector('details');assert.equal(resumed.querySelector('input').value,'');resumed.querySelector('.api-codes-reveal').click();await tick();assert.equal(resumed.querySelector('input').value,'remembered draft');assert.equal(resumed.querySelector('.save-btn').disabled,true);assert.equal(resumed.querySelector('.api-codes-refresh').hidden,false);afterSignin.stop();
 const broken=window.createApiCodesWorkspace({api:{codesSchema:async()=>({properties:[schema],versions:{}})},list:document.getElementById('list'),historyButton:document.getElementById('all'),historyPanel:document.getElementById('panel'),renderProperty:render,isCurrent:()=>true,onAccessLost:()=>{},confirmChange:()=>true,formatTime:()=>''});
 await assert.rejects(broken.start(),/mismatch/i);assert.equal(document.querySelectorAll('details').length,0);
 console.log('Codes API workspace: hidden defaults, explicit reveal, safe merge, conflict, restore, session cleanup passed');
})().catch(e=>{console.error(e);process.exit(1)});
