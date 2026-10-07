const { test, expect } = require('@playwright/test');
const { labFixture, installLabFixture } = require('./lab-scenarios');

test('Saved plan Matrix inspects repeat/status/quality without starting work', async ({ page }) => {
  const fixture = labFixture(); await installLabFixture(page, fixture);
  await page.goto('/#overview');
  await expect(page.locator('#overviewMatrix')).toContainText('Repeat 2');
  await expect(page.locator('#overviewMatrix')).toContainText('missing 1');
  await expect(page.locator('#overviewGate')).toHaveText('FORMAL BLOCKED');
  await expect(page.locator('#averagePower')).toHaveText('N/A');
  await expect(page.locator('#powerCoverage')).toHaveText('1/2 observed · W');
  const cell = page.locator('#overviewMatrix .matrix-cell').first();
  await cell.focus(); await cell.press('Enter');
  await expect(page.locator('#matrixInspectDialog')).toBeVisible();
  await expect(page.locator('#matrixInspectContent')).toContainText('pi-baseline');
  await expect(page.locator('#matrixInspectContent')).toContainText('Quality');
  await page.keyboard.press('Escape'); await expect(cell).toBeFocused();
  await page.locator('.nav-link[href="#campaign"]').click();
  await expect(page.locator('#campaignMatrix')).toContainText('Repeat 2');
  await expect(page.locator('[data-campaign-action="resume"]')).toBeDisabled();
  expect(fixture.unexpected).toEqual([]);
  expect(fixture.requests.filter(request => request.method !== 'GET')).toEqual([]);
});

test('Explicit baseline and controlled axes synchronize chart, table, CSV and filter removal', async ({ page }) => {
  const fixture = labFixture(); await installLabFixture(page, fixture); await page.goto('/#compare');
  await expect(page.locator('#compareRunsTable')).toContainText('pi-failed');
  await expect(page.locator('#compareBaseline')).toHaveValue('');
  await page.locator('#compareBaseline').selectOption('pi-baseline');
  const row = page.locator('#compareRunsTable tr').filter({ hasText: 'pi-round-robin' });
  await expect(row).toContainText('N/A');
  await page.locator('#compareAxis').selectOption('strategy,node_count');
  await expect(row).toContainText('1.89×');
  await expect(page.locator('#compareRunsTable tr').filter({ hasText: 'pi-broadcast' })).toContainText('N/A');
  await expect(page.locator('#compareRunsTable tr').filter({ hasText: 'jetson-other' })).toContainText('N/A');
  await page.locator('#compareMetric').selectOption('success_rate');
  await expect(row).toContainText('-5.0 pp');
  await page.locator('#compareMetric').selectOption('throughput_tokens_s');
  const downloadPromise = page.waitForEvent('download'); await page.locator('#researchCompareCsv').click();
  expect((await downloadPromise).suggestedFilename()).toContain('throughput_tokens_s.csv');
  await page.locator('[data-compare-filter="strategy"]').selectOption('replicated_round_robin');
  await expect(page.locator('#compareBaseline')).toHaveValue('pi-baseline');
  await expect(page.locator('#comparisonNotice')).toContainText('집합 밖');
  await expect(row).toContainText('N/A');
  await page.locator('#resetCompareFilters').click();
  await expect(page.locator('#compareBaseline')).toHaveValue('pi-baseline');
  expect(fixture.unexpected).toEqual([]);
});

