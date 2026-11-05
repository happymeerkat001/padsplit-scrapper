/* Route the overview's existing sections without rebuilding forms or drafts. */
(() => {
  const views = {
    '': {title:'Operations overview', description:'Occupancy, tasks and property conditions at a glance.', show:['overview-summary','overview-notes']},
    '#occupancy-section': {title:'Properties & rooms', description:'Search rooms and review their current observed status.', show:['occupancy-section']},
    '#content': {title:'Tasks', description:'Review available tasks and internal discussions.', show:['task-sync','content']},
    '#climate-section': {title:'Climate', description:'Review the latest thermostat observations and weather.', show:['climate-section']},
    '#system-health': {title:'System health', description:'Check collection status and source freshness.', show:['system-health']}
  };
  const sections = ['overview-summary','overview-notes','occupancy-section','task-sync','content','climate-section','system-health'];
  function showView() {
    const view = views[location.hash === '#overview' ? '' : location.hash] || views[''];
    const apiRequired = window.PADSPLIT_RUNTIME_CONFIG?.dataMode === 'api' || document.querySelector?.('meta[name="padsplit-data-mode"]')?.content === 'api';
    sections.forEach(id => { const node = document.getElementById(id); if (node) node.hidden = !view.show.includes(id) || (id === 'overview-notes' && apiRequired && node.dataset.notesAllowed !== 'true'); });
    const title = document.getElementById('view-title');
    const description = document.getElementById('view-description');
    if (title) title.textContent = view.title;
    if (description) description.textContent = view.description;
    document.title = `${view.title} · PadSplit Operations`;
  }
  window.addEventListener('hashchange', showView);
  showView();
})();
