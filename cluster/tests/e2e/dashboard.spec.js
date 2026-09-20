const { test, expect } = require("@playwright/test");

const MODEL_ID = "qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf";
const RUN_ID = "run-e2e-001";

function fixtureState() {
  const nodes = [
    { name: "jetson-worker-01", role: "worker", host: "192.168.0.26", user: "jetson", ssh_port: 22, api_port: 8000, project_dir: "/home/jetson/llm-cluster-benchmark", enabled: true, platform: "jetson" },
    { name: "pi-worker-02", role: "worker", host: "192.168.0.16", user: "pi", ssh_port: 22, api_port: 8000, project_dir: "/home/pi/llm-cluster-benchmark", enabled: true, platform: "raspberry-pi" },
  ];
  const status = [
    {
      name: "jetson-worker-01", api: true, model_ids: [MODEL_ID], model_count: 1,
      profile: { platform_kind: "jetson", hostname: "jetson-lab" }, capabilities: { inference_ready: true },
      metrics: { sampled_at: "2026-08-24T10:00:00Z", cpu_pct: 31, gpu_pct: 44, ram_pct: 52, power_w: 18.4, gpu_temp_c: 53 },
      current: { model_id: MODEL_ID },
    },
    {
      name: "pi-worker-02", api: true, model_ids: [MODEL_ID], model_count: 1,
      profile: { platform_kind: "raspberry-pi", hostname: "pi-worker-2" }, capabilities: { inference_ready: true },
      metrics: { sampled_at: "2026-08-24T10:00:00Z", cpu_pct: 46, ram_pct: 61, cpu_temp_c: 49 },
      power_integrity: {
        available: true, status: "history_warning", raw_hex: "0x50000", source: "vcgencmd",
        current: { undervoltage: false, throttled: false }, history: { undervoltage: true, throttled: true },
        observed_at: "2026-08-24T10:00:00Z",
      },
      current: { model_id: MODEL_ID },
    },
  ];
  const model = {
    id: MODEL_ID, filename: "qwen2.5-1.5b-instruct-q4_k_m.gguf", size_bytes: 1_150_000_000,
    size_gb: 1.07, quantization: "Q4_K_M", installed_nodes: nodes.map(node => node.name),
    catalog: {
      display_name: "Qwen2.5 1.5B Instruct", vendor: "Qwen", family: "Qwen2.5", license: "Apache-2.0",
      recommendation_tier: "smoke", summary_ko: "엣지 클러스터 기본 동작 검증 모델", parameters_total_b: 1.5,
      context_length_advertised: 32768, default_context: 4096, identity_locked: true, official_gguf: true,
    },
  };
  const participantNodes = nodes.map(node => ({
    name: node.name, configured_platform: node.platform, detected_platform: node.platform,
    hostname: node.name === "pi-worker-02" ? "pi-worker-2" : "jetson-lab", host: node.host, api_port: node.api_port,
    board_model: node.platform === "jetson" ? "Jetson Orin Nano" : "Raspberry Pi 5 Model B",
    os: "Ubuntu 24.04", cpu_model: node.platform === "jetson" ? "ARM Cortex-A78AE" : "Cortex-A76",
    cpu_cores_logical: node.platform === "jetson" ? 6 : 4, memory_total_mb: node.platform === "jetson" ? 8192 : 4096,
    runtime_backend: { kind: node.platform === "jetson" ? "cuda" : "openblas", llama_cpp_python: "0.3.20" },
    inference_threads: node.platform === "jetson" ? 6 : 4, capture_status: "captured", git_commit: "951edb3",
  }));
  const run = {
    run_id: RUN_ID, experiment_id: "e2e-experiment", name: "browser-e2e", status: "completed",
    execution_strategy: "replicated_round_robin", model_id: MODEL_ID, nodes: nodes.map(node => node.name),
    participant_nodes: participantNodes, started_at: "2026-08-24T09:58:00Z", finished_at: "2026-08-24T10:00:00Z",
    logical_requests: 2, physical_requests: 2, success_rate: 1, cluster_tokens_per_s: 18.25,
    ttft_p50_s: 0.42, e2e_p95_s: 2.15, measurement_quality: "warning",
    power_integrity: { nodes: { "pi-worker-02": { quality: "warning", pre_measurement: status[1].power_integrity, postflight: status[1].power_integrity, measurement: { valid_sample_count: 2, sample_count: 2, active_warning_samples: 0 } } }, warnings: [] },
    actual_model_config: nodes.map(node => ({ node: node.name, model_id: MODEL_ID, n_ctx: 4096, n_gpu_layers: node.platform === "jetson" ? 30 : 0, n_batch: 512 })),
    per_node: nodes.map((node, index) => ({ node: node.name, tokens_per_s: index ? 7.5 : 10.75, requests: 1 })),
  };
  return { nodes, status, model, run, actions: [], deletedRuns: new Set(), restoredRuns: new Set(), activeExperiment: null, deletePayload: null, experimentPayload: null };
}

