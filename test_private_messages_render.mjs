import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const html=readFileSync(new URL('./docs/private-messages.html',import.meta.url),'utf8');
const start=html.indexOf('    function fmtDate('),end=html.indexOf('    function initApp()',start);
assert.ok(start>=0 && end>start,'message renderer exists');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.className='';this.textContent='';this._html='';this.attributes={};this.events={};this.value='';}
 set innerHTML(value){this._html=value;this.children=[];}
 get innerHTML(){return this._html;}
 appendChild(child){this.children.push(child);return child;}
 replaceChildren(...children){this.children=children;}
 setAttribute(name,value){this.attributes[name]=value;}
 addEventListener(name,fn){this.events[name]=fn;}
 querySelectorAll(selector){return selector==='.conversation-entry'?this.children.filter(c=>c.className.includes('conversation-entry')):[];}
}
const list=new Element('div'),count=new Element('span'),pane=new Element('section');
const document={getElementById(id){return ({list,count,'conversation-pane':pane})[id];},createElement(tag){return new Element(tag);},createTextNode(text){const n=new Element('#text');n.textContent=text;return n;}};
const window={events:{},addEventListener(name,fn){this.events[name]=fn;}};
const storageMap=new Map();
const sessionStorage={setItem(k,v){storageMap.set(k,v);},getItem(k){return storageMap.get(k)||null;},removeItem(k){storageMap.delete(k);}};
const createApi=()=>new Function('document','window','sessionStorage',`${html.slice(start,end)}; return {renderList, selectConversation, allow: () => {privateContentOmitted=false;}, omit: () => {privateContentOmitted=true;}, load: uid => {activeUid=uid;restoreDraftSession(uid);}};`)(document,window,sessionStorage);
const api=createApi();api.load('fictional-user');
api.allow();
const attack='<img src=x onerror=alert(1)>';
const first={id:'one',title:attack,lastMessage:{text:attack,sender:{displayName:attack}}},second={id:'two',title:'Another',lastMessage:{text:'Hello'}};
api.renderList([first,second]);
const nodes=[];(function walk(node){nodes.push(node);node.children.forEach(walk);})(list);(function walk(node){nodes.push(node);node.children.forEach(walk);})(pane);
assert.ok(nodes.some(node=>node.textContent===attack),'untrusted data displayed as text');
assert.ok(nodes.every(node=>!node.innerHTML.includes(attack)),'untrusted data never becomes HTML');
assert.equal(count.textContent,'2 threads');
let draft=pane.children.find(node=>node.id==='conversation-draft');draft.value='unsent';draft.events.input();
list.children[1].events.click();
assert.equal(pane.children.find(node=>node.id==='conversation-draft').value,'');
list.children[0].events.click();
assert.equal(pane.children.find(node=>node.id==='conversation-draft').value,'unsent','draft survives selection changes');
const existingDraft=pane.children.find(node=>node.id==='conversation-draft');
api.renderList([second]);
assert.equal(pane.children.find(node=>node.id==='conversation-draft'),existingDraft,'filtering preserves selected conversation and draft');
const reloaded=createApi();reloaded.load('fictional-user');reloaded.allow();reloaded.renderList([first,second]);
assert.equal(pane.children.find(node=>node.id==='conversation-draft').value,'unsent','draft survives a tab reload');
let prevented=false;const event={preventDefault(){prevented=true;}};window.events.beforeunload(event);assert.ok(prevented,'navigation warns for unsent draft');
api.omit();api.renderList([]);assert.equal(count.textContent,'');assert.match(list.innerHTML,/unavailable/);
console.log('two-pane inbox renders safely and retains drafts across selection and filtering');
