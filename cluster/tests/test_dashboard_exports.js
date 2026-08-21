#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appPath = path.resolve(__dirname, "../dashboard/static/app.js");
const dashboardRoot = path.resolve(__dirname, "../dashboard");
const context = vm.createContext({
  console,
  TextEncoder,
  Uint8Array,
  DataView,
  Math,
  Number,
  String,
  Set,
  Map,
  Date,
  JSON,
  document: { addEventListener() {} },
});
context.window = context;
vm.runInContext(fs.readFileSync(appPath, "utf8"), context, { filename: appPath });
for (const moduleName of ["utils.js", "console.js", "models.js", "results.js"]) {
  const modulePath = path.join(dashboardRoot, "static/js", moduleName);
  vm.runInContext(fs.readFileSync(modulePath, "utf8"), context, { filename: modulePath });
}

const template = fs.readFileSync(path.join(dashboardRoot, "templates/index.html"), "utf8");
const appSource = fs.readFileSync(appPath, "utf8");
assert.match(template, /01<\/span>개요[\s\S]*02<\/span>노드[\s\S]*03<\/span>모델[\s\S]*04<\/span>실험[\s\S]*05<\/span>결과/);
assert.match(template, /CONTROLLER[\s\S]*DASHBOARD[\s\S]*SCHEDULER[\s\S]*STORAGE/);
assert.doesNotMatch(template, /HEAD · CONTROL \+ INFERENCE/);
for (const moduleName of ["utils.js", "console.js", "models.js", "results.js"]) {
  assert.match(template, new RegExp(`/static/js/${moduleName.replace(".", "\\.")}`));
}

assert.equal(vm.runInContext(`ClusterDashboard.utils.statusPresentation("completed").tone`, context), "completed");
assert.equal(vm.runInContext(`ClusterDashboard.utils.statusPresentation("cancelled").icon`, context), "−");
assert.equal(vm.runInContext(`ClusterDashboard.terminals.limit`, context), 200);
assert.equal(vm.runInContext(`typeof ClusterDashboard.modelLibrary.recordProgress`, context), "function");
assert.match(appSource, /return state\.nodes\.filter\(node => node\.role === "worker"\)/);
assert.match(appSource, /telemetryDegraded/);
assert.match(appSource, /channel === "experiment"/);
assert.match(appSource, /headers\["X-Cluster-Token"\] = state\.token/);
assert.match(appSource, /authenticatedEventStream\("\/api\/events"\)/);
assert.doesNotMatch(appSource, /\/api\/events\?token=/);
assert.doesNotMatch(appSource, /sessionStorage\.setItem\("clusterToken", fromUrl\)/);
assert.match(template, /ssh-identity-panel[\s\S]*WORKER TERMINAL COMMAND[\s\S]*pairingCommandTarget[\s\S]*pairingCommand/);
assert.match(template, /PUBLIC KEY · 실행 명령 아님/);
assert.match(template, /styles\.css\?v=20260821\.4/);
assert.match(template, /app\.js\?v=20260821\.4/);
assert.match(template, /nodeRenameDialog[\s\S]*nodeRenameForm[\s\S]*renameNodeInput/);
assert.match(template, /data-node-platform-tab="all"[\s\S]*data-node-platform-tab="jetson"[\s\S]*data-node-platform-tab="raspberry-pi"/);
const workerRegistrationCommand = vm.runInContext(`buildWorkerKeyRegistrationCommand("ssh-ed25519 AAAA-test controller@mac")`, context);
assert.match(workerRegistrationCommand, /^umask 077; mkdir -p ~\/\.ssh/);
assert.match(workerRegistrationCommand, /grep -qxF "\$KEY"/);
assert.match(workerRegistrationCommand, /chmod 600 ~\/\.ssh\/authorized_keys$/);
assert.doesNotMatch(workerRegistrationCommand, /\| ssh /);
assert.match(fs.readFileSync(path.join(dashboardRoot, "static/js/results.js"), "utf8"), /output_sha256/);
const responseGrouping = vm.runInContext(`ClusterDashboard.results.responseGroups([
  { logical_request_id: 1, node: "jetson-a" },
  { logical_request_id: 1, node: "pi-b" },
  { logical_request_id: 2, node: "jetson-a" }
]).map(([id, records]) => [id, records.length])`, context);
assert.deepEqual(JSON.parse(JSON.stringify(responseGrouping)), [["1", 2], ["2", 1]]);

const publication = vm.runInContext(`buildPublicationSvg({
  type: "bar",
  title: "Model <comparison>",
  subtitle: "same experiment & strategy",
  xLabel: "Model",
  yLabel: "Throughput",
  unit: "tok/s",
  strategy: "replicated_round_robin",
  labels: ["model-a", "model-b"],
  series: [{ label: "Throughput", values: [4.2, 5.1] }],
  runs: []
}, { width: 1004, sizeMm: 85, generatedAt: "2026-08-13T00:00:00Z" })`, context);

