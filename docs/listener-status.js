/* A missing listener result is not an empty collection. */
function listenerMessage(kind, state) {
  const name = kind === 'manual' ? 'Manual tasks' : 'Internal discussions';
  if (state === 'loading') return kind === 'manual' ? 'Loading manual tasks…' : 'Loading internal discussions…';
  if (state === 'ready') return `${name} connected.`;
  if (state === 'denied') return `${name} unavailable: permission denied.`;
  return `${name} unavailable. Try again later.`;
}
function manualCountLabel(loaded, count) { return loaded ? String(count) : '—'; }