function bootstrapPayload(fixture) {
  const visibleRuns = fixture.deletedRuns.has(RUN_ID) ? [] : [fixture.run];
  const models = fixture.models || [fixture.model];
  return {
    nodes: fixture.nodes, status: fixture.status, models, model_catalog: models.map(model => model.catalog),
    model_recommendations: fixture.modelRecommendations || {
      "jetson-worker-01": [{ id: MODEL_ID, status: "recommended", reasons_ko: ["CUDA smoke 검증"], cautions_ko: [], memory: { fits: true, required_mb: 1700, safe_available_mb: 5000 } }],
      "pi-worker-02": [{ id: MODEL_ID, status: "compatible", reasons_ko: ["OpenBLAS smoke 검증"], cautions_ko: ["CPU 추론"], memory: { fits: true, required_mb: 1700, safe_available_mb: 2600 } }],
    },
    model_starter_packs: [{ id: "minimal_smoke", label_ko: "Minimal Smoke Pack", model_ids: [MODEL_ID, "missing-model/catalog.gguf"] }], model_catalog_policy: {}, runs: visibleRuns, suites: [],
    experiment_groups: [{ experiment_id: "e2e-experiment", name: "browser-e2e", run_count: visibleRuns.length, runs: visibleRuns, latest_run: visibleRuns[0] || null, default_config: { execution_strategy: "replicated_round_robin", node_names: fixture.nodes.map(node => node.name), model_ids: [MODEL_ID] } }],
    actions: fixture.actions, environment: fixture.nodes.map(node => ({ node: node.name, status: "ready", backend: { kind: node.platform === "jetson" ? "cuda" : "openblas", verified: true }, checked_at: "2026-08-24T10:00:00Z", checks: [] })),
    settings: { worker_api_auth: false, dashboard_token_auth: false }, onboarding: {}, active_experiment: fixture.activeExperiment,
    defaults: { name: "browser-e2e", execution_strategy: "replicated_round_robin", node_names: fixture.nodes.map(node => node.name), model_id: MODEL_ID, model_ids: [MODEL_ID], requests: 2, concurrency: 1, max_tokens: 16, n_ctx: 4096, n_gpu_layers: 30, warmup_requests: 0, temperature: 0, top_p: 0.9, seed: 42, require_uniform_config: false, model_cooldown_s: 0, continue_on_model_error: true, sweep_mode: "cumulative", rpc_split_mode: "layer", rpc_split_policy: "auto", rpc_tensor_split: [], acknowledge_experimental_rpc: false, prompt: "엣지 LLM 장점을 설명해줘." },
  };
}

async function installApiFixture(page, fixture) {
  await page.route("**/api/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (path === "/api/events") return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": fixture\n\n" });
    if (path === "/api/controller/status") return json({ role: "controller", inference_enabled: false, dashboard: { healthy: true } });
    if (path === "/api/bootstrap") return json(bootstrapPayload(fixture));
    if (fixture.sweepApi) {
      const handled = await fixture.sweepApi({ route, request, url, path, json });
      if (handled) return;
    }
    if (path === "/api/actions") return json({ actions: fixture.actions });
    if (path === "/api/campaigns") return json({ campaigns: [] });
    if (path === "/api/research/compare") return json({ rows: [], filters: {} });
    if (path === "/api/research/readiness") return json({ ready: false, checks: [] });
    if (path === "/api/experiments" && request.method() === "GET") {
      const payload = bootstrapPayload(fixture);
      return json({ runs: payload.runs, suites: payload.suites, experiment_groups: payload.experiment_groups });
    }
    if (path === "/api/experiments" && request.method() === "POST") {
      fixture.experimentPayload = request.postDataJSON();
      fixture.activeExperiment = { id: "job-e2e", name: fixture.experimentPayload.name, nodes: fixture.experimentPayload.node_names, status: "running", phase: "measurement", completed: 1, total: 2, execution_strategy: fixture.experimentPayload.execution_strategy };
      return json({ experiment: fixture.activeExperiment, definition: { experiment_id: "e2e-experiment" }, warnings: [] });
    }
    if (path === `/api/runs/${RUN_ID}/responses`) return json({ run_id: RUN_ID, response_storage_status: "mixed", responses: [{ logical_request_id: 1, request_id: 1, node: "jetson-worker-01", model_id: MODEL_ID, prompt: "엣지 LLM 장점을 설명해줘.", response: "네트워크 의존도를 낮추고 지연을 줄일 수 있습니다.", response_storage_status: "stored", output_sha256: "a".repeat(64), ok: true, ttft_s: 0.42, e2e_s: 2.01, generated_tokens: 14, tokens_per_s: 10.75 }, { logical_request_id: 2, request_id: 2, node: "pi-worker-02", model_id: MODEL_ID, response_storage_status: "hash_only", output_chars: 21, output_sha256: "b".repeat(64), ok: true, ttft_s: 0.71, e2e_s: 3.4, generated_tokens: 11, tokens_per_s: 7.5 }] });
    if (path === `/api/runs/${RUN_ID}` && request.method() === "DELETE") { fixture.deletedRuns.add(RUN_ID); return json({ ok: true, run_id: RUN_ID, trash: true }); }
    if (path === "/api/results/trash" && request.method() === "GET") return json({ trash: fixture.deletedRuns.has(RUN_ID) ? [{ trash_id: `${RUN_ID}-20260824`, run_id: RUN_ID, status: "completed", deleted_at_epoch_s: 1787500000, archive_sha256: "b".repeat(64), protected: false, campaign_id: null, suite_id: null }] : [] });
    if (path === `/api/results/trash/${RUN_ID}-20260824/restore` && request.method() === "POST") { fixture.deletedRuns.delete(RUN_ID); fixture.restoredRuns.add(RUN_ID); return json({ ok: true, run_id: RUN_ID, suite_restored: false }); }
    if (path.endsWith("/power")) return json({ power: { supported: true, ok: true, modes: [{ id: 0, name: "MAXN", power_budget_w: 25, is_default: true }], current: { id: 0, name: "MAXN" }, default_mode: 0 } });
    if (path.startsWith("/api/nodes/") && request.method() === "DELETE") {
      const name = decodeURIComponent(path.split("/").at(-1));
      fixture.deletePayload = request.postDataJSON();
      fixture.nodes = fixture.nodes.filter(node => node.name !== name);
      fixture.status = fixture.status.filter(item => item.name !== name);
      return json({ ok: true, node: name, cleanup: { removed: false } });
    }
    return json({});
  });
}

