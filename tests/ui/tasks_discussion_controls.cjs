const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {parseHTML}=require(path.join(process.env.PADSPLIT_UI_TEST_MODULES,'linkedom'));
async function scenario(role){
 const {window}=parseHTML(fs.readFileSync('docs/tasks.html','utf8')),document=window.document,tick=()=>new Promise(resolve=>setImmediate(resolve));
 let state='open',roleLabel='Repairman #1',statusLabel='In Process';
 for(const [id,get,set] of [['task-state',()=>state,v=>state=v],['discussion-role',()=>roleLabel,v=>roleLabel=v],['discussion-status',()=>statusLabel,v=>statusLabel=v]])Object.defineProperty(document.getElementById(id),'value',{get,set});
 let posts=[],deleted=[];const api={me:async()=>({uid:'u1',role}),properties:async()=>({items:[{id:'p1',name:'Example'}]}),tasks:async()=>({items:[{id:'task1',property_id:'p1',title:'Example',status:'open',version:1}]}),discussion:async()=>({items:[{id:'d1',author_uid:'real-uid',source_role:'Owner',source_status:'Done',body:'Example'}]}),addDiscussion:async(...args)=>posts.push(args),deleteDiscussion:async(...args)=>deleted.push(args)};
 const ctx={window,document,console,setTimeout,clearTimeout,crypto:{randomUUID:()=> 'fixture-key'}};ctx.globalThis=ctx;vm.createContext(ctx);vm.runInContext(fs.readFileSync('docs/tasks-api.js','utf8'),ctx);
 const ws=window.createTasksWorkspace({api,document,getUid:()=> 'u1',isCurrent:()=>true,onAccessLost:()=>assert.fail('revocation'),confirmChange:()=>true});await ws.start();document.querySelector('[data-open-task]').click();await tick();
 assert.match(document.getElementById('task-discussions').textContent,/Owner.*Example.*Done/);
 const button=document.querySelector('[data-delete-discussion]');
 if(role==='viewer'){assert.equal(button,null);document.getElementById('discussion-body').value='hello';document.getElementById('discussion-form').dispatchEvent(new window.Event('submit',{cancelable:true}));await tick();assert.equal(posts.length,0);}
 else {assert.ok(button);button.click();await tick();assert.deepEqual(deleted[0],['task1','d1']);document.getElementById('discussion-body').value='hello';document.getElementById('discussion-form').dispatchEvent(new window.Event('submit',{cancelable:true}));await tick();assert.equal(posts[0][0],'task1');assert.equal(posts[0][1],'hello');assert.equal(posts[0][2],'fixture-key');assert.equal(posts[0][3].roleLabel,'Repairman #1');assert.equal(posts[0][3].statusLabel,'In Process');let release;api.addDiscussion=()=>new Promise(resolve=>{release=resolve});document.getElementById('discussion-body').value='pending draft';document.getElementById('discussion-form').dispatchEvent(new window.Event('submit',{cancelable:true}));await tick();statusLabel='Done';release();await tick();assert.equal(document.getElementById('discussion-body').value,'pending draft');}
 ws.stop();
}
(async()=>{await scenario('manager');await scenario('viewer');console.log('Discussion role/status labels and scoped deletion controls passed.');})().catch(e=>{console.error(e);process.exit(1)});
