#!/usr/bin/env node
/**
 * Lucide 图标校验与自动修复工具 — 扫描幻灯片 HTML 中的图标引用，修复不存在的图标名。
 *
 * 用法:
 *   validate-icons.mjs <项目目录>            # 检测并自动修复
 *   validate-icons.mjs <项目目录> --dry-run  # 仅检测，不修改文件
 *
 * 工作原理:
 *   1. 从 unpkg CDN 拉取 Lucide 最新图标名列表（失败时使用本地缓存）
 *   2. 扫描 slides/ 目录下所有 HTML 文件，提取 data-lucide="xxx" 中的图标名
 *   3. 对不存在的图标名，用编辑距离模糊匹配最接近的有效图标名
 *   4. 编辑距离 ≤ 3 的自动替换；> 3 的也替换但标记"建议二次确认"
 *
 * 退出码:
 *   0 — 所有图标均有效（或已全部自动修复且无需二次确认），
 *       或 slides 目录/HTML 文件不存在、无法获取图标列表时静默跳过
 *   1 — 存在需要二次确认的修复项
 *   2 — 运行错误（项目目录不存在）
 *
 * 示例:
 *   node validate-icons.mjs ./my-ppt
 *   node validate-icons.mjs ./my-ppt --dry-run
 */

import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const ICON_LIST_URL = "https://unpkg.com/lucide-static@latest/icon-nodes.json";
const FETCH_TIMEOUT_MS = 10_000;
const CACHE_FILENAME = ".lucide-icon-cache.json";
const LUCIDE_ATTR_RE = /data-lucide\s*=\s*"([^"]+)"/g;
const AUTO_FIX_THRESHOLD = 3;

// ── 图标列表获取 ─────────────────────────────────────────────────────────────

async function fetchIconNames() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    const resp = await fetch(ICON_LIST_URL, { signal: controller.signal });
    clearTimeout(timer);
    if (!resp.ok) return null;
    const data = await resp.json();
    return new Set(Object.keys(data));
  } catch {
    return null;
  }
}

function loadCache() {
  const cachePath = join(__dirname, CACHE_FILENAME);
  if (!existsSync(cachePath)) return null;
  try {
    return new Set(JSON.parse(readFileSync(cachePath, "utf-8")));
  } catch {
    return null;
  }
}

function saveCache(icons) {
  const cachePath = join(__dirname, CACHE_FILENAME);
  try {
    writeFileSync(cachePath, JSON.stringify([...icons].sort()), "utf-8");
  } catch {
    // 写缓存失败不影响主流程
  }
}

async function getIconNames() {
  const icons = await fetchIconNames();
  if (icons) {
    saveCache(icons);
    return icons;
  }

  process.stderr.write("⚠ 无法从 CDN 获取图标列表，尝试使用本地缓存...\n");
  const cached = loadCache();
  if (cached) {
    process.stderr.write(`  使用缓存（${cached.size} 个图标）\n`);
    return cached;
  }

  process.stderr.write("⚠ 无法获取图标列表且无本地缓存，跳过图标校验。\n");
  return null;
}

// ── 字符串相似度 ─────────────────────────────────────────────────────────────

/** Levenshtein 编辑距离 */
function editDistance(a, b) {
  if (a.length < b.length) return editDistance(b, a);
  if (b.length === 0) return a.length;

  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 0; i < a.length; i++) {
    const curr = [i + 1];
    for (let j = 0; j < b.length; j++) {
      const cost = a[i] === b[j] ? 0 : 1;
      curr.push(Math.min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost));
    }
    prev = curr;
  }
  return prev[b.length];
}

/**
 * 类似 Python difflib.get_close_matches：
 * 基于 Jaro-Winkler 近似的简单相似度过滤（用编辑距离比例实现）。
 */
function getCloseMatches(word, possibilities, n = 5, cutoff = 0.4) {
  const scored = [];
  for (const p of possibilities) {
    const maxLen = Math.max(word.length, p.length);
    if (maxLen === 0) continue;
    const sim = 1 - editDistance(word, p) / maxLen;
    if (sim >= cutoff) scored.push({ name: p, sim });
  }
  scored.sort((a, b) => b.sim - a.sim);
  return scored.slice(0, n).map((x) => x.name);
}

/**
 * 生成名称的重排变体，覆盖 Lucide 常见的命名重构模式：
 *   user-circle  → circle-user     （整体反转）
 *   bar-chart-3  → chart-bar-3     （前缀反转 + 数字后缀保留）
 *   arrow-up-right → right-up-arrow（整体反转）
 */
function nameVariants(name) {
  const parts = name.split("-");
  const variants = new Set([name]);

  // 整体反转
  variants.add(parts.slice().reverse().join("-"));

  // 带数字后缀时：前缀反转 + 后缀保留（bar-chart-3 → chart-bar-3）
  if (parts.length >= 3 && /^\d+$/.test(parts[parts.length - 1])) {
    const suffix = parts[parts.length - 1];
    const prefix = parts.slice(0, -1);
    variants.add([...prefix.reverse(), suffix].join("-"));
  }

  return [...variants];
}

