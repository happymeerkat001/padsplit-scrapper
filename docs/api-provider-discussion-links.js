/* Explicit reviews bind an exact source observation to one internal task. */
async function loadApiProviderDiscussionLinks(api) {
  const links=[],seen=new Set(),validId=x=>typeof x==='string'&&/^[A-Za-z0-9_.:-]{1,200}$/.test(x)&&x!=='.'&&x!=='..';
  for(let offset=0;offset<=10000;offset+=100){
    const page=await api.providerDiscussionLinks({limit:100,offset});
    if(!page||page.coverage!=='reviewed_links_only'||page.ingestion!=='staging_only'||!Array.isArray(page.items)||page.items.length>100)throw new Error('Invalid reviewed provider discussion links');
    for(const row of page.items){
      if(!row||!validId(row.task_observation_id)||!validId(row.task_key)||!validId(row.internal_task_id)||!validId(row.property_id)||seen.has(row.task_observation_id))throw new Error('Invalid reviewed provider discussion link');
      seen.add(row.task_observation_id);links.push(row);
    }
    if(page.items.length<100)return links;
  }
  throw new Error('Reviewed provider discussion links exceed supported pagination');
}
function providerDiscussionHref(task,links){
  if(!task||!Array.isArray(links))return null;
  const row=links.find(link=>link.task_observation_id===task.api_observation_id&&link.task_key===task.api_task_key&&link.property_id===task.property_id);
  return row?'./tasks.html#task='+encodeURIComponent(row.internal_task_id):null;
}
