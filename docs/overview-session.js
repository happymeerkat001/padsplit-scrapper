/* Keep unsent drafts in memory per operator while private DOM and listeners are cleared. */
function createOverviewSession({canAccess, stopListeners, captureDrafts, clearPrivate, restoreDrafts, startListeners}) {
  let uid;
  const drafts = new Map();
  return {
    transition(user) {
      const next = canAccess(user) ? user.uid : null;
      if (uid === next) return false;
      if (uid) {
        drafts.set(uid, captureDrafts());
        stopListeners();
      }
      uid = next;
      clearPrivate();
      if (uid) {
        restoreDrafts(drafts.get(uid) || null);
        startListeners(user);
      }
      return true;
    },
    currentUid() { return uid || null; },
    discardDrafts(draftUid) { drafts.delete(draftUid); }
  };
}
