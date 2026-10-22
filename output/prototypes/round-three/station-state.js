/* Standalone demo state survives browser restarts; real movements belong to the API. */
(() => {
  const key='fabric-station-demo-v1';
  let saved=null;try{saved=JSON.parse(localStorage.getItem(key)||'null');}catch{}
  window.openai={
    widgetState:saved,
    async setWidgetState(value){localStorage.setItem(key,JSON.stringify(value));this.widgetState=value;}
  };
})();
