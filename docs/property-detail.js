/* Conservative joins: never infer that similarly named properties are the same. */
function propertyDetailData(room, context = {}) {
  const text = value => value == null || value === '' ? 'Not reported' : String(value);
  const boolean = value => value === true ? 'Yes' : value === false ? 'No' : 'Not reported';
  const present = room?.occupant_present === true ? 'Present' : room?.occupant_present === false ? 'Not present' : 'Unknown';
  const validTasks = context.tasks && typeof context.tasks === 'object' && !Array.isArray(context.tasks) && Object.values(context.tasks).every(Array.isArray);
  const reviewedMode = context.taskLinkMode === 'reviewed';
  const validLinks = reviewedMode && Array.isArray(context.reviewedLinks) && typeof room?.api_room_id === 'string' && typeof room?.api_observation_id === 'string';
  let linked = [];
  if (context.tasksAuthorized === true && validTasks && validLinks && room?.property_id != null && room?.property_id !== '') {
    linked = Object.values(context.tasks).filter(Array.isArray).flat().filter(task => task && task.property_id === room.property_id &&
      context.reviewedLinks.some(link => link && link.room_id === room.api_room_id && link.occupancy_observation_id === room.api_observation_id &&
        link.task_key === task.api_task_key && link.task_observation_id === task.api_observation_id));
  } else if (!reviewedMode && context.tasksAuthorized === true && validTasks && room?.property_id != null && room?.property_id !== '' && room?.room_number != null && room?.room_number !== '') {
    linked = Object.values(context.tasks).filter(Array.isArray).flat().filter(task =>
      task && task.property_id != null && task.property_id !== '' &&
      task.room_number != null && task.room_number !== '' &&
      String(task.property_id) === String(room.property_id) && String(task.room_number) === String(room.room_number));
  }
  const normalize = value => String(value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();
  const reviewedClimate = context.climateLinkMode === 'reviewed';
  const locationMatches = Array.isArray(context.climate?.locations)
    ? context.climate.locations.filter(location => reviewedClimate
      ? room?.property_id != null && location?.property_id != null && String(location.property_id) === String(room.property_id)
      : room?.address && normalize(location?.name) === normalize(room.address)) : [];
  const locations = (reviewedClimate || locationMatches.length === 1)
    ? locationMatches.flatMap(location => Array.isArray(location.devices) ? location.devices : []) : [];
  return {
    address: text(room?.address), roomNumber: text(room?.room_number), presence: present,
    vacant: boolean(room?.vacant), turned: boolean(room?.turned), rentReady: boolean(room?.rent_ready),
    listedMoveOut: text(room?.listed_move_out), nextMoveIn: text(room?.next_move_in),
    occupancyObserved: text(context.occupancyObserved),
    tasks: linked,
    taskState: context.tasksAuthorized !== true ? (context.taskUnavailableReason || 'Sign in to see private task details.')
      : !validTasks ? 'Private task data unavailable.'
      : reviewedMode && !validLinks ? 'Reviewed task links are unavailable.'
      : reviewedMode ? (linked.length ? 'Reviewed links shown; other tasks may not be linked.' : 'No reviewed links; other tasks may not be linked.')
      : room?.property_id == null || room.property_id === '' || room?.room_number == null || room.room_number === '' ? 'Task linkage is unavailable for this room.'
      : linked.length ? 'Direct room matches' : 'No directly linked room tasks in this snapshot.',
    climate: locations,
    climateObserved: text(context.climate?.scraped_at),
    climateState: locations.length ? (reviewedClimate ? 'Reviewed property mapping; observed readings.' : 'Exact property-name match; observed readings.') : 'No verified climate link for this property.'
  };
}

/* Root stays stable when the room table is re-rendered. No networking or writes. */
function attachPropertyDetail({root, getContext}) {
  const dialog = document.createElement('dialog');
  dialog.className = 'property-detail-dialog';
  dialog.setAttribute('aria-labelledby', 'property-detail-title');
  const content = document.createElement('div');
  const close = document.createElement('button');
  close.type = 'button'; close.className = 'property-detail-close'; close.textContent = 'Close';
  close.setAttribute('aria-label', 'Close property details');
  dialog.append(close, content); document.body.appendChild(dialog);
  let opener = null;
  const addText = (parent, tag, value) => { const node = document.createElement(tag); node.textContent = value; parent.appendChild(node); return node; };
  function open(index, button) {
    const context = getContext();
    if (!context || !Array.isArray(context.rooms) || !Number.isInteger(index) || !context.rooms[index]) return;
    const data = propertyDetailData(context.rooms[index], context);
    content.replaceChildren(); opener = button;
    const title = addText(content, 'h2', `${data.address} · Room ${data.roomNumber}`); title.id = 'property-detail-title';
    addText(content, 'p', `Occupancy observed: ${data.occupancyObserved}`);
    const facts = document.createElement('dl'); facts.className = 'property-detail-facts';
    for (const [label, value] of [['Presence',data.presence], ['Vacant',data.vacant], ['Turn complete',data.turned], ['Rent-ready',data.rentReady], ['Listed move-out',data.listedMoveOut], ['Next move-in',data.nextMoveIn]]) {
      addText(facts, 'dt', label); addText(facts, 'dd', value);
    }
    content.appendChild(facts);
    addText(content, 'h3', 'Linked tasks'); addText(content, 'p', data.taskState);
    for (const task of data.tasks) {
      const row = document.createElement('div'); row.className = 'property-detail-item';
      addText(row, 'strong', String(task.category || 'Task'));
      addText(row, 'p', String(task.details || task.description || 'No details reported.'));
      addText(row, 'p', `Status: ${task.status || 'Not reported'}`); content.appendChild(row);
    }
    addText(content, 'h3', 'Climate'); addText(content, 'p', data.climateState);
    if (data.climate.length) addText(content, 'p', `Observed: ${data.climateObserved}`);
    for (const device of data.climate) {
      const row = document.createElement('p');
      const temp = Number.isFinite(device?.temp) ? `${device.temp}°F` : 'Not reported';
      const cool = Number.isFinite(device?.cool_setpoint) ? `${device.cool_setpoint}°F` : 'Not reported';
      const heat = Number.isFinite(device?.heat_setpoint) ? `${device.heat_setpoint}°F` : 'Not reported';
      row.textContent = `${device?.name || 'Device'}: ${temp}; cool setting ${cool}; heat setting ${heat}`; content.appendChild(row);
    }
    if (!dialog.open) dialog.showModal();
    close.focus();
  }
  const onClick = event => {
    const button = event.target.closest('[data-room-detail]');
    if (!button || !root.contains(button)) return;
    const value = button.getAttribute('data-room-detail');
    if (!/^(0|[1-9]\d*)$/.test(value || '')) return;
    open(Number(value), button);
  };
  const onClose = () => { content.replaceChildren(); if (opener?.isConnected) opener.focus(); opener = null; };
  const onCloseButton = () => dialog.close();
  root.addEventListener('click', onClick); close.addEventListener('click', onCloseButton); dialog.addEventListener('close', onClose);
  return {
    clear() { content.replaceChildren(); if (dialog.open) dialog.close(); },
    destroy() { root.removeEventListener('click', onClick); dialog.removeEventListener('close', onClose); close.removeEventListener('click', onCloseButton); dialog.remove(); }
  };
}
