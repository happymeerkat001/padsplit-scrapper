function validDiscussionText(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

async function deleteTaskRecord(button, remove, notify, errorMessage) {
  if (button.disabled) return;
  button.disabled = true;
  try { await remove(); }
  catch { notify(errorMessage); }
  finally { button.disabled = false; }
}
