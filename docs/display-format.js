/* Display-only preferences; business calculations retain their configured timezone. */
function getDisplayPreferences() {
  let timezone='browser', dateFormat='browser';
  try {
    const zone=localStorage.getItem('operations-display-timezone');
    const format=localStorage.getItem('operations-display-date-format');
    if (['browser','UTC','America/Chicago'].includes(zone)) timezone=zone;
    if (['browser','month-first','day-first'].includes(format)) dateFormat=format;
  } catch (_) { /* Use defaults when storage is unavailable. */ }
  return {timezone,dateFormat};
}
function setDisplayPreferences({timezone,dateFormat}) {
  if (!['browser','UTC','America/Chicago'].includes(timezone) || !['browser','month-first','day-first'].includes(dateFormat)) return false;
  try {
    localStorage.setItem('operations-display-timezone',timezone);
    localStorage.setItem('operations-display-date-format',dateFormat);
    return true;
  } catch (_) { return false; }
}
function formatDisplayTime(value) {
  const date = value == null ? null : new Date(value);
  if (!date || Number.isNaN(date.getTime())) return 'Not reported';
  const {timezone,dateFormat}=getDisplayPreferences();
  const locale=dateFormat==='day-first'?'en-GB':dateFormat==='month-first'?'en-US':undefined;
  return date.toLocaleString(locale,{...(timezone==='browser'?{}:{timeZone:timezone}),timeZoneName:'short'});
}
