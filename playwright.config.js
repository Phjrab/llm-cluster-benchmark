const path = require("node:path");
const { defineConfig } = require("@playwright/test");

const projectRoot = __dirname;
const artifactRoot = path.join(projectRoot, ".artifacts", "playwright");

module.exports = defineConfig({
  testDir: path.join(projectRoot, "cluster", "tests", "e2e"),
  outputDir: path.join(artifactRoot, "test-results"),
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ["line"],
    ["html", { outputFolder: path.join(artifactRoot, "report"), open: "never" }],
    ["junit", { outputFile: path.join(artifactRoot, "junit.xml") }],
  ],
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
    headless: true,
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: ".venv/bin/python -m uvicorn cluster.dashboard.app:app --host 127.0.0.1 --port 4173 --no-access-log",
    cwd: projectRoot,
    url: "http://127.0.0.1:4173/dashboard/health",
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      ...process.env,
      PYTHONPATH: projectRoot,
      CLUSTER_RUNTIME_DIR: path.join(artifactRoot, "runtime"),
      CLUSTER_RESULTS_DIR: path.join(artifactRoot, "results"),
      CLUSTER_INVENTORY: path.join(artifactRoot, "runtime", "nodes.local.csv"),
    },
  },
});