test("Dashboard core flow renders workers, models, power warning, creates and recovers an experiment", async ({ page }) => {
  const fixture = fixtureState();
  await installApiFixture(page, fixture);
  await page.goto("/");

  await expect(page.locator('[data-node-card="jetson-worker-01"]')).toBeVisible();
  await expect(page.locator('[data-node-card="pi-worker-02"]')).toContainText("POWER WARNING · HISTORY");
  await expect(page.locator("#experimentPowerBanner")).toContainText("일반 실험에서는 비차단 측정 품질");
  await expect(page.locator("#experimentPowerBanner")).toContainText("PI_POWER_ACTIVE");
  await expect(page.locator("#orbitWorkers [data-orbit-worker]")).toHaveCount(2);
  await expect(page.locator('#orbitWorkers [data-orbit-worker="jetson-worker-01"]')).toContainText("ONLINE");
  await expect(page.locator('#orbitWorkers [data-orbit-worker="pi-worker-02"]')).toContainText("ONLINE");
  await expect(page.locator("#workerCount")).toHaveText("2");
  await expect(page.locator("#modelLibrary")).toContainText("Qwen2.5 1.5B Instruct");
  await page.locator('[data-model-pack="minimal_smoke"]').click();
  await expect(page.locator("#modelStarterPackPreview")).toBeVisible();
  await expect(page.locator("#modelStarterPackPreview")).toContainText("Qwen2.5 1.5B Instruct");
  await expect(page.locator("#modelStarterPackPreview")).toContainText("missing-model/catalog.gguf");
  await expect(page.locator("#modelStarterPackPreview")).toContainText("설치됨 1개 · 미설치 1개");
  await expect(page.locator("[data-pack-apply]")).toContainText("설치된 1개 모델을 실험에 적용");
  await page.locator('[data-model-view="vendors"]').click();
  await expect(page.locator(".vendor-overview-card")).toHaveCount(1);
  await expect(page.locator(".vendor-overview-card")).toContainText("Qwen");
  await expect(page.locator(".vendor-overview-card header > strong")).toContainText("1");
  await expect(page.locator(".vendor-overview-card header > strong small")).toHaveText("MODELS");
  await page.locator('[data-vendor-open="Qwen"]').click();
  await expect(page.locator("#modelLibrary")).toContainText("Qwen2.5 1.5B Instruct");
  await page.evaluate(() => {
    const base = window.ClusterDashboard.state.models[0];
    window.__e2eBaseModel = base;
    window.ClusterDashboard.state.models = Array.from({ length: 12 }, (_, index) => ({
      ...base, id: `qwen-page-${index + 1}.gguf`, filename: `qwen-page-${index + 1}.gguf`,
      catalog: { ...base.catalog, display_name: `Qwen Page Model ${index + 1}` },
    }));
    window.ClusterDashboard.renderModelLibrary();
  });
  await page.locator("#modelPageSize").selectOption("5");
  await expect(page.locator("#modelLibrary .library-model-card")).toHaveCount(5);
  await expect(page.locator("#modelPageInfo")).toHaveText("1–5 / 12개");
  await page.locator('[data-model-page="2"]:not([aria-label])').click();
  await expect(page.locator("#modelPageInfo")).toHaveText("6–10 / 12개");
  await page.evaluate(() => {
    window.ClusterDashboard.state.models = [window.__e2eBaseModel];
    window.ClusterDashboard.renderModels({ model_ids: [window.__e2eBaseModel.id] });
  });

  await page.locator("#experimentName").fill("phase-08-browser-flow");
  await page.locator("#requestsInput").fill("2");
  await page.locator("#experimentForm").evaluate(form => form.scrollIntoView());
  await page.locator("#runButton").click();
  await expect.poll(() => fixture.experimentPayload?.name).toBe("phase-08-browser-flow");
  expect(fixture.experimentPayload.model_ids).toEqual([MODEL_ID]);
  expect(fixture.experimentPayload.node_names).toEqual(["jetson-worker-01", "pi-worker-02"]);
  expect(fixture.experimentPayload.persist_prompt).toBe(true);
  expect(fixture.experimentPayload.response_storage_mode).toBe("full");
  await expect(page.locator("#runPhase")).toHaveText("부하 측정");

  await page.reload();
  await expect(page.locator("#runPhase")).toHaveText("부하 측정");
  await expect(page.locator("#runProgressText")).toHaveText("50%");
});

