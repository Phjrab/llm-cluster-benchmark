const { test, expect } = require('@playwright/test');
const { labFixture, installLabFixture } = require('./lab-scenarios');

function observation(state = 'LOOP_ERROR') {
  return { schema_version: 1, server: 'RESPONDING', display_state: state,
    loop_state: state === 'LOOP_ERROR' ? 'ERROR' : 'WAITING', freshness: 'FRESH',
    serving_instance_id: 'controller-current', loop_instance_id: 'controller-current',
    observed_at: '2026-10-23T00:00:00Z', owner_evidence: 'LOCAL_LOOP_OBSERVED',
    last_error_code: state === 'LOOP_ERROR' ? 'PROGRESS_LOOP_ERROR' : null,
    request_progress: 'UNKNOWN', workers: { freshness: 'STALE', nodes: [{name:'pi-demo', response:'UNKNOWN'}] },
    children: [{job_id:'job-one', trial_id:'trial-one', attempt_id:'attempt-one', status:'running',
      phase:'measurement', cleanup_status:'held', process_liveness:'UNKNOWN'}] };
}

function diagnostics(runId) {
  return { run_id: runId, availability: 'RECORDED', requests: [{
    identity: { diagnostic_id:'a'.repeat(32), run_id:runId, job_id:'job-one', attempt_id:'attempt-one',
      node:'pi-demo', logical_request_id:1, request_id:1, replica_index:0 },
    controller:{terminal_stage:'timeout', last_stage:'timeout', failure_code:'REQUEST_TIMEOUT'},
    worker:{last_stage:'generation_completed', observed_outcome:'generation_completed', record_status:'ACQUIRED'},
    cleanup:{status:'UNCERTAIN'}, resources:{values:{worker_rss_bytes:{available:false,value:null}}},
    durations:{controller_ttft:{available:false,seconds:null}},
    worker_completion_found_after_controller_failure:true,
    missing_evidence:['OWNERSHIP_CLEANUP_NOT_VERIFIED'], cause:{classification:'INSUFFICIENT_EVIDENCE'},
  }] };
}

test('Campaign shows loop error separately from stored running child and stale Worker cache', async ({page}) => {
  const fixture = labFixture(); fixture.campaign.progress_observation = observation();
  await installLabFixture(page, fixture); await page.goto('/#campaign');
  const panel = page.locator('#campaignDetail .execution-observation');
  await expect(panel).toContainText('진행 루프 오류');
  await expect(panel).toContainText('job-one');
  await expect(panel).toContainText('process live UNKNOWN');
  await expect(panel).toContainText('pi-demo: UNKNOWN');
  await panel.locator('summary').click();
  await expect(panel).toContainText('PROGRESS_LOOP_ERROR');
  expect(fixture.requests.filter(r => r.method !== 'GET')).toEqual([]);
});

test('Sweep observation retains theme and fits mobile for long child and cooldown', async ({page}) => {
  const fixture = labFixture(); const original = fixture.sweepApi;
  fixture.sweepApi = async args => {
    if (args.path === '/api/sweeps') return args.json({sweeps:[{sweep_id:'observation-sweep'}]}), true;
    if (args.path === '/api/sweeps/observation-sweep') return args.json({
      draft:{sweep_id:'observation-sweep', status:'paused'}, sweep:{status:'paused', trials:[]},
      progress_observation:observation('COOLDOWN') }), true;
    if (args.path.startsWith('/api/sweeps/observation-sweep/events')) return args.json({events:[],next_cursor:0}), true;
    if (args.path === '/api/sweeps/observation-sweep/results') return args.json({cells:[],coverage:{}}), true;
    return original(args);
  };
  await page.setViewportSize({width:390,height:844});
  await installLabFixture(page, fixture); await page.goto('/#sweeps');
  const panel = page.locator('#activeSweepGrid .execution-observation');
  await expect(panel).toContainText('cooldown');
  await panel.locator('summary').click();
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({path:'.artifacts/f03/progress-mobile-dark.png',fullPage:true});
  expect(fixture.requests.filter(r => r.method !== 'GET')).toEqual([]);
});

test('Result diagnosis keeps timeout and late evidence, UNKNOWN memory and privacy', async ({page}) => {
  const fixture = labFixture(); await installLabFixture(page, fixture);
  await page.route('**/api/runs/*/responses', route => {
    const runId = new URL(route.request().url()).pathname.split('/')[3];
    return route.fulfill({contentType:'application/json',body:JSON.stringify({run_id:runId,
      response_storage_status:'not_persisted', responses:[], diagnostics:diagnostics(runId)})});
  });
  await page.goto('/#results');
  await page.locator('[data-view-run]').first().click();
  const panel = page.locator('.request-diagnostics');
  await expect(panel).toContainText('timeout');
  await expect(panel).toContainText('원래 실패 결과는 유지');
  await expect(panel).toContainText('메모리 UNKNOWN');
  await expect(panel).toContainText('cleanup UNCERTAIN');
  await panel.locator('summary').click();
  await expect(panel).not.toContainText('0 bytes');
  await expect(panel).not.toContainText('0 s');
  await expect(panel).toContainText('INSUFFICIENT_EVIDENCE');
  await expect(page.locator('#resultInspector')).toContainText('응답을 저장하지 않도록 설정');
  await page.screenshot({path:'.artifacts/f03/diagnostic-result-light.png',fullPage:true});
  expect(fixture.requests.filter(r => r.method !== 'GET')).toEqual([]);
});

test('Legacy diagnostic fallback and delayed result selection never mix runs', async ({page}) => {
  const fixture = labFixture(); await installLabFixture(page, fixture); await page.goto('/#results');
  await page.locator('[data-view-run]').first().click();
  await expect(page.locator('.request-diagnostics')).toContainText('NO_DIAGNOSTIC_RECORD');
  await page.evaluate(() => {
    const d = ClusterDashboard;
    d.state.runs.push({run_id:'run_other',status:'failed'});
    d.api = path => new Promise(resolve => {
      if (path.includes('run_other')) resolve({run_id:'run_other', responses:[],diagnostics:{run_id:'run_other',availability:'NO_DIAGNOSTIC_RECORD'}});
      else window.resolveOldResult = () => resolve({run_id:d.state.runs[0].run_id,responses:[],diagnostics:{run_id:d.state.runs[0].run_id,availability:'OLD_RESULT_MUST_NOT_APPEAR'}});
    });
    d.results.show(d.state.runs[0].run_id);
    d.results.show('run_other');
  });
  await page.evaluate(() => window.resolveOldResult());
  await expect(page.locator('.request-diagnostics')).toContainText('NO_DIAGNOSTIC_RECORD');
  await expect(page.locator('#resultInspector')).not.toContainText('OLD_RESULT_MUST_NOT_APPEAR');
});
