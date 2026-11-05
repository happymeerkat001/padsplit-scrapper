/* A shared navigation model keeps production and the isolated demo in sync. */
var operationsNavigation = [
  {id:'overview',label:'Overview',href:'./index.html#overview',group:'Operations'},
  {id:'properties',label:'Properties & rooms',href:'./index.html#occupancy-section',group:'Operations'},
  {id:'tasks',label:'Tasks',href:'./index.html#content',group:'Operations'},
  {id:'inbox',label:'Tenant inbox',href:'./private-messages.html',group:'Operations'},
  {id:'climate',label:'Climate',href:'./index.html#climate-section',group:'Operations'},
  {id:'reports',label:'Reports',href:'./stats.html',group:'Operations'},
  {id:'vendors',label:'Vendors',href:'./vendors.html',group:'Operations'},
  {id:'templates',label:'Templates',href:'./templates.html',group:'Operations'},
  {id:'codes',label:'Access codes',href:'./codes.html',group:'Administration'},
  {id:'integrations',label:'Integrations',href:'./integrations.html',group:'Administration'},
  {id:'health',label:'System health',href:'./index.html#system-health',group:'Administration'},
  {id:'settings',label:'Settings',href:'./settings.html',group:'Administration'}
];
/* The same allowlisted visual component is used by demo and production. */
function operationsNavIcon(name) {
  const allowed = ['overview','properties','tasks','inbox','climate','reports','vendors','templates','team','codes','integrations','health','settings'];
  const safeName = allowed.includes(name) ? name : 'overview';
  return `<svg class="ops-icon" aria-hidden="true"><use href="./operations-icons.svg#${safeName}"></use></svg>`;
}
function setOperationsDensity(compact) {
  const value = compact ? 'compact' : 'comfortable';
  if (document.documentElement) document.documentElement.dataset.density = value;
  try { localStorage.setItem('operations-density', value); return true; }
  catch (_) { return false; }
}
try {
  if (document.documentElement) document.documentElement.dataset.density = localStorage.getItem('operations-density') === 'compact' ? 'compact' : 'comfortable';
} catch (_) { /* Storage may be unavailable in private browser modes. */ }
/* Existing pages and section anchors remain directly addressable. */
(() => {
  const nav = document.querySelector('.ops-nav');
  if (!nav) return;
  const current = location.pathname.split('/').pop() || 'index.html';
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'ops-menu-toggle'; button.textContent = 'Menu';
  button.setAttribute('aria-controls','ops-nav-links'); button.setAttribute('aria-expanded','false');
  const backdrop = document.createElement('button'); backdrop.type='button'; backdrop.className='ops-menu-backdrop'; backdrop.setAttribute('aria-label','Close navigation');
  const list = document.createElement('div'); list.className = 'ops-links'; list.id = 'ops-nav-links';
  const closeButton = document.createElement('button'); closeButton.type='button'; closeButton.className='ops-menu-close'; closeButton.textContent='Close navigation'; list.appendChild(closeButton);
  function close() { nav.dataset.open = 'false'; button.setAttribute('aria-expanded','false'); }
  function closeAndFocus() { close(); button.focus(); }
  backdrop.addEventListener('click',closeAndFocus);
  closeButton.addEventListener('click',closeAndFocus);
  button.addEventListener('click', () => {
    const open = button.getAttribute('aria-expanded') !== 'true';
    nav.dataset.open = String(open); button.setAttribute('aria-expanded',String(open));
    if (open) closeButton.focus();
  });
  nav.addEventListener('keydown', event => {
    if (event.key === 'Escape' && button.getAttribute('aria-expanded') === 'true') {
      closeAndFocus();
    }
  });
  let currentGroup = '';
  // API-mode preview must not link users to pages that still use legacy Firestore.
  const apiMode = window.PADSPLIT_RUNTIME_CONFIG?.dataMode === 'api' || document.querySelector('meta[name="padsplit-data-mode"]')?.content === 'api';
  const visibleNavigation = apiMode ? operationsNavigation
    .filter(item => ['overview','tasks','vendors','templates','codes','integrations','settings'].includes(item.id))
    .map(item => item.id === 'tasks' ? {...item,href:'./tasks.html'} : item) : operationsNavigation;
  if (apiMode && window.PADSPLIT_RUNTIME_CONFIG?.teamEnabled === true) {
    visibleNavigation.push({id:'team',label:'Team',href:'./team.html',group:'Administration'});
  }
  visibleNavigation.forEach(({label,href,id,group}) => {
    if (group !== currentGroup) {
      const heading = document.createElement('span'); heading.className = 'ops-group-label'; heading.textContent = group; list.appendChild(heading);
      currentGroup = group;
    }
    const a = document.createElement('a'); a.href=href;
    a.innerHTML = `${operationsNavIcon(id)}<span>${label}</span>`;
    const [path,hash] = href.slice(2).split('#');
    a.dataset.path = path; a.dataset.hash = hash || '';
    a.addEventListener('click',close); list.appendChild(a);
  });
  function updateActive() {
    Array.from(list.children).forEach(a => {
      if (!a.dataset.path) return;
      const match = current === a.dataset.path && (!location.hash && (!a.dataset.hash || a.dataset.hash === 'overview') || !!a.dataset.hash && location.hash === `#${a.dataset.hash}`);
      if (match) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
    });
  }
  window.addEventListener('hashchange',updateActive);
  nav.replaceChildren(button,backdrop,list); close(); updateActive();
})();
