/* Listener registrations survive sign-out; active subscriptions do not. */
function createAuthListeners({onIdentityChange = () => {}} = {}) {
  const factories = [];
  let active = [];
  let currentUid = null;
  return {
    add(factory) {
      factories.push(factory);
      if (currentUid !== null) active.push(factory());
    },
    setUser(user) {
      const nextUid = user?.uid || null;
      if (currentUid === nextUid) return;
      active.forEach(unsubscribe => unsubscribe());
      active = [];
      onIdentityChange(currentUid, nextUid);
      currentUid = nextUid;
      if (nextUid !== null) active = factories.map(factory => factory());
    }
  };
}
/* Remove records that disappeared while a listener was detached. */
function staleListenerIds(localIds, remoteIds) {
  const current = new Set(remoteIds);
  return Array.from(localIds).filter(id => !current.has(id));
}
