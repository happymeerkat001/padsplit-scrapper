import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import vm from 'node:vm';
const ctx={};vm.runInNewContext(readFileSync('docs/password-reset.js','utf8'),ctx);
assert.equal(ctx.validResetEmail('person@example.test'),true);
assert.equal(ctx.validResetEmail('not-an-email'),false);
assert.equal(ctx.resetErrorMessage({code:'auth/network-request-failed'}),'Network error. Check your connection and try again.');
assert.equal(ctx.resetErrorMessage({code:'auth/too-many-requests'}),'Too many requests. Please wait and try again.');
assert.equal(ctx.resetErrorMessage({code:'auth/user-not-found'}),'If an eligible account exists for this email, you’ll receive password reset instructions.');
let calls=0;const send=async(auth,email)=>{calls++;assert.equal(email,'person@example.test')};
assert.equal(await ctx.requestPasswordReset(send,{},' person@example.test '),'If an eligible account exists for this email, you’ll receive password reset instructions.');
assert.equal(calls,1);await assert.rejects(ctx.requestPasswordReset(send,{},'invalid'));assert.equal(calls,1);
console.log('password reset validates input and does not disclose account existence');
class Node {
 constructor(tag='div'){this.tag=tag;this.style={display:'flex'};this.dataset={};this.listeners={};this.children=[];this.value='';this.hidden=false;this.disabled=false;this.textContent='';}
 setAttribute(k,v){this[k]=v;} append(...v){this.children.push(...v);} after(...v){this.siblings=v;} addEventListener(event,fn){this.listeners[event]=fn;} focus(){this.focused=true;}
}
const dom={document:{createElement:tag=>new Node(tag)}};vm.createContext(dom);vm.runInContext(readFileSync('docs/password-reset.js','utf8'),dom);
const form=new Node('form'), signin=new Node('input');let called=0;
dom.mountPasswordReset({auth:{},sender:async()=>{called++},form,signInEmail:signin});
const [trigger,reset]=form.siblings;assert.equal(reset.hidden,true);trigger.listeners.click();assert.equal(reset.hidden,false);assert.equal(form.hidden,true);
const [title,info,label,email,feedback,submit,back]=reset.children;email.value='bad';await reset.listeners.submit({preventDefault(){}});assert.equal(called,0);assert.match(feedback.textContent,/valid email/);
email.value='person@example.test';await reset.listeners.submit({preventDefault(){}});assert.equal(called,1);assert.match(feedback.textContent,/If an eligible account/);back.listeners.click();assert.equal(reset.hidden,true);assert.equal(form.hidden,false);assert.equal(trigger.focused,true);
console.log('password reset form shows feedback and returns keyboard focus');
assert.equal(await ctx.requestPasswordReset(async()=>{throw {code:'auth/user-not-found'}},{},'nobody@example.test'),'If an eligible account exists for this email, you’ll receive password reset instructions.');
assert.equal(await ctx.requestPasswordReset(async()=>{throw {code:'auth/user-disabled'}},{},'nobody@example.test'),'If an eligible account exists for this email, you’ll receive password reset instructions.');
assert.equal(await ctx.requestPasswordReset(async()=>{throw {code:'auth/network-request-failed'}},{},'nobody@example.test'),'Network error. Check your connection and try again.');
let resolveSend, concurrentCalls=0;const secondForm=new Node('form');dom.mountPasswordReset({auth:{},sender:()=>{concurrentCalls++;return new Promise(resolve=>{resolveSend=resolve;});},form:secondForm,signInEmail:new Node('input')});
const [open2,reset2]=secondForm.siblings;open2.listeners.click();const email2=reset2.children[3];email2.value='person@example.test';const first=reset2.listeners.submit({preventDefault(){}});await reset2.listeners.submit({preventDefault(){}});assert.equal(concurrentCalls,1);resolveSend();await first;
