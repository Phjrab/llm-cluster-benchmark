#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const process = require("node:process");
const { chromium } = require("playwright");
const playwrightVersion = require("playwright/package.json").version;

function usage() {
  throw new Error("usage: render_publication_png.js --figures <directory> [--dpi 300,600] [--width-mm 180]");
}

function parseArgs(argv) {
  const args = { dpi: [300, 600], widthMm: 180, figures: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--figures") args.figures = argv[++index] || "";
    else if (value === "--dpi") args.dpi = (argv[++index] || "").split(",").map(Number);
    else if (value === "--width-mm") args.widthMm = Number(argv[++index]);
    else usage();
  }
  if (!args.figures || !Number.isFinite(args.widthMm) || args.widthMm <= 0) usage();
  if (!args.dpi.length || args.dpi.some(value => !Number.isInteger(value) || value < 72 || value > 1200)) usage();
  return args;
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const chunk = Buffer.alloc(12 + data.length);
  chunk.writeUInt32BE(data.length, 0);
  typeBuffer.copy(chunk, 4);
  data.copy(chunk, 8);
  chunk.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 8 + data.length);
  return chunk;
}

function injectDpi(png, dpi) {
  const signature = png.subarray(0, 8);
  const chunks = [];
  let offset = 8;
  while (offset < png.length) {
    const length = png.readUInt32BE(offset);
    const type = png.toString("ascii", offset + 4, offset + 8);
    const end = offset + 12 + length;
    if (type !== "pHYs") chunks.push(png.subarray(offset, end));
    if (type === "IHDR") {
      const data = Buffer.alloc(9);
      const pixelsPerMetre = Math.round(dpi / 0.0254);
      data.writeUInt32BE(pixelsPerMetre, 0);
      data.writeUInt32BE(pixelsPerMetre, 4);
      data[8] = 1;
      chunks.push(pngChunk("pHYs", data));
    }
    offset = end;
  }
  return Buffer.concat([signature, ...chunks]);
}

function svgDimensions(svg) {
  const match = svg.match(/viewBox=["']\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s*["']/i);
  if (!match) throw new Error("SVG lacks a numeric viewBox");
  const width = Number(match[3]);
  const height = Number(match[4]);
  if (!(width > 0 && height > 0)) throw new Error("SVG viewBox is invalid");
  return { width, height };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const figures = path.resolve(args.figures);
  const stat = fs.lstatSync(figures);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error("figures must be a real directory");
  const svgFiles = fs.readdirSync(figures).filter(name => name.endsWith(".svg")).sort();
  if (!svgFiles.length) throw new Error("no SVG figures found");
  const browser = await chromium.launch({ headless: true });
  const browserVersion = browser.version();
  const outputs = [];
  try {
    for (const dpi of args.dpi) {
      const outputDirectory = path.join(figures, `png-${dpi}dpi`);
      fs.mkdirSync(outputDirectory, { recursive: false, mode: 0o700 });
      fs.chmodSync(outputDirectory, 0o700);
      for (const filename of svgFiles) {
        const svg = fs.readFileSync(path.join(figures, filename), "utf8");
        const dimensions = svgDimensions(svg);
        const pixelWidth = Math.round(args.widthMm / 25.4 * dpi);
        const pixelHeight = Math.round(pixelWidth * dimensions.height / dimensions.width);
        const page = await browser.newPage({ viewport: { width: pixelWidth, height: pixelHeight }, deviceScaleFactor: 1 });
        try {
          await page.setContent(`<style>html,body{margin:0;background:#fff;width:${pixelWidth}px;height:${pixelHeight}px;overflow:hidden}svg{display:block;width:${pixelWidth}px!important;height:${pixelHeight}px!important}</style>${svg}`, { waitUntil: "load" });
          const raw = await page.locator("svg").screenshot({ type: "png", omitBackground: false, animations: "disabled" });
          const png = injectDpi(raw, dpi);
          const output = path.join(outputDirectory, filename.replace(/\.svg$/i, ".png"));
          fs.writeFileSync(output, png, { mode: 0o600 });
          fs.chmodSync(output, 0o600);
          outputs.push({ dpi, width_mm: args.widthMm, width_px: pixelWidth, height_px: pixelHeight, path: path.relative(figures, output).split(path.sep).join("/") });
        } finally {
          await page.close();
        }
      }
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(`${JSON.stringify({ status: "completed", renderer: "playwright-chromium", playwright_version: playwrightVersion, browser_version: browserVersion, outputs })}\n`);
}

main().catch(error => {
  process.stderr.write(`render-publication-png: ${error.message}\n`);
  process.exitCode = 2;
});
