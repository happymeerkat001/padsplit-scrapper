/* Preserve local changes when a Firestore snapshot arrives after focus moves. */
function canApplyRemote(element, activeElement) {
  return activeElement !== element && element.dataset.dirty !== 'true';
}
function markDraft(element) { element.dataset.dirty = 'true'; }
function markDraftSaved(element, savedValue) {
  const current = element.isContentEditable ? element.textContent.trim() : element.value;
  if (current === savedValue) element.dataset.dirty = 'false';
}
