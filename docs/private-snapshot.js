/* Read a complete, versioned private snapshot; never return partial data. */
async function readPrivateSnapshot(readDocument) {
  const pointer = await readDocument('private_dashboard/current');
  if (!pointer) return null;
  if (pointer.schema_version !== 1 || !/^[a-f0-9]{32}$/.test(pointer.run_id || '') ||
      !Number.isInteger(pointer.chunk_count) || pointer.chunk_count < 1 || pointer.chunk_count > 100 ||
      typeof pointer.scraped_at !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(pointer.scraped_at) ||
      Number.isNaN(Date.parse(pointer.scraped_at))) throw new Error('Invalid private snapshot metadata');
  const pieces = [];
  for (let i = 0; i < pointer.chunk_count; i++) {
    const chunk = await readDocument(`private_dashboard_runs/${pointer.run_id}/chunks/${i}`);
    if (!chunk || chunk.index !== i || typeof chunk.data !== 'string' || chunk.data.length > 400000 || /[^\x00-\x7f]/.test(chunk.data))
      throw new Error('Private snapshot incomplete');
    pieces.push(chunk.data);
  }
  const value = JSON.parse(pieces.join(''));
  if (!value || value.scraped_at !== pointer.scraped_at || !Array.isArray(value.messages) ||
      !value.messages.every(message => message && typeof message === 'object' && !Array.isArray(message)) ||
      !value.tasks || typeof value.tasks !== 'object' || Array.isArray(value.tasks) ||
      !Object.values(value.tasks).every(Array.isArray) ||
      (value.occupancy != null && (typeof value.occupancy !== 'object' || Array.isArray(value.occupancy) ||
        !Array.isArray(value.occupancy.rooms) || !value.occupancy.rooms.every(room => room && typeof room === 'object' && !Array.isArray(room)) ||
        typeof value.occupancy.scraped_at !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.occupancy.scraped_at) ||
        Number.isNaN(Date.parse(value.occupancy.scraped_at)))))
    throw new Error('Private snapshot is invalid');
  return value;
}
