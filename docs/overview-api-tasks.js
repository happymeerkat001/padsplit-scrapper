/* Small, scoped summary of API-managed tasks. Full records and discussions live in Tasks. */
(function (root) {
  'use strict';
  const idPattern = /^[A-Za-z0-9_.:-]{1,200}$/;
  function createOverviewApiTasks({api, output, isCurrent, onAccessLost}) {
    let stopPolling = null;
    const clear = () => { output.textContent = 'Sign in to load task status.'; };
    function show(page) {
      if (!isCurrent()) return;
      if (!page || !Array.isArray(page.items) || page.items.length > 100) throw new Error('Invalid task page');
      const counts = {open:0, in_progress:0, done:0}, ids = new Set();
      for (const row of page.items) {
        if (!row || typeof row.id !== 'string' || !idPattern.test(row.id) || row.id === '.' || row.id === '..' || ids.has(row.id) ||
            typeof row.title !== 'string' ||
            !Object.prototype.hasOwnProperty.call(counts, row.status) ||
            !(row.property_id === null || typeof row.property_id === 'string' && idPattern.test(row.property_id))) {
          throw new Error('Invalid task page');
        }
        ids.add(row.id); counts[row.status]++;
      }
      output.textContent = `${counts.open} open · ${counts.in_progress} in progress · ${counts.done} done (first 100 tasks; see Tasks for all pages).`;
    }
    function fail(error) {
      if (!isCurrent()) return;
      if (error?.status === 401 || error?.status === 403) { onAccessLost(error); return; }
      output.textContent = 'Task status is unavailable. Open Tasks to retry.';
    }
    return {
      start() {
        if (stopPolling) throw new Error('Task summary already started');
        output.textContent = 'Loading task status…';
        stopPolling = api.poll(signal => api.tasks({limit:100,offset:0,signal}), show, fail);
      },
      stop() { if (stopPolling) stopPolling(); stopPolling = null; clear(); }
    };
  }
  root.createOverviewApiTasks = createOverviewApiTasks;
})(typeof window !== 'undefined' ? window : globalThis);