test("Result responses, private trash deletion, and safe worker disconnect remain interactive", async ({ page }) => {
  const fixture = fixtureState();
  await installApiFixture(page, fixture);
  await page.goto("/#results");

  await page.evaluate(() => {
    const base = window.ClusterDashboard.state.runs[0];
    const runs = [base, ...Array.from({ length: 11 }, (_, index) => ({ ...base, run_id: `run-page-${index + 2}`, name: `browser-page-${index + 2}` }))];
    window.ClusterDashboard.state.runs = runs;
    window.ClusterDashboard.state.experimentGroups[0].runs = runs;
    window.ClusterDashboard.state.experimentGroups[0].run_count = runs.length;
    window.ClusterDashboard.renderRuns();
  });
  await page.locator("#resultPageSize").selectOption("5");
  await expect(page.locator("#runsTable tr")).toHaveCount(5);
  await expect(page.locator("#resultPageInfo")).toHaveText("1–5 / 12개");
  await page.locator('[data-result-page="2"]:not([aria-label])').click();
  await expect(page.locator("#resultPageInfo")).toHaveText("6–10 / 12개");
  await page.locator('[data-result-page="1"]:not([aria-label])').click();

  await page.locator(`[data-view-run="${RUN_ID}"]`).click();
  await expect(page.locator("#resultInspector")).toContainText("엣지 LLM 장점을 설명해줘.");
  await expect(page.locator("#resultInspector")).toContainText("네트워크 의존도를 낮추고 지연을 줄일 수 있습니다.");
  await expect(page.locator("#resultInspector")).toContainText("실험 참여 노드 · 2대");
  await expect(page.locator("#resultInspector")).toContainText("원문 저장됨");
  await expect(page.locator("#resultInspector")).toContainText("해시만 저장됨");
  await expect(page.locator("#resultInspector")).toContainText("응답 원문은 저장하지 않았습니다.");

  page.once("dialog", dialog => dialog.accept());
  await page.locator(`[data-delete-run="${RUN_ID}"]`).click();
  await expect.poll(() => fixture.deletedRuns.has(RUN_ID)).toBe(true);
  await expect(page.locator("#runsTable")).toContainText("실행 기록 없음");

  await page.locator("#openResultTrashButton").click();
  await expect(page.locator("#resultTrashDialog")).toBeVisible();
  await expect(page.locator("#resultTrashList")).toContainText(RUN_ID);
  await page.locator(`[data-restore-trash="${RUN_ID}-20260824"]`).click();
  await expect.poll(() => fixture.restoredRuns.has(RUN_ID)).toBe(true);
  await expect(page.locator("#resultTrashList")).toContainText("휴지통이 비어 있습니다.");
  await page.locator("#resultTrashDialog [data-close-dialog]").first().click();
  await expect(page.locator("#runsTable")).toContainText("browser-e2e");

  await page.locator('[data-node-detail="pi-worker-02"]').click();
  await page.locator("#nodeDeleteButton").click();
  await expect(page.locator("#nodeDeleteDialog")).toBeVisible();
  await expect(page.locator("#removeWorkerFilesInput")).not.toBeChecked();
  await page.locator("#confirmNodeDeleteButton").click();
  await expect.poll(() => fixture.deletePayload?.remove_worker_files).toBe(false);
  await expect(page.locator('[data-node-card="pi-worker-02"]')).toHaveCount(0);
});

test("Node detail presents a running runtime preparation as a structured operation card", async ({ page }) => {
  const fixture = fixtureState();
  fixture.actions = [{
    id: "prepare-jetson-01",
    action: "prepare",
    status: "running",
    nodes: ["jetson-worker-01"],
    log: ["[jetson-worker-01] checking/installing runtime"],
  }];
  await installApiFixture(page, fixture);
  await page.goto("/#nodes");

  await page.locator('[data-node-card="jetson-worker-01"] .node-detail-button').click();
  const card = page.locator(".node-action-detail");
  await expect(card).toBeVisible();
  await expect(card).toHaveClass(/running/);
  await expect(card.locator(".node-action-head")).toContainText("LLM 런타임 준비");
  await expect(card.locator(".node-action-state")).toHaveText("진행 중");
  await expect(card.locator(".node-action-meta")).toContainText("jetson@192.168.0.26");
  await expect(card.locator(".node-action-meta")).toContainText("/home/jetson/llm-cluster-benchmark");
  await expect(card.locator(".node-action-log")).toContainText("[jetson-worker-01] checking/installing runtime");
});

