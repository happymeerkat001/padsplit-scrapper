import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import vm from 'node:vm';
const ctx={};vm.createContext(ctx);vm.runInContext(readFileSync('docs/api-reviewed-links.js','utf8'),ctx);
const row={room_id:'room-a',task_key:'task-a',occupancy_observation_id:'1',task_observation_id:'obs-a'};
const page=items=>({items,coverage:'reviewed_links_only',ingestion:'staging_only'});
const result=await ctx.loadApiReviewedLinks({roomTaskLinks:async()=>page([row])});assert.equal(result[0].task_observation_id,'obs-a');
await assert.rejects(ctx.loadApiReviewedLinks({roomTaskLinks:async()=>page([row,row])}),/duplicate/i);
await assert.rejects(ctx.loadApiReviewedLinks({roomTaskLinks:async()=>page([{...row,occupancy_observation_id:'0'}])}),/invalid/i);
await assert.rejects(ctx.loadApiReviewedLinks({roomTaskLinks:async()=>({items:[row],coverage:'all',ingestion:'staging_only'})}),/invalid/i);
console.log('Reviewed link adapter requires explicit partial coverage and observation identities');
