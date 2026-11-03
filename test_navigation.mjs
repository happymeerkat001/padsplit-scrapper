import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import vm from 'node:vm';
class E {constructor(tag){this.tag=tag;this.attrs={};this.children=[];this.listeners={};this.dataset={};} setAttribute(k,v){this.attrs[k]=String(v)} removeAttribute(k){delete this.attrs[k]} getAttribute(k){return this.attrs[k]??null} appendChild(x){this.children.push(x)} replaceChildren(...x){this.children=x} addEventListener(k,f){this.listeners[k]=f} click(){this.listeners.click?.()} focus(){this.focused=true} }
const nav=new E('nav');const events={};const ctx={document:{querySelector:()=>nav,createElement:t=>new E(t)},location:{pathname:'/index.html',hash:''},window:{addEventListener:(name,fn)=>{events[name]=fn;}}};
vm.runInNewContext(readFileSync('docs/navigation.js','utf8'),ctx);
const menu=nav.children.find(n=>n.tag==='button');assert.ok(menu,'mobile menu button');assert.equal(menu.getAttribute('aria-expanded'),'false');menu.click();assert.equal(menu.getAttribute('aria-expanded'),'true');menu.click();assert.equal(menu.getAttribute('aria-expanded'),'false');
menu.click();nav.listeners.keydown({key:'Escape'});assert.equal(menu.getAttribute('aria-expanded'),'false');assert.equal(menu.focused,true);
const links=nav.children.find(n=>n.tag==='div');assert.ok(links.children.length>=10);assert.equal(links.children[0].getAttribute('aria-current'),'page');
console.log('mobile navigation toggles and retains page links');

ctx.location.hash='#content'; events.hashchange(); assert.equal(links.children[2].getAttribute('aria-current'),'page'); assert.equal(links.children[0].getAttribute('aria-current'),null);
