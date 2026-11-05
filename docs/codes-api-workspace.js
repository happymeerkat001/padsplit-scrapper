/* Isolated owner-only access-code UI over the encrypted API. */
(function(root){
  'use strict';
  function createApiCodesWorkspace({api,list,historyButton,historyPanel,renderProperty,isCurrent,onAccessLost,confirmChange,formatTime,onValue=()=>{},initialDrafts=new Map()}) {
    const doc=list.ownerDocument,states=new Map();let stopped=false;
    const active=()=>!stopped && isCurrent();
    const clear=element=>element.replaceChildren();
    const fail=(error,status,message)=>{
      if(!active())return;
      if(error?.status===401 || error?.status===403) { onAccessLost(); return; }
      status.textContent=error?.status===409 ? 'This property changed. Your draft is preserved; refresh before retrying.' : message;
    };
    const button=(text,cls)=>{const item=doc.createElement('button');item.type='button';item.className=cls;item.textContent=text;return item;};
    const inputNodes=node=>Array.from(node.querySelectorAll('input[data-prop][data-field], textarea[data-prop][data-field]'));
    function hide(state) {
      state.base=null;state.revealed=false;
      for(const input of state.inputs){ input.value='';input.disabled=true;delete input.dataset.dirty;if(input.dataset.sensitive==='true')input.type='password';onValue(input); }
      state.save.disabled=true;state.reveal.textContent='Reveal values';state.status.textContent='Values hidden. Reveal this property to edit.';
    }
    function validateReveal(data){
      if(!data || !Number.isSafeInteger(data.version) || data.version<1 || !data.fields || typeof data.fields!=='object' || Array.isArray(data.fields) || Object.values(data.fields).some(v=>typeof v!=='string' || v==='••••'))throw new Error('Invalid values.');
      return Object.assign(Object.create(null),data.fields);
    }
    async function reveal(state,discard=false) {
      if(!active() || state.pending)return;
      if(discard && !confirmChange('Refresh this property and discard its unsaved edits?'))return;
      state.reveal.disabled=true;state.refresh.disabled=true;
      try {
        const data=await api.revealCodes(state.property.slug), base=validateReveal(data);
        if(!active())return;
        // Recheck after the async read so new input is never silently discarded.
        if(discard && state.inputs.some(input=>input.dataset.dirty==='true' && input.value!==state.discardValues?.get(input))) {
          state.status.textContent='Draft changed while refreshing and was preserved.';return;
        }
        state.version=data.version;state.base=base;state.revealed=true;state.conflict=false;
        for(const input of state.inputs){input.value=Object.prototype.hasOwnProperty.call(base,input.dataset.field)?base[input.dataset.field]:'';input.disabled=false;delete input.dataset.dirty;if(input.dataset.sensitive==='true')input.type='text';onValue(input);}
        if(state.savedDraft && !discard){
          for(const input of state.inputs)if(Object.prototype.hasOwnProperty.call(state.savedDraft.fields,input.dataset.field)){input.value=state.savedDraft.fields[input.dataset.field];input.dataset.dirty='true';onValue(input);}
          state.conflict=state.savedDraft.version!==data.version;
          state.status.textContent=state.conflict?'Values changed while signed out. Your draft is preserved; refresh after reviewing.':'Your previous draft was restored.';
        } else state.status.textContent='Values revealed for editing.';
        state.savedDraft=null;state.save.disabled=state.conflict;state.reveal.textContent='Hide values';state.refresh.hidden=!state.conflict;
      } catch(error){fail(error,state.status,'Values could not be revealed. Your draft is preserved.');}
      finally {state.reveal.disabled=false;state.refresh.disabled=false;state.discardValues=null;}
    }
    async function save(state) {
      if(!active() || state.pending || !state.base)return;
      const dirty=state.inputs.filter(input=>input.dataset.dirty==='true');
      if(!dirty.length){state.status.textContent='No changes to save.';return;}
      let values;
      try { values=mergeApiCodeDraft(state.base,dirty.map(input=>({key:input.dataset.field,value:input.value,dirty:true}))); }
      catch {state.status.textContent='The code fields cannot be saved safely.';return;}
      const submitted=new Map(dirty.map(input=>[input,input.value]));
      const previousVersion=state.version;state.pending=true;state.save.disabled=true;state.status.textContent='Saving…';
      try {
        const result=await api.saveCodes(state.property.slug,values,previousVersion);
        if(!active())return;
        if(!Number.isSafeInteger(result?.version) || result.version<=previousVersion)throw new Error('Invalid version.');
        state.version=result.version;state.base=values;state.conflict=false;state.refresh.hidden=true;
        for(const [input,submittedValue] of submitted)if(input.value===submittedValue)delete input.dataset.dirty;
        state.status.textContent='Saved.';
      } catch(error){
        if(!active())return;
        if(error?.status===409){state.conflict=true;state.refresh.hidden=false;}
        fail(error,state.status,'Save could not be confirmed. Your draft is preserved.');
      } finally {state.pending=false;if(active())state.save.disabled=!state.base || state.conflict;}
    }
    async function restore(state,id,panel){
      if(!active() || state.pending || !confirmChange('Restore this snapshot? Unsaved edits for this property will be discarded.'))return;
      const expected=state.version;state.pending=true;state.save.disabled=true;state.reveal.disabled=true;state.refresh.disabled=true;
      state.inputs.forEach(input=>{input.disabled=true;});
      try{
        const result=await api.restoreCodes(state.property.slug,id,expected);
        if(!active())return;
        if(!Number.isSafeInteger(result?.version)||result.version<=expected)throw new Error('Invalid version.');
        state.version=result.version;state.conflict=false;state.refresh.hidden=true;clear(panel);hide(state);state.status.textContent='Snapshot restored. Values are hidden.';
      }catch(error){
        if(!active())return;
        if(error?.status===409){state.conflict=true;state.refresh.hidden=false;}
        fail(error,state.status,'Restore could not be confirmed. Your draft is preserved.');
        if(state.revealed)state.inputs.forEach(input=>{input.disabled=false;});
      }finally{state.pending=false;if(active()){state.save.disabled=!state.base || state.conflict;state.reveal.disabled=false;state.refresh.disabled=false;}}
    }
    async function history(state,panel,offset=0){
      if(!active())return;
      try{
        const result=await api.codesHistory(state.property.slug,{limit:50,offset});
        if(!active())return;
        if(!result || !Array.isArray(result.items) || result.items.length>50)throw new Error('Invalid history.');
        if(offset===0)clear(panel);
        if(!result.items.length && offset===0){panel.textContent='No previous snapshots.';return;}
        for(const row of result.items){
          if(!row || typeof row.id!=='string' || !/^[A-Za-z0-9_.:-]{1,200}$/.test(row.id))throw new Error('Invalid history.');
          const line=doc.createElement('div'),label=doc.createElement('span'),restoreBtn=button('Restore','api-codes-restore'),revealBtn=button('Reveal snapshot values','api-codes-history-reveal'),preview=doc.createElement('pre');
          label.textContent=`Snapshot${Number.isSafeInteger(row.source_version)?'':' (restore unavailable)'} · ${formatTime(row.created_at)}`;
          restoreBtn.disabled=!Number.isSafeInteger(row.source_version) || row.source_version<1;
          revealBtn.disabled=restoreBtn.disabled;
          revealBtn.addEventListener('click',async event=>{
            event.preventDefault();event.stopPropagation();
            if(revealBtn.getAttribute('aria-pressed')==='true'){preview.textContent='';revealBtn.setAttribute('aria-pressed','false');revealBtn.textContent='Reveal snapshot values';return;}
            revealBtn.disabled=true;
            try{
              const result=await api.revealCodeHistory(state.property.slug,row.id);
              if(!active()||!panel.contains(line))return;
              if(!result||!result.fields||typeof result.fields!=='object'||Array.isArray(result.fields)||Object.keys(result.fields).length>200||Object.entries(result.fields).some(([key,value])=>!/^[A-Za-z0-9_-]{1,100}$/.test(key)||typeof value!=='string'||value.length>300))throw new Error('Invalid history.');
              preview.textContent=Object.keys(result.fields).sort().map(key=>`${key}: ${result.fields[key]}`).join('\n');
              revealBtn.setAttribute('aria-pressed','true');revealBtn.textContent='Hide snapshot values';
            }catch(error){if(active()&&panel.contains(line))fail(error,state.status,'Snapshot could not be revealed.');}
            finally{if(active()&&panel.contains(line))revealBtn.disabled=false;}
          });
          restoreBtn.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();restore(state,row.id,panel);});
          line.append(label,revealBtn,restoreBtn,preview);panel.appendChild(line);
        }
        if(result.items.length===50){const older=button('Older','history-older');older.addEventListener('click',event=>{event.preventDefault();older.remove();history(state,panel,offset+50);});panel.appendChild(older);}
      }catch(error){if(!active())return;fail(error,state.status,'History could not be loaded.');panel.textContent='History could not be loaded.';}
    }
    function makeProperty(property,version){
      const node=renderProperty(property,{},false),summary=node.querySelector('.flex-between'),body=node.querySelector('.property-body'),saveButton=node.querySelector('.save-btn');
      if(!summary || !body || !saveButton)throw new Error('Invalid property layout.');
      const revealBtn=button('Reveal values','api-codes-reveal'),refreshBtn=button('Refresh property','api-codes-refresh'),prev=button('Previous','history-btn');
      refreshBtn.hidden=true;const status=doc.createElement('p');status.setAttribute('role','status');
      const panel=doc.createElement('div');panel.className='history-panel hidden';
      summary.insertBefore(revealBtn,saveButton);summary.insertBefore(refreshBtn,saveButton);
      if(property.history_enabled!==false)summary.insertBefore(prev,saveButton);
      body.prepend(panel);body.prepend(status);
      const state={property,node,inputs:inputNodes(node),save:saveButton,reveal:revealBtn,refresh:refreshBtn,status,panel,version,base:null,revealed:false,conflict:false,pending:false,savedDraft:initialDrafts.get(property.slug)||null};
      node.addEventListener('input',event=>{if(state.inputs.includes(event.target))event.target.dataset.dirty='true';});
      revealBtn.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();if(state.revealed){if(state.inputs.some(input=>input.dataset.dirty==='true')){state.status.textContent='Save or refresh the draft before hiding.';return;}hide(state);}else reveal(state);});
      refreshBtn.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();state.discardValues=new Map(state.inputs.map(input=>[input,input.value]));reveal(state,true);});
      saveButton.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();if(!state.conflict)save(state);else state.status.textContent='Refresh before saving.';});
      prev.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();panel.classList.toggle('hidden');if(!panel.classList.contains('hidden'))history(state,panel);else clear(panel);});
      hide(state);states.set(property.slug,state);return node;
    }
    return {
      async start(){
        const data=await api.codesSchema();if(!active())return;
        const parsed=parseApiCodesSchema(data);clear(list);states.clear();clear(historyPanel);historyButton.disabled=true;
        if(!parsed.properties.length){list.textContent='Private code layouts have not been migrated.';return;}
        const fragment=doc.createDocumentFragment();for(const property of parsed.properties)fragment.appendChild(makeProperty(property,parsed.versions[property.slug]));
        if(!active())return;list.appendChild(fragment);historyButton.disabled=false;
        historyButton.onclick=event=>{event.preventDefault();clear(historyPanel);historyPanel.classList.toggle('hidden');if(historyPanel.classList.contains('hidden'))return;
          for(const state of states.values()){if(state.property.history_enabled===false)continue;const item=button(state.property.address,'history-row');item.addEventListener('click',()=>history(state,historyPanel));historyPanel.appendChild(item);}};
      },
      captureDrafts(){
        const result=new Map();
        for(const [id,state] of states){
          const fields=Object.create(null);for(const input of state.inputs)if(input.dataset.dirty==='true')fields[input.dataset.field]=input.value;
          if(Object.keys(fields).length)result.set(id,{version:state.version,fields});
          else if(state.savedDraft)result.set(id,state.savedDraft);
        }
        return result;
      },
      stop(){stopped=true;for(const state of states.values())state.base=null;states.clear();clear(list);clear(historyPanel);historyButton.disabled=true;historyButton.onclick=null;}
    };
  }
  root.createApiCodesWorkspace=createApiCodesWorkspace;
})(typeof window!=='undefined'?window:globalThis);
