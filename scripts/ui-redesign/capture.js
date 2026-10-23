// Static loopback server: never imports/starts Controller jobs or Worker services.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('@playwright/test');
const { labFixture, installLabFixture, FIXTURE_ID } = require('../../cluster/tests/e2e/lab-scenarios');
const root = path.resolve(__dirname, '../..');
const stage = process.argv[2] || 'after';
if (!['before', 'after'].includes(stage)) throw new Error('stage must be before or after');
if (stage === 'after') {
  // A manifest SHA must name the exact source rendered, including new modules.
  execFileSync('git', ['diff', '--quiet', 'HEAD', '--', 'cluster/dashboard'], { cwd: root });
  const untracked = execFileSync('git', ['ls-files', '--others', '--exclude-standard', 'cluster/dashboard'], { cwd: root, encoding: 'utf8' }).trim();
  if (untracked) throw new Error('Commit dashboard source before capturing after evidence');
}
const out = path.join(root, 'docs/ui-redesign/captures');
const sha = execFileSync('git', ['rev-parse', stage === 'before' ? '9245e24' : 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  const file = pathname === '/' ? path.join(root, 'cluster/dashboard/templates/index.html') : path.resolve(root, 'cluster/dashboard', '.' + pathname);
  if (!file.startsWith(path.join(root, 'cluster/dashboard/')) || pathname.startsWith('/api/')) { res.writeHead(403); return res.end(); }
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); return res.end(); }
  res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'application/javascript' : 'text/html');
  res.end(stage === 'before' ? execFileSync('git', ['show', `9245e24:${path.relative(root, file)}`], { cwd: root }) : fs.readFileSync(file));
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const manifest = path.join(out, 'CAPTURE_MANIFEST.csv');
  fs.mkdirSync(out, { recursive: true });
  if (!fs.existsSync(manifest)) fs.writeFileSync(manifest, 'filename,repo,commit_sha,captured_at,viewport,route,scenario,fixture_id,data_provenance,live_actions,privacy_review\n');
  if (fs.existsSync(manifest)) fs.writeFileSync(manifest, fs.readFileSync(manifest, 'utf8').split('\n').filter(line => !line.startsWith(stage + '-')).join('\n'));
  const errors = []; const details = [];
  try {
    for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height }, locale: 'ko-KR', timezoneId: 'Asia/Seoul', reducedMotion: 'reduce' });
      const fixture = labFixture();
      await installLabFixture(page, fixture);
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
      for (const [route, scenario] of [['overview', 'matrix'], ['compare', 'compare'], ['nodes', 'workers'], ['sweeps', 'sweep-blockers'], ['research', 'formal-blocked']]) {
        await page.goto(`${url}/#${route}`);
        await page.waitForFunction(() => window.ClusterDashboard?.research?.state.readiness);
        await page.evaluate(() => document.fonts.ready);
        if (route === 'compare') { await page.locator('#compareBaseline').selectOption('pi-baseline'); }
        if (route === 'sweeps') { await page.locator('[data-sweep-model]').first().check(); await page.locator('#sweepPreviewButton').click(); await page.locator('#sweepPreviewContent').waitFor(); }
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(100);
        details.push(await page.evaluate(() => ({ route: location.hash, context: document.querySelector('#labContext')?.innerHTML, scroll: scrollY, toasts: document.querySelector('#toastStack').textContent })));
        await page.screenshot({ path: path.join(out, `${stage}-${scenario}-${width}.png`), animations: 'disabled' });
        fs.appendFileSync(manifest, `${stage}-${scenario}-${width}.png,Phjrab/llm-cluster-benchmark,${sha},${new Date().toISOString()},${width}x${height},#${route},${scenario},${FIXTURE_ID},synthetic,none,synthetic-only\n`);
        if (width === 1440 && ['overview', 'compare'].includes(route)) {
          const filename = `${stage}-${scenario}-full-${width}.png`;
          await page.screenshot({ path: path.join(out, filename), animations: 'disabled', fullPage: true });
          fs.appendFileSync(manifest, `${filename},Phjrab/llm-cluster-benchmark,${sha},${new Date().toISOString()},${width}x${height}-fullpage,#${route},${scenario}-full,${FIXTURE_ID},synthetic,none,synthetic-only\n`);
        }
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
        if (overflow > 2) errors.push(`${stage} ${route} ${width}: overflow ${overflow}`);
      }
      if (width === 1440) {
        await page.goto(`${url}/#compare`);
        await page.locator('#compareBaseline').selectOption('pi-baseline');
        for (const format of ['svg', 'png']) {
          await page.locator('[data-paper-export="researchCompareChart"]').click();
          await page.locator('#publicationTitleInput').fill('UI DEMO / SYNTHETIC FIXTURE');
          await page.locator('#publicationFormatSelect').selectOption(format);
          const pending = page.waitForEvent('download');
          await page.locator('#publicationForm button[type="submit"]').click();
          const download = await pending;
          await download.saveAs(path.join(out, `${stage}-physical-throughput-paper.${format}`));
          fs.appendFileSync(manifest, `${stage}-physical-throughput-paper.${format},Phjrab/llm-cluster-benchmark,${sha},${new Date().toISOString()},85mm-300dpi,#compare,publication-export,${FIXTURE_ID},synthetic,none,synthetic-only\n`);
        }
        await page.evaluate(() => document.querySelector('#toastStack').innerHTML = '');
      }
      await page.goto(`${url}/#nodes`);
      await page.locator('[data-node-card="jetson-worker-01"] .node-detail-button').click();
      await page.screenshot({ path: path.join(out, `${stage}-worker-detail-${width}.png`), animations: 'disabled' });
      fs.appendFileSync(manifest, `${stage}-worker-detail-${width}.png,Phjrab/llm-cluster-benchmark,${sha},${new Date().toISOString()},${width}x${height},#nodes,worker-detail,${FIXTURE_ID},synthetic,none,synthetic-only\n`);
      if (fixture.unexpected.length) errors.push(...fixture.unexpected);
      details.push({ viewport: `${width}x${height}`, network: fixture.networkRequests.map(item => ({ method: item.method, path: new URL(item.url).pathname })) });
      await page.close();
      const emptyPage = await browser.newPage({ viewport: { width, height }, locale: 'ko-KR' });
      await installLabFixture(emptyPage, labFixture({ empty: true }));
      await emptyPage.goto(url);
      await emptyPage.waitForFunction(() => window.ClusterDashboard?.research?.state.readiness);
      await emptyPage.evaluate(() => window.scrollTo(0, 0));
      await emptyPage.screenshot({ path: path.join(out, `${stage}-empty-${width}.png`), animations: 'disabled' });
      fs.appendFileSync(manifest, `${stage}-empty-${width}.png,Phjrab/llm-cluster-benchmark,${sha},${new Date().toISOString()},${width}x${height},#overview,empty,${FIXTURE_ID}-empty,synthetic,none,synthetic-only\n`);
      await emptyPage.close();
    }
    fs.writeFileSync(path.join(out, `${stage}-browser-observations.json`), JSON.stringify({ errors, details }, null, 2));
    console.log(`${stage}: captured 30 real browser PNGs; observations: ${JSON.stringify(errors)}`);
    if (errors.length) process.exitCode = 1;
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
