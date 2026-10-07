const { test, expect } = require('@playwright/test');
const { labFixture, installLabFixture } = require('./lab-scenarios');
const { sweepPlan } = require('./research-lab-fixture');
const KEY = 'llmClusterBenchmark.theme';
const toggle = page => page.locator('#themeToggle');
const theme = (page, value) => expect(page.locator('html')).toHaveAttribute('data-theme', value);

test('System default, manual persistence and cross-tab changes follow one preference', async ({ page, context }) => {
  const fixture = labFixture(); await installLabFixture(page, fixture);
  await page.emulateMedia({ colorScheme: 'dark' }); await page.goto('/');
  await theme(page, 'dark'); await expect(toggle(page)).toHaveAccessibleName('다크 모드');
  await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true');
  await page.emulateMedia({ colorScheme: 'light' }); await theme(page, 'light');
  await toggle(page).click(); await theme(page, 'dark');
  await page.reload(); await theme(page, 'dark');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.emulateMedia({ colorScheme: 'light' }); await theme(page, 'dark');
  const other = await context.newPage(); const otherFixture = labFixture();
  await installLabFixture(other, otherFixture); await other.emulateMedia({ colorScheme: 'light' });
  await other.goto('/'); await theme(other, 'dark');
  await toggle(other).click(); await theme(page, 'light');
  await other.evaluate(key => localStorage.removeItem(key), KEY);
  await page.emulateMedia({ colorScheme: 'dark' }); await theme(page, 'dark');
  expect(fixture.unexpected).toEqual([]); expect(otherFixture.unexpected).toEqual([]);
});

for (const storage of ['invalid', 'blocked']) {
  test(`Theme remains usable with ${storage} storage`, async ({ page }) => {
    await installLabFixture(page, labFixture());
    await page.addInitScript(({ key, storage }) => {
      if (storage === 'invalid') localStorage.setItem(key, 'unsupported-theme');
      else Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('Blocked', 'SecurityError'); } });
    }, { key: KEY, storage });
    await page.emulateMedia({ colorScheme: 'light' }); await page.goto('/');
    await theme(page, 'light'); await toggle(page).click(); await theme(page, 'dark');
  });
}

test('Theme preserves forms, baseline, chart visibility and sweep hash approval', async ({ page }) => {
  const fixture = labFixture(), original = fixture.sweepApi;
  fixture.sweepApi = async args => {
    if (args.path !== '/api/sweeps/preview') return original(args);
    fixture.requests.push({ method: args.request.method(), path: args.path });
    args.json({ plan: sweepPlan(args.request.postDataJSON()), candidates: [], evidence_mode: 'cached' }); return true;
  };
  await installLabFixture(page, fixture); await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/#experiment'); await expect(page.locator('#workerCount')).toHaveText('2');
  await page.locator('#experimentName').fill('theme-preserved-draft');
  await page.locator('.nav-link[href="#compare"]').click();
  await page.locator('#compareBaseline').selectOption('pi-baseline');
  await page.locator('#compareAxis').selectOption('strategy,node_count');
  const legend = page.locator('#researchCompareChartLegend button').first();
  await legend.click(); await expect(legend).toHaveAttribute('aria-pressed', 'false');
  const pixel = () => page.locator('#researchCompareChart').evaluate(canvas => [...canvas.getContext('2d').getImageData(1, 1, 1, 1).data]);
  expect(await pixel()).toEqual([255,255,255,255]);
  await toggle(page).click(); await theme(page, 'dark');
  expect(await pixel()).toEqual([24,34,49,255]);
  await expect(legend).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#compareBaseline')).toHaveValue('pi-baseline');
  await expect(page.locator('#compareAxis')).toHaveValue('strategy,node_count');
  await page.locator('#compareMetric').selectOption('generated_tokens_per_j');
  await expect(page.locator('#researchCompareChart')).toHaveAttribute('data-empty-message', '필터에 맞는 완료 측정값이 없습니다');
  await toggle(page).click();
  expect(await pixel()).toEqual([255,255,255,255]);
  await expect(page.locator('#researchCompareChart')).toHaveAttribute('data-empty-message', '필터에 맞는 완료 측정값이 없습니다');
  await page.locator('.nav-link[href="#sweeps"]').click();
  await page.locator('[data-sweep-model]').first().check();
  await page.locator('#sweepPreviewButton').click();
  await expect(page.locator('#sweepPreviewContent')).toBeVisible();
  await page.locator('#sweepApprovalCheck').check();
  await expect(page.locator('#sweepSaveButton')).toBeEnabled();
  const selection = await page.locator('[data-sweep-worker]:checked').count();
  await toggle(page).click();
  await expect(page.locator('#sweepApprovalCheck')).toBeChecked();
  await expect(page.locator('#sweepSaveButton')).toBeEnabled();
  expect(await page.locator('[data-sweep-worker]:checked').count()).toBe(selection);
  await expect(page.locator('[data-sweep-model]').first()).toBeChecked();
  await expect(page.locator('#sweepPreviewContent')).toBeVisible();
  await page.locator('#sweepIdInput').fill('changed-draft');
  await expect(page.locator('#sweepApprovalCheck')).not.toBeChecked();
  await expect(page.locator('#sweepSaveButton')).toBeDisabled();
  await page.locator('.nav-link[href="#experiment"]').click();
  await expect(page.locator('#experimentName')).toHaveValue('theme-preserved-draft');
  expect(fixture.requests.filter(r => r.method !== 'GET').map(r => r.path)).toEqual(['/api/sweeps/preview']);
  expect(fixture.unexpected).toEqual([]);
});