test('Empty campaigns and failed research requests remain explicit', async ({ page }) => {
  const fixture = labFixture({ empty: true }); await installLabFixture(page, fixture); await page.goto('/');
  await expect(page.locator('#overviewMatrix')).toContainText('저장된 campaign이 없습니다');
  await expect(page.locator('#overviewMatrix .matrix-cell')).toHaveCount(0);
  await page.route('**/api/campaigns', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Synthetic service unavailable"}' }));
  await page.evaluate(() => ClusterDashboard.research.load());
  await expect(page.locator('#overviewMatrix')).toContainText('조회 실패');
  await expect(page.locator('#overviewGate')).toHaveText('FORMAL BLOCKED');
  expect(fixture.unexpected).toEqual([]);
});

test('Offline Worker, long escaped identity, 200% equivalent reflow and protected trash stay usable', async ({ page }) => {
  const fixture = labFixture(); fixture.status[1].api = false;
  fixture.model.catalog.display_name = 'Long model identity '.repeat(12) + '<img src=x onerror=alert(1)>';
  await installLabFixture(page, fixture); await page.goto('/#nodes');
  await expect(page.locator('[data-node-card="pi-worker-02"]')).toContainText('OFFLINE');
  await page.locator('.nav-link[href="#models"]').click();
  await expect(page.locator('#modelLibrary')).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.locator('#modelLibrary img')).toHaveCount(0);
  await page.setViewportSize({ width: 720, height: 450 });
  await page.locator('#pageSelect').selectOption('overview');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(2);
  await page.route('**/api/results/trash', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ trash: [{ trash_id: 'protected', run_id: 'formal-run', protected: true, status: 'completed', campaign_id: 'formal-campaign', archive_sha256: 'a'.repeat(64) }] }) }));
  await page.locator('#pageSelect').selectOption('results');
  await page.locator('#openResultTrashButton').click();
  await expect(page.locator('[data-purge-trash="protected"]')).toBeDisabled();
  await expect(page.locator('#resultTrashDialog')).toContainText('RETENTION PROTECTED');
  expect(fixture.unexpected).toEqual([]);
});

test('Pending experiment submission sends one mocked request and formal controls send none', async ({ page }) => {
  const fixture = labFixture(); await installLabFixture(page, fixture);
  let submissions = 0;
  await page.route('**/api/experiments', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ contentType: 'application/json', body: '{"runs":[],"suites":[],"experiment_groups":[]}' });
    submissions += 1;
    await new Promise(resolve => setTimeout(resolve, 150));
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ experiment: { id: 'synthetic-job', name: 'pending-demo', status: 'running', nodes: fixture.nodes.map(node => node.name) }, definition: { experiment_id: 'synthetic-definition' }, warnings: [] }) });
  });
  await page.goto('/#experiment'); await expect(page.locator('#workerCount')).toHaveText('2');
  await page.evaluate(() => { const form = document.querySelector('#experimentForm'); form.requestSubmit(); form.requestSubmit(); });
  await expect.poll(() => submissions).toBe(1);
  await expect(page.locator('#runPhase')).toHaveText('running');
  await page.evaluate(() => ClusterDashboard.research.controlCampaign('resume'));
  expect(fixture.requests.filter(request => request.method !== 'GET')).toEqual([]);
  expect(fixture.unexpected).toEqual([]);
});

test('Late campaign read cannot replace the latest selection', async ({ page }) => {
  const fixture = labFixture(); await installLabFixture(page, fixture);
  const slow = { ...fixture.campaign, campaign_id: 'slow-plan' };
  const fast = { ...fixture.campaign, campaign_id: 'fast-plan' };
  await page.route('**/api/campaigns', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ campaigns: [fixture.campaign, slow, fast] }) }));
  await page.route('**/api/campaigns/slow-plan', async route => { await new Promise(resolve => setTimeout(resolve, 250)); await route.fulfill({ contentType: 'application/json', body: JSON.stringify(slow) }); });
  await page.route('**/api/campaigns/fast-plan', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(fast) }));
  await page.goto('/'); await expect(page.locator('#overviewMatrix')).toContainText('lab-demo-plan');
  await page.locator('#overviewCampaignSelect').selectOption('slow-plan');
  await page.locator('#overviewCampaignSelect').selectOption('fast-plan');
  await expect(page.locator('#overviewMatrix .matrix-caption')).toContainText('fast-plan');
  await page.waitForTimeout(350);
  await expect(page.locator('#overviewMatrix .matrix-caption')).toContainText('fast-plan');
  expect(fixture.unexpected).toEqual([]);
});