function sweepPlan(payload, hash = "c".repeat(64), options = {}) {
  const spec = payload?.spec || options.spec || {
    base: { model_ref: "model_1", prompt_ref: "prompt_main", worker_ids: options.workers || ["jetson-worker-01"], execution_strategy: "replicated_round_robin", n_ctx: 1024, concurrency: 1, max_tokens: 64 },
    revision: 1, repeat_count: 1, axes: [], rpc_profiles: [], execution: { max_parallel_jobs: 1 }, budget: { max_trials: 500, max_physical_requests: 1000000 },
  };
  const axes = spec.axes || [];
  const cells = axes.reduce((count, axis) => count * (axis.values?.length || 1), 1);
  const trials = cells * (spec.repeat_count || 1);
  const modelRef = spec.base?.model_ref || "model_1";
  const modelId = payload?.model_selections?.[modelRef] || options.modelId || MODEL_ID;
  const rpc = spec.base?.execution_strategy === "model_parallel_rpc";
  const profile = (spec.rpc_profiles || [])[0] || null;
  const workers = rpc ? (profile?.worker_ids || options.workers || []) : (spec.base?.worker_ids || options.workers || []);
  const capabilities = options.capabilities || [];
  const condition = { ...spec.base, worker_ids: workers };
  return {
    schema_version: 1, artifact_type: "exploratory_sweep_plan", spec,
    context: { models: [], prompts: [], workers: [] }, plan_sha256: hash,
    cells: [{ cell_id: `cell_${"1".repeat(64)}`, candidate_index: 0, condition, model: { model_id: modelId }, prompt: { ref: "prompt_main" }, workers: workers.map(worker_id => ({ worker_id })), rpc_profile: profile, capabilities, workload: { scenarios: 1, logical_requests: 6, physical_requests: 6, warmup_calls: 1, model_loads: workers.length || 1 }, status: capabilities.some(item => item.status === "blocked") ? "blocked" : capabilities.some(item => item.status === "unknown") ? "unknown" : "valid" }],
    trials: Array.from({ length: trials }, (_, index) => ({ trial_id: `trial-${index + 1}`, cell_id: `cell_${"1".repeat(64)}`, sweep_repeat_index: index + 1, generation_order_index: index, execution_order_index: index, status: "pending" })),
    counts: { candidate_cells: cells, unique_cells: cells, duplicate_cells: 0, excluded_cells: 0, included_cells: cells, valid_cells: cells, blocked_cells: 0, unknown_cells: 0, trials, workload: { scenarios: cells, logical_requests: cells * 6, physical_requests: cells * 6, warmup_calls: cells, model_loads: cells * Math.max(1, workers.length) } },
    capabilities: [{ status: "valid", code: "DURABLE_SWEEP_EXECUTION_AVAILABLE", subject: "" }], resolution_state: "resolved", executable: true,
  };
}

function sweepDetail(id, workers, status = "running", hash = "d".repeat(64), modelId = MODEL_ID) {
  const plan = sweepPlan(null, hash, { workers, modelId });
  const draft = { schema_version: 1, artifact_type: "sweep_api_draft", sweep_id: id, status: status === "draft" ? "draft" : "started", plan_revision: 1, plan_sha256: hash, resolution_request: {}, candidate_count: 1, created_at_unix: 1, updated_at_unix: 1 };
  if (status === "draft") return { draft, sweep: null };
  const trials = [{ trial_id: `${id}-trial-1`, cell_id: plan.cells[0].cell_id, status: status === "completed" ? "completed" : "running", official_attempt_id: `${id}-attempt-1`, attempts: [{ attempt_id: `${id}-attempt-1`, status, cleanup_status: status === "completed" ? "verified" : "pending" }] }];
  return { draft, sweep: { sweep_id: id, status, plan_sha256: hash, plan_snapshot: plan, trials, coverage: { completed: status === "completed" ? 1 : 0 } } };
}

