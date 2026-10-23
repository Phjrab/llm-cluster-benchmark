const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { labFixture, installLabFixture, FIXTURE_ID } = require('./lab-scenarios');
const stage = process.env.TYPOGRAPHY_STAGE || 'after';
const out = process.env.TYPOGRAPHY_OUTPUT_DIR || path.resolve(__dirname, '../../..', 'docs/ui-redesign/captures/typography', stage);
const routes = ['overview','nodes','models','experiment','sweeps','results','campaign','compare','research'];
async function navigate(page, route, width) {
  if (width < 781) await page.locator('#pageSelect').selectOption(route);
  else await page.locator(`.nav-link[href="#${route}"]`).click();
  await expect(page.locator(`#${route}`)).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}
async function capture(page, name) {
  fs.mkdirSync(out, {recursive:true});
  await page.screenshot({path:path.join(out, name + '.png'), animations:'disabled'});
  if((name.startsWith('compare-') && !name.includes('dark')) || name.startsWith('models-')) await page.screenshot({path:path.join(out,name+'-full.png'),fullPage:true,animations:'disabled'});
}
async function fontsFor(cdp, selector) {
  const {root} = await cdp.send('DOM.getDocument');
  const {nodeId} = await cdp.send('DOM.querySelector', {nodeId:root.nodeId, selector});
  return (await cdp.send('CSS.getPlatformFontsForNode', {nodeId})).fonts;
}
for (const [width,height] of [[1920,1080],[1440,900],[1280,800],[390,844]]) {
  test(`Typography same-fixture captures and rendered fonts ${width}x${height}`, async ({page,context}) => {
    test.setTimeout(120000);
    await page.setViewportSize({width,height});
    await page.emulateMedia({colorScheme:'light',reducedMotion:'reduce'});
    const fixture=labFixture();
    // Identity stress is synthetic; metric values retain the shared fixture contract.
    fixture.compareRuns[3].run_id = 'run-20261007-research-identity-' + '0123456789abcdef'.repeat(5);
    fixture.compareRuns[3].model_id = '/models/한글 경로/' + 'long-model-directory/'.repeat(5) + 'model-Q4_K_M.gguf';
    await installLabFixture(page,fixture);
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.goto('/#overview');
    await expect(page.locator('#overviewGate')).toHaveText('FORMAL BLOCKED');
    await page.waitForFunction(()=>Boolean(ClusterDashboard.research.state.readiness));
    for (const route of routes) {
      await navigate(page,route,width);
      if(route==='compare') await page.locator('#compareBaseline').selectOption('pi-baseline');
      if(route==='sweeps') {await page.locator('[data-sweep-model]').first().check(); await page.locator('#sweepPreviewButton').click(); await expect(page.locator('#sweepPreviewContent')).toBeVisible();}
      await capture(page,`${route}-${width}`);
      if(route==='models') {
        await page.locator('[data-model-view="vendors"]').click();
        await expect(page.locator('[data-vendor-open]').first()).toBeAttached();
        await page.locator('[data-vendor-open]').first().evaluate(e=>e.scrollIntoView({block:'center',behavior:'instant'}));
        await capture(page,`vendors-${width}`);
        await page.locator('[data-model-view="models"]').click();
      }
      expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(2);
    }
    await navigate(page,'overview',width);
    await page.locator('#overviewMatrix .matrix-cell').first().click();
    await capture(page,`matrix-modal-${width}`);
    const dialog=page.locator('#matrixInspectDialog');
    expect(await dialog.evaluate(e=>e.getBoundingClientRect().width)).toBeLessThanOrEqual(width);
    await page.keyboard.press('Escape');
    await navigate(page,'nodes',width);
    await page.locator('[data-node-card="jetson-worker-01"] .node-detail-button').click();
    await capture(page,`worker-modal-${width}`);
    if(stage==='after') { const size=await page.locator('#nodeRenameButton').boundingBox();expect(size.width).toBeGreaterThan(65);expect(size.height).toBeLessThan(50); }
    await page.keyboard.press('Escape');
    await navigate(page,'compare',width);
    await page.locator('#themeToggle').click(); await capture(page,`compare-dark-${width}`);
    await page.locator('#themeToggle').click();
    if(width===1440) {
      const cdp=await context.newCDPSession(page); await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
      // Test-only specimen, clearly separated from actual benchmark results.
      await page.evaluate(()=> {
        const panel=document.createElement('section'); panel.id='typographySpecimen';
        panel.style='position:fixed;inset:100px 200px auto 200px;z-index:1000;background:var(--surface);color:var(--ink);padding:24px;border:1px solid var(--line)';
        panel.innerHTML='<h3>FONT SPECIMEN / SYNTHETIC / 서체 확인 전용·실측 아님</h3><p id="fontKorean">실험 조건 비교</p><p id="fontWarning">측정 품질 · 실행 불가</p><p id="fontLatin">Benchmark Research Lab</p><p id="fontAxis">Throughput / TTFT / E2E p95</p><p id="fontNumeric" class="numeric">18.30　118.30　0.420　99.5%　1.83×</p><p id="fontIdentity" class="identifier">0 O 1 I l · run-20261007-0123456789abcdef · 한글 로그</p><code id="fontPath">/models/한글 경로/very-long-model-path/model-Q4_K_M.gguf</code>';
        document.body.append(panel);
      });
      await page.evaluate(()=>document.fonts.ready);
      const fonts={}; for(const selector of ['#fontKorean','#fontLatin','#fontNumeric','#fontIdentity','#fontPath','#compareRunsTable tr:first-child td:first-child strong','#compareRunsTable tr:first-child td:nth-child(7) strong']) fonts[selector]=await fontsFor(cdp,selector);
      await capture(page,'specimen-1440');
      fs.writeFileSync(path.join(out,'rendered-fonts.json'),JSON.stringify(fonts,null,2));
      if(stage==='after') {
        for(const selector of ['#fontKorean']) expect(fonts[selector].some(f=>f.isCustomFont && f.familyName.startsWith('Pretendard') && f.glyphCount>0)).toBeTruthy();
        for(const selector of ['#fontLatin','#fontNumeric','#compareRunsTable tr:first-child td:nth-child(7) strong']) expect(fonts[selector].some(f=>f.isCustomFont && f.familyName==='Inter Variable' && f.glyphCount>0)).toBeTruthy();
        for(const selector of ['#fontIdentity','#fontPath','#compareRunsTable tr:first-child td:first-child strong']) expect(fonts[selector].some(f=>f.isCustomFont && f.familyName==='IBM Plex Mono' && f.glyphCount>0)).toBeTruthy();
        expect(fonts['#fontIdentity'].some(f=>f.familyName.startsWith('Pretendard'))).toBeTruthy();
        const widths=await page.evaluate(()=> ['0','1','8'].map(text=> { const e=document.createElement('span'); e.className='numeric'; e.style.display='inline-block'; e.textContent=text;document.querySelector('#typographySpecimen').append(e);const width=e.getBoundingClientRect().width;e.remove();return width; }));
        // DOM tabular figure alignment is verified below; Canvas uses the same numeric face.
        expect(Math.max(...widths)-Math.min(...widths)).toBeLessThan(.05);
        expect(await page.locator('#compareRunsTable tr:first-child td:nth-child(7)').evaluate(e=>getComputedStyle(e).textAlign)).toBe('right');
      }
      await page.locator('#typographySpecimen').evaluate(e=>e.remove());
      await page.locator('#researchCompareChart').focus(); await page.locator('#researchCompareChart').press('Home');
      await expect(page.locator('#researchCompareChartTooltip')).toBeVisible();
      await capture(page,'tooltip-1440'); await page.locator('#researchCompareChart').press('Escape');
      const tableWrap=page.locator('.research-table-wrap');
      expect(await tableWrap.evaluate(e=>getComputedStyle(e).overflowX)).toBe('auto');
      await page.locator('#compareRunsTable tr').last().scrollIntoViewIfNeeded();
      await capture(page,'table-identities-1440');
      await page.locator('#compareRunsTable tr').nth(3).locator('summary').click();
      await capture(page,'identity-details-1440');
      for(const [selector,name] of [['[data-chart-png="researchCompareChart"]','dashboard.png'],['[data-paper-export="researchCompareChart"]','publication.svg'],['[data-paper-export="researchCompareChart"]','publication.png']]) {
        if(name.startsWith('publication')) {await page.locator(selector).click();await page.locator('#publicationFormatSelect').selectOption(name.endsWith('svg')?'svg':'png');}
        const pending=page.waitForEvent('download');
        await page.locator(name.startsWith('publication')?'#publicationForm button[type="submit"]':selector).click();
        await (await pending).saveAs(path.join(out,name));
      }
      const observations=await page.evaluate(()=>({fonts:[...document.fonts].map(f=>({family:f.family,weight:f.weight,status:f.status})),requests:performance.getEntriesByType('resource').filter(r=>/\.(woff2?|ttf)/.test(r.name)).map(r=>({url:r.name,bytes:r.transferSize})),canvasFont:document.querySelector('#researchCompareChart').getContext('2d').font}));
      fs.writeFileSync(path.join(out,'font-loading.json'),JSON.stringify(observations,null,2));
      if(stage==='after') expect(observations.canvasFont).toContain('Inter');
      await page.setViewportSize({width:720,height:450});
      await navigate(page,'compare',720);
      expect(await tableWrap.evaluate(e=>e.scrollWidth>e.clientWidth)).toBeTruthy();
      await capture(page,'table-scroll-720');
      await navigate(page,'overview',720); await capture(page,'zoom-equivalent-200');
      expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(2);
    }
    expect(errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
    expect(fixture.requests.filter(r=>r.method!=='GET').map(r=>r.path)).toEqual(['/api/sweeps/preview']);
    fs.writeFileSync(path.join(out,`manifest-${width}.json`),JSON.stringify({stage,viewport:[width,height],fixture:FIXTURE_ID,source_commit:execFileSync('git',['rev-parse',process.env.TYPOGRAPHY_SOURCE_REF||'HEAD'],{encoding:'utf8'}).trim(),source_diff:process.env.TYPOGRAPHY_SOURCE_REF?'':execFileSync('git',['diff','--','cluster/dashboard'],{encoding:'utf8'}),live_actions:0,errors},null,2));
  });
}
test('Typography empty and failed-request warnings remain visible',async({page})=> {
  await installLabFixture(page,labFixture({empty:true})); await page.goto('/');
  await expect(page.locator('#overviewMatrix')).toContainText('저장된 campaign이 없습니다'); await capture(page,'empty-1440');
  await page.route('**/api/campaigns',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"Synthetic service unavailable"}'}));
  await page.evaluate(()=>ClusterDashboard.research.load()); await expect(page.locator('#overviewMatrix')).toContainText('조회 실패');
  await capture(page,'error-1440');
});

test('Late local fonts repaint Canvas while baseline and hidden series remain selected', async ({page})=> {
  test.skip(stage==='before','Before source has no local font loading contract');
  const fixture=labFixture(); await installLabFixture(page,fixture);
  let release; const ready=new Promise(resolve=>release=resolve);
  await page.route('**/static/fonts/**/*.woff2', async route=>{await ready;await route.continue();});
  await page.addInitScript(()=> {
    window.typographyPaints=[];
    const original=CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText=function(text,...args) {
      window.typographyPaints.push({text:String(text),font:this.font,status:document.fonts.status,canvas:this.canvas.id});
      return original.call(this,text,...args);
    };
  });
  await page.goto('/#compare',{waitUntil:'domcontentloaded'});
  await expect(page.locator('#compareBaseline option[value="pi-baseline"]')).toHaveCount(1);
  await page.locator('#compareBaseline').selectOption('pi-baseline');
  await page.locator('#compareAxis').selectOption('strategy,node_count');
  await page.locator('#researchCompareChartLegend button').filter({hasText:'WARNING'}).click();
  const before=await page.evaluate(()=>typographyPaints.length);
  const beforePixels=await page.locator('#researchCompareChart').evaluate(c=>c.toDataURL());
  expect(await page.evaluate(()=>document.fonts.status)).toBe('loading');
  release(); await page.evaluate(()=>document.fonts.ready);
  await expect.poll(()=>page.evaluate(()=>typographyPaints.length)).toBeGreaterThan(before);
  expect(await page.locator('#researchCompareChart').evaluate(c=>c.toDataURL())).not.toBe(beforePixels);
  await expect(page.locator('#compareBaseline')).toHaveValue('pi-baseline');
  await expect(page.locator('#compareAxis')).toHaveValue('strategy,node_count');
  await expect(page.locator('#researchCompareChartLegend button').filter({hasText:'WARNING'})).toHaveAttribute('aria-pressed','false');
  const paints=await page.evaluate(()=>typographyPaints.slice(-30));
  expect(paints.some(p=>p.canvas==='researchCompareChart' && p.font.includes('Inter') && p.status==='loaded')).toBeTruthy();
  await capture(page,'late-font-compare-1440');
  fs.writeFileSync(path.join(out,'late-font-paints.json'),JSON.stringify(paints,null,2));
  expect(fixture.unexpected).toEqual([]); expect(fixture.requests.filter(r=>r.method!=='GET')).toEqual([]);
});

test('Sweep SVG axes use loaded UI faces at readable size on mobile',async({page,context})=> {
  const fixture=labFixture(); await installLabFixture(page,fixture);
  const result={sweep_id:'type-demo',trials:[{trial_id:'type-trial',repeat_index:1,status:'completed',condition:{n_ctx:4096,concurrency:1},attempts:[{attempt_id:'type-attempt',run_id:'type-run',representative:true,status:'completed',metrics:{cluster_tokens_per_s:7.5},request_evidence:{}}]}]};
  await page.route('**/api/sweeps/type-demo/results',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(result)}));
  await page.setViewportSize({width:390,height:844}); await page.goto('/#sweeps');
  await expect(page.locator('#activeSweepGrid')).toContainText('저장된 sweep 없음');
  await page.evaluate(async()=> {ClusterDashboard.state.selectedSweepId='type-demo';ClusterDashboard.state.sweepDetails.set('type-demo',{sweep:{sweep_id:'type-demo',status:'completed'}});await ClusterDashboard.sweepResults.load('type-demo');});
  await expect(page.locator('.sweep-chart svg')).toBeVisible();
  await page.evaluate(()=>document.fonts.ready);
  await page.locator('.sweep-chart').scrollIntoViewIfNeeded(); await capture(page,'sweep-svg-390');
  if(stage==='after') {
    const cdp=await context.newCDPSession(page);await cdp.send('DOM.enable');await cdp.send('CSS.enable');
    const fonts=await fontsFor(cdp,'.sweep-chart text');expect(fonts.some(f=>f.isCustomFont && f.familyName==='Inter Variable')).toBeTruthy();
    const sizes=await page.locator('.sweep-chart text').evaluate(e=>({visible:Number.parseFloat(getComputedStyle(e).fontSize)*e.getScreenCTM().a,scroll:e.closest('.sweep-chart').scrollWidth>e.closest('.sweep-chart').clientWidth}));
    expect(sizes.visible).toBeGreaterThanOrEqual(12);expect(sizes.scroll).toBeTruthy();
    fs.writeFileSync(path.join(out,'sweep-svg-fonts.json'),JSON.stringify({fonts,sizes},null,2));
  }
  expect(fixture.unexpected).toEqual([]);expect(fixture.requests.filter(r=>r.method!=='GET')).toEqual([]);
});

test('Publication SVG keeps academic fonts and renders on the local export host',async({context})=> {
  test.skip(stage==='before','Export host evidence is recorded with final source');
  const svg=fs.readFileSync(path.join(out,'publication.svg'),'utf8');
  expect(svg).toContain('Arial, Noto Sans CJK KR, Noto Sans KR, sans-serif');
  expect(svg).not.toContain('InterVariable');expect(svg).toContain('width="85mm"');
  const exportPage=await context.newPage();
  await exportPage.setContent(svg);await exportPage.evaluate(()=>document.fonts.ready);
  const cdp=await context.newCDPSession(exportPage);await cdp.send('DOM.enable');await cdp.send('CSS.enable');
  const fonts=await fontsFor(cdp,'svg > text');
  fs.writeFileSync(path.join(out,'publication-host-fonts.json'),JSON.stringify({host:'local macOS Chromium',fonts,remoteLinuxServer:'not tested; Arial/CJK availability is host-dependent'},null,2));
  expect(fonts.reduce((count,font)=>count+font.glyphCount,0)).toBeGreaterThan(0);
  await exportPage.close();
});