/** 为无效图标名找到最接近的有效图标名，返回 { best, dist }。 */
function findBestMatch(invalidName, validIcons) {
  const variants = nameVariants(invalidName);

  // 第一层：精确匹配（某个重排变体本身就是有效图标）
  for (const v of variants) {
    if (validIcons.has(v)) {
      return { best: v, dist: 0 };
    }
  }

  // 第二层：对所有变体做模糊匹配，汇总候选
  // 距离取候选与所有变体中的最小值（因为匹配可能经由变体找到）
  const allCandidates = new Map();
  for (const v of variants) {
    for (const m of getCloseMatches(v, validIcons, 5, 0.4)) {
      const d = Math.min(...variants.map((vv) => editDistance(vv, m)));
      if (!allCandidates.has(m) || d < allCandidates.get(m)) {
        allCandidates.set(m, d);
      }
    }
  }

  // 第三层：仍无结果则全量扫描取编辑距离最小值（同样考虑变体）
  if (allCandidates.size === 0) {
    let best = null;
    let bestDist = Infinity;
    for (const icon of validIcons) {
      const d = Math.min(...variants.map((v) => editDistance(v, icon)));
      if (d < bestDist) {
        bestDist = d;
        best = icon;
      }
    }
    return { best, dist: bestDist };
  }

  let best = null;
  let bestDist = Infinity;
  for (const [name, d] of allCandidates) {
    if (d < bestDist) {
      bestDist = d;
      best = name;
    }
  }
  return { best, dist: bestDist };
}

// ── 扫描与修复 ───────────────────────────────────────────────────────────────

/** 扫描并修复。返回 { fileCount, autoCount, manualCount }。 */
function scanAndFix(projectDir, validIcons, dryRun) {
  const slidesDir = join(projectDir, "slides");
  if (!existsSync(slidesDir)) {
    process.stderr.write(`⚠ slides 目录不存在: ${slidesDir}，请检查目录。\n`);
    return { fileCount: 0, autoCount: 0, manualCount: 0 };
  }

  const htmlFiles = readdirSync(slidesDir)
    .filter((f) => f.endsWith(".html"))
    .sort();

  if (htmlFiles.length === 0) {
    process.stderr.write("⚠ slides 目录中没有 HTML 文件，请检查目录。\n");
    return { fileCount: 0, autoCount: 0, manualCount: 0 };
  }

  let totalAuto = 0;
  let totalManual = 0;

  for (const filename of htmlFiles) {
    const filepath = join(slidesDir, filename);
    const content = readFileSync(filepath, "utf-8");

    const usedIcons = [...content.matchAll(LUCIDE_ATTR_RE)].map((m) => m[1]);
    if (usedIcons.length === 0) continue;

    const invalidIcons = [...new Set(usedIcons.filter((icon) => !validIcons.has(icon)))];
    if (invalidIcons.length === 0) continue;

    const replacements = new Map();
    for (const iconName of invalidIcons) {
      const { best, dist } = findBestMatch(iconName, validIcons);
      const needsConfirm = dist > AUTO_FIX_THRESHOLD;
      replacements.set(iconName, { best, dist, needsConfirm });

      const marker = needsConfirm ? " ⚠ 建议二次确认" : "";
      const action = dryRun ? "→ 建议替换为" : "→ 已替换为";
      console.log(`  ${filename}: "${iconName}" ${action} "${best}" (距离: ${dist})${marker}`);

      if (needsConfirm) totalManual++;
      else totalAuto++;
    }

    if (!dryRun) {
      let newContent = content;
      for (const [oldName, { best }] of replacements) {
        newContent = newContent.replaceAll(`data-lucide="${oldName}"`, `data-lucide="${best}"`);
      }
      writeFileSync(filepath, newContent, "utf-8");
    }
  }

  return { fileCount: htmlFiles.length, autoCount: totalAuto, manualCount: totalManual };
}

// ── 入口 ─────────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);

if (args.length === 0 || args[0] === "-h" || args[0] === "--help") {
  const self = readFileSync(fileURLToPath(import.meta.url), "utf-8");
  // 提取开头 /** ... */ 块注释
  const doc = self.match(/^\/\*\*([\s\S]*?)\*\//m)?.[1] ?? "";
  console.log(doc.replace(/^ \* ?/gm, "").trim());
  process.exit(0);
}

const projectDir = args[0];
const dryRun = args.includes("--dry-run");

if (!existsSync(projectDir)) {
  process.stderr.write(`✗ 项目目录不存在: ${projectDir}\n`);
  process.exit(2);
}

const validIcons = await getIconNames();
if (!validIcons) {
  process.exit(0);
}
const { fileCount, autoCount, manualCount } = scanAndFix(projectDir, validIcons, dryRun);

const totalFixes = autoCount + manualCount;
if (totalFixes === 0) {
  console.log(`✓ 共检查 ${fileCount} 个文件，所有图标均有效`);
  process.exit(0);
}

const mode = dryRun ? "检测到" : "修复";
console.log(`\n${"─".repeat(40)}`);
console.log(`共检查 ${fileCount} 个文件，${mode} ${totalFixes} 个无效图标`);
if (autoCount) console.log(`  ✓ 自动${mode}: ${autoCount} 个`);
if (manualCount) {
  console.log(`  ⚠ 需二次确认: ${manualCount} 个（编辑距离 > ${AUTO_FIX_THRESHOLD}，语义可能不匹配）`);
}

process.exit(manualCount > 0 ? 1 : 0);
