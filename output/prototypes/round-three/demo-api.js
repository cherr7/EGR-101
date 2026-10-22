/* Local stand-in for the project's future HTTP API. No network or SQL access. */
(() => {
  const MAX_ID=1099511627775n;
  const clone=value=>JSON.parse(JSON.stringify(value));
  const config=[
    {stack:'A',base:100000000100,count:10,project:'00073-LAB',material:'Cotton twill',test:'D5034',treatment:'AR'},
    {stack:'B',base:1099511627755,count:20,project:'AB-004',material:'Nylon weave',test:'D2261',treatment:'3L'},
    {stack:'C',base:271000000000,count:6,project:'00702',material:'Polyester mesh',test:'D5034',treatment:'1L'}
  ];
  function decodeId(input,format='decimal') {
    let text=String(input).trim(),value;
    if(/^0x[0-9a-f]+$/i.test(text))value=BigInt(text);
    else if(/^0b[01]+$/i.test(text))value=BigInt(text);
    else if(format==='binary'&&/^[01]+$/.test(text))value=BigInt('0b'+text);
    else if(format==='hex'&&/^[0-9a-f]+$/i.test(text))value=BigInt('0x'+text);
    else if(format==='decimal'&&/^\d+$/.test(text))value=BigInt(text);
    else throw new Error('ID format not recognized. Check the scanner input setting.');
    if(value<0n||value>MAX_ID)throw new Error('ID is outside the unsigned 40-bit range.');
    return Number(value);
  }
  function decodeBytes(bytes,littleEndian=false) {
    if(!(bytes instanceof Uint8Array)||bytes.length!==5)throw new Error('Expected five ID bytes.');
    const ordered=littleEndian?Array.from(bytes).reverse():Array.from(bytes);
    return Number(ordered.reduce((n,b)=>(n<<8n)|BigInt(b),0n));
  }
  function localInput(iso) {
    if(!iso)return '';
    const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso));
    const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));
    return p.year+'-'+p.month+'-'+p.day+'T'+p.hour+':'+p.minute;
  }
  function utcInput(value) {
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))throw new Error('Enter a valid movement time.');
    const wall=Date.parse(value+'Z');let guess=wall;
    for(let i=0;i<3;i++)guess+=wall-Date.parse(localInput(new Date(guess).toISOString())+'Z');
    const result=new Date(guess).toISOString();
    if(localInput(result)!==value)throw new Error('That local time does not exist.');
    return result;
  }
  function clockText(iso) {return new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(iso));}
  function dateText(iso) {return iso?new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso)):'—';}
  function create(options={}) {
    const groups=options.overview?config.concat([
      {stack:'D',base:410000000000,count:8,project:'00073-LAB',material:'Cotton twill',test:'D5034',treatment:'1L'},
      {stack:'E',base:510000000000,count:12,project:'Q-001',material:'Aramid blend',test:'D2261',treatment:'5L'},
      {stack:'F',base:610000000000,count:8,project:'00018',material:'Polyester knit',test:'D5034',treatment:'AR'}
    ]):config;
    const fixtures=groups.flatMap(group=>Array.from({length:group.count},(_,i)=>({ID:group.base+i+1,prep:{...group,number:i+1}})));
    const ids=stack=>fixtures.filter(r=>r.prep.stack===stack).map(r=>r.ID);
    const initial=[
      {transaction_id:'T001',type:'movement',direction:'IN',room:'Shared',specimen_ids:ids('A'),observed_at:'2026-10-07T12:40:00.000Z',recorded_at:'2026-10-07T12:40:00.000Z',note:''},
      {transaction_id:'T002',type:'movement',direction:'IN',room:'Shared',specimen_ids:ids('C'),observed_at:'2026-10-08T10:40:00.000Z',recorded_at:'2026-10-08T10:40:00.000Z',note:''},
      {transaction_id:'T003',type:'printed',specimen_ids:ids('B'),recorded_at:'2026-10-08T12:00:00.000Z'}
    ];
    if(options.mistake)initial.push({transaction_id:'T004',type:'movement',direction:'OUT',room:'Shared',specimen_ids:ids('A'),observed_at:'2026-10-08T14:10:00.000Z',recorded_at:'2026-10-08T14:10:00.000Z',note:''});
    if(options.overview)[
      {direction:'IN',room:'PPE',specimen_ids:ids('D'),observed_at:'2026-10-07T15:10:00.000Z'},
      {direction:'IN',room:'Shared',specimen_ids:ids('E').slice(0,8),observed_at:'2026-10-07T13:00:00.000Z'},
      {direction:'IN',room:'Shared',specimen_ids:ids('E').slice(8),observed_at:'2026-10-08T04:40:00.000Z'},
      {direction:'OUT',room:'Shared',specimen_ids:ids('E').slice(0,5),observed_at:'2026-10-08T14:00:00.000Z'},
      {direction:'IN',room:'Footwear',specimen_ids:ids('F'),observed_at:'2026-10-05T12:00:00.000Z'},
      {direction:'OUT',room:'Footwear',specimen_ids:ids('F'),observed_at:'2026-10-06T13:00:00.000Z'}
    ].forEach(event=>initial.push({transaction_id:'T'+String(initial.length+1).padStart(3,'0'),type:'movement',recorded_at:event.observed_at,note:'',...event}));
    let events=clone(initial),revision=0,now=Date.parse('2026-10-08T14:40:00.000Z'),scenario='normal';
    const requests=new Map();
    function effective(source=events) {
      const map=new Map();
      source.forEach(event=>{
        if(event.type==='movement')map.set(event.transaction_id,{...clone(event),cancelled:false,modified:false});
        if(event.type==='modification'&&map.has(event.target_transaction))map.set(event.target_transaction,{...map.get(event.target_transaction),...clone(event.changes),modified:true});
        if(event.type==='undo'&&map.has(event.target_transaction))map.get(event.target_transaction).cancelled=true;
      });
      return Array.from(map.values());
    }
    function project(source=events,strict=false) {
      const rows=fixtures.map(r=>({...clone(r),ENTRY:null,EXIT:null,room:null,raw_json_time:[]}));
      const byId=new Map(rows.map(r=>[r.ID,r]));
      const movements=effective(source).filter(e=>!e.cancelled).sort((a,b)=>a.observed_at.localeCompare(b.observed_at)||a.transaction_id.localeCompare(b.transaction_id));
      movements.forEach(event=>event.specimen_ids.forEach(id=>{
        const row=byId.get(id);if(!row)throw new Error('Unknown specimen ID.');
        if(event.direction==='IN') {
          if(strict&&row.ENTRY)throw new Error('Specimen '+row.prep.number+' already has its one ENTRY. Correct the existing transaction.');
          row.ENTRY=event.observed_at;row.room=event.room;
        } else {
          if(strict&&(!row.ENTRY||row.EXIT))throw new Error('Specimen '+row.prep.number+' needs one ENTRY before one EXIT.');
          if(strict&&event.observed_at<row.ENTRY)throw new Error('EXIT must be after ENTRY.');
          row.EXIT=event.observed_at;
        }
      }));
      source.forEach(event=>{
        const target=event.target_transaction?source.find(e=>e.transaction_id===event.target_transaction):null;
        const affected=new Set([...(event.specimen_ids||[]),...(target?.specimen_ids||[]),...(event.changes?.specimen_ids||[])]);
        affected.forEach(id=>{if(byId.has(id))byId.get(id).raw_json_time.push(clone(event));});
      });
      rows.forEach(row=>row.status=!row.ENTRY?'Awaiting entry':row.EXIT?'Finished':'Conditioning');
      return rows;
    }
    function append(event) {
      const id='T'+String(events.length+1).padStart(3,'0');now+=1000;
      const full={transaction_id:id,recorded_at:new Date(now).toISOString(),...event};
      project(events.concat(full),true);events.push(full);revision++;
      return full;
    }
    function delay(fn) {
      return new Promise((resolve,reject)=>setTimeout(()=>{
        if(scenario==='offline'){reject(new Error('NOT AVAILABLE · no database connection.'));return;}
        try {resolve(fn());}catch(error){reject(error);}
      },200));
    }
    function lookup(id) {
      const rows=project(),record=rows.find(r=>r.ID===Number(id));
      if(!record)throw new Error('Specimen ID not registered.');
      return {record,members:rows.filter(r=>r.prep.stack===record.prep.stack),version:revision};
    }
    function eligible(records,direction) {return records.filter(r=>direction==='IN'?!r.ENTRY:!!r.ENTRY&&!r.EXIT);}
    function canUndo(transactionId) {
      const target=effective().find(e=>e.transaction_id===transactionId);
      if(!target||target.cancelled)return {allowed:false,reason:'This transaction is already cancelled.'};
      const index=events.findIndex(e=>e.transaction_id===transactionId);
      for(const later of events.slice(index+1)) {
        const affected=later.specimen_ids||events.find(e=>e.transaction_id===later.target_transaction)?.specimen_ids||[];
        if(affected.some(id=>target.specimen_ids.includes(id)))return {allowed:false,reason:'A later transaction affects these specimens. Use the log editor.'};
      }
      try {project(events.concat({type:'undo',target_transaction:transactionId}),true);}
      catch(error){return {allowed:false,reason:error.message};}
      return {allowed:true,count:target.specimen_ids.length,direction:target.direction,specimen_ids:target.specimen_ids};
    }
    const api={
      fixtures:clone(fixtures),decodeId,decodeBytes,eligible,clockText,dateText,localInput,utcInput,
      setScenario(value){scenario=value;},
      now(){return now;},
      lookup(id){return delay(()=>lookup(id));},
      listRecords(){return clone(project());},
      listTransactions(){return clone(events);},
      listMovements(){return clone(effective());},
      canUndo,
      snapshot(){return {events:clone(events),revision,now,requests:Array.from(requests.entries()).slice(-3)};},
      restore(snapshot){
        if(!snapshot||!Array.isArray(snapshot.events))return;
        try {project(snapshot.events,true);events=clone(snapshot.events);revision=Number(snapshot.revision)||0;now=Number(snapshot.now)||now;requests.clear();(snapshot.requests||[]).forEach(([id,record])=>requests.set(id,record));}catch {}
      },
      previewModification(transactionId,changes){
        return clone(project(events.concat({type:'modification',target_transaction:transactionId,changes}),true));
      },
      move(request){
        return delay(()=>{
          const old=requests.get(request.request_id);
          if(old) {
            if(JSON.stringify(old.payload)!==JSON.stringify(request))throw new Error('Request ID cannot be reused with different details.');
            return clone(old.result);
          }
          if(request.version!==revision||scenario==='stale')throw new Error('The stack changed. Rescan before submitting.');
          const {members}=lookup(request.scanned_id);
          const selected=request.scope==='stack'?eligible(members,request.direction):members.filter(r=>request.specimen_ids.includes(r.ID));
          if(request.scope==='individual'&&request.specimen_ids.some(id=>!members.some(r=>r.ID===id)))throw new Error('Selection contains a specimen from another stack.');
          if(!selected.length){
            const result={noop:true,direction:request.direction,detail:request.direction==='IN'?'Already entered or finished · no new conditioning period.':'Already OUT or not yet entered · no transaction added.'};
            requests.set(request.request_id,{payload:clone(request),result:clone(result)});return result;
          }
          if(selected.some(r=>!eligible([r],request.direction).length))throw new Error('Some selected specimens cannot make this movement.');
          if(request.direction==='OUT'&&selected.some(r=>r.room!==request.room))throw new Error('Checkout room does not match ENTRY.');
          const early=request.direction==='OUT'?selected.filter(r=>now-Date.parse(r.ENTRY)<24*3600000):[];
          if(early.length&&!request.acknowledge_early)throw new Error('Early checkout needs an override.');
          const transaction=append({type:'movement',direction:request.direction,room:request.room,specimen_ids:selected.map(r=>r.ID),observed_at:new Date(now+1000).toISOString(),request_id:request.request_id,note:'',early_override:!!early.length});
          const result={transaction_id:transaction.transaction_id,direction:request.direction,count:selected.length,specimen_ids:selected.map(r=>r.ID),recorded_at:transaction.recorded_at,early_override:!!early.length};
          requests.set(request.request_id,{payload:clone(request),result:clone(result)});
          if(scenario==='lost'){const error=new Error('Response lost · confirmation pending.');error.pending=true;error.request_id=request.request_id;throw error;}
          return result;
        });
      },
      status(requestId){return delay(()=>{const record=requests.get(requestId);if(!record)throw new Error('Request not found.');return clone(record.result);});},
      undo(transactionId){
        return delay(()=>{
          const verdict=canUndo(transactionId);if(!verdict.allowed)throw new Error(verdict.reason);
          if(scenario==='stale')throw new Error('Transaction changed. Refresh before undo.');
          const event=append({type:'undo',target_transaction:transactionId,specimen_ids:verdict.specimen_ids});
          return {transaction_id:event.transaction_id,target_transaction:transactionId,count:verdict.count,recorded_at:event.recorded_at};
        });
      },
      modify(transactionId,changes,version){
        return delay(()=>{
          if(version!==revision||scenario==='stale')throw new Error('Transaction changed elsewhere. Reopen the row.');
          const original=effective().find(e=>e.transaction_id===transactionId);
          if(!original||original.cancelled)throw new Error('Transaction cannot be edited.');
          if(changes.observed_at&&Date.parse(changes.observed_at)>now)throw new Error('Movement time is in the future.');
          if(changes.specimen_ids&&(!changes.specimen_ids.length||new Set(changes.specimen_ids).size!==changes.specimen_ids.length||changes.specimen_ids.some(id=>!fixtures.some(r=>r.ID===id&&r.prep.stack===fixtures.find(x=>x.ID===original.specimen_ids[0]).prep.stack))))throw new Error('Select registered specimens from this stack, or cancel the transaction.');
          const event=append({type:'modification',target_transaction:transactionId,changes:clone(changes)});
          return {transaction_id:event.transaction_id,recorded_at:event.recorded_at};
        });
      },
      version(){return revision;},
      recordMissed(details){
        return delay(()=>{
          if(scenario==='stale')throw new Error('Records changed elsewhere. Reopen the form.');
          if(Date.parse(details.observed_at)>now)throw new Error('Movement time is in the future.');
          if(!Array.isArray(details.specimen_ids)||!details.specimen_ids.length||new Set(details.specimen_ids).size!==details.specimen_ids.length)throw new Error('Select numbered specimens for the manual record.');
          const event=append({type:'movement',source:'manual',...clone(details)});
          return {transaction_id:event.transaction_id,recorded_at:event.recorded_at};
        });
      },
      simulateLaterMovement(){
        const row=project().find(r=>r.ENTRY&&!r.EXIT);
        if(row)append({type:'movement',direction:'OUT',room:row.room,specimen_ids:[row.ID],observed_at:new Date(now+1000).toISOString(),note:''});
      }
    };
    if(options.saved)api.restore(options.saved);
    return api;
  }
  window.FabricPrototypeAPI={create,config,decodeId,decodeBytes,localInput,utcInput,clockText,dateText};
})();
