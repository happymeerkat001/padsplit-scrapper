import assert from 'node:assert/strict'; import {readFileSync} from 'node:fs'; import vm from 'node:vm';
const ctx={};vm.runInNewContext(readFileSync('docs/owner-notes.js','utf8'),ctx);
function el(){return {value:'',dataset:{},textContent:'',disabled:false,handlers:{},addEventListener(k,f){this.handlers[k]=f;}};}
const ta=el(),saveBtn=el(),copyBtn=el(),status=el();let cb,unsub=0;
const notes=ctx.createOwnerNotesSession({ownerUid:'owner',ta,saveBtn,copyBtn,status,subscribe:(next)=>{cb=next;return()=>unsub++;},save:async()=>{},copy:async()=>{}});
assert.equal(ta.disabled,true,'notes are disabled before auth resolution');
notes.setUser(null);assert.equal(ta.disabled,true);
notes.setUser({uid:'other'});assert.equal(ta.disabled,true);
notes.setUser({uid:'owner'});assert.equal(ta.disabled,false);cb({exists:()=>true,data:()=>({text:'remote'})});assert.equal(ta.value,'remote');
ta.ownerDocument={activeElement:ta};cb({exists:()=>true,data:()=>({text:'interrupt'})});assert.equal(ta.value,'remote','focused notes are not replaced by a snapshot');ta.ownerDocument.activeElement=null;
ta.value='unsent';ta.handlers.input();const old=cb;notes.setUser(null);assert.equal(unsub,1);assert.equal(ta.value,'');assert.equal(ta.disabled,true);
old({exists:()=>true,data:()=>({text:'late'})});assert.equal(ta.value,'');
notes.setUser({uid:'owner'});assert.equal(ta.value,'unsent');cb({exists:()=>true,data:()=>({text:'new remote'})});assert.equal(ta.value,'unsent');
console.log('owner notes gate, cleanup and same-account draft restoration ok');
