/* Collection observations do not prove current provider configuration or connectivity. */
function integrationSourceStatus(snapshot) {
  const states = {ok:'Successful', degraded:'Degraded', failed:'Failed', skipped:'Skipped'};
  const sources = snapshot?.run_status?.sources || {};
  return [['messages','Messages'], ['stats','Statistics'], ['occupancy','Occupancy']].map(([id,label]) => {
    const source = sources[id] || {};
    const parsed = typeof source.scraped_at === 'string' ? new Date(source.scraped_at) : null;
    return {id, label, collection:Object.hasOwn(states, source.state) ? states[source.state] : 'Not reported',
      observed:parsed && !Number.isNaN(parsed.getTime()) ? (typeof formatDisplayTime === 'function' ? formatDisplayTime(parsed) : parsed.toLocaleString(undefined,{timeZoneName:'short'})) : 'Not reported',
      configuration:'Not reported', connection:'Not verified'};
  });
}
