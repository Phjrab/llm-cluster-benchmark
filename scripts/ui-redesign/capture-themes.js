// Actual Chromium captures of synthetic data; no live Controller/Worker is used.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { chromium } = require('@playwright/test');
const { labFixture, installLabFixture, FIXTURE_ID } = require('../../cluster/tests/e2e/lab-scenarios');
const root = path.resolve(__dirname, '../..');
execFileSync('git', ['diff', '--quiet', 'HEAD', '--', 'cluster/dashboard'], { cwd: root });
assert.equal(execFileSync('git', ['ls-files', '--others', '--exclude-standard', 'cluster/dashboard'], { cwd: root, encoding: 'utf8' }).trim(), '');
const sha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
const out = path.join(root, 'docs/ui-redesign/captures/themes');
const dashboard = path.join(root, 'cluster/dashboard');
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  const file = pathname === '/' ? path.join(dashboard, 'templates/index.html') : path.resolve(dashboard, '.' + pathname);
  if (!file.startsWith(dashboard + '/') || pathname.startsWith('/api/') || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); return res.end(); }
  res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'application/javascript' : 'text/html');
  res.end(fs.readFileSync(file));
});
(async () => {
  fs.mkdirSync(out, { recursive: true });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  const rows = [], errors = [];
  const record = (filename, mode, width, route, scenario) => rows.push({ filename, repo: 'Phjrab/llm-cluster-benchmark', source_commit: sha, captured_at: new Date().toISOString(), theme: mode, viewport: width === 1440 ? '1440x900' : '390x844', route, scenario, fixture: FIXTURE_ID, data_provenance: 'synthetic', live_actions: 0 });
  try {
    for (const width of [1440, 390]) {
      for (const mode of ['light', 'dark']) {
        const page = await browser.newPage({ viewport: { width, height: width === 1440 ? 900 : 844 }, colorScheme: mode, reducedMotion: 'reduce', locale: 'ko-KR' });
        const fixture = labFixture(); await installLabFixture(page, fixture);
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
        for (const [route, scenario] of [['overview','matrix'], ['compare','compare'], ['sweeps','sweep-blockers'], ['nodes','worker-dialog']]) {
          await page.goto(`${url}/#${route}`);
          await page.waitForFunction(() => ClusterDashboard.research.state.readiness);
          if (route === 'compare') await page.locator('#compareBaseline').selectOption('pi-baseline');
          if (route === 'sweeps') {
            await page.locator('[data-sweep-model]').first().check();
            await page.locator('#sweepPreviewButton').click(); await page.locator('#sweepPreviewContent').waitFor();
          }
          if (route === 'nodes') await page.locator('[data-node-card="jetson-worker-01"] .node-detail-button').click();
          await page.evaluate(() => document.fonts.ready);
          await page.waitForTimeout(250);
          assert.equal(await page.locator('html').getAttribute('data-theme'), mode);
          const filename = `${mode}-${scenario}-${width}.png`;
          await page.screenshot({ path: path.join(out, filename), animations: 'disabled' });
          record(filename, mode, width, `#${route}`, scenario);
          if (route === 'compare' && width === 1440) {
            const full = `${mode}-compare-full-${width}.png`;
            await page.screenshot({ path: path.join(out, full), fullPage: true, animations: 'disabled' });
            record(full, mode, width, '#compare', 'compare-full');
          }
          if (route === 'compare' && width === 1440 && mode === 'dark') {
            for (const format of ['svg','png']) {
              await page.locator('[data-paper-export="researchCompareChart"]').click();
              await page.locator('#publicationTitleInput').fill('UI DEMO / SYNTHETIC FIXTURE');
              await page.locator('#publicationFormatSelect').selectOption(format);
              const pending = page.waitForEvent('download'); await page.locator('#publicationForm button[type="submit"]').click();
              const filename = `dark-publication-white.${format}`, download = await pending;
              await download.saveAs(path.join(out, filename));
              if (format === 'svg') assert.ok(fs.readFileSync(path.join(out, filename), 'utf8').includes('<rect width="100%" height="100%" fill="#ffffff"/>'));
              else {
                const data = fs.readFileSync(path.join(out, filename)).toString('base64');
                const pixel = await page.evaluate(async data => {
                  const img = new Image(); img.src = 'data:image/png;base64,' + data; await img.decode();
                  const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
                  const ctx = canvas.getContext('2d'); ctx.drawImage(img,0,0);
                  return [...ctx.getImageData(1,1,1,1).data];
                }, data);
                assert.deepEqual(pixel, [255,255,255,255]);
              }
              record(filename, mode, width, '#compare', 'publication-white-export-85mm-300dpi');
            }
          }
          assert.ok(await page.evaluate(() => document.documentElement.scrollWidth-innerWidth) <= 2);
        }
        assert.deepEqual(fixture.unexpected, []);
        assert.deepEqual(fixture.requests.filter(r => r.method !== 'GET').map(r => r.path), ['/api/sweeps/preview']);
        await page.close();
      }
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(out, 'MANIFEST.json'), JSON.stringify({ source_commit: sha, errors, live_actions: 0, captures: rows }, null, 2) + '\n');
    console.log(`Captured ${rows.length} artifacts; white publication SVG/PNG verified; no live actions or browser errors.`);
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
