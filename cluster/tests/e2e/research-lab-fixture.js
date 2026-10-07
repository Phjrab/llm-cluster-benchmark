// Synthetic UI fixture, never hardware evidence. No backend writes.
const MODEL_ID = "qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf";
const RUN_ID = "run-e2e-001";

function fixtureState() {
  const nodes = [
    { name: "jetson-worker-01", role: "worker", host: "192.0.2.26", user: "jetson", ssh_port: 22, api_port: 8000, project_dir: "/home/jetson/llm-cluster-benchmark", enabled: true, platform: "jetson" },
    { name: "pi-worker-02", role: "worker", host: "192.0.2.16", user: "pi", ssh_port: 22, api_port: 8000, project_dir: "/home/pi/llm-cluster-benchmark", enabled: true, platform: "raspberry-pi" },
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
    if (path === "/api/research/readiness") return json({
      eligible: false, model_counts: { approved: 0, total: 1 }, approved_models: [],
      license_blockers: [], workers: [], controller_source: { status: "unverified" },
      blocking_issues: [{ code: "UI_FIXTURE_NOT_HARDWARE_EVIDENCE" }],
      execution_gate: { formal_execution_allowed: false, blocking_requirements: ["실장비 연구 준비도 미평가 · UI fixture"] },
    });
    if (path === "/api/huggingface/status") return json({ installed: true, verified: false, login_command: ".venv/bin/hf auth login" });
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
    fixture.unexpected?.push(path);
    return json({ detail: "Unmapped synthetic endpoint" }, 404);
  });
}

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


module.exports = { fixtureState, bootstrapPayload, installApiFixture, sweepPlan };