function installSweepApi(fixture) {
  const details = new Map([
    ["sweep-a", sweepDetail("sweep-a", ["jetson-worker-01"], "running", "a".repeat(64))],
    ["sweep-b", sweepDetail("sweep-b", ["pi-worker-02"], "running", "b".repeat(64))],
  ]);
  fixture.sweepDetails = details;
  fixture.driftOnResume = false;
  fixture.largeModelInstallRequested = false;
  fixture.sweepApi = async ({ route, request, url, path, json }) => {
    if (path.includes("/install")) fixture.largeModelInstallRequested = true;
    if (path === "/api/sweeps/capabilities") return json({ max_trials: 500, max_parallel_jobs: 2, preview_uses_cached_evidence: true }), true;
    if (["/api/sweeps/preview", "/api/sweeps/readiness/refresh"].includes(path) && request.method() === "POST") {
      fixture.sweepPreviewPayload = request.postDataJSON();
      const rpc = fixture.sweepPreviewPayload.spec.base.execution_strategy === "model_parallel_rpc";
      const unsupported = rpc && fixture.sweepPreviewPayload.spec.rpc_profiles.some(profile => profile.split_mode === "row");
      const plan = sweepPlan(fixture.sweepPreviewPayload, "c".repeat(64), { capabilities: unsupported ? [{ status: "unknown", code: "RPC_ROW_CAPABILITY_UNKNOWN", subject: "jetson-worker-03" }] : [] });
      if (unsupported) { plan.counts.valid_cells = 0; plan.counts.unknown_cells = plan.counts.candidate_cells; }
      return json({ plan, candidates: [], evidence_mode: path.endsWith("refresh") ? "fresh" : "cached" }), true;
    }
    if (path === "/api/sweeps/drafts" && request.method() === "POST") {
      const body = request.postDataJSON();
      fixture.savedSweepPayload = body;
      const plan = sweepPlan(body, "c".repeat(64));
      details.set(body.sweep_id, { draft: { schema_version: 1, artifact_type: "sweep_api_draft", sweep_id: body.sweep_id, status: "draft", plan_revision: body.spec.revision, plan_sha256: plan.plan_sha256, resolution_request: {}, candidate_count: plan.counts.candidate_cells, created_at_unix: 2, updated_at_unix: 2 }, sweep: null, plan });
      return json(details.get(body.sweep_id).draft), true;
    }
    if (path === "/api/sweeps" && request.method() === "GET") return json({ sweeps: [...details.values()].map(item => item.draft), offset: 0, limit: 100 }), true;
    const match = path.match(/^\/api\/sweeps\/([^/]+)(?:\/(.*))?$/);
    if (!match) return false;
    const sweepId = decodeURIComponent(match[1]);
    const suffix = match[2] || "";
    const detail = details.get(sweepId);
    if (!detail) return json({ detail: "Sweep not found" }, 404), true;
    if (!suffix && request.method() === "GET") return json({ draft: detail.draft, sweep: detail.sweep }), true;
    if (suffix === "events" && request.method() === "GET") {
      const cursor = Number(url.searchParams.get("cursor") || 0);
      const events = cursor ? [] : [{ type: "sweep_status", status: detail.sweep?.status || "draft", message: `${sweepId} restored`, at: "2026-09-19T10:00:00Z" }];
      return json({ sweep_id: sweepId, events, cursor, next_cursor: cursor + events.length, has_more: false }), true;
    }
    if (suffix === "events/stream") return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": fake sweep evidence\n\n" }), true;
    if (suffix === "results") {
      const cell = detail.sweep?.plan_snapshot?.cells?.[0] || {};
      const trials = (detail.sweep?.trials || []).map((trial, index) => ({
        ...trial, repeat_index: index + 1, representative_attempt_id: trial.official_attempt_id,
        condition: cell.condition || {},
        model_identity: { model_id: cell.model?.model_id || MODEL_ID, artifact_sha256: "a".repeat(64) },
        prompt_identity: { template_sha256: "b".repeat(64) }, rpc_profile: cell.rpc_profile,
        attempts: (trial.attempts || []).map(attempt => ({
          ...attempt, representative: true, run_id: `${sweepId}-run-1`,
          requested_config: { n_ctx: 4096, n_gpu_layers: 30 }, effective_config: { n_ctx: 3072, n_gpu_layers: 30 },
          condition_mismatch: true, mismatch_fields: ["n_ctx"],
          metrics: { cluster_tokens_per_s: 12.5, effective_user_tokens_per_s: 11.2, requests_per_s: 1.2, ttft_p50_s: 0.4, e2e_p50_s: 1.3, success_rate: 1, generated_tokens_per_j: null },
          energy: { generated_tokens_per_j: null, quality: "partial", available: false, reason: "one_or_more_nodes_unavailable", coverage: { complete: false, coverage_ratio: 0.5, unavailable_nodes: { "jetson-worker-01": "power_sampling_gap_exceeded" } } }, measurement_count: 0,
          request_evidence: { actual_input_tokens: [64], finish_reasons: ["stop"], early_eos_count: 1, input_tokens_exact: true, output_tokens_exact: true },
          responses: [{ request_id: 1, input_tokens: 64, input_tokens_exact: true, generated_tokens: 8, output_tokens_exact: true, finish_reason: "stop", response_storage_status: "stored", response: "fixture response" }],
          parallel_context: { label: "parallel exploratory", overlapping_run_ids: [`${sweepId}-run-2`], isolation: "shared controller/network/storage are not isolated" },
        })),
      }));
      return json({ sweep_id: sweepId, status: detail.sweep?.status, coverage: detail.sweep?.coverage || {}, trials, aggregates: [{ cell_id: cell.cell_id, independent_run_count: 1, metrics: {} }], statistical_unit: "independent completed run per repeat", selection_policy: "official attempt; otherwise latest completed; otherwise latest attempt", comparison_warnings: ["RPC, replicated, and broadcast throughput have different request/token meanings."] }), true;
    }
    if (suffix === "start" && request.method() === "POST") {
      const plan = detail.plan || sweepPlan(fixture.savedSweepPayload, detail.draft.plan_sha256);
      detail.sweep = { sweep_id: sweepId, status: "running", plan_sha256: detail.draft.plan_sha256, plan_snapshot: plan, trials: [{ trial_id: `${sweepId}-trial-1`, cell_id: plan.cells[0].cell_id, status: "running", official_attempt_id: `${sweepId}-attempt-1`, attempts: [{ attempt_id: `${sweepId}-attempt-1`, status: "running" }] }], coverage: { completed: 0 } };
      return json({ sweep: detail.sweep, idempotent_replay: false }), true;
    }
    if (["pause", "resume", "cancel"].includes(suffix) && request.method() === "POST") {
      fixture.lifecycleCalls = [...(fixture.lifecycleCalls || []), { sweepId, operation: suffix, body: request.postDataJSON() }];
      if (suffix === "resume" && fixture.driftOnResume) return json({ detail: "Saved sweep plan is stale against fresh preflight" }, 409), true;
      detail.sweep.status = suffix === "pause" ? "paused" : suffix === "resume" ? "running" : "cancelled";
      if (suffix === "cancel") detail.sweep.trials[0].status = "cancelled";
      return json({ sweep: detail.sweep, idempotent_replay: false }), true;
    }
    if (/^trials\/[^/]+\/retry$/.test(suffix) && request.method() === "POST") return json({ sweep: detail.sweep, idempotent_replay: false }), true;
    if (/^trials\/[^/]+\/clone-draft$/.test(suffix) && request.method() === "POST") {
      const newId = request.postDataJSON().new_sweep_id;
      const cloned = sweepDetail(newId, detail.sweep.plan_snapshot.cells[0].condition.worker_ids, "draft", "e".repeat(64));
      details.set(newId, cloned);
      return json({ draft: cloned.draft, source: { sweep_id: sweepId }, started: false }), true;
    }
    return false;
  };
}

