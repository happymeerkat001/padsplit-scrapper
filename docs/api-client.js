/* Backend adapter. Firebase remains responsible for authentication. */
(function (root) {
  'use strict';
  function apiOrigin(value) {
    const url = new URL(value);
    const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if ((url.protocol !== 'https:' && !(loopback && url.protocol === 'http:')) ||
        url.username || url.password || url.search || url.hash || url.pathname !== '/') {
      throw new Error('A secure API origin is required.');
    }
    return url.origin;
  }
  function createOperationsApi({baseUrl, getUser, fetchImpl = fetch}) {
    const base = apiOrigin(baseUrl);
    let generation = 0;
    const inflight = new Set();
    function clearSession() {
      generation++;
      for (const controller of inflight) controller.abort();
      inflight.clear();
    }
    async function request(path, {method = 'GET', body, signal, idempotencyKey} = {}) {
      if (idempotencyKey !== undefined && !/^[A-Za-z0-9_.:-]{1,200}$/.test(idempotencyKey)) throw new Error('Invalid request identifier.');
      const user = getUser();
      if (!user || typeof user.getIdToken !== 'function') throw new Error('Sign in is required.');
      const version = generation;
      const token = await user.getIdToken();
      if (generation !== version || getUser() !== user) throw new Error('Session changed.');
      const controller = new AbortController();
      const cancel = () => controller.abort();
      if (signal?.aborted) cancel(); else signal?.addEventListener('abort', cancel, {once:true});
      inflight.add(controller);
      try {
        const response = await fetchImpl(base + path, {
          method, headers: {Authorization:`Bearer ${token}`, ...(body === undefined ? {} : {'Content-Type':'application/json'}), ...(idempotencyKey === undefined ? {} : {'Idempotency-Key':idempotencyKey})},
          ...(body === undefined ? {} : {body:JSON.stringify(body)}), signal:controller.signal,
          credentials:'omit', cache:'no-store', redirect:'error'
        });
        if (generation !== version || getUser() !== user) throw new Error('Session changed.');
        if (!response.ok) {
          const error = new Error(response.status === 409 ? 'This record changed. Refresh and try again.' :
            response.status === 401 ? 'Your session has expired. Sign in again.' :
            response.status === 403 ? 'You do not have access to this information.' : 'The service request failed.');
          error.status = response.status;
          throw error;
        }
        const data = await response.json();
        if (generation !== version || getUser() !== user) throw new Error('Session changed.');
        return data;
      } finally {
        inflight.delete(controller);
        signal?.removeEventListener('abort', cancel);
      }
    }
    function tasks(options = {}) {
      const query = new URLSearchParams();
      if (options.propertyId) query.set('property_id', options.propertyId);
      if (options.limit !== undefined) query.set('limit', String(options.limit));
      if (options.offset !== undefined) query.set('offset', String(options.offset));
      return request('/v1/tasks' + (query.size ? '?' + query : ''), {signal:options.signal});
    }
    function segment(id) {
      if (typeof id !== 'string' || !/^[A-Za-z0-9_.:-]{1,200}$/.test(id) || id === '.' || id === '..') throw new Error('Invalid identifier.');
      return encodeURIComponent(id);
    }
    function taskPath(id) { return '/v1/tasks/' + segment(id); }
    function codesPath(id) { return '/v1/properties/' + segment(id) + '/codes'; }
    function paged(path, {limit = 50, offset = 0, signal} = {}) {
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 || !Number.isSafeInteger(offset) || offset < 0 || offset > 10000) throw new Error('Invalid page.');
      return request(path + '?' + new URLSearchParams({limit:String(limit),offset:String(offset)}), {signal});
    }
    function requestKey(key) {
      if (typeof key !== 'string' || !/^[A-Za-z0-9_.:-]{1,200}$/.test(key)) throw new Error('Invalid request identifier.');
      return key;
    }
    function versionQuery(version) {
      if (!Number.isSafeInteger(version) || version < 1) throw new Error('Invalid record version.');
      return '?expected_version=' + version;
    }
    function recordPath(kind, id) { return '/v1/' + kind + '/' + segment(id); }
    function poll(load, onData, onError, intervalMs = 30000) {
      if (!Number.isFinite(intervalMs) || intervalMs < 5000) throw new Error('Polling interval is too short.');
      let stopped = false, timer, active;
      const tick = async () => {
        if (stopped) return;
        active = new AbortController();
        try { const data = await load(active.signal); if (!stopped) onData(data); }
        catch (error) { if (!stopped && error.name !== 'AbortError') onError(error); }
        finally { active = null; if (!stopped) timer = setTimeout(tick, intervalMs); }
      };
      tick();
      return () => { stopped = true; clearTimeout(timer); active?.abort(); };
    }
    return {
      me: () => request('/v1/me'),
      properties: () => request('/v1/properties'),
      occupancy: options => paged('/v1/occupancy', options),
      climate: options => paged('/v1/climate', options),
      providerTasks: options => paged('/v1/provider-tasks', options),
      roomTaskLinks: options => paged('/v1/room-task-links', options),
      providerDiscussionLinks: options => paged('/v1/provider-discussion-links', options),
      reportAccess: () => request('/v1/report-access'),
      report: (id, {signal} = {}) => {
        if (!['latest','monthly_history'].includes(id)) throw new Error('Invalid report.');
        return request(recordPath('reports', id), {signal});
      },
      vendors: options => paged('/v1/vendors', options),
      templates: options => paged('/v1/templates', options),
      teamMembers: options => paged('/v1/team/members', options),
      teamMember: id => request(recordPath('team/members', id)),
      setTeamMember: (id, body) => request(recordPath('team/members', id), {method:'PUT',body}),
      note: (id, {signal} = {}) => request(recordPath('notes', id), {signal}),
      saveNote: (id, text, expectedVersion) => request(recordPath('notes', id), {method:'PUT',body:{text,expected_version:expectedVersion}}),
      createVendor: (body, key) => request('/v1/vendors', {method:'POST',body,idempotencyKey:requestKey(key)}),
      updateVendor: (id, body) => request(recordPath('vendors', id), {method:'PATCH',body}),
      deleteVendor: (id, version) => request(recordPath('vendors', id) + versionQuery(version), {method:'DELETE'}),
      createTemplate: (body, key) => request('/v1/templates', {method:'POST',body,idempotencyKey:requestKey(key)}),
      updateTemplate: (id, body) => request(recordPath('templates', id), {method:'PATCH',body}),
      deleteTemplate: (id, version) => request(recordPath('templates', id) + versionQuery(version), {method:'DELETE'}),
      tasks,
      task: id => request(taskPath(id)),
      updateTask: (id, body) => request(taskPath(id), {method:'PATCH', body}),
      createTask: (body, idempotencyKey) => request('/v1/tasks', {method:'POST', body, idempotencyKey:requestKey(idempotencyKey)}),
      deleteTask: (id, expectedVersion) => {
        return request(taskPath(id) + versionQuery(expectedVersion), {method:'DELETE'});
      },
      discussion: (id, options) => paged(taskPath(id) + '/discussion', options),
      addDiscussion: (id, body, idempotencyKey, {roleLabel, statusLabel} = {}) => request(taskPath(id) + '/discussion', {method:'POST',body:{body,...(roleLabel === undefined ? {} : {role_label:roleLabel}),...(statusLabel === undefined ? {} : {status_label:statusLabel})},idempotencyKey:requestKey(idempotencyKey)}),
      deleteDiscussion: (id, discussionId) => request(taskPath(id) + '/discussion/' + segment(discussionId), {method:'DELETE'}),
      conversations: ({limit = 50, offset = 0, q = '', unread = false, signal} = {}) => {
        if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 || !Number.isSafeInteger(offset) || offset < 0 || offset > 10000 || typeof q !== 'string' || q.length > 100 || typeof unread !== 'boolean') throw new Error('Invalid inbox search.');
        return request('/v1/inbox/search', {method:'POST',body:{limit,offset,q,unread},signal});
      },
      messages: (id, {limit = 50, offset = 0, order, signal} = {}) => {
        const path = '/v1/inbox/conversations/' + segment(id) + '/messages';
        if (order === undefined) return paged(path, {limit, offset, signal});
        if (!['asc','desc'].includes(order)) throw new Error('Invalid message order.');
        if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 || !Number.isSafeInteger(offset) || offset < 0 || offset > 10000) throw new Error('Invalid page.');
        return request(path + '?' + new URLSearchParams({limit:String(limit),offset:String(offset),order}), {signal});
      },
      codes: id => request(codesPath(id)),
      codesSchema: () => request('/v1/codes/schema'),
      revealCodes: id => request(codesPath(id) + '/reveal', {method:'POST',body:{}}),
      saveCodes: (id, fields, expectedVersion) => request(codesPath(id), {method:'PUT',body:{fields,expected_version:expectedVersion}}),
      codesHistory: (id, options) => paged(codesPath(id) + '/history', options),
      revealCodeHistory: (id, historyId) => request(codesPath(id) + '/history/' + segment(historyId) + '/reveal', {method:'POST',body:{}}),
      restoreCodes: (id, historyId, expectedVersion) => request(codesPath(id) + '/history/' + segment(historyId) + '/restore', {method:'POST',body:{expected_version:expectedVersion}}),
      poll, clearSession
    };
  }
  root.createOperationsApi = createOperationsApi;
})(typeof window !== 'undefined' ? window : globalThis);
