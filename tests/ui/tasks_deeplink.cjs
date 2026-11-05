const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {parseHTML}=require(path.join(process.env.PADSPLIT_UI_TEST_MODULES,'linkedom'));
async function scenario(id,deny=false){
 const {window}=parseHTML(fs.readFileSync('docs/tasks.html','utf8')),document=window.document;
 let state='open';Object.defineProperty(document.getElementById('task-state'),'value',{get:()=>state,set:v=>{state=v}});
 let calls=0,lost=0;const task={id:'deep-task',property_id:'p1',title:'Private fixture task',status:'open',version:1};
 const api={me:async()=>({uid:'user',role:'viewer'}),properties:async()=>({items:[{id:'p1',name:'Example'}]}),tasks:async()=>({items:[]}),task:async(value)=>{calls++;assert.equal(value,'deep-task');if(deny){const e=Error();e.status=403;throw e}return task},discussion:async()=>({items:[]})};
 const context={window,document,console,setTimeout,clearTimeout};context.globalThis=context;vm.createContext(context);vm.runInContext(fs.readFileSync('docs/tasks-api.js','utf8'),context);
 const ws=window.createTasksWorkspace({api,document,getUid:()=> 'user',isCurrent:()=>true,onAccessLost:()=>lost++,initialTaskId:id});await ws.start();
 if(id==='deep-task'&&!deny){assert.equal(calls,1);assert.equal(document.getElementById('task-title').value,task.title);assert.equal(document.getElementById('task-detail').hidden,false)}
 if(deny){assert.equal(calls,1);assert.equal(lost,0);assert.match(document.getElementById('tasks-status').textContent,/unavailable/i);assert.equal(document.getElementById('task-detail').hidden,true)}
 if(id==='../../private')assert.equal(calls,0);
 ws.stop();
}
(async()=>{await scenario('deep-task');await scenario('deep-task',true);await scenario('../../private');console.log('Scoped task deep links reauthorize, fail closed and reject invalid ids.');})().catch(e=>{console.error(e);process.exit(1)});
