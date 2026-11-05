/* Team administration UI: explicit API mode only; server makes every authorization decision. */
(function(root){
  'use strict';
  const ID=/^[A-Za-z0-9_.:-]{1,200}$/, ROLES=new Set(['owner','manager','operator','viewer']);
  function validId(id){return typeof id==='string' && ID.test(id) && id!=='.' && id!=='..';}
  function validateTeamCommand(value){
    if(!value || !ROLES.has(value.role) || typeof value.active!=='boolean' || !Array.isArray(value.properties) ||
       value.properties.length>50 || value.properties.some(id=>!validId(id)) || new Set(value.properties).size!==value.properties.length ||
       !Number.isSafeInteger(value.expected_version) || value.expected_version<0) throw new Error('Invalid access settings.');
    if((value.active && value.role!=='owner' && !value.properties.length) || (!value.active && value.properties.length))
      throw new Error('Choose properties for active scoped roles; clear them for inactive accounts.');
    return {role:value.role,active:value.active,properties:[...value.properties],expected_version:value.expected_version};
  }
  function validateTeamRow(row){
    if(!row || !validId(row.uid) || !Number.isSafeInteger(row.version) || row.version<1)throw new Error('Invalid team record.');
    const command=validateTeamCommand({role:row.role,active:row.active,properties:row.properties,expected_version:row.version});
    return {uid:row.uid,role:command.role,active:command.active,properties:command.properties,version:row.version};
  }
  async function loadTeamRoster(api,signal){
    const rows=[],seen=new Set();
    for(let offset=0;offset<=10000;offset+=100){
      const page=await api.teamMembers({limit:100,offset,signal});
      if(!page || !Array.isArray(page.items) || page.items.length>100)throw new Error('Invalid team response.');
      for(const value of page.items){const row=validateTeamRow(value);if(seen.has(row.uid))throw new Error('Duplicate team record.');seen.add(row.uid);rows.push(row);}
      if(page.items.length<100)return rows;
    }
    throw new Error('Team roster exceeds supported pagination.');
  }
  function validateProperties(data){
    if(!data || !Array.isArray(data.items) || data.items.length>10000)throw new Error('Invalid properties.');
    const seen=new Set();return data.items.map(row=>{
      if(!row || !validId(row.id) || typeof row.name!=='string' || seen.has(row.id))throw new Error('Invalid property.');
      seen.add(row.id);return {id:row.id,name:row.name};
    });
  }
  function createTeamWorkspace({api,document,getUid,isCurrent,onAccessLost,confirmChange}){
    const $=id=>document.getElementById(id),form=$('team-form'),uid=$('target-uid'),role=$('target-role'),active=$('target-active'),scope=$('property-scope'),status=$('team-status'),roster=$('team-roster'),save=$('team-save'),load=$('team-load'),refresh=$('team-refresh');
    let stopped=false,request=0,rosterSequence=0,target=null,version=null,dirty=false,properties=[],validScope=true;
    const alive=()=>!stopped && isCurrent();
    const setStatus=text=>{status.textContent=text;};
    const selected=()=>Array.from(scope.querySelectorAll('input[type="checkbox"]')).filter(input=>input.checked).map(input=>input.value);
    const snapshot=()=>JSON.stringify({uid:uid.value.trim(),role:role.value,active:active.checked,properties:selected(),version});
    const setBusy=busy=>{load.disabled=busy;refresh.disabled=busy;save.disabled=busy || !target || !validScope;};
    function denied(error){if(error?.status===401 || error?.status===403){onAccessLost();return true;}return false;}
    function renderScope(chosen=[]){
      scope.replaceChildren();const ids=new Set(properties.map(p=>p.id));validScope=chosen.every(id=>ids.has(id));
      for(const property of properties){const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');input.type='checkbox';input.value=property.id;input.checked=chosen.includes(property.id);input.addEventListener('change',()=>{dirty=true;});text.textContent=property.name;label.append(input,text);scope.appendChild(label);}
      if(!properties.length){const text=document.createElement('p');text.textContent='No properties have been configured.';scope.appendChild(text);}
    }
    async function loadRecord(identifier){
      if(!validId(identifier)){setStatus('Enter a valid Firebase user ID.');return;}
      if(dirty && target && !confirmChange('Load another record and discard the current unsaved edits?'))return;
      const generation=++request,before=snapshot();setBusy(true);setStatus('Loading access…');
      try{
        let row;
        try{row=validateTeamRow(await api.teamMember(identifier));}
        catch(error){if(error.status!==404)throw error;row={uid:identifier,role:'viewer',active:true,properties:[],version:0};}
        if(!alive() || generation!==request)return;
        if(snapshot()!==before){setStatus('The draft changed while loading and was preserved.');return;}
        if(row.uid!==identifier)throw new Error('Invalid team response.');
        uid.value=identifier;target=identifier;version=row.version;role.value=row.role;active.checked=row.active;renderScope(row.properties);dirty=false;
        setStatus(!validScope?'This member references unavailable properties. Review the migration before editing.':row.version===0?'No team record. Saving verifies that this Firebase user exists.':'Access loaded.');
      } catch(error){if(alive() && generation===request && !denied(error))setStatus('Access could not be loaded. Your draft is preserved.');}
      finally{if(alive() && generation===request)setBusy(false);}
    }
    function renderRoster(rows){
      roster.replaceChildren();
      if(!rows.length){const item=document.createElement('p');item.textContent='No team records.';roster.appendChild(item);return;}
      for(const row of rows){const button=document.createElement('button');button.type='button';button.className='team-row';button.textContent=`${row.uid} · ${row.role} · ${row.active?'Active':'Inactive'}`;button.addEventListener('click',()=>loadRecord(row.uid));roster.appendChild(button);}
    }
    async function refreshRoster(){
      if(!alive())return;
      const sequence=++rosterSequence;
      refresh.disabled=true;
      try{const rows=await loadTeamRoster(api);if(alive() && sequence===rosterSequence)renderRoster(rows);}
      catch(error){if(alive() && sequence===rosterSequence && !denied(error))setStatus('Roster could not be loaded. Existing edits are preserved.');}
      finally{if(alive() && sequence===rosterSequence)refresh.disabled=false;}
    }
    uid.addEventListener('input',()=>{dirty=true;});
    role.addEventListener('change',()=>{dirty=true;});active.addEventListener('change',()=>{dirty=true;});
    load.addEventListener('click',()=>loadRecord(uid.value.trim()));
    refresh.addEventListener('click',refreshRoster);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(!alive() || save.disabled)return;
      if(uid.value.trim()!==target){setStatus('Load this Firebase user before saving.');return;}
      let command;try{command=validateTeamCommand({role:role.value,active:active.checked,properties:selected(),expected_version:version});}
      catch(error){setStatus(error.message);return;}
      if(!confirmChange('Apply these access changes to this account?'))return;
      const submitted=snapshot(),generation=request;setBusy(true);setStatus('Saving access…');
      try{
        const result=await api.setTeamMember(target,command);
        if(!alive() || generation!==request)return;
        if(!result || result.ok!==true || !Number.isSafeInteger(result.version) || result.version!==version+1)throw new Error('Invalid save response.');
        const unchanged=snapshot()===submitted;version=result.version;dirty=!unchanged;
        setStatus(unchanged?'Access saved.':'Access saved; newer unsaved edits are preserved.');
        refreshRoster();
      }catch(error){if(alive() && generation===request && !denied(error))setStatus(error.status===409?'Access changed elsewhere or last-owner protection applied. Your draft is preserved; reload the record.':'Access could not be confirmed. Your draft is preserved; check the record before retrying.');}
      finally{if(alive() && generation===request)setBusy(false);}
    });
    setBusy(true);
    return {
      async start(){
        const identity=await api.me();if(!alive())return;
        if(!identity || identity.uid!==getUid() || identity.role!=='owner')throw Object.assign(new Error('Owner access required.'),{status:403});
        const [data,rows]=await Promise.all([api.properties(),loadTeamRoster(api)]);if(!alive())return;
        properties=validateProperties(data);renderScope();renderRoster(rows);target=null;version=null;dirty=false;
        setBusy(false);setStatus('Select a team member or load an existing Firebase user.');
      },
      isDirty(){return dirty;},
      stop(){stopped=true;request++;rosterSequence++;target=null;version=null;properties=[];dirty=false;roster.replaceChildren();scope.replaceChildren();form.reset();setBusy(true);setStatus('');}
    };
  }
  root.validateTeamRow=validateTeamRow;root.validateTeamCommand=validateTeamCommand;root.loadTeamRoster=loadTeamRoster;root.createTeamWorkspace=createTeamWorkspace;
})(typeof window!=='undefined'?window:globalThis);
