function healthLabel(source) {
  const labels = {ok:'Successful', degraded:'Degraded', failed:'Failed'};
  return labels[source?.state] || 'Not reported';
}
function healthTime(value) {
  if (typeof value !== 'string') return 'Not reported';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Not reported' : typeof formatDisplayTime === 'function' ? formatDisplayTime(date) : date.toLocaleString(undefined,{timeZoneName:'short'});
}
