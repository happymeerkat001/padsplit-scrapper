/* Provider observations are read-only and must represent one coherent time. */
async function loadApiProviderTasks(api) {
  const tasks=Object.create(null),seen=new Set(),seenSource=new Set();let observed=null,count=0;
  for(let offset=0;offset<=10000;offset+=100){
    const page=await api.providerTasks({limit:100,offset});
    if(!page||page.ingestion!=='staging_only'||!Array.isArray(page.items)||page.items.length>100)throw new Error('Invalid provider task response');
    for(const row of page.items){
      if(!row||typeof row.task_key!=='string'||!row.task_key||typeof row.observation_id!=='string'||!row.observation_id||!(typeof row.source_id==='string'&&row.source_id||Number.isSafeInteger(row.source_id))||
         typeof row.bucket!=='string'||!row.bucket||row.bucket.length>100||(row.property_id!==null&&typeof row.property_id!=='string')||
         typeof row.observed_at!=='string'||!Number.isFinite(Date.parse(row.observed_at))||!row.data||typeof row.data!=='object'||Array.isArray(row.data))throw new Error('Invalid provider task');
      const sourceKey=JSON.stringify([row.bucket,String(row.source_id)]);
      if(seen.has(row.task_key)||seenSource.has(sourceKey))throw new Error('Duplicate provider task');seen.add(row.task_key);seenSource.add(sourceKey);
      const time=Date.parse(row.observed_at);if(observed!==null&&time!==observed)throw new Error('Mixed provider task snapshot times');observed=time;
      (tasks[row.bucket] ||= []).push({...row.data,id:String(row.source_id),property_id:row.property_id,api_task_key:row.task_key,api_observation_id:row.observation_id});count++;
    }
    if(page.items.length<100)return count?{tasks,scraped_at:new Date(observed).toISOString()}:null;
  }
  throw new Error('Provider tasks exceed supported pagination');
}
