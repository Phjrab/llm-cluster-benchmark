const base = require('./playwright.config');
const port = Number(process.env.DASHBOARD_TEST_PORT || 46178);
const url = `http://127.0.0.1:${port}`;
module.exports = {
  ...base,
  testMatch: 'typography.spec.js',
  use: { ...base.use, baseURL: url },
  webServer: {
    ...base.webServer,
    command: 'node scripts/ui-redesign/serve-typography.js',
    url,
    env: { ...process.env, DASHBOARD_TEST_PORT: String(port) },
  },
};
