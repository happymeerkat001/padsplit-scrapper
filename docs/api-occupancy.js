/* A single snapshot time is required before computing cross-room summaries. */
async function loadApiOccupancy(api) {
  const rooms=[],seen=new Set();let observed=null;
  for(let offset=0;offset<=10000;offset+=100){
    const page=await api.occupancy({limit:100,offset});
    if(!page||page.ingestion!=='staging_only'||!Array.isArray(page.items)||page.items.length>100)throw new Error('Invalid occupancy response');
    for(const row of page.items){
      if(!row||typeof row.room_id!=='string'||!row.room_id||typeof row.observation_id!=='string'||!/^[1-9][0-9]*$/.test(row.observation_id)||typeof row.property_id!=='string'||!row.property_id||
         typeof row.observed_at!=='string'||!Number.isFinite(Date.parse(row.observed_at))||!row.data||typeof row.data!=='object'||Array.isArray(row.data))throw new Error('Invalid occupancy record');
      if(seen.has(row.room_id))throw new Error('Duplicate occupancy record');
      seen.add(row.room_id);
      const time=Date.parse(row.observed_at);
      if(observed!==null&&time!==observed)throw new Error('Mixed occupancy snapshot times');
      observed=time;rooms.push({...row.data,property_id:row.property_id,api_room_id:row.room_id,api_observation_id:row.observation_id});
    }
    if(page.items.length<100)return rooms.length?{rooms,scraped_at:new Date(observed).toISOString()}:null;
  }
  throw new Error('Occupancy exceeds supported pagination');
}
