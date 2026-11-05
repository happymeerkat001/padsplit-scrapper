/* Version-aware shared notes; dirty drafts are never overwritten by polling. */
(function (root) {
  'use strict';
  function createApiNoteBinding({api, textarea, status, getUid, noteId = 'dashboard', onAccessLost = () => {}}) {
    let version = null, generation = 0, stopPolling = null;
    function stop() { generation++; stopPolling?.(); stopPolling = null; version = null; }
    function start(expectedVersion = null) {
      stop();
      if (Number.isSafeInteger(expectedVersion) && expectedVersion >= 0) version = expectedVersion;
      const current = ++generation, uid = getUid();
      if (!uid) return;
      status.textContent = 'Loading shared notes…';
      stopPolling = api.poll(signal => api.note(noteId, {signal}), data => {
        if (generation !== current || getUid() !== uid) return;
        if (!Number.isSafeInteger(data.version) || data.version < 0 || typeof data.text !== 'string') {
          status.textContent = 'Shared notes returned an invalid response.'; return;
        }
        if (version !== null && data.version < version) return;
        if (textarea.dataset.dirty !== 'true' && document.activeElement !== textarea) {
          textarea.value = data.text; version = data.version;
          status.textContent = 'Shared notes connected.';
        } else if (version === null) {
          status.textContent = 'Finish or clear the draft before loading notes.';
        }
      }, error => {
        if (generation !== current || getUid() !== uid) return;
        status.textContent = 'Shared notes unavailable.';
        if (error?.status === 401 || error?.status === 403) onAccessLost(error);
      });
    }
    async function save() {
      if (version === null) throw new Error('Wait for notes to load before saving.');
      const current = generation, uid = getUid(), savedText = textarea.value;
      if (!uid) throw new Error('Sign in is required.');
      const result = await api.saveNote(noteId, savedText, version);
      if (generation !== current || getUid() !== uid) return false;
      if (!Number.isSafeInteger(result.version) || result.version <= version) throw new Error('Invalid save response.');
      version = result.version;
      if (textarea.value === savedText) textarea.dataset.dirty = 'false';
      return true;
    }
    return {start,stop,save,version:() => version};
  }
  root.createApiNoteBinding = createApiNoteBinding;
})(typeof window !== 'undefined' ? window : globalThis);
