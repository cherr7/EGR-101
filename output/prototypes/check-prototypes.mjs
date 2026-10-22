import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';

const root = new URL('.', import.meta.url);
const screenshotPath = name => fileURLToPath(new URL(`screenshots/${name}`,root));
const base = process.env.PROTOTYPE_BASE_URL || 'http://127.0.0.1:8090';
const errors = [], apiRequests = [], failures = [];
const result = { concepts: 40, layout_checks: 0, carousel_state: false, gallery_shortlist: false, combined_navigation: false, errors, apiRequests, failures };
for (const name of ['scanner-designs.html','correction-designs.html','interface-landscape.html','workflow-explorer.html']) {
  const source = await fs.readFile(new URL(name,root),'utf8');
  assert(Buffer.byteLength(source) < 1_000_000, `${name} exceeds fragment limit`);
  assert(!/<!doctype|<html\b|<head\b|<body\b/i.test(source), `${name} is not a fragment`);
  assert(!/\bfetch\s*\(|XMLHttpRequest|new\s+WebSocket|\.sendBeacon\s*\(/.test(source), `${name} contains network API calls`);
  for (const match of source.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    if (!/application\/json/.test(match[1])) new vm.Script(match[2], { filename:name });
  }
}
const browser = await chromium.launch({headless:true});
const context = await browser.newContext({reducedMotion:'reduce'});
const page = await context.newPage();
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => { if(/\/api(?:\/|$)/.test(request.url())) apiRequests.push(request.url()); });

async function openPreview(name,width=1024) {
  await page.setViewportSize({width:width+32,height:1100});
  await page.goto(`${base}/previews/${name}.html`);
  const frame = page.frameLocator('#codex-visualization');
  await frame.locator('body').waitFor();
  return frame;
}
async function measure(frame,label) {
  const sizing = await frame.locator('html').evaluate(el => ({ width:el.clientWidth, scroll:el.scrollWidth }));
  result.layout_checks++;
  if(sizing.scroll > sizing.width+1) failures.push(`${label}: horizontal overflow ${sizing.scroll}/${sizing.width}`);
}
async function variant(frame,name) {
  await frame.locator('.viz-carousel-picker summary').click();
  await frame.locator('.viz-carousel-options').getByRole('button',{name,exact:true}).click();
  assert.equal(await frame.locator('.viz-carousel').getAttribute('data-active-variant'), name);
  return frame.locator('[data-variant]:not([hidden])');
}

for (const [preview,catalogue] of [['scanners','scanner-catalogue.json'],['corrections','correction-catalogue.json']]) {
  const items = JSON.parse(await fs.readFile(new URL(catalogue,root),'utf8'));
  assert.equal(items.length,16);
  for(const width of [320,736,1024]) {
    const frame = await openPreview(preview,width);
    assert.equal(await frame.locator('.viz-carousel > [data-variant]').count(),16);
    for (const item of items) {
      await variant(frame,item.name);
      await measure(frame,`${preview}/${item.name}/${width}`);
    }
  }
  const frame = await openPreview(preview,1024);
  await page.emulateMedia({colorScheme:'dark'});
  await frame.locator('body').screenshot({path:screenshotPath(`${preview}-dark.png`)});
  await page.emulateMedia({colorScheme:'light'});
  await frame.locator('body').screenshot({path:screenshotPath(`${preview}.png`)});
}

for(const width of [320,736,1024]) {
  const frame = await openPreview('landscape',width);
  const items = JSON.parse(await fs.readFile(new URL('architecture-catalogue.json',root),'utf8'));
  for(const item of items) {
    await frame.locator('#landscape-arrangement').selectOption(item.id);
    await measure(frame,`landscape/${item.name}/${width}`);
  }
}
let frame = await openPreview('landscape');
await frame.locator('body').screenshot({path:screenshotPath('landscape.png')});
await frame.locator('#landscape-arrangement').selectOption('request-approval');
await frame.locator('[data-action=correct]').click();
assert.match(await frame.locator('#landscape-outcome').innerText(), /request submitted.*unchanged/i);
await frame.locator('#landscape-approve').click();
assert.match(await frame.locator('#landscape-outcome').innerText(), /correction committed/i);
await frame.locator('#landscape-arrangement').selectOption('pi-portal');
await frame.locator('#landscape-scenario').selectOption('network');
await frame.locator('[data-action=scan]').click();
assert.match(await frame.locator('#landscape-outcome').innerText(), /NOT AVAILABLE/);
await frame.locator('#landscape-offline').check();
assert.match(await frame.locator('#landscape-outcome').innerText(), /UNCONFIRMED.*no green/i);
await frame.locator('#landscape-arrangement').selectOption('site-hybrid');
await frame.locator('[data-action=scan]').click();
assert.match(await frame.locator('#landscape-outcome').innerText(), /confirmed movement/i);

frame = await openPreview('explorer');
await frame.getByTestId('persistent-in').click();
await frame.getByTestId('persistent-code').fill('UL1:P:00000000-0000-4000-8000-000000000003');
await frame.getByTestId('persistent-code').press('Enter');
await frame.getByTestId('persistent-result').filter({hasText:'IN confirmed by simulated server'}).waitFor();
await frame.locator('#explorer-correction-tab').click();
await frame.locator('[data-variant="Specimen timeline"] [data-testid=correction-reason]').fill('Confirmed from the room log.');
await frame.locator('#explorer-landscape-tab').click();
await frame.locator('#landscape-arrangement').selectOption('role-web-apps');
await frame.locator('#explorer-scan-tab').click();
assert.match(await frame.getByTestId('persistent-result').innerText(), /IN confirmed/);
await frame.locator('#explorer-correction-tab').click();
assert.equal(await frame.locator('[data-variant="Specimen timeline"] [data-testid=correction-reason]').inputValue(),'Confirmed from the room log.');
await frame.locator('#explorer-landscape-tab').click();
assert.equal(await frame.locator('#landscape-arrangement').inputValue(),'role-web-apps');
result.combined_navigation = true;
for(const width of [320,736,1024]) {
  const combined = await openPreview('explorer',width);
  for(const tab of ['#explorer-scan-tab','#explorer-correction-tab','#explorer-landscape-tab']) {
    await combined.locator(tab).click();
    await measure(combined,`combined/${tab}/${width}`);
  }
}

frame = await openPreview('scanners');
await frame.getByTestId('persistent-out').click();
await variant(frame,'Scanner command cards');
await variant(frame,'Persistent mode');
assert.equal(await frame.getByTestId('persistent-out').getAttribute('aria-pressed'),'true');
result.carousel_state = true;

await page.setViewportSize({width:1200,height:1000});
await page.goto(base);
assert.equal(await page.locator('.concept').count(),40);
await page.getByRole('button',{name:'Pin Persistent mode',exact:true}).click();
await page.reload();
assert.equal(await page.getByRole('button',{name:'Unpin Persistent mode',exact:true}).getAttribute('aria-pressed'),'true');
await page.locator('#device-filter').selectOption('handheld');
assert((await page.locator('.concept').count())>0);
await page.locator('#family-filter').selectOption('correction');
assert((await page.locator('.concept').count())>0);
result.gallery_shortlist = true;
await page.locator('#device-filter').selectOption('all');
await page.locator('#family-filter').selectOption('all');
await page.getByRole('button',{name:'Unpin Persistent mode',exact:true}).click();
await page.locator('header').scrollIntoViewIfNeeded();
await page.screenshot({path:screenshotPath('gallery.png')});
for(const width of [320,736,1024]) {
  await page.setViewportSize({width,height:900});
  const sizing = await page.locator('html').evaluate(el => ({width:el.clientWidth,scroll:el.scrollWidth}));
  result.layout_checks++;
  if(sizing.scroll>sizing.width+1) failures.push(`gallery/${width}: ${sizing.scroll}/${sizing.width}`);
}
await browser.close();
await fs.writeFile(new URL('verification.json',root),JSON.stringify(result,null,2)+'\n');
assert.equal(errors.length,0,`Browser errors: ${errors.join('; ')}`);
assert.equal(apiRequests.length,0,`Unexpected live API requests: ${apiRequests.join('; ')}`);
assert.equal(failures.length,0,failures.join('; '));
console.log(JSON.stringify(result,null,2));
