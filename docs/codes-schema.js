/* Schema describes presentation only; access values live in protected code documents. */
function validateCodesSchema(data) {
  const fail = () => { throw new Error('Invalid private property schema.'); };
  if (!data || typeof data !== 'object' || !Array.isArray(data.properties) || data.properties.length < 1 || data.properties.length > 100) fail();
  const slugs = new Set();
  return data.properties.map(property => {
    if (!property || typeof property !== 'object' || typeof property.slug !== 'string' || !/^[a-z0-9_-]{1,100}$/.test(property.slug) || slugs.has(property.slug)) fail();
    slugs.add(property.slug);
    if (typeof property.address !== 'string' || !property.address.trim() || property.address.length > 200 || !Array.isArray(property.sections) || property.sections.length < 1 || property.sections.length > 30) fail();
    if (property.history_enabled !== undefined && typeof property.history_enabled !== 'boolean') fail();
    const keys = new Set();
    const sections = property.sections.map(section => {
      if (!section || typeof section.label !== 'string' || !section.label.trim() || section.label.length > 200 || !Array.isArray(section.fields) || section.fields.length > 100) fail();
      const fields = section.fields.map(field => {
        if (!field || typeof field.key !== 'string' || !/^[a-z0-9_]{1,100}$/.test(field.key) || keys.has(field.key) || typeof field.label !== 'string' || field.label.length > 200 || (field.value !== undefined && field.value !== '')) fail();
        if (field.placeholder !== undefined && (typeof field.placeholder !== 'string' || field.placeholder.length > 200)) fail();
        if (field.type !== undefined && !['text','date','textarea'].includes(field.type)) fail();
        keys.add(field.key);
        return {key:field.key,label:field.label,value:'',...(field.placeholder === undefined ? {} : {placeholder:field.placeholder}),...(field.type === undefined ? {} : {type:field.type})};
      });
      return {label:section.label,fields};
    });
    return {slug:property.slug,address:property.address.trim(),sections,history_enabled:property.history_enabled !== false};
  });
}
