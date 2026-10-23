/* Runs before React: the installed room is a default; staff may select a room. */
(() => {
  const config = window.fabricStationConfig || {};
  const defaults = config.stationId || 'shared-door';
  let stored;
  try { stored = localStorage.getItem('fabric-tracker.station'); } catch {}
  const station = ['ppe-door', 'shared-door', 'footwear-door'].includes(stored) ? stored : defaults;
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(station)) {
    throw new Error('Invalid default room configuration. Reinstall the station package.');
  }
  function stayOnScanner() {
    const url = new URL(window.location.href);
    let remembered;
    try { remembered = localStorage.getItem('fabric-tracker.station'); } catch {}
    const selected = ['ppe-door', 'shared-door', 'footwear-door'].includes(remembered) ? remembered : station;
    if (url.searchParams.get('station') === selected && url.hash === '#kiosk') return;
    url.searchParams.set('station', selected);
    url.hash = 'kiosk';
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }
  stayOnScanner();
  window.addEventListener('hashchange', () => {
    if (window.location.hash !== '#kiosk') {
      stayOnScanner();
      // The app also observes hash changes; deliver the corrected route.
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    }
  });
})();
