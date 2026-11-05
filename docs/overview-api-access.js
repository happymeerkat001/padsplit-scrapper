/* Client UI gate complements, but never replaces, server authorization. */
function classifyOverviewIdentity(user, identity) {
  const roles=['owner','manager','operator','viewer'];
  if (!user || typeof user.uid !== 'string' || !identity || identity.uid !== user.uid ||
      !roles.includes(identity.role) || !Array.isArray(identity.properties) ||
      identity.properties.some(id => typeof id !== 'string' || !/^[A-Za-z0-9_.:-]{1,200}$/.test(id))) return null;
  if (new Set(identity.properties).size !== identity.properties.length) return null;
  return {uid:identity.uid,role:identity.role,properties:[...identity.properties].sort()};
}
function startOverviewAccessWatch({api,user,identity,onInvalid}) {
  const expected=classifyOverviewIdentity(user,identity);
  if (!expected) throw new Error('Invalid overview identity');
  const fingerprint=JSON.stringify(expected);let watching=true;
  const stop=api.poll(()=>api.me(),current=>{
    if (!watching) return;
    const verified=classifyOverviewIdentity(user,current);
    if (!verified || JSON.stringify(verified)!==fingerprint) onInvalid({discardDrafts:true});
  },error=>{if (watching) onInvalid(error);},30000);
  return ()=>{watching=false;stop();};
}
function applyOverviewNotesAccess(document, {signedIn,apiMode,role,overviewVisible=true}) {
  const allowed=!!signedIn && (!apiMode || role === 'owner');
  const section=document.getElementById('overview-notes');
  section.dataset.notesAllowed=String(!apiMode || allowed);
  section.hidden=(!apiMode ? false : !allowed) || !overviewVisible;
  for (const id of ['notes-textarea','notes-save-btn','notes-copy-btn']) document.getElementById(id).disabled = !allowed;
}
