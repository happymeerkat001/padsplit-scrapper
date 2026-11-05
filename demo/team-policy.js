/* Fictional demo only. Production authorization is enforced separately. */
function demoCanManageTeam(members,id) {
  const actor=Array.isArray(members)?members.find(item=>item.id===id):null;
  return !!actor && actor.role==='owner' && actor.active===true;
}
function demoCanChangeMember(members,id,nextRole,nextActive) {
  if (!Array.isArray(members) || !['owner','manager','operator','viewer'].includes(nextRole) || typeof nextActive!=='boolean') return false;
  const member=members.find(item=>item.id===id);
  if (!member) return false;
  if (member.role==='owner' && member.active===true && (nextRole!=='owner'||!nextActive)) {
    return members.filter(item=>item.role==='owner' && item.active===true).length>1;
  }
  return true;
}