assert.match(publication.svg, /^<svg /);
assert.match(publication.svg, /width="85mm"/);
assert.match(publication.svg, /Model &lt;comparison&gt;/);
assert.doesNotMatch(publication.svg, /Model <comparison>/);
assert.match(publication.svg, /Throughput \(tok\/s\)/);
assert.match(publication.svg, /2026-08-13T00:00:00Z/);
assert.match(publication.svg, /font-size="(?:2[89]|3[01])\./); // 11 pt at 85 mm / 1004 units

const twoColumn = vm.runInContext(`buildPublicationSvg({
  type: "bar", title: "Two column", subtitle: "same physical font size",
  xLabel: "Model", yLabel: "Throughput", unit: "tok/s",
  strategy: "replicated_round_robin", labels: ["a"],
  series: [{ label: "Throughput", values: [5] }], runs: []
}, { width: 2126, sizeMm: 180 })`, context);
assert.match(twoColumn.svg, /width="180mm"/);
const oneTitleSize = Number(publication.svg.match(/font-size="([0-9.]+)"/)[1]);
const twoTitleSize = Number(twoColumn.svg.match(/font-size="([0-9.]+)"/)[1]);
assert.ok(Math.abs(oneTitleSize / 1004 * 85 - twoTitleSize / 2126 * 180) < 0.001);

const metadata = vm.runInContext(`publicationMetadata({ strategy: "replicated_round_robin", runs: [{
  model_id: "model.gguf", benchmark_parameters: { n_ctx: 4096, requested_n_gpu_layers: 30 },
  actual_model_config: [{ n_ctx: 2048, n_gpu_layers: 24 }, { n_ctx: 1024, n_gpu_layers: 20 }]
}] })`, context);
assert.match(metadata, /ctx 1024–2048/);
assert.match(metadata, /GPU layers 20–24/);

const rpcMetadata = vm.runInContext(`publicationMetadata({ strategy: "model_parallel_rpc", runs: [{
  model_id: "model.gguf", execution_strategy: "model_parallel_rpc",
  benchmark_parameters: { n_ctx: 2048, requested_n_gpu_layers: 30, effective_n_gpu_layers: "all" },
  topology: { requested_gpu_layers: "all" }
}] })`, context);
assert.match(rpcMetadata, /GPU layers all/);
assert.doesNotMatch(rpcMetadata, /GPU layers 30/);

const suiteOutcomes = vm.runInContext(`suiteOutcomeRuns({
  suite_id: "suite-partial", status: "partial", experiment_id: "exp-1", name: "suite",
  models: [
    { model_id: "a.gguf", model_index: 1, status: "completed", cleanup_status: "completed", errors: [] },
    { model_id: "b.gguf", model_index: 2, status: "failed", cleanup_status: "failed", errors: [{ error: "unload failed" }] },
    { model_id: "c.gguf", model_index: 3, status: "unrun", cleanup_status: "unrun", errors: [] }
  ]
}, [{ suite_id: "suite-partial", model_id: "a.gguf", model_index: 1, status: "completed", cluster_tokens_per_s: 3.2 }])`, context);
assert.equal(suiteOutcomes.length, 3);
assert.equal(suiteOutcomes[1].status, "failed");
assert.match(suiteOutcomes[1].error, /unload failed/);
assert.equal(suiteOutcomes[2].status, "unrun");

const signatureOne = vm.runInContext(`publicationComparisonSignature({
  execution_strategy: "single_node", nodes: ["head"],
  benchmark_parameters: { prompt_sha256: "abc", n_ctx: 1024, concurrency: 1, max_tokens: 32, seed: 42 },
  actual_model_config: [{ node: "head", n_ctx: 1024, n_gpu_layers: 30 }]
})`, context);
const signatureTwo = vm.runInContext(`publicationComparisonSignature({
  execution_strategy: "single_node", nodes: ["head"],
  benchmark_parameters: { prompt_sha256: "abc", n_ctx: 1024, concurrency: 8, max_tokens: 32, seed: 42 },
  actual_model_config: [{ node: "head", n_ctx: 1024, n_gpu_layers: 30 }]
})`, context);
assert.notEqual(signatureOne, signatureTwo);

const newestStandalone = vm.runInContext(`latestResultArtifact([
  { run_id: "20260813_130000_new", finished_at: "2026-08-13T13:00:00Z" }
], [
  { suite_id: "suite_old", updated_at: "2026-08-13T12:00:00Z" }
])`, context);
assert.equal(newestStandalone.suite, null);
assert.equal(newestStandalone.run.run_id, "20260813_130000_new");

const newestSuite = vm.runInContext(`latestResultArtifact([
  { run_id: "20260813_120000_old", finished_at: "2026-08-13T12:00:00Z" }
], [
  { suite_id: "suite_new", updated_at: "2026-08-13T13:00:00Z" }
])`, context);
assert.equal(newestSuite.suite.suite_id, "suite_new");

const topology = vm.runInContext(`(() => {
  state.nodes = [
    { name: "edge-head", role: "head", enabled: true, platform: "jetson" },
    { name: "jetson-1", role: "worker", enabled: true, platform: "jetson" },
    { name: "jetson-2", role: "worker", enabled: true, platform: "jetson" },
    { name: "jetson-3", role: "worker", enabled: true, platform: "jetson" },
    { name: "pi-1", role: "worker", enabled: true, platform: "raspberry-pi" },
    { name: "pi-2", role: "worker", enabled: true, platform: "raspberry-pi" },
    { name: "unknown-1", role: "worker", enabled: true, platform: "auto" }
  ];
  state.status = [];
  state.selectedNodes = new Set();
  reconcileSelection();
  const counts = platformNodeCounts();
  state.nodePlatformTab = "jetson";
  const jetsons = visibleTopologyNodes().map(node => node.name);
  state.nodePlatformTab = "raspberry-pi";
  const pis = visibleTopologyNodes().map(node => node.name);
  return {
    names: topologyNodes().map(node => node.name),
    selected: [...state.selectedNodes],
    counts,
    jetsons,
    pis
  };
})()`, context);
assert.equal(topology.names.length, 6);
assert.equal(topology.selected.length, 6);
assert.deepEqual(JSON.parse(JSON.stringify(topology.counts)), { all: 6, jetson: 3, "raspberry-pi": 2, unknown: 1 });
assert.deepEqual([...topology.jetsons], ["jetson-1", "jetson-2", "jetson-3"]);
assert.deepEqual([...topology.pis], ["pi-1", "pi-2"]);

const renamedNodeState = vm.runInContext(`(() => {
  state.nodes = [{ name: "worker-wrong-name", role: "worker", enabled: true }];
  state.selectedNodes = new Set(["worker-wrong-name"]);
  state.detailNode = "worker-wrong-name";
  state.renameNode = "worker-wrong-name";
  state.rpcCoordinatorNode = "worker-wrong-name";
  state.metricHistory = new Map([["worker-wrong-name", [{ cpu_pct: 12 }]]]);
  state.status = [{ name: "worker-wrong-name", api: true }];
  state.environment = [{ node: "worker-wrong-name", status: "ready" }];
  state.devices = [{ known_node: "worker-wrong-name" }];
  migrateRenamedNodeState("worker-wrong-name", "jetson-worker-02");
  return {
    selected: [...state.selectedNodes],
    detailNode: state.detailNode,
    renameNode: state.renameNode,
    rpcCoordinatorNode: state.rpcCoordinatorNode,
    historyNames: [...state.metricHistory.keys()],
    status: state.status,
    environment: state.environment,
    knownNode: state.devices[0].known_node
  };
})()`, context);
assert.deepEqual([...renamedNodeState.selected], ["jetson-worker-02"]);
assert.equal(renamedNodeState.detailNode, "jetson-worker-02");
assert.equal(renamedNodeState.renameNode, "jetson-worker-02");
assert.equal(renamedNodeState.rpcCoordinatorNode, "jetson-worker-02");
assert.deepEqual([...renamedNodeState.historyNames], ["jetson-worker-02"]);
assert.equal(renamedNodeState.status.length, 0);
assert.equal(renamedNodeState.environment.length, 0);
assert.equal(renamedNodeState.knownNode, "jetson-worker-02");

const wrappedLegend = vm.runInContext(`buildPublicationSvg({
  type: "line", title: "Many models", subtitle: "legend wrapping",
  xLabel: "Nodes", yLabel: "Throughput", unit: "tok/s", strategy: "node_sweep",
  labels: ["1", "2"],
  series: Array.from({ length: 10 }, (_, index) => ({ label: "long-model-name-" + index, values: [index + 1, index + 2] })),
  runs: []
}, { width: 640, sizeMm: 85 })`, context);
assert.ok(wrappedLegend.height > Math.round(640 * 0.64));
assert.match(wrappedLegend.svg, /long-model-name-9/);

const png = new Uint8Array(33);
png.set([137, 80, 78, 71, 13, 10, 26, 10]);
const encoded = context.pngBytesWithDpi(png.buffer, 300);
assert.equal(encoded.length, 54);
assert.equal(String.fromCharCode(...encoded.slice(37, 41)), "pHYs");
const view = new DataView(encoded.buffer, encoded.byteOffset, encoded.byteLength);
assert.equal(view.getUint32(41), Math.round(300 / 0.0254));
assert.equal(view.getUint32(45), Math.round(300 / 0.0254));
assert.equal(encoded[49], 1);

console.log("dashboard export fixtures: OK");