for (const width of [1440, 390]) {
  test(`Both themes fit all nine routes and maintain contrast at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = labFixture(); await installLabFixture(page, fixture);
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto('/'); await expect(page.locator('#overviewGate')).toHaveText('FORMAL BLOCKED');
    for (const mode of ['light', 'dark']) {
      await page.evaluate(mode => ClusterDashboard.theme.set(mode), mode);
      await page.evaluate(() => ClusterDashboard.toast('테마 검증', '읽을 수 있는 알림 문구'));
      for (const route of ['overview','nodes','models','experiment','sweeps','results','campaign','compare','research']) {
        if (width === 390) await page.locator('#pageSelect').selectOption(route);
        else await page.locator(`.nav-link[href="#${route}"]`).click();
        await expect(page.locator(`#${route}`)).toBeVisible();
        // Sample settled colors, after existing hover/navigation CSS transitions.
        await page.evaluate(async () => {
          void document.body.offsetWidth;
          await Promise.all(document.getAnimations().filter(a => a.constructor.name === 'CSSTransition').map(a => a.finished.catch(() => {})));
        });
        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), `${mode}/${route}`).toBeLessThanOrEqual(2);
        const ratios = await page.evaluate(() => {
          const rgb = color => color.match(/[\d.]+/g).map(Number);
          const luminance = c => c.slice(0,3).map(v => v/255).map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4).reduce((sum,v,i) => sum+v*[.2126,.7152,.0722][i],0);
          const background = el => {
            if (!el) return [255,255,255];
            const c = rgb(getComputedStyle(el).backgroundColor), a = c[3] ?? 1;
            return a === 1 ? c : c.slice(0,3).map((v,i) => v*a+background(el.parentElement)[i]*(1-a));
          };
          const text = ['#pageTitle','#themeToggle','.nav-link.active','.section-heading h2','#overviewGate','#comparisonNotice','#researchEligibility','.strategy-caution','.primary-result span','.primary-result small','.primary-result .result-strategy','.participant-card dt','#sweepWorkerHint','.toast span','.model-library-viewmodes button.active'].flatMap(selector => {
            const el = document.querySelector(selector); if (!el?.getClientRects().length) return [];
            const fg = luminance(rgb(getComputedStyle(el).color)), bg = luminance(background(el));
            return [{ selector, target: 4.5, ratio: (Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05) }];
          });
          const controls = ['#themeToggle','#compareBaseline','#compareMetric','#sweepIdInput'].flatMap(selector => {
            const el = document.querySelector(selector); if (!el?.getClientRects().length) return [];
            const border = luminance(rgb(getComputedStyle(el).borderTopColor)), bg = luminance(background(el));
            return [{ selector, target: 3, ratio: (Math.max(border,bg)+.05)/(Math.min(border,bg)+.05) }];
          });
          return [...text, ...controls];
        });
        for (const sample of ratios) expect(sample.ratio, `${mode}/${route}/${sample.selector}`).toBeGreaterThanOrEqual(sample.target);
      }
    }
    expect(errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
    expect(fixture.requests.filter(r => r.method !== 'GET')).toEqual([]);
  });
}

test('Dark dialogs retain keyboard focus and publication export stays white', async ({ page }) => {
  const fixture = labFixture(); await installLabFixture(page, fixture);
  await page.emulateMedia({ colorScheme: 'dark' }); await page.goto('/#nodes');
  const detail = page.locator('[data-node-card="jetson-worker-01"] .node-detail-button');
  await detail.focus(); await detail.press('Enter');
  await expect(page.locator('#nodeDetailDialog')).toBeVisible();
  await page.evaluate(() => ClusterDashboard.theme.set('light'));
  await page.evaluate(() => ClusterDashboard.theme.set('dark'));
  await page.keyboard.press('Escape'); await expect(detail).toBeFocused();
  await page.locator('.nav-link[href="#compare"]').click();
  await page.locator('#compareBaseline').selectOption('pi-baseline');
  await page.locator('[data-paper-export="researchCompareChart"]').click();
  await page.locator('#publicationFormatSelect').selectOption('svg');
  const pending = page.waitForEvent('download');
  await page.locator('#publicationForm button[type="submit"]').click();
  const download = await pending;
  const svg = require('node:fs').readFileSync(await download.path(), 'utf8');
  expect(svg).toContain('<rect width="100%" height="100%" fill="#ffffff"/>');
  expect(svg).not.toContain('#8AB4F8');
  expect(fixture.unexpected).toEqual([]);
});