for (const [width, height] of [[1440, 900], [1366, 768], [1024, 768], [390, 844]]) {
  test(`Research workspace fits ${width}x${height}, with readable labels and native focus`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const fixture = labFixture(); await installLabFixture(page, fixture);
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    for (const route of ['overview', 'compare', 'nodes', 'sweeps', 'research']) {
      await page.goto(`/#${route}`); await expect(page.locator('#overviewGate')).toHaveText('FORMAL BLOCKED');
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), route).toBeLessThanOrEqual(2);
      const ratios = await page.locator('body').evaluate(() => {
        const luminance = rgb => rgb.match(/[\d.]+/g).slice(0,3).map(Number).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126,.7152,.0722][i], 0);
        const selectors = ['#pageTitle', '.nav-link.active', '.section-heading h2', '#overviewGate', '#comparisonNotice', '#researchEligibility'];
        const textSamples = selectors.flatMap(selector => {
          const el = document.querySelector(selector); if (!el || !el.getClientRects().length) return [];
          let parent = el; while (parent && getComputedStyle(parent).backgroundColor === 'rgba(0, 0, 0, 0)') parent = parent.parentElement;
          const fg = luminance(getComputedStyle(el).color), bg = luminance(getComputedStyle(parent).backgroundColor);
          return [{ selector, ratio: (Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05), target: 4.5 }];
        });
        const boundarySamples = ['#compareBaseline', '#compareMetric', '#sweepIdInput', '#refreshButton'].flatMap(selector => {
          const el = document.querySelector(selector); if (!el || !el.getClientRects().length) return [];
          let parent = el; while (parent && getComputedStyle(parent).backgroundColor === 'rgba(0, 0, 0, 0)') parent = parent.parentElement;
          const a = luminance(getComputedStyle(el).borderTopColor), b = luminance(getComputedStyle(parent).backgroundColor);
          return [{ selector, target: 3, ratio: (Math.max(a,b)+.05)/(Math.min(a,b)+.05) }];
        });
        return [...textSamples, ...boundarySamples];
      });
      for (const sample of ratios) expect(sample.ratio, sample.selector).toBeGreaterThanOrEqual(sample.target);
    }
    expect(errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
  });
}


test('Worker dialog close glyph and model selection have visible contrast', async ({ page }) => {
  const fixture = labFixture(); await installLabFixture(page, fixture);
  const contrast = async locator => locator.evaluate(el => {
    const luminance = color => color.match(/[\d.]+/g).slice(0,3).map(Number).map(v => v/255).map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4).reduce((sum,v,i) => sum + v*[.2126,.7152,.0722][i],0);
    const style = getComputedStyle(el); const fg = luminance(style.color), bg = luminance(style.backgroundColor);
    return (Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05);
  });
  await page.goto('/#nodes');
  const detail = page.locator('[data-node-card="jetson-worker-01"] .node-detail-button');
  await detail.focus(); await detail.press('Enter');
  const close = page.locator('#nodeDetailDialog .close-button');
  await expect(close).toHaveAccessibleName('닫기');
  expect(await contrast(close)).toBeGreaterThanOrEqual(4.5);
  await close.hover(); expect(await contrast(close)).toBeGreaterThanOrEqual(4.5);
  await close.focus(); await close.press('Enter');
  await expect(page.locator('#nodeDetailDialog')).not.toBeVisible();
  await expect(detail).toBeFocused();
  await page.locator('.nav-link[href="#models"]').click();
  const active = page.locator('.model-library-filters button.active').first();
  await expect(active).toBeVisible(); expect(await contrast(active)).toBeGreaterThanOrEqual(4.5);
  expect(fixture.unexpected).toEqual([]);
});