test("Sweep Builder previews 108 trials and controls disjoint durable runs without hidden authority", async ({ page }, testInfo) => {
  const fixture = fixtureState();
  const third = { ...fixture.nodes[0], name: "jetson-worker-03", host: "192.168.0.28" };
  fixture.nodes.push(third);
  fixture.status[0].capabilities = { inference_ready: true, rpc_layer_v1: true, rpc_row_v1: true };
  fixture.status[1].capabilities = { inference_ready: true, rpc_layer_v1: true, rpc_row_v1: true };
  fixture.status.push({ ...fixture.status[0], name: third.name, profile: { platform_kind: "jetson", hostname: "jetson-lab-3" }, capabilities: { inference_ready: true, rpc_layer_v1: true, rpc_row_v1: false } });
  const second = { ...fixture.model, id: "gemma-2b/gemma-2b-q4_k_m.gguf", filename: "gemma-2b-q4_k_m.gguf", installed_nodes: fixture.nodes.map(node => node.name), catalog: { ...fixture.model.catalog, display_name: "Gemma 2B Instruct", vendor: "Google", family: "Gemma", parameters_total_b: 2 } };
  fixture.model.installed_nodes = fixture.nodes.map(node => node.name);
  const missing = { ...fixture.model, id: "large/14b-q4_k_m.gguf", filename: "14b-q4_k_m.gguf", installed_nodes: [], catalog: { ...fixture.model.catalog, display_name: "Large 14B Candidate", vendor: "Example", family: "Large", parameters_total_b: 14, formal_approved: false, official_gguf: true } };
  fixture.models = [fixture.model, second, missing];
  fixture.modelRecommendations = Object.fromEntries(fixture.nodes.map(node => [node.name, [fixture.model, second].map(model => ({ id: model.id, status: "recommended", memory: { fits: true, required_mb: 1700, safe_available_mb: node.platform === "raspberry-pi" ? 2200 : 5600 } }))]));
  installSweepApi(fixture);
  await installApiFixture(page, fixture);
  await page.goto("/#sweeps");

  await expect(page.locator("[data-sweep-worker]")).toHaveCount(3);
  await expect(page.locator("[data-sweep-model]")).toHaveCount(3);
  await expect(page.locator('[data-sweep-model="large/14b-q4_k_m.gguf"]')).toBeDisabled();
  await expect(page.locator("#sweepModelPicker")).toContainText("선택 Worker 전체에 설치되지 않음");
  await expect(page.locator("#sweepModelPicker")).toContainText("formal 별도");
  await page.locator(`[data-sweep-model="${MODEL_ID}"]`).check();
  await page.locator('[data-sweep-model="gemma-2b/gemma-2b-q4_k_m.gguf"]').check();
  await page.locator("#sweepPreviewButton").click();
  await expect(page.locator("#sweepCountGrid")).toContainText("108");
  await expect(page.locator("#sweepBudgetNote")).toContainText("실행 가능한 plan");
  expect(fixture.sweepPreviewPayload.model_selections).toEqual({ model_1: MODEL_ID, model_2: "gemma-2b/gemma-2b-q4_k_m.gguf" });
  expect(fixture.sweepPreviewPayload.spec.mode).toBe("exploratory");
  expect(fixture.sweepPreviewPayload.spec.axes.map(axis => axis.name)).toEqual(["model_ref", "n_ctx", "concurrency", "max_tokens"]);
  await page.locator("#sweepApprovalCheck").check();
  await page.locator("#sweepSaveButton").click();
  await expect(page.locator('[data-sweep-card="context-model-grid"]')).toBeVisible();
  await page.locator('[data-sweep-card="context-model-grid"] [data-sweep-action="start"]').click();
  await expect(page.locator('[data-sweep-card="context-model-grid"]')).toContainText("RUNNING");
  expect(fixture.largeModelInstallRequested).toBe(false);

  await expect(page.locator('[data-sweep-card="sweep-a"]')).toContainText("RUNNING");
  await expect(page.locator('[data-sweep-card="sweep-b"]')).toContainText("RUNNING");
  await page.locator('[data-sweep-card="sweep-b"] [data-sweep-select]').click();
  await expect(page.locator("#sweepEventLog")).toContainText("sweep-b restored");
  await expect(page.locator("#sweepTrialResults")).toContainText("Exploratory only");
  await expect(page.locator("#sweepTrialResults")).toContainText("CONDITION MISMATCH");
  await expect(page.locator("#sweepTrialResults")).toContainText("fixture response");
  await expect(page.locator("#sweepTrialResults")).toContainText("parallel exploratory");
  await expect(page.locator("#sweepTrialResults")).toContainText("50% coverage");
  await expect(page.locator("#sweepTrialResults")).toContainText("power_sampling_gap_exceeded");
  await expect(page.locator("#sweepTrialResults svg")).toBeVisible();
  await page.locator("[data-clone-trial]").click();
  await expect(page.locator(".toast-stack")).toContainText("실행되지 않음");
  await expect(page.locator('[data-sweep-card^="sweep-b-rerun-"]')).toContainText("DRAFT");
  await page.locator('[data-sweep-card="sweep-a"] [data-sweep-action="cancel"]').click();
  await expect(page.locator('[data-sweep-card="sweep-a"]')).toContainText("CANCELLED");
  await expect(page.locator('[data-sweep-card="sweep-b"]')).toContainText("RUNNING");
  await expect(page.locator("#sweepEventLog")).toContainText("sweep-b restored");
  await page.locator('[data-sweep-card="sweep-b"] [data-sweep-action="pause"]').click();
  await expect(page.locator('[data-sweep-card="sweep-b"]')).toContainText("PAUSED");
  await page.locator('[data-sweep-card="sweep-b"] [data-sweep-action="resume"]').click();
  await expect(page.locator('[data-sweep-card="sweep-b"]')).toContainText("RUNNING");
  await page.reload();
  await expect(page.locator('[data-sweep-card="sweep-a"]')).toContainText("CANCELLED");
  await expect(page.locator('[data-sweep-card="sweep-b"]')).toContainText("RUNNING");

  await page.locator('[data-sweep-card="sweep-b"] [data-sweep-action="pause"]').click();
  fixture.driftOnResume = true;
  await page.locator('[data-sweep-card="sweep-b"] [data-sweep-action="resume"]').click();
  await expect(page.locator('[data-sweep-card="sweep-b"]')).toContainText("Saved sweep plan is stale against fresh preflight");
  await expect(page.locator('[data-sweep-card="sweep-a"]')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("S08_FAKE_DATA_sweep-dashboard.png"), fullPage: true });

  await page.locator("#sweepStrategySelect").selectOption("model_parallel_rpc");
  await page.locator("#addRpcProfileButton").click();
  await page.locator('[data-rpc-profile-index="0"] [data-rpc-worker="jetson-worker-03"]').uncheck();
  await page.locator('[data-rpc-profile-index="0"] [data-rpc-field="split_policy"]').selectOption("custom");
  await page.locator('[data-rpc-profile-index="0"] [data-rpc-weight="jetson-worker-01"]').fill("2");
  await page.locator("#addRpcProfileButton").click();
  await page.locator('[data-rpc-profile-index="1"] [data-rpc-field="split_mode"]').selectOption("row");
  await expect(page.locator('[data-rpc-profile-index="0"] [data-rpc-worker]:checked')).toHaveCount(2);
  await expect(page.locator('[data-rpc-profile-index="1"] [data-rpc-worker]:checked')).toHaveCount(3);
  await expect(page.locator('[data-rpc-profile-index="1"]')).toContainText("row unsupported");
  await page.locator('[data-sweep-worker="jetson-worker-03"]').uncheck();
  await expect(page.locator("[data-rpc-profile-index]")).toHaveCount(0);
  await expect(page.locator("#sweepRpcNotice")).toContainText("이전 coordinator와 custom 비율을 폐기");
  await page.locator("#sweepStrategySelect").selectOption("replicated_round_robin");
  await page.locator(`[data-sweep-model="${MODEL_ID}"]`).check();
  await page.locator('[data-sweep-model="gemma-2b/gemma-2b-q4_k_m.gguf"]').check();
  await page.locator(".sweep-advanced summary").click();
  await page.locator("#addSweepPromptButton").click();
  await page.locator('[data-prompt-index="1"] [data-prompt-field="text"]').fill("두 번째 길이 프로필 입력");
  await page.locator('[data-prompt-index="1"] [data-prompt-field="mode"]').selectOption("token_length_profile");
  await page.locator('[data-prompt-index="1"] [data-prompt-field="target_input_tokens"]').fill("256");
  const variantRequest = await page.evaluate(() => window.ClusterDashboard.sweepBuilder.buildRequest());
  expect(variantRequest.prompts).toHaveLength(2);
  expect(variantRequest.spec.axes.find(axis => axis.name === "prompt_ref").values).toEqual(["prompt_main", "prompt_2"]);
});
