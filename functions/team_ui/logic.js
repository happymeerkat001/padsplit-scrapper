/* Only action-specific fields are sent; the server determines the actor. */
function teamMemberCommand(action, fields, currentUid) {
  if (action === 'accept_invitation') return {action, uid:currentUid};
  if (action === 'deactivate') return {action, uid:fields.uid.trim()};
  return {action, uid:fields.uid.trim(), role:fields.role,
    properties:fields.properties.split(',').map(value=>value.trim()).filter(Boolean)};
}
function teamTaskCommand(action, fields) {
  const base = {action, property:fields.property.trim(), task:fields.task.trim()};
  if (action !== 'delete_task') { base.title=fields.title.trim(); base.status=fields.status; }
  if (action !== 'create_task') base.expected_version=Number(fields.expected_version);
  return base;
}
