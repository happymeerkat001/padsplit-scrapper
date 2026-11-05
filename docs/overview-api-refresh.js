/* Refresh only the current authorized session; defer while an operator is editing. */
function createOverviewApiRefresh({refresh,shouldDefer,isCurrent,onDeferred,onError=()=>{},schedule=setTimeout,cancel=clearTimeout,interval=300000,retryInterval=5000}) {
  let stopped=false,timer;
  function next(delay) { if (!stopped && isCurrent()) timer=schedule(tick,delay); }
  async function tick() {
    if (stopped || !isCurrent()) return;
    if (shouldDefer()) { onDeferred(); next(retryInterval); return; }
    try { await refresh(); }
    catch (error) { if (!stopped && isCurrent()) onError(error); }
    finally { next(interval); }
  }
  next(interval);
  return {stop(){stopped=true;cancel(timer);}};
}
