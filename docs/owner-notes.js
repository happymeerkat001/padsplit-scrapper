/* Same-account drafts stay in tab memory; private content leaves the DOM on auth change. */
function createOwnerNotesSession({ownerUid, ta, saveBtn, copyBtn, status, subscribe, save, copy}) {
  let uid, unsubscribe = () => {};
  const drafts = new Map();
  const enable = value => { ta.disabled = !value; saveBtn.disabled = !value; copyBtn.disabled = !value; };
  ta.addEventListener('input', () => { ta.dataset.dirty = 'true'; });
  saveBtn.addEventListener('click', async () => {
    if (uid !== ownerUid || saveBtn.disabled) return;
    const submitted = ta.value, previous = uid;
    saveBtn.disabled = true; status.textContent = 'Saving notes…';
    try {
      await save(submitted);
      if (uid === previous) {
        if (ta.value === submitted) { ta.dataset.dirty = 'false'; drafts.delete(uid); }
        status.textContent = 'Notes saved.';
      }
    } catch { if (uid === previous) status.textContent = 'Notes could not be saved. Your draft is still here.'; }
    finally { if (uid === previous) saveBtn.disabled = false; }
  });
  copyBtn.addEventListener('click', async () => {
    if (uid !== ownerUid) return;
    try { await copy(ta.value); status.textContent = 'Notes copied.'; }
    catch { status.textContent = 'Notes could not be copied.'; }
  });
  enable(false);
  status.textContent = 'Sign in as the operator to use shared notes.';
  return {
    setUser(user) {
      const next = user?.uid === ownerUid ? ownerUid : null;
      if (next === uid) return;
      if (uid === ownerUid && ta.dataset.dirty === 'true') drafts.set(uid, ta.value);
      unsubscribe(); unsubscribe = () => {};
      uid = next; ta.value = ''; ta.dataset.dirty = 'false'; enable(false);
      if (!uid) { status.textContent = 'Sign in as the operator to use shared notes.'; return; }
      enable(true);
      if (drafts.has(uid)) { ta.value = drafts.get(uid); ta.dataset.dirty = 'true'; }
      status.textContent = 'Loading shared notes…';
      const observedUid = uid;
      unsubscribe = subscribe(snap => {
        if (uid !== observedUid) return;
        status.textContent = 'Shared notes connected.';
        if (ta.dataset.dirty !== 'true' && ta.ownerDocument?.activeElement !== ta && snap.exists()) ta.value = snap.data().text || '';
      }, err => {
        if (uid !== observedUid) return;
        status.textContent = err?.code === 'permission-denied' ? 'Shared notes unavailable: permission denied.' : 'Shared notes unavailable.';
      });
    }
  };
}
