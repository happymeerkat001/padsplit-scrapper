(() => {
  'use strict';
  let token = null, uid = null, authOrigin = null, pending = false, generation = 0;
  const $ = selector => document.querySelector(selector);
  const signin = $('#signin-form'), member = $('#member-form'), task = $('#task-form');
  const result = $('#action-status');
  function busy(value) { pending=value; document.querySelectorAll('form button, #accept').forEach(button=>{button.disabled=value;}); }
  function signedOut() { generation++; token=null; uid=null; busy(false); signin.reset(); member.reset(); task.reset(); $('#identity').textContent=''; $('#workspace').hidden=true; $('#signin-section').hidden=false; result.textContent=''; }
  async function action(command) {
    if (!token || pending) return;
    const requestGeneration=generation; busy(true); result.textContent='Saving…';
    try {
      const response=await fetch('/team/action',{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`},body:JSON.stringify(command),signal:AbortSignal.timeout(10000)});
      if(requestGeneration!==generation)return;
      result.textContent=response.ok?'Saved in the emulator.':response.status===401?'Session expired. Sign in again.':response.status===403?'Permission denied for this action.':'Could not save. Check the fields and current version.';
      if(response.status===401) { signedOut(); $('#signin-status').textContent='Session expired. Sign in again.'; }
    } catch { if(requestGeneration===generation)result.textContent='Could not confirm the result. Check the emulator state before retrying.'; }
    finally { if(requestGeneration===generation)busy(false); }
  }
  signin.addEventListener('submit',async event=>{
    event.preventDefault(); if(pending)return; busy(true); $('#signin-status').textContent='Signing in…';
    try {
      const values=new FormData(signin);
      if (!authOrigin) { const config=await fetch('/team/config',{signal:AbortSignal.timeout(10000)}); if(!config.ok)throw new Error(); authOrigin=(await config.json()).authOrigin; }
      const response=await fetch(`${authOrigin}/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=demo`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:values.get('email'),password:values.get('password'),returnSecureToken:true}),signal:AbortSignal.timeout(10000)});
      signin.elements.namedItem('password').value='';
      if(!response.ok) { $('#signin-status').textContent='Sign-in failed. Check the emulator account and try again.'; return; }
      const data=await response.json(); if(!data.idToken||!data.localId)throw new Error();
      token=data.idToken; uid=data.localId; $('#identity').textContent=uid; $('#signin-section').hidden=true; $('#workspace').hidden=false; $('#signin-status').textContent='';
    } catch { signin.elements.namedItem('password').value=''; $('#signin-status').textContent='Auth emulator unavailable.'; }
    finally { busy(false); }
  });
  member.addEventListener('submit',event=>{event.preventDefault();const operation=event.submitter?.value;if(!operation)return;if(operation==='deactivate'&&!confirm('Deactivate this emulator member?'))return;action(teamMemberCommand(operation,Object.fromEntries(new FormData(member)),uid));});
  task.addEventListener('submit',event=>{event.preventDefault();const operation=event.submitter?.value;if(!operation)return;if(operation==='delete_task'&&!confirm('Delete this emulator task?'))return;action(teamTaskCommand(operation,Object.fromEntries(new FormData(task))));});
  $('#accept').addEventListener('click',()=>action(teamMemberCommand('accept_invitation',{},uid)));
  $('#signout').addEventListener('click',signedOut);
})();
