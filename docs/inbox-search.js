/* Adapter-neutral matching; never mutates conversations or their drafts. */
function inboxMatches(item, query, unreadOnly) {
  if (unreadOnly && item.unread !== true) return false;
  const needle = String(query || '').trim().toLocaleLowerCase();
  return !needle || [item.name, item.property, item.preview, item.room]
    .some(value => String(value ?? '').toLocaleLowerCase().includes(needle));
}
