/* Owner-only API schema and dirty-field merging for access codes. */
(function(root){
  'use strict';
  function parseApiCodesSchema(data) {
    if(!data || !Array.isArray(data.properties) || !data.versions || typeof data.versions!=='object' || Array.isArray(data.versions)) throw new Error('Invalid private schema.');
    if(data.properties.length===0) {
      if(Object.keys(data.versions).length) throw new Error('Schema/version mismatch.');
      return {properties:[],versions:{}};
    }
    const properties=validateCodesSchema(data),ids=properties.map(p=>p.slug),keys=Object.keys(data.versions);
    if(keys.length!==ids.length || ids.some(id=>!Object.prototype.hasOwnProperty.call(data.versions,id))) throw new Error('Schema/version mismatch.');
    if(ids.some(id=>!Number.isSafeInteger(data.versions[id]) || data.versions[id]<1)) throw new Error('Invalid schema version.');
    return {properties,versions:Object.fromEntries(ids.map(id=>[id,data.versions[id]]))};
  }
  function mergeApiCodeDraft(base, changes) {
    if(!base || typeof base!=='object' || Array.isArray(base)) throw new Error('Reveal values before saving.');
    const merged=Object.create(null);
    for(const [key,value] of Object.entries(base)) {
      if(typeof value!=='string' || value==='••••') throw new Error('Invalid masked baseline.');
      merged[key]=value;
    }
    for(const item of changes) if(item.dirty) {
      if(typeof item.key!=='string' || !/^[a-z0-9_]{1,100}$/.test(item.key) || typeof item.value!=='string') throw new Error('Invalid code field.');
      merged[item.key]=item.value.trim();
    }
    return merged;
  }
  root.parseApiCodesSchema=parseApiCodesSchema;
  root.mergeApiCodeDraft=mergeApiCodeDraft;
})(typeof window!=='undefined'?window:globalThis);
