#!/usr/bin/env node
/**
 * 将幻灯片 HTML 文件截图并拼合为总览图，同时执行布局溢出校验。
 *
 * 用法：
 *   node thumbnail.mjs <项目目录> [--out <输出目录>] [--width <宽度>] [--cols <列数>]
 *   node thumbnail.mjs <单个.html文件> [--out <输出目录>]
 *
 * 选项：
 *   --out <目录>    输出目录（默认：<项目目录>/thumbnails/）
 *   --width <px>    单张缩略图宽度（默认：400）
 *   --cols <n>      拼图列数（默认：3）
 *
 * 输出：
 *   - 每页单独截图（全分辨率）：thumbnails/slide_001.png, slide_002.png, ...
 *   - 拼合总览图：thumbnails/overview.png
 *   - 布局校验报告（终端输出）：检测文字/内容元素是否溢出 1280×720 画布
 *
 * 依赖：项目根目录已安装 playwright（导出工具的依赖）
 */

import { readFileSync, existsSync, mkdirSync, writeFileSync, unlinkSync } from "fs";
import { resolve, join, basename, dirname } from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";
import { homedir } from "os";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── 参数解析 ──────────────────────────────────────────────

const args = process.argv.slice(2);
if (!args.length || args[0] === "--help" || args[0] === "-h") {
  console.log(`用法：node thumbnail.mjs <项目目录|html文件> [--out <目录>] [--width <px>] [--cols <n>]`);
  process.exit(0);
}

const target = resolve(args[0]);

function getArg(flag, defaultVal) {
  const idx = args.indexOf(flag);
  return idx !== -1 ? args[idx + 1] : defaultVal;
}

const THUMB_WIDTH = parseInt(getArg("--width", "400"), 10);
const GRID_COLS = parseInt(getArg("--cols", "3"), 10);
const CANVAS_W = 1280;
const CANVAS_H = 720;
const THUMB_HEIGHT = Math.round(THUMB_WIDTH * CANVAS_H / CANVAS_W);

// ── 收集 HTML 文件列表 ────────────────────────────────────

let htmlFiles = [];   // [{ file: string, index: number, label: string }]
let outDir;

if (target.endsWith(".html") && existsSync(target)) {
  htmlFiles = [{ file: target, index: 1, label: basename(target) }];
  outDir = resolve(getArg("--out", join(dirname(target), "thumbnails")));
} else if (existsSync(target)) {
  const pjsonPath = join(target, "presentation.json");
  if (!existsSync(pjsonPath)) {
    console.error(`✗ 未找到 presentation.json：${pjsonPath}`);
    process.exit(1);
  }
  const pjson = JSON.parse(readFileSync(pjsonPath, "utf-8"));
  const slides = pjson.slides ?? [];
  htmlFiles = slides
    .map((s, i) => ({
      file: join(target, "slides", s.file),
      index: i + 1,
      label: s.title ?? s.file,
    }))
    .filter(({ file }) => existsSync(file));
  outDir = resolve(getArg("--out", join(target, "thumbnails")));
} else {
  console.error(`✗ 路径不存在：${target}`);
  process.exit(1);
}

if (!htmlFiles.length) {
  console.error("✗ 没有找到任何 HTML 文件");
  process.exit(1);
}

mkdirSync(outDir, { recursive: true });

// ── 加载 Playwright ───────────────────────────────────────

// 让 Playwright 使用系统标准缓存目录，而非沙箱路径（仅 macOS 需要）
if (process.platform === "darwin") {
  process.env.PLAYWRIGHT_BROWSERS_PATH = join(homedir(), "Library", "Caches", "ms-playwright");
}

// 优先查找工作区根目录的 playwright（和 export-pptx.mjs 共享依赖）
const searchRoots = [
  resolve(__dirname, "../../../node_modules"),   // 工作区根
  resolve(__dirname, "../../node_modules"),      // packages/
  resolve(__dirname, "../node_modules"),         // skill 包内
];

let chromium;
for (const root of searchRoots) {
  const candidate = join(root, "playwright");
  if (existsSync(candidate)) {
    try {
      const req = createRequire(join(root, "dummy.js"));
      ({ chromium } = req("playwright"));
      break;
    } catch {
      // 继续尝试下一个路径
    }
  }
}

if (!chromium) {
  console.error("✗ 未找到 playwright，请先安装：npm install playwright && npx playwright install chromium");
  process.exit(1);
}

// ── 截图（同时保存单页 png 和内存 buffer）────────────────

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: { width: CANVAS_W, height: CANVAS_H },
  deviceScaleFactor: 1,
});
const page = await context.newPage();

// slides: [{ buffer: Buffer, index: number, label: string, slidePath: string }]
const slides = [];
// layoutIssues: [{ index: number, label: string, issues: string[] }]
const layoutIssues = [];

