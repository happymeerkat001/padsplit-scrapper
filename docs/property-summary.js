/* Shared read-only presentation. Never join rooms without a property ID. */
function matchesPropertyScope(room, scope) {
  return scope == null || (room?.property_id != null && String(room.property_id) === String(scope));
}
function summarizePropertyRooms(rooms) {
  const groups = new Map();
  let unlinked = 0;
  for (const room of Array.isArray(rooms) ? rooms : []) {
    const idValue = room?.property_id;
    if (!room || !(typeof idValue === 'string' || (typeof idValue === 'number' && Number.isFinite(idValue))) || String(idValue).trim() === '') { unlinked++; continue; }
    const id = String(room.property_id);
    if (!groups.has(id)) groups.set(id, {id, addresses: new Set(), rooms: 0, ready: 0, unknown: 0, present: 0, presenceUnknown: 0});
    const row = groups.get(id);
    if (typeof room.address === 'string' && room.address.trim()) row.addresses.add(room.address.trim());
    row.rooms++;
    if (room.rent_ready === true) row.ready++;
    else if (room.rent_ready !== false) row.unknown++;
    if (room.occupant_present === true) row.present++;
    else if (room.occupant_present !== false) row.presenceUnknown++;
  }
  const rows = Array.from(groups.values(), row => ({
    id: row.id,
    label: row.addresses.size === 1 ? [...row.addresses][0] : `Property ${row.id}`,
    rooms: row.rooms, ready: row.ready, unknown: row.unknown, present: row.present, presenceUnknown: row.presenceUnknown
  })).sort((a,b) => a.label.localeCompare(b.label));
  return {rows, unlinked};
}
function renderPropertySummary(summary, {selectable = false} = {}) {
  const escape = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  if (!summary.rows.length && !summary.unlinked) return '<p class="property-summary-empty">No property records in this snapshot.</p>';
  const table = summary.rows.length ? `<div class="property-summary-wrap"><table class="property-summary-table"><thead><tr><th scope="col">Property</th><th scope="col">Rooms</th><th scope="col">Present</th><th scope="col">Presence unknown</th><th scope="col">Rent-ready</th><th scope="col">Readiness unknown</th></tr></thead><tbody>${summary.rows.map(row => `<tr><th scope="row">${selectable ? `<button type="button" class="property-scope-link" data-property-scope="${escape(row.id)}">${escape(row.label)}</button>` : escape(row.label)}</th><td>${row.rooms}</td><td>${row.present}</td><td>${row.presenceUnknown}</td><td>${row.ready}</td><td>${row.unknown}</td></tr>`).join('')}</tbody></table></div>` : '';
  const note = summary.unlinked ? `<p class="property-summary-empty">${summary.unlinked} ${summary.unlinked === 1 ? 'room' : 'rooms'} without a property identifier ${summary.unlinked === 1 ? 'is' : 'are'} excluded.</p>` : '';
  return table + note;
}
