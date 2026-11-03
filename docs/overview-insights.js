/* The categories come from the existing occupancy definitions, not listed status. */
function buildOverviewInsights(lists = {}) {
  const rooms = key => Array.isArray(lists[key]) ? lists[key] : [];
  const label = room => `${typeof room.address === 'string' && room.address.trim() ? room.address.trim() : 'Unknown property'} · Room ${room.room_number == null ? '—' : String(room.room_number)}`;
  const attention = [
    ...rooms('occupiedAfterMoveOut').map(room => ({label:label(room), kind:'Occupied after listed move-out'})),
    ...rooms('vacantNotReady').map(room => ({label:label(room), kind:'Vacant, not rent-ready'}))
  ];
  const upcoming = rooms('incoming').map(room => ({label:label(room), date:typeof room.next_move_in === 'string' ? room.next_move_in : 'Date unavailable'}));
  return {attention, upcoming};
}
function renderOverviewInsights(insights) {
  const escape = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const items = (rows, detail) => rows.slice(0, 6).map(row => `<li><strong>${escape(row.label)}</strong><span>${escape(row[detail])}</span></li>`).join('');
  return {
    attention: insights.attention.length ? `<ul>${items(insights.attention, 'kind')}</ul>` : '<p class="muted">No known rooms need attention in this snapshot.</p>',
    upcoming: insights.upcoming.length ? `<ul>${items(insights.upcoming, 'date')}</ul>` : '<p class="muted">No upcoming move-ins in this snapshot.</p>'
  };
}
