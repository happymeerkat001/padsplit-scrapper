/* Private collection observations are not a provider connectivity check. */
(function(root){
 'use strict';
 function createIntegrationsWorkspace({api,document,getUid,isCurrent,onAccessLost}){
  const body=document.getElementById('integration-rows'),status=document.getElementById('integration-status');
  let uid=null,stopped=false,generation=0;
  const alive=()=>!stopped&&isCurrent()&&getUid()===uid;
  function render(snapshot){
   body.replaceChildren();
   for(const row of integrationSourceStatus(snapshot)){
    const tr=document.createElement('tr');
    for(const [i,value] of [row.label,row.collection,row.observed,row.configuration,row.connection].entries()){
     const cell=document.createElement(i===0?'th':'td');if(i===0)cell.scope='row';cell.textContent=value;tr.appendChild(cell);
    }
    body.appendChild(tr);
   }
  }
  async function refresh(){
   const current=++generation;
   try{
    const result=await api.report('latest');
    if(!alive()||current!==generation)return;
    if(!result||result.id!=='latest'||typeof result.json!=='string'||result.json.length>2000000)throw Error('Invalid observation');
    const snapshot=JSON.parse(result.json);
    if(!snapshot||typeof snapshot!=='object'||Array.isArray(snapshot))throw Error('Invalid observation');
    render(snapshot);status.textContent='Most recent stored collection observations. Provider connections are not verified.';
   }catch(error){
    if(!alive()||current!==generation)return;
    if(error?.status===401||error?.status===403){onAccessLost();return;}
    render({});status.textContent=error?.status===404?'No collection observations are stored yet.':'Collection observations are unavailable. Refresh to retry.';
   }
  }
  document.getElementById('integration-refresh').addEventListener('click',refresh);
  return {async start(){uid=getUid();if(!uid)throw Error('Authentication required');const identity=await api.me();
    if(!alive()||identity?.uid!==uid||identity.role!=='owner')throw Error('Access denied');await refresh();},
   refresh,stop(){stopped=true;generation++;body.replaceChildren();status.textContent='';}};
 }
 root.createIntegrationsWorkspace=createIntegrationsWorkspace;
})(typeof window!=='undefined'?window:globalThis);
