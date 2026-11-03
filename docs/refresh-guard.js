/* Keep unsent work in place when the periodic page refresh is due. */
function shouldDeferRefresh({ editing, draft, notesDirty }) {
  return Boolean(editing || draft || notesDirty);
}
