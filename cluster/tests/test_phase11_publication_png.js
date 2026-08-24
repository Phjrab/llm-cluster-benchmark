#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const root = path.resolve(__dirname, "../..");
const renderer = path.join(root, "scripts/research/render_publication_png.js");
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "phase11-png-"));

function pngChunks(buffer) {
  const chunks = [];
  let offset = 8;
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    chunks.push({ type, data: buffer.subarray(offset + 8, offset + 8 + length) });
    offset += length + 12;
  }
  return chunks;
}

try {
  const figures = path.join(temporary, "figures");
  fs.mkdirSync(figures, { mode: 0o700 });
  fs.writeFileSync(path.join(figures, "fixture.svg"), `<svg xmlns="http://www.w3.org/2000/svg" width="180mm" height="90mm" viewBox="0 0 1400 700"><rect width="1400" height="700" fill="#fff"/><line x1="100" y1="600" x2="1300" y2="100" stroke="#0072B2" stroke-width="8"/><text x="100" y="80" font-family="Arial" font-size="40">Fixture</text></svg>`, { mode: 0o600 });
  const result = spawnSync(process.execPath, [renderer, "--figures", figures, "--dpi", "300,600", "--width-mm", "180"], {
    cwd: root,
    encoding: "utf8",
    timeout: 120000,
  });
  assert.equal(result.status, 0, result.stderr);
  const metadata = JSON.parse(result.stdout);
  assert.equal(metadata.status, "completed");
  assert.equal(metadata.renderer, "playwright-chromium");
  assert.equal(metadata.outputs.length, 2);
  for (const expectedDpi of [300, 600]) {
    const item = metadata.outputs.find(output => output.dpi === expectedDpi);
    assert.ok(item);
    assert.equal(item.width_px, Math.round(180 / 25.4 * expectedDpi));
    assert.equal(item.height_px, Math.round(item.width_px / 2));
    const output = path.join(figures, item.path);
    const png = fs.readFileSync(output);
    assert.equal(png.subarray(1, 4).toString("ascii"), "PNG");
    const chunks = pngChunks(png);
    const header = chunks.find(chunk => chunk.type === "IHDR");
    const physical = chunks.find(chunk => chunk.type === "pHYs");
    assert.ok(header);
    assert.ok(physical);
    assert.equal(header.data.readUInt32BE(0), item.width_px);
    assert.equal(header.data.readUInt32BE(4), item.height_px);
    assert.equal(physical.data.readUInt32BE(0), Math.round(expectedDpi / 0.0254));
    assert.equal(physical.data.readUInt32BE(4), Math.round(expectedDpi / 0.0254));
    assert.equal(physical.data[8], 1);
    assert.equal(fs.statSync(output).mode & 0o777, 0o600);
  }
  process.stdout.write("phase11 publication PNG fixtures: OK\n");
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
