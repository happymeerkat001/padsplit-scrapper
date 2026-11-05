/* Small, testable boundary for API-backed vendor pages. No credentials are stored here. */
(function(root){
  'use strict';
  function resolveVendorRuntime(config, apiRequired) {
    const active = apiRequired || config?.dataMode === 'api';
    if (!active) return {mode:'legacy'};
    if (!config || config.dataMode !== 'api' || !['preview','production'].includes(config.environment) ||
        !config.firebase || typeof config.firebase.apiKey !== 'string' || !config.firebase.apiKey.trim() ||
        typeof config.firebase.projectId !== 'string' || !/^[a-z][a-z0-9-]{4,61}[a-z0-9]$/.test(config.firebase.projectId) ||
        config.firebase.authDomain !== `${config.firebase.projectId}.firebaseapp.com` || typeof config.apiOrigin !== 'string' || !config.apiOrigin)
      throw new Error('API configuration is incomplete.');
    if (config.environment === 'preview' && config.firebase.projectId === 'padsplit-scrapper')
      throw new Error('Preview cannot use the production Firebase project.');
    let api;
    try { api = new URL(config.apiOrigin); } catch { throw new Error('API configuration is invalid.'); }
    const loopback = ['localhost','127.0.0.1','[::1]'].includes(api.hostname);
    if ((api.protocol !== 'https:' && !(config.environment === 'preview' && loopback && api.protocol === 'http:')) ||
        api.username || api.password || api.search || api.hash || api.pathname !== '/')
      throw new Error('API configuration is invalid.');
    return {mode:'api',firebase:config.firebase,apiOrigin:api.origin};
  }
  async function loadAllApiVendors(api, signal) {
    const result=[], seen=new Set();
    for(let offset=0;offset<=10000;offset+=100) {
      const page=await api.vendors({limit:100,offset,signal});
      if(!page || !Array.isArray(page.items) || page.items.length>100) throw new Error('Invalid vendor response.');
      for(const item of page.items) {
        if (!item || typeof item.id !== 'string' || !/^[A-Za-z0-9_.:-]{1,200}$/.test(item.id) ||
            item.id==='.' || item.id==='..' || seen.has(item.id) || typeof item.name !== 'string' ||
            !Number.isSafeInteger(item.version) || item.version<1 || !item.data || typeof item.data!=='object' || Array.isArray(item.data))
          throw new Error('Invalid vendor response.');
        seen.add(item.id);
        result.push({id:item.id,name:item.name,version:item.version,
          specialty:typeof item.data.specialty==='string'?item.data.specialty:'',
          contact:typeof item.data.contact==='string'?item.data.contact:'',
          location:typeof item.data.location==='string'?item.data.location:''});
      }
      if(page.items.length<100)return result;
    }
    throw new Error('Vendor count exceeds supported pagination.');
  }
  function vendorRemoteDecision(localVersion,remoteVersion,hasDraft) {
    return {replace:!hasDraft,conflict:hasDraft && Number.isSafeInteger(localVersion) && localVersion!==remoteVersion};
  }
  function vendorChangedFields(values, dirtyKeys) {
    const result={};
    for(const key of ['name','specialty','contact','location']) {
      if(dirtyKeys.has(key)) result[key]=values[key];
    }
    return result;
  }
  root.resolveVendorRuntime=resolveVendorRuntime;
  root.resolveOperationsRuntime=resolveVendorRuntime;
  root.loadAllApiVendors=loadAllApiVendors;
  root.vendorRemoteDecision=vendorRemoteDecision;
  root.vendorChangedFields=vendorChangedFields;
})(typeof window!=='undefined'?window:globalThis);
