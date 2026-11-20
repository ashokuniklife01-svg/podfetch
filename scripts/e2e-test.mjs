import { chromium } from 'playwright';
const T = '/workspace/mt-test/', OUT = '/workspace/mt-test/out/';
import fs from 'fs'; fs.mkdirSync(OUT, { recursive: true });
const only = process.argv[2] || 'all';
const browser = await chromium.launch();
const ctx = await browser.newContext({ acceptDownloads: true });
const page = await ctx.newPage();
const errors = [];
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`[${m.type()}] ${m.text()} @ ${m.location()?.url}`); });
page.on('pageerror', (e) => errors.push('[pageerror] ' + e.message));
page.on('requestfailed', (r) => errors.push('[reqfail] ' + r.url() + ' ' + r.failure()?.errorText));
page.on('response', (r) => { if (r.status() >= 400) errors.push('[http ' + r.status() + '] ' + r.url()); });
await page.goto(process.env.URL || 'http://127.0.0.1:8099/'); console.log('isolated:', await page.evaluate(()=>crossOriginIsolated));
async function dl(sel, name) { const [d] = await Promise.all([page.waitForEvent('download'), page.click(sel)]); await d.saveAs(OUT + name); return OUT + name; }
const t = async (name, fn) => { const s = Date.now(); try { await fn(); console.log('PASS', name, ((Date.now() - s) / 1000).toFixed(1) + 's'); } catch (e) { console.log('FAIL', name, e.message.split('\n')[0]); } };
if (only === 'all' || only === 'mp3') {
  await t('mp3 mp4', async () => {
    await page.click('.tab[data-tab=mp3]'); await page.setInputFiles('#mp3-file', T + 'test.mp4'); await page.click('#mp3-go');
    await page.waitForSelector('#mp3-dl', { timeout: 180000 }); await dl('#mp3-dl', 'test.mp3');
  });
  await t('mp3 webm', async () => {
    await page.setInputFiles('#mp3-file', T + 'test.webm'); await page.click('#mp3-go');
    await page.waitForSelector('#mp3-dl', { timeout: 180000 }); await dl('#mp3-dl', 'webm.mp3');
  });
  await t('mp3 noaudio -> friendly error', async () => {
    await page.setInputFiles('#mp3-file', T + 'noaudio.mp4'); await page.click('#mp3-go');
    await page.waitForSelector('#mp3-msg.error', { timeout: 120000 }); console.log('   msg:', await page.textContent('#mp3-msg'));
  });
}
if (only === 'all' || only === 'cmp') {
  for (const [q, res] of [['medium', '720'], ['small', '480'], ['high', '0']]) {
    await t('compress ' + q + ' ' + res, async () => {
      await page.click('.tab[data-tab=compress]'); await page.setInputFiles('#cmp-file', T + 'test.mp4');
      await page.check(`input[name=cmp-q][value=${q}]`, { force: true }); await page.selectOption('#cmp-res', res); await page.click('#cmp-go');
      await page.waitForSelector('#cmp-dl', { timeout: 600000 }); console.log('   stats:', (await page.textContent('#cmp-result .stats')).replace(/\s+/g, ' '));
      await dl('#cmp-dl', `cmp-${q}.mp4`);
    });
  }
}
if (only === 'all' || only === 'cmp2' || only === 'big' || only === 'noise') {
  const q = async (file, preset, res, out) => t(`quality ${preset} ${res} ${file}`, async () => {
    await page.click('.tab[data-tab=compress]'); await page.click('.mode[data-mode=quality]');
    await page.setInputFiles('#cmp-file', T + file); await page.check(`input[name=cmp-q][value=${preset}]`, { force: true }); await page.selectOption('#cmp-res', res);
    await page.click('#cmp-go'); await page.waitForSelector('#cmp-dl', { timeout: 900000 });
    console.log('   stats:', (await page.textContent('#cmp-result .stats')).replace(/\s+/g, ' ')); await dl('#cmp-dl', out);
  });
  const tg = async (file, mb, out, to = 900000) => t(`target ${mb}MB ${file}`, async () => {
    await page.click('.tab[data-tab=compress]'); await page.click('.mode[data-mode=target]');
    await page.setInputFiles('#cmp-file', T + file); await page.fill('#cmp-target', String(mb));
    await page.waitForFunction(() => /→/.test(document.querySelector('#cmp-plan').textContent), null, { timeout: 30000 });
    console.log('   plan:', await page.textContent('#cmp-plan'));
    await page.click('#cmp-go'); await page.waitForSelector('#cmp-dl', { timeout: to });
    console.log('   stats:', (await page.textContent('#cmp-result .stats')).replace(/\s+/g, ' '));
    const hint = await page.$('#cmp-result .hint'); if (hint) console.log('   note:', await hint.textContent());
    await dl('#cmp-dl', out);
  });
  if (only === 'all' || only === 'cmp2') {
    await q('test.mp4', 'lowest', '0', 'q-lowest.mp4');
    await q('test.mp4', 'medium', '240', 'q-240.mp4');
    await q('test.mp4', 'small', '144', 'q-144.mp4');
    await q('vertical.mp4', 'medium', '480', 'q-vert480.mp4');
    await tg('test.mp4', 2, 't-2mb.mp4');
    await tg('vertical.mp4', 1, 't-vert1mb.mp4');
    await t('target too small warning', async () => { await page.setInputFiles('#cmp-file', T + 'test.mp4'); await page.fill('#cmp-target', '0.03'); await page.waitForTimeout(500); console.log('   plan:', await page.textContent('#cmp-plan')); });
  }
  if (only === 'big') await tg('big200.mp4', 12, 't-big12.mp4', 1800000);
  if (only === 'noise') await tg('noise.mp4', 1, 't-noise1.mp4', 900000);
}
if (only === 'all' || only === 'pdf') {
  await t('pdf 4 images + reorder + bad file', async () => {
    await page.click('.tab[data-tab=pdf]');
    await page.setInputFiles('#pdf-file', [T + 'land.jpg', T + 'port.png', T + 'sq.webp', T + 'person.jpg']);
    await page.click('.thumb:nth-child(2) [data-a=left]'); await page.click('.thumb:nth-child(1) [data-a=rot]');
    console.log('   order:', await page.$$eval('.thumb .name', (e) => e.map((x) => x.textContent).join(',')));
    await page.click('#pdf-go'); await page.waitForSelector('#pdf-dl', { timeout: 60000 }); await dl('#pdf-dl', 'out.pdf');
    await page.selectOption('#pdf-size', 'fit'); await page.click('#pdf-go'); await page.waitForTimeout(500); await page.waitForSelector('#pdf-dl'); await dl('#pdf-dl', 'fit.pdf');
    await page.selectOption('#pdf-size', 'letter'); await page.selectOption('#pdf-orient', 'landscape'); await page.click('#pdf-go'); await page.waitForTimeout(500); await page.waitForSelector('#pdf-dl'); await dl('#pdf-dl', 'land.pdf');
  });
}
if (only === 'all' || only === 'bg') {
  await t('bg remove', async () => {
    await page.click('.tab[data-tab=bg]'); await page.setInputFiles('#bg-file', T + 'person.jpg'); await page.click('#bg-go');
    await page.waitForSelector('#bg-dl', { timeout: 300000 }); await dl('#bg-dl', 'nobg.png');
    await page.selectOption('#bg-mode', 'white'); await page.waitForTimeout(1500); await dl('#bg-dl', 'whitebg.png');
  });
}
if (only === 'all' || only === 'bad') {
  await t('bg bad file', async () => { await page.click('.tab[data-tab=bg]'); await page.setInputFiles('#bg-file', T + 'bad.jpg'); await page.click('#bg-go'); await page.waitForSelector('#bg-msg.error', { timeout: 60000 }); console.log('   msg:', await page.textContent('#bg-msg')); });
  await t('pdf bad only', async () => { await page.click('.tab[data-tab=pdf]'); await page.click('#pdf-clear').catch(()=>{}); await page.setInputFiles('#pdf-file', T + 'bad.jpg'); await page.click('#pdf-go'); await page.waitForSelector('#pdf-msg.error', { timeout: 60000 }); console.log('   msg:', await page.textContent('#pdf-msg')); });
  await t('mp3 non-video', async () => { await page.click('.tab[data-tab=mp3]'); await page.setInputFiles('#mp3-file', T + 'land.jpg'); await page.waitForSelector('#mp3-msg.error'); console.log('   msg:', await page.textContent('#mp3-msg')); });
}
await page.screenshot({ path: OUT + 'shot.png', fullPage: true });
console.log('CONSOLE/NET ISSUES:', errors.length ? '\n' + errors.join('\n') : 'none');
await browser.close();
