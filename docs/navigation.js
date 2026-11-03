/* Existing pages and section anchors remain directly addressable. */
(() => {
  const nav = document.querySelector('.ops-nav');
  if (!nav) return;
  const links = [
    ['Overview','./index.html#overview'], ['Properties & rooms','./index.html#occupancy-section'],
    ['Tasks','./index.html#content'], ['Tenant inbox','./private-messages.html'],
    ['Climate','./index.html#climate-section'], ['Reports','./stats.html'],
    ['Vendors','./vendors.html'], ['Templates','./templates.html'],
    ['Access codes','./codes.html'], ['System health','./index.html#system-health']
  ];
  const current = location.pathname.split('/').pop() || 'index.html';
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'ops-menu-toggle'; button.textContent = 'Menu';
  button.setAttribute('aria-controls','ops-nav-links'); button.setAttribute('aria-expanded','false');
  const list = document.createElement('div'); list.className = 'ops-links'; list.id = 'ops-nav-links';
  function close() { nav.dataset.open = 'false'; button.setAttribute('aria-expanded','false'); }
  button.addEventListener('click', () => {
    const open = button.getAttribute('aria-expanded') !== 'true';
    nav.dataset.open = String(open); button.setAttribute('aria-expanded',String(open));
  });
  nav.addEventListener('keydown', event => {
    if (event.key === 'Escape' && button.getAttribute('aria-expanded') === 'true') {
      close(); button.focus();
    }
  });
  links.forEach(([label,href]) => {
    const a = document.createElement('a'); a.href=href; a.textContent=label;
    const [path,hash] = href.slice(2).split('#');
    a.dataset.path = path; a.dataset.hash = hash || '';
    a.addEventListener('click',close); list.appendChild(a);
  });
  function updateActive() {
    Array.from(list.children).forEach(a => {
      const match = current === a.dataset.path && (!location.hash && (!a.dataset.hash || a.dataset.hash === 'overview') || !!a.dataset.hash && location.hash === `#${a.dataset.hash}`);
      if (match) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current');
    });
  }
  window.addEventListener('hashchange',updateActive);
  nav.replaceChildren(button,list); close(); updateActive();
})();
