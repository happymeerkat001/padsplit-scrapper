(() => {
  const body = document.getElementById('integration-rows');
  const status = document.getElementById('integration-status');
  function render(snapshot) {
    body.replaceChildren();
    for (const row of integrationSourceStatus(snapshot)) {
      const tr = document.createElement('tr');
      for (const [index,value] of [row.label,row.collection,row.observed,row.configuration,row.connection].entries()) {
        const cell = document.createElement(index === 0 ? 'th' : 'td');
        if (index === 0) cell.scope = 'row';
        cell.textContent = value; tr.appendChild(cell);
      }
      body.appendChild(tr);
    }
  }
  fetch('./data/latest.json', {cache:'no-store'})
    .then(response => { if (!response.ok) throw new Error('unavailable'); return response.json(); })
    .then(snapshot => { render(snapshot); status.textContent = 'Most recent recorded collection observations.'; })
    .catch(() => { render({}); status.textContent = 'Collection status is unavailable. Retry by refreshing this page.'; });
})();
