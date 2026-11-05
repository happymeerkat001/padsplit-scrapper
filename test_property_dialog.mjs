import assert from 'node:assert/strict'; import {readFileSync} from 'node:fs'; import vm from 'node:vm';
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.listeners={};this.open=false;this.isConnected=true;this._text='';}
 set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 setAttribute(key,value){this.attributes[key]=String(value);} getAttribute(key){return this.attributes[key]??null;}
 append(...items){this.children.push(...items);} appendChild(item){this.children.push(item);return item;}
 replaceChildren(...items){this.children=[...items];this._text='';}
 addEventListener(type,fn){this.listeners[type]=fn;} removeEventListener(type){delete this.listeners[type];}
 dispatch(type,event={}){this.listeners[type]?.(event);} showModal(){this.open=true;} close(){this.open=false;this.pendingClose=true;}
 focus(){this.focused=true;} contains(){return true;} remove(){this.removed=true;}
}
const body=new Element('body');const document={body,createElement:tag=>new Element(tag)}; const ctx={document}; vm.createContext(ctx);
vm.runInContext(readFileSync('docs/property-detail.js','utf8'),ctx);
const root=new Element('div');const room={address:'Example St',property_id:5,room_number:2,occupant_present:true};
let authorized=true;
const controller=ctx.attachPropertyDetail({root,getContext:()=>({rooms:[room],tasksAuthorized:authorized,tasks:{Requests:[{property_id:5,room_number:2,details:'private test'}]}})});
const button=new Element('button');button.setAttribute('data-room-detail','0');button.closest=()=>button;
root.dispatch('click',{target:button});const dialog=body.children[0];assert.equal(dialog.open,true);assert.match(dialog.textContent,/private test/);
controller.clear();assert.equal(dialog.open,false);assert.doesNotMatch(dialog.textContent,/private test/);dialog.dispatch('close');assert.equal(button.focused,true);
authorized=false;root.dispatch('click',{target:button});assert.doesNotMatch(dialog.textContent,/private test/);assert.match(dialog.textContent,/Sign in/);
controller.destroy();assert.equal(dialog.removed,true);assert.equal(root.listeners.click,undefined);
console.log('property dialog closes, clears private content, restores focus and cleans up');