for (let i = 0; i < htmlFiles.length; i++) {
  const { file, index, label } = htmlFiles[i];

  process.stdout.write(`  截图 [${i + 1}/${htmlFiles.length}] ${index}. ${label} ... `);

  await page.goto(`file://${file}`, { waitUntil: "networkidle", timeout: 15000 });
  await page.waitForTimeout(300);

  // ── 布局溢出校验 ──
  const issues = await page.evaluate(({ canvasW, canvasH }) => {
    const found = [];

    const isDecorativeAncestor = (el) => {
      let cur = el;
      while (cur && cur !== document.body) {
        const z = parseInt(getComputedStyle(cur).zIndex, 10);
        if (z <= 0 && cur.classList.contains("absolute")) return true;
        cur = cur.parentElement;
      }
      return false;
    };

    const hasDirectText = (el) => {
      for (const node of el.childNodes) {
        if (node.nodeType === Node.TEXT_NODE && node.textContent.trim().length > 0) return true;
      }
      return false;
    };

    const contentTags = new Set([
      "P", "SPAN", "H1", "H2", "H3", "H4", "H5", "H6",
      "LI", "TD", "TH", "LABEL", "A", "STRONG", "EM", "B", "I",
      "BLOCKQUOTE", "FIGCAPTION", "DT", "DD", "CAPTION",
    ]);

    const mediaTags = new Set(["IMG", "SVG", "VIDEO", "CANVAS"]);

    const allElements = document.body.querySelectorAll("*");

    for (const el of allElements) {
      const isText = hasDirectText(el) || contentTags.has(el.tagName);
      const isMedia = mediaTags.has(el.tagName);

      if (!isText && !isMedia) continue;

      if (isDecorativeAncestor(el)) continue;

      const text = el.textContent?.trim() || "";
      if (!isMedia && text.length === 0) continue;

      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;

      const overflows = [];
      if (rect.bottom > canvasH + 1) {
        overflows.push(`底部溢出 ${Math.round(rect.bottom - canvasH)}px`);
      }
      if (rect.right > canvasW + 1) {
        overflows.push(`右侧溢出 ${Math.round(rect.right - canvasW)}px`);
      }
      if (rect.top < -1) {
        overflows.push(`顶部溢出 ${Math.round(-rect.top)}px`);
      }
      if (rect.left < -1) {
        overflows.push(`左侧溢出 ${Math.round(-rect.left)}px`);
      }

      if (overflows.length === 0) continue;

      const snippet = isMedia
        ? `<${el.tagName.toLowerCase()}>`
        : text.slice(0, 40) + (text.length > 40 ? "…" : "");

      found.push(`${overflows.join("、")} — "${snippet}"`);
    }

    // 检查容器是否有被 overflow:hidden 裁剪的内容
    const containers = document.body.querySelectorAll("*");
    for (const el of containers) {
      const style = getComputedStyle(el);
      if (style.overflow !== "hidden" && style.overflowY !== "hidden") continue;
      if (el.scrollHeight <= el.clientHeight + 2) continue;
      if (isDecorativeAncestor(el)) continue;

      const text = el.textContent?.trim() || "";
      if (text.length === 0) continue;

      const clipped = el.scrollHeight - el.clientHeight;
      const snippet = text.slice(0, 40) + (text.length > 40 ? "…" : "");
      found.push(`容器内容被裁剪 ${clipped}px（overflow:hidden）— "${snippet}"`);
    }

    return found;
  }, { canvasW: CANVAS_W, canvasH: CANVAS_H });

  if (issues.length > 0) {
    layoutIssues.push({ index, label, issues });
  }

  const buffer = await page.screenshot({
    clip: { x: 0, y: 0, width: CANVAS_W, height: CANVAS_H },
  });

  // 保存单页全分辨率截图，文件名与幻灯片序号对应
  const slideFilename = `slide_${String(index).padStart(3, "0")}.png`;
  const slidePath = join(outDir, slideFilename);
  writeFileSync(slidePath, buffer);

  slides.push({ buffer, index, label, slidePath });
  console.log(issues.length > 0 ? `✓ (${issues.length} 个布局问题)` : "✓");
}

await browser.close();

// ── 生成总览图 ────────────────────────────────────────────

const overviewPath = join(outDir, "overview.png");
await buildOverview(slides, overviewPath);

console.log(`\n✓ 单页截图已保存至：${outDir}/slide_NNN.png`);
console.log(`✓ 总览图已保存：${overviewPath}`);

// ── 布局校验报告 ──────────────────────────────────────────

if (layoutIssues.length > 0) {
  console.log(`\n⚠ 布局校验发现 ${layoutIssues.length} 页存在溢出问题：\n`);
  for (const { index, label, issues } of layoutIssues) {
    console.log(`  第 ${index} 页「${label}」— ${issues.length} 个问题：`);
    for (const issue of issues) {
      console.log(`    • ${issue}`);
    }
    console.log();
  }
  console.log("  请检查上述页面，修复溢出后重新运行截图校验。可以考虑减小字体大小、缩减内外边距或者删减内容。");
} else {
  console.log("\n✓ 布局校验通过：所有页面内容均在 1280×720 画布范围内");
}

