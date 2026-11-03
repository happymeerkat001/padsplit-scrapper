function matchesTask(query, text) {
  return String(text || '').toLocaleLowerCase().includes(String(query || '').trim().toLocaleLowerCase());
}
function shouldClearTaskDraft(submitted, current) { return String(submitted) === String(current); }
function taskMatchesStatus(filter, status) { return !filter || String(filter) === String(status || ''); }
function shouldFollowDiscussion(scrollHeight, scrollTop, clientHeight) {
  return scrollHeight - scrollTop - clientHeight <= 24;
}
