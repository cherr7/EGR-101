import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import { chromium } from '../../../frontend/node_modules/playwright/index.mjs';
import { fileURLToPath } from 'node:url';

const base=process.env.PROTOTYPE_BASE_URL||'http://127.0.0.1:8090';
const root=new URL('.',import.meta.url),errors=[],requests=[];
const output={scanner_layouts:8,log_layouts:6,layout_checks:0,movement_checks:0,log_checks:0,errors,requests};
await fs.mkdir(new URL('screenshots/',root),{recursive:true});
for(const name of ['scanner-layouts.html','transaction-log-layouts.html']) {
  const source=await fs.readFile(new URL(name,root),'utf8');
  assert(!/<!doctype|<html\b|<head\b|<body\b/i.test(source));
  assert(!/\bfetch\s*\(|XMLHttpRequest|WebSocket/.test(source));
  assert(Buffer.byteLength(source)<1000000);
  for(const match of source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi))new vm.Script(match[1],{filename:name});
}
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({reducedMotion:'reduce'});
page.on('pageerror',e=>errors.push(e.message));
page.on('request',r=>{if(/\/api(?:\/|$)/.test(r.url()))requests.push(r.url());});
async function open(name,width=1024) {
  await page.setViewportSize({width:width+32,height:1100});
  await page.goto(base+'/round-two/previews/'+name+'.html');
  const frame=page.frameLocator('#codex-visualization');
  await frame.locator('.viz-carousel').waitFor();
  return frame;
}
async function pick(frame,name) {
  await frame.locator('.viz-carousel-picker summary').click();
  await frame.locator('.viz-carousel-options').getByRole('button',{name,exact:true}).click();
  return frame.locator('[data-variant]:not([hidden])');
}
async function reset(frame){await frame.locator('[data-demo=reset]').click();}
const scannerNames=['Bottom dock','Top controls','Side controls','History rail','History first','Receipt header','Review card','Review bar'];
const logNames=['Table and popup','Side inspector','Inline row editor','Direct cells','Expanded specimens','Table and JSON'];
for(const [preview,names] of [['scanners',scannerNames],['logs',logNames]]) {
  for(const width of [320,736,800,1024]) {
    const f=await open(preview,width);
    for(const name of names) {
      const variant=await pick(f,name);
      const sizing=await f.locator('html').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
      assert(sizing.scroll<=sizing.width+1,preview+'/'+name+'/'+width+' overflow');
      if(preview==='scanners') {
        const d=variant.locator('.r2-device'),box=await d.boundingBox();
        assert.equal(box.height,480);
        if(width>=800)assert.equal(Math.round(box.width),800);
        const anchor=await d.locator('.r2-actions').boundingBox();
        await d.locator('.r2-body').evaluate(el=>el.scrollTop=el.scrollHeight);
        const after=await d.locator('.r2-actions').boundingBox();
        assert.equal(after.y,anchor.y,'toolbar moved while scrolling '+name);
        assert(anchor.y+anchor.height<=box.y+480.1,'toolbar outside device '+name);
        const overflowing=await d.evaluate(el=>Array.from(el.querySelectorAll('.r2-head,.r2-actions,.r2-input,.r2-direction')).filter(node=>node.scrollWidth>node.clientWidth+2).map(n=>n.className));
        assert.deepEqual(overflowing,[],'toolbar overflow '+name+'/'+width);
      }
      output.layout_checks++;
    }
    console.log('Layout checks passed: '+preview+' / '+width);
  }
}
let f=await open('scanners',800);
for(const name of scannerNames) {
  await f.locator('[data-demo=scenario]').selectOption('normal');await reset(f);
  const v=await pick(f,name),d=v.locator('.r2-device'),review=name.startsWith('Review');
  await d.locator('[data-action=direction][data-value=OUT]').click();
  await f.locator('[data-demo=label]').selectOption('A');
  await f.locator('[data-demo=scan]').click();
  if(review){
    assert.equal(await d.locator('[data-slot=history] .history-row').count(),32);
    await d.locator('[data-action=commit]').click();
  }
  await d.locator('[data-slot=history] .history-row').first().filter({hasText:'Stack A · 10 specimens'}).waitFor();
  assert.match(await d.locator('[data-slot=last]').innerText(),/OUT 10/);
  assert.equal(await d.locator('[data-slot=last]').getAttribute('data-kind'),'success');
  assert.equal(await d.locator('[data-action=direction][data-value=OUT]').getAttribute('aria-pressed'),'true');
  const count=await d.locator('[data-slot=history] .history-row').count();
  await f.locator('[data-demo=scan]').click();if(review)await d.locator('[data-action=commit]').click();
  assert.equal(await d.locator('[data-slot=history] .history-row').count(),count,'repeat created movement '+name);
  assert.match(await d.locator('[data-slot=receipt]').innerText(),/Already recorded OUT/);
  output.movement_checks+=2;
  await reset(f);
  await d.locator('[data-action=direction][data-value=OUT]').click();
  await d.locator('[data-action=scope][data-value=individual]').click();
  await f.locator('[data-demo=scan]').click();
  const dialog=d.getByRole('dialog',{name:'Choose numbered specimens'});
  for(const n of ['01','02','03'])await dialog.getByRole('checkbox',{name:'Specimen '+n,exact:true}).check();
  await dialog.locator('[data-action=apply-selection]').click();if(review)await d.locator('[data-action=commit]').click();
  await d.locator('[data-slot=last]').filter({hasText:'OUT 3'}).waitFor();
  assert.equal(await d.locator('[data-action=scope][data-value=stack]').getAttribute('aria-pressed'),'true');
  await f.locator('[data-demo=scan]').click();if(review)await d.locator('[data-action=commit]').click();
  await d.locator('[data-slot=last]').filter({hasText:'OUT 7'}).waitFor();
  output.movement_checks+=2;
  await f.locator('[data-demo=scenario]').selectOption('early');
  await d.locator('[data-action=direction][data-value=OUT]').click();
  await f.locator('[data-demo=scan]').click();if(review)await d.locator('[data-action=commit]').click();
  await d.getByRole('dialog',{name:'Early checkout',exact:true}).waitFor();
  assert.equal(await d.locator('[data-slot=history] .history-row').count(),32);
  await d.locator('[data-action=override]').click();
  await d.locator('[data-slot=last]').filter({hasText:'OUT 10'}).waitFor();
  output.movement_checks++;
  await f.locator('[data-demo=scenario]').selectOption('lost');
  await d.locator('[data-action=direction][data-value=OUT]').click();
  await f.locator('[data-demo=scan]').click();if(review)await d.locator('[data-action=commit]').click();
  await d.locator('[data-slot=receipt]:visible,[data-slot=strip-receipt]:visible').filter({hasText:'Confirmation pending'}).waitFor();
  const committed=await d.locator('[data-slot=history] .history-row').count();
  await d.locator('[data-action=reconcile]:visible').click();
  await d.locator('[data-slot=last]').filter({hasText:'OUT 10'}).waitFor();
  assert.equal(await d.locator('[data-slot=history] .history-row').count(),committed);
  output.movement_checks++;
  console.log('Scanner workflow passed: '+name);
  await f.locator('[data-demo=scenario]').selectOption('offline');
  await f.locator('[data-demo=scan]').click();
  assert.equal(await d.locator('[data-slot=history] .history-row').count(),32);
  assert.match(await d.locator('.connection').innerText(),/NOT AVAILABLE/);
  assert(await d.locator('.r2-input input').isDisabled());
  output.movement_checks++;
  await f.locator('[data-demo=scenario]').selectOption('normal');
  await d.locator('[data-action=scope][data-value=individual]').click();
  await f.locator('[data-demo=label]').selectOption('B');
  await f.locator('[data-demo=scan]').click();
  assert.equal(await d.getByRole('dialog').getByRole('checkbox').count(),20);
  const dialogBox=await d.getByRole('dialog').boundingBox(),deviceBox=await d.boundingBox();
  assert(dialogBox.y>=deviceBox.y&&dialogBox.y+dialogBox.height<=deviceBox.y+480.1);
  output.movement_checks++;
}
await f.locator('[data-demo=scenario]').selectOption('normal');await reset(f);await pick(f,'Bottom dock');
await f.locator('.r2-device:visible .r2-input input').fill('UL1:S:00000000-0000-4000-8000-000000000002');
await f.locator('.r2-device:visible .r2-input input').press('Enter');
await f.locator('.r2-device:visible [data-slot=last]').filter({hasText:'IN 20'}).waitFor();
await f.locator('.r2-device:visible').screenshot({path:fileURLToPath(new URL('screenshots/scanner.png',root))});
await page.emulateMedia({colorScheme:'dark'});
await f.locator('.r2-device:visible').screenshot({path:fileURLToPath(new URL('screenshots/scanner-dark.png',root))});
await page.emulateMedia({colorScheme:'light'});

