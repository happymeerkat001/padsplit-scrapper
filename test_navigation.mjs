import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import vm from 'node:vm';
class E {constructor(tag){this.tag=tag;this.attrs={};this.children=[];this.listeners={};this.dataset={};} setAttribute(k,v){this.attrs[k]=String(v)} removeAttribute(k){delete this.attrs[k]} getAttribute(k){return this.attrs[k]??null} appendChild(x){this.children.push(x)} replaceChildren(...x){this.children=x} addEventListener(k,f){this.listeners[k]=f} click(){this.listeners.click?.()} focus(){this.focused=true} }
const nav=new E('nav');const events={};const ctx={document:{querySelector:()=>nav,createElement:t=>new E(t)},location:{pathname:'/index.html',hash:''},window:{addEventListener:(name,fn)=>{events[name]=fn;}}};
vm.runInNewContext(readFileSync('docs/navigation.js','utf8'),ctx);
assert.equal(ctx.operationsNavigation.filter(item=>item.group==='Operations').length,8);
assert.ok(ctx.operationsNavigation.some(item=>item.group==='Administration'&&item.id==='settings'));
assert.match(ctx.operationsNavIcon('tasks'), /operations-icons\.svg#tasks/);
assert.doesNotMatch(ctx.operationsNavIcon('<script>alert(1)<\/script>'), /script|alert/);
const menu=nav.children.find(n=>n.tag==='button');assert.ok(menu,'mobile menu button');assert.equal(menu.getAttribute('aria-expanded'),'false');menu.click();assert.equal(menu.getAttribute('aria-expanded'),'true');menu.click();assert.equal(menu.getAttribute('aria-expanded'),'false');
menu.click();nav.listeners.keydown({key:'Escape'});assert.equal(menu.getAttribute('aria-expanded'),'false');assert.equal(menu.focused,true);
const links=nav.children.find(n=>n.tag==='div');const anchors=links.children.filter(n=>n.tag==='a');assert.equal(anchors.length,12);assert.equal(anchors[0].getAttribute('aria-current'),'page');assert.equal(links.children.filter(n=>n.className==='ops-group-label').length,2);assert.ok(ctx.operationsNavigation.some(item=>item.id==='integrations'&&item.href==='./integrations.html'));
console.log('mobile navigation toggles and retains page links');

ctx.location.hash='#content'; events.hashchange(); assert.equal(anchors[2].getAttribute('aria-current'),'page'); assert.equal(anchors[0].getAttribute('aria-current'),null);

const stored={}; const root={dataset:{}};
const prefCtx={document:{documentElement:root,querySelector:()=>null},localStorage:{getItem:k=>stored[k]??null,setItem:(k,v)=>stored[k]=v}};
vm.runInNewContext(readFileSync('docs/navigation.js','utf8'),prefCtx);
prefCtx.setOperationsDensity(true);
assert.equal(root.dataset.density,'compact');
assert.equal(stored['operations-density'],'compact');
prefCtx.setOperationsDensity(false);
assert.equal(root.dataset.density,'comfortable');