// ── 拼图函数 ──────────────────────────────────────────────

async function buildOverview(slides, outputPath) {
  for (const root of searchRoots) {
    try {
      const req = createRequire(join(root, "dummy.js"));
      const sharp = req("sharp");
      await buildOverviewWithSharp(slides, outputPath, sharp);
      return;
    } catch { /* 继续尝试下一个路径 */ }
  }

  // fallback：用 Playwright 渲染一张内嵌所有缩略图的 HTML 页面
  await buildOverviewWithPlaywright(slides, outputPath);
}

async function buildOverviewWithSharp(slides, outputPath, sharp) {
  const PADDING = 16;
  const BADGE_SIZE = 36;   // 序号角标高度
  const cols = GRID_COLS;
  const rows = Math.ceil(slides.length / cols);

  const cellW = THUMB_WIDTH + PADDING;
  const cellH = THUMB_HEIGHT + PADDING;
  const totalW = cols * cellW + PADDING;
  const totalH = rows * cellH + PADDING;

  const base = sharp({
    create: { width: totalW, height: totalH, channels: 3, background: { r: 245, g: 245, b: 245 } },
  }).png();

  const composites = [];
  for (let i = 0; i < slides.length; i++) {
    const { buffer, index } = slides[i];
    const col = i % cols;
    const row = Math.floor(i / cols);
    const x = PADDING + col * cellW;
    const y = PADDING + row * cellH;

    const thumb = await sharp(buffer)
      .resize(THUMB_WIDTH, THUMB_HEIGHT, { fit: "cover" })
      .toBuffer();

    composites.push({ input: thumb, left: x, top: y });

    // 序号角标：白底圆角矩形 + 黑色粗体数字，叠加在缩略图左上角
    const numStr = String(index);
    const badgeW = Math.max(BADGE_SIZE, numStr.length * 18 + 16);
    const badgeSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="${badgeW}" height="${BADGE_SIZE}">
      <rect x="0" y="0" width="${badgeW}" height="${BADGE_SIZE}" rx="6" ry="6"
            fill="white" opacity="0.92"/>
      <text x="${badgeW / 2}" y="${BADGE_SIZE * 0.72}"
            font-family="Arial,sans-serif" font-size="20" font-weight="bold"
            fill="#111" text-anchor="middle">${numStr}</text>
    </svg>`;
    const badgeBuf = Buffer.from(badgeSvg);
    composites.push({ input: badgeBuf, left: x + 8, top: y + 8 });
  }

  await base.composite(composites).toFile(outputPath);
}

async function buildOverviewWithPlaywright(slides, outputPath) {
  const cols = GRID_COLS;
  const PADDING = 12;
  const totalW = cols * (THUMB_WIDTH + PADDING) + PADDING;
  const rows = Math.ceil(slides.length / cols);
  const totalH = rows * (THUMB_HEIGHT + PADDING) + PADDING;

  const items = slides.map(({ buffer, index, label }, i) => {
    const b64 = buffer.toString("base64");
    const col = i % cols;
    const row = Math.floor(i / cols);
    const x = PADDING + col * (THUMB_WIDTH + PADDING);
    const y = PADDING + row * (THUMB_HEIGHT + PADDING);
    return `
      <div style="position:absolute;left:${x}px;top:${y}px;width:${THUMB_WIDTH}px;">
        <img src="data:image/png;base64,${b64}"
             width="${THUMB_WIDTH}" height="${THUMB_HEIGHT}"
             title="${label}"
             style="display:block;border:1px solid #ddd;border-radius:4px;" />
        <div style="
          position:absolute;left:8px;top:8px;
          background:rgba(255,255,255,0.92);
          color:#111;font:bold 20px/36px Arial,sans-serif;
          min-width:36px;height:36px;padding:0 8px;
          border-radius:6px;text-align:center;
          box-shadow:0 1px 4px rgba(0,0,0,0.18);
          white-space:nowrap;
        ">${index}</div>
      </div>`;
  }).join("\n");

  const html = `<!DOCTYPE html><html><body style="margin:0;background:#f5f5f5;position:relative;width:${totalW}px;height:${totalH}px;">${items}</body></html>`;

  const tmpHtml = join(outDir, "_overview_tmp.html");
  writeFileSync(tmpHtml, html, "utf-8");

  const browser2 = await chromium.launch();
  const ctx2 = await browser2.newContext({ viewport: { width: totalW, height: totalH } });
  const pg2 = await ctx2.newPage();
  await pg2.goto(`file://${tmpHtml}`, { waitUntil: "load" });
  await pg2.screenshot({ path: outputPath, clip: { x: 0, y: 0, width: totalW, height: totalH } });
  await browser2.close();

  try { unlinkSync(tmpHtml); } catch { /* ignore */ }
}