f=await open('logs',1024);
for(const name of logNames) {
  await f.locator('[data-demo=scenario]').selectOption('normal');await reset(f);
  const v=await pick(f,name),app=v.locator('.r2-log-app');
  await app.locator('[data-action=edit][data-id=T004]').click();
  if(name==='Table and JSON'){
    const area=app.getByRole('textbox',{name:'Editable transaction JSON'});
    const payload=JSON.parse(await area.inputValue());payload.specimens=[1,2,3];await area.fill(JSON.stringify(payload,null,2));
    await app.locator('[data-action=save-json]').click();
  }else{
    if(name==='Direct cells')await app.locator('[data-action=specimens][data-id=T004]').click();
    for(let n=4;n<=10;n++)await app.getByRole('checkbox',{name:'Specimen '+String(n).padStart(2,'0'),exact:true}).uncheck();
    if(name==='Direct cells')await app.locator('[data-action=use-specimens]').click();
    await app.locator('[data-action=save]:visible').click();
  }
  await app.locator('.log-message').filter({hasText:'T004 updated'}).waitFor();
  assert.match(await app.locator('.projection').innerText(),/7 inside · 3 outside/);
  await app.locator('[data-action=view][data-value=all]').click();
  assert.match(await app.locator('.table-pane').innerText(),/T007/);
  await app.locator('[data-action=inspect-event][data-id=T007]').click();
  assert.match(await app.getByRole('dialog').innerText(),/modify_movement/);
  assert.match(await app.getByRole('dialog').innerText(),/T004/);
  output.log_checks++;
  console.log('Log workflow passed: '+name);
  await reset(f);
  await app.locator('[data-action=edit][data-id=T004]').click();
  if(name==='Direct cells')await app.locator('[data-action=cancel-event]').click();
  else await app.locator('[data-action=cancel-event]:visible').click();
  await app.locator('[data-action=confirm-cancel]').click();
  await app.locator('.log-message').filter({hasText:'T004 cancelled'}).waitFor();
  assert.match(await app.locator('.projection').innerText(),/10 inside · 0 outside/);
  output.log_checks++;
  await reset(f);
  await app.locator('[data-action=edit][data-id=T004]').click();
  if(name==='Table and JSON'){
    const area=app.getByRole('textbox',{name:'Editable transaction JSON'}),payload=JSON.parse(await area.inputValue());payload.occurred_at='2026-10-08T11:00';await area.fill(JSON.stringify(payload));await app.locator('[data-action=save-json]').click();
  }else{
    await app.locator('[data-field=occurred_at]').fill('2026-10-08T11:00');await app.locator('[data-action=save]:visible').click();
  }
  assert.match(await app.locator('.log-message').innerText(),/after the demo clock/);
  assert.match(await app.locator('.projection').innerText(),/6 transactions · 0 modifications/);
  output.log_checks++;
}
await f.locator('[data-demo=scenario]').selectOption('normal');await reset(f);
let v=await pick(f,'Side inspector');
await v.locator('[data-action=edit][data-id=T004]').click();
await v.locator('.r2-log-app').screenshot({path:fileURLToPath(new URL('screenshots/log-editor.png',root))});
await page.emulateMedia({colorScheme:'dark'});
await v.locator('.r2-log-app').screenshot({path:fileURLToPath(new URL('screenshots/log-editor-dark.png',root))});
await page.emulateMedia({colorScheme:'light'});
await page.setViewportSize({width:800,height:480});await page.goto(base+'/round-two/station.html');
const hardware=page.locator('.r2-device:visible');
assert.deepEqual(await page.locator('html').evaluate(el=>({width:el.clientWidth,height:el.clientHeight,scrollWidth:el.scrollWidth,scrollHeight:el.scrollHeight})),{width:800,height:480,scrollWidth:800,scrollHeight:480});
assert.equal(await page.locator('.r2-input input:visible').evaluate(el=>el===document.activeElement),true);
await hardware.locator('[data-action=direction][data-value=OUT]').click();
await page.keyboard.type('UL1:S:00000000-0000-4000-8000-000000000001');await page.keyboard.press('Enter');
await hardware.locator('[data-slot=last]').filter({hasText:'OUT 10'}).waitFor();
await page.goto(base+'/round-two/demo-labels.html');
assert.equal(await page.locator('.label').count(),36);
await page.locator('#identity').selectOption('specimen');
assert.match(await page.locator('.label img').first().getAttribute('src'),/A-01/);
await page.setViewportSize({width:1200,height:1000});await page.goto(base+'/round-two/');
await page.screenshot({path:fileURLToPath(new URL('screenshots/gallery.png',root)),fullPage:true});
assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
await fs.writeFile(new URL('verification.json',root),JSON.stringify(output,null,2));
await browser.close();
console.log(JSON.stringify(output,null,2));
