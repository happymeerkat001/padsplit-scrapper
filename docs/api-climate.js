/* Read-only staged observations; property scope comes only from reviewed mappings. */
async function loadApiClimate(api) {
  const locations=[],groups=new Map(),seen=new Set();let observed=null;
  for(let offset=0;offset<=10000;offset+=100){
    const page=await api.climate({limit:100,offset});
    if(!page||page.ingestion!=='staging_only'||!Array.isArray(page.items)||page.items.length>100)throw new Error('Invalid climate response');
    for(const row of page.items){
      if(!row||typeof row.device_key!=='string'||!row.device_key||typeof row.property_id!=='string'||!row.property_id||
         typeof row.observed_at!=='string'||!Number.isFinite(Date.parse(row.observed_at))||!row.data||typeof row.data!=='object'||Array.isArray(row.data))throw new Error('Invalid climate reading');
      if(seen.has(row.device_key))throw new Error('Duplicate climate device');seen.add(row.device_key);
      const time=Date.parse(row.observed_at);if(observed!==null&&time!==observed)throw new Error('Mixed climate observation times');observed=time;
      const data=row.data;
      if(typeof data.location_name!=='string'||data.location_name.length>1000||(data.name!=null&&(typeof data.name!=='string'||data.name.length>500)))throw new Error('Invalid climate label');
      for(const field of ['temp','heat_setpoint','cool_setpoint','humidity','outdoor_temp'])if(data[field]!=null&&(typeof data[field]!=='number'||!Number.isFinite(data[field])))throw new Error('Invalid climate value');
      const key=JSON.stringify([row.property_id,data.location_name]);let group=groups.get(key);
      if(!group){group={property_id:row.property_id,name:data.location_name,devices:[]};groups.set(key,group);locations.push(group)}
      group.devices.push({...data});
    }
    if(page.items.length<100)return locations.length?{locations,scraped_at:new Date(observed).toISOString()}:null;
  }
  throw new Error('Climate observations exceed supported pagination');
}
function renderClimateRows(data){
 const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const value=(n,suffix='')=>typeof n==='number'&&Number.isFinite(n)?escape(n)+suffix:'—';
 return (data?.locations||[]).flatMap(location=>(location.devices||[]).map(device=>{
   const klass=typeof device.temp==='number'&&device.temp>=78?'temp-high':typeof device.temp==='number'&&device.temp<=65?'temp-ok':'';
   return `<tr><td>${escape(location.name||device.name||'Not reported')}</td><td class="${klass}">${value(device.temp,'°F')}</td><td>${value(device.heat_setpoint,'°F')}</td><td>${value(device.cool_setpoint,'°F')}</td><td>${value(device.humidity,'%')}</td><td>${value(device.outdoor_temp,'°F')}</td></tr>`;
 })).join('');
}
