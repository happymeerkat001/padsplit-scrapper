/* The transaction rechecks the live document before replacing it. */
async function restoreCodeFields({run, ref, expectedHash, hash, fields, timestamp, canWrite}) {
  return run(async transaction => {
    if (!canWrite()) throw Object.assign(new Error('Session changed'), {code:'codes/session-changed'});
    const current = await transaction.get(ref);
    const actualHash = await hash(current.exists() ? current.data() : {});
    if (actualHash !== expectedHash) throw Object.assign(new Error('Live codes changed'), {code:'codes/conflict'});
    if (!canWrite()) throw Object.assign(new Error('Session changed'), {code:'codes/session-changed'});
    transaction.set(ref, {...fields, updatedAt:timestamp()});
  });
}
