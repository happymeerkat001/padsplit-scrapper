/* Display helpers: absence of a value is not evidence of vacancy or zero. */
function presenceLabel(room) {
  if (room.occupant_present === true) return 'Present';
  if (room.occupant_present === false) return 'Not present';
  return 'Unknown';
}
function displayDays(room) {
  return Number.isFinite(room.days_vacant) && room.days_vacant >= 0 ? String(room.days_vacant) : '—';
}
function roomActionLabel(room) {
  if (room.rent_ready === true) return 'Rent-ready';
  if (room.vacant === true) return 'Vacant · awaiting turn';
  if (room.occupant_present === true) return 'Present';
  return 'Unknown';
}
