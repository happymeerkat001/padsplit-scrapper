/* Filter existing controls without replacing drafts or subscriptions. */
function filterEditableCards(cards, query) {
  const needle = String(query || '').trim().toLocaleLowerCase();
  let count = 0;
  for (const card of cards) {
    const values = Array.from(card.querySelectorAll('input, textarea')).map(input => input.value);
    const text = [card.textContent, ...values].join(' ').toLocaleLowerCase();
    card.hidden = !!needle && !text.includes(needle);
    if (!card.hidden) count++;
  }
  return count;
}

function normalizeVendor(values) {
  const result = {};
  for (const key of ['name', 'specialty', 'contact', 'location']) {
    result[key] = String(values[key] || '').trim();
    if (result[key].length > 200) return null;
  }
  return result.name ? result : null;
}
