/* Fixed nine-slot migration contract; ambiguous rows never silently disappear. */
(function(root){
  'use strict';
  async function loadTemplateSlots(api, signal) {
    const slots=Array(9).fill(null);
    for(let offset=0;offset<=10000;offset+=100) {
      const page=await api.templates({limit:100,offset,signal});
      if(!page || !Array.isArray(page.items) || page.items.length>100) throw new Error('Invalid template response.');
      for(const item of page.items) {
        if(!item || typeof item.id!=='string') throw new Error('Invalid template response.');
        const match=/^template_shared_([0-8])$/.exec(item.id);
        if(!match) { const error=new Error('Unmapped template records require review.'); error.code='templates/unmapped'; throw error; }
        const index=Number(match[1]);
        if(slots[index]) { const error=new Error('Duplicate template slot.'); error.code='templates/unmapped'; throw error; }
        if(typeof item.name!=='string' || typeof item.body!=='string' || !Number.isSafeInteger(item.version) || item.version<1)
          throw new Error('Invalid template response.');
        slots[index]={id:item.id,name:item.name,body:item.body,version:item.version};
      }
      if(page.items.length<100)return slots;
    }
    throw new Error('Template count exceeds supported pagination.');
  }
  root.loadTemplateSlots=loadTemplateSlots;
})(typeof window!=='undefined'?window:globalThis);
