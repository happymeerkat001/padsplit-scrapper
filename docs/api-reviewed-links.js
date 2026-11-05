/* Only explicitly reviewed links are available; coverage is not complete. */
async function loadApiReviewedLinks(api){
 const links=[],seen=new Set(),validId=x=>typeof x==='string'&&/^[A-Za-z0-9_.:-]{1,200}$/.test(x)&&x!=='.'&&x!=='..';
 for(let offset=0;offset<=10000;offset+=100){
  const page=await api.roomTaskLinks({limit:100,offset});
  if(!page||page.coverage!=='reviewed_links_only'||page.ingestion!=='staging_only'||!Array.isArray(page.items)||page.items.length>100)throw new Error('Invalid reviewed links response');
  for(const row of page.items){
   if(!row||!validId(row.room_id)||!validId(row.task_key)||!validId(row.task_observation_id)||typeof row.occupancy_observation_id!=='string'||!/^[1-9][0-9]*$/.test(row.occupancy_observation_id))throw new Error('Invalid reviewed link');
   const key=JSON.stringify([row.task_observation_id,row.occupancy_observation_id]);if(seen.has(key))throw new Error('Duplicate reviewed link');seen.add(key);links.push(row);
  }
  if(page.items.length<100)return links;
 }
 throw new Error('Reviewed links exceed supported pagination');
}
