/* Installed beside the local kiosk page, never in the conversation mockup. */
(() => {
  // Idle time lets the station agent update only when nobody is using the station.
  window.fabricLastActivity = Date.now();
  const touch = () => { window.fabricLastActivity = Date.now(); };
  for (const name of ['keydown', 'pointerdown', 'touchstart']) window.addEventListener(name, touch, true);
  function send() {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),1500);
    fetch('/station/heartbeat',{
      method:'POST',cache:'no-store',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        focused:document.hasFocus(),
        visible:document.visibilityState==='visible',
        capture_ready:window.location.hash==='#kiosk'&&window.fabricScannerCaptureReady===true,
        // A live socket alone does not prove that this page owns a usable reader.
        input_bridge:window.fabricInputBridgeReady===true,
        idle_seconds:Math.max(0,Math.floor((Date.now()-(window.fabricLastActivity||0))/1000))
      }),signal:controller.signal
    }).catch(()=>{}).finally(()=>clearTimeout(timer));
  }
  setInterval(send,2000);
  window.addEventListener('focus',send);
  window.addEventListener('blur',send);
  document.addEventListener('visibilitychange',send);
  send();
})();
