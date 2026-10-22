/* Runs before the React module; this local package is dedicated to one station. */
(() => {
  const config = window.fabricStationConfig || {};
  const station = config.stationId || 'shared-door';
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(station)) {
    throw new Error('Invalid fixed station configuration. Reinstall the station package.');
  }
  function stayOnScanner() {
    const url = new URL(window.location.href);
    if (url.searchParams.get('station') === station && url.hash === '#kiosk') return;
    url.searchParams.set('station', station);
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
