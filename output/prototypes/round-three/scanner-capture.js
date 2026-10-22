/* Keyboard-wedge scanner capture. No input focus, USB permission, or network needed. */
(() => {
  function attach(options) {
    const target=options.target||document,view=target.defaultView||window;
    const gapMs=options.gapMs||250,maxLength=options.maxLength||40;
    let buffer='',timeout=null,invalid=false,started=false;
    function reset(){buffer='';invalid=false;started=false;clearTimeout(timeout);}
    function consume(event){event.preventDefault();event.stopImmediatePropagation();}
    function keydown(event){
      if(event.isComposing||event.ctrlKey||event.altKey||event.metaKey){reset();return;}
      if(options.ignore?.(event.target)){reset();return;}
      const format=options.format?.()||'binary';
      const valid=format==='binary'?/^[01]$/:format==='hex'?/^[0-9a-f]$/i:/^[0-9]$/;
      if(event.key==='Enter'){
        consume(event);
        if(!started)return;
        const code=buffer,bad=invalid;reset();
        if(bad){options.onReject?.('Invalid or overlong scanner input. Rescan the complete label.');return;}
        if(!options.enabled()){options.onBlocked?.();return;}
        options.onScan(code);return;
      }
      if(event.key.length===1){
        // A malformed character must not turn a prefix/suffix into a valid specimen.
        if(event.key===' '&&!started)return;
        consume(event);started=true;
        if(!valid.test(event.key)||buffer.length>=maxLength)invalid=true;
        else buffer+=event.key;
        clearTimeout(timeout);timeout=setTimeout(()=>{buffer='';invalid=true;options.onReject?.('Incomplete scan cleared. Rescan the complete label.');},gapMs);
      }else if(started&&event.key!=='Shift'){
        consume(event);reset();
      }
    }
    function interrupted(){reset();options.onFocusChange?.();}
    target.addEventListener('keydown',keydown,true);
    target.addEventListener('visibilitychange',interrupted);
    view.addEventListener('blur',interrupted);view.addEventListener('focus',interrupted);
    return {reset,dispose(){reset();target.removeEventListener('keydown',keydown,true);target.removeEventListener('visibilitychange',interrupted);view.removeEventListener('blur',interrupted);view.removeEventListener('focus',interrupted);}};
  }
  window.FabricScannerCapture={attach};
})();
