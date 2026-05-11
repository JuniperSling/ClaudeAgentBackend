---
name: aippt-maker
description: >-
  Generate professional PowerPoint presentations locally. Use when the user asks
  to make a PPT, create slides, build a deck, generate a slideshow, design a
  keynote, make a presentation, or produce report/proposal materials. Also
  applies to incremental edits: modifying a single slide, changing theme or
  color scheme, adding or removing pages, or exporting existing content to
  .pptx. Trigger this skill for ANY slide or PPT creation and editing task.
---

# AI PPT Maker Skill

> [!IMPORTANT]
> ## 🌐 Language & Communication Rule
>
> - **Response language**: Always match the language of the user's input. If the user writes in Chinese, respond in Chinese; if in English, respond in English.
> - **Explicit override**: If the user requests a specific language (e.g. "reply in English" / "请用中文回答"), use that language instead.

---

## Conventions

- **SKILL_DIR**: The directory containing this SKILL.md file — no need to locate it separately.
- **`dist/export-pptx.mjs`** is a ~2 MB compiled bundle. Reading it wastes tokens and the content is not human-readable. Execute it directly with `node`.

## Reference Documents

| Document | Path | When to Read |
|----------|------|--------------|
| Research Collection Guide | `references/research-collection.md` | MUST read before generating `research.md` |
| HTML Technical Spec | `references/html-spec.md` | MUST read before generating any HTML slide |
| PPT Content Design Guide | `references/ppt-design.md` | Read when planning outline and `content_spec` |
| Slide Generation Guide | `references/slide-generation.md` | Read before Step 6 to choose serial vs. parallel mode |

---

## How It Works

This skill uses a **Research → HTML → PPTX** three-stage pipeline:

1. **`research.md` as the unified content layer** — Before drafting the outline, consolidate all content material into `<project_dir>/research.md`. This applies whether the source is web research results, user-provided files, or AI-organized knowledge. Downstream outline planning and HTML generation always pull from this file — never from context memory.

2. **`presentation.json` as the single source of truth** — Records the title, theme, per-slide metadata, and file mappings. All create/read/update/delete operations go through this file, ensuring cross-slide consistency and reliable file discovery during export.

3. **HTML as the intermediate representation** — Each slide is a standalone HTML file (1280×720 canvas) using Tailwind CSS + Lucide icons for layout. HTML is chosen over direct PPTX manipulation because LLMs generate HTML/CSS naturally, the visual expressiveness far exceeds raw PPTX XML, and slides can be previewed directly in a browser.

4. **Per-slide isolated generation** — Each HTML file is generated independently with no shared context between slides. Style consistency is enforced solely through the `theme` field. Default mode is serial (one slide at a time); parallel mode (sub-agents) is only enabled when the current platform is confirmed to support sub-agent file writes (e.g. Claude Code).

---

## Project File Structure

```
<project_dir>/
├── research.md          ← Content layer — single source for all slide content
├── presentation.json    ← State file — slide registry and metadata
└── slides/
    ├── slide_001.html
    ├── slide_002.html
    └── ...
```

### `presentation.json` Format

```json
{
  "title": "Presentation Title",
  "theme": "Natural-language style description (see ppt-design.md)",
  "slides": [
    {
      "file": "slide_001.html",
      "type": "title | section | content",
      "title": "Slide Title",
      "content_spec": "What this slide covers and how it is structured (see below)",
      "local_theme": "Optional — describe special visual treatment for this slide only (e.g. cover uses a contrasting color, data slide uses dark background)"
    }
  ]
}
```

- The **order** of the `slides` array defines slide order. The export tool processes slides in array order.
- `file` is a stable identifier — **never rename a file after it is created**. To add or remove slides, modify only the JSON array.
- Naming rule: `slide_<three-digit-index>.html` (e.g. `slide_001.html`). Indices are monotonically increasing and are never reused after deletion.
- If `content_spec` or other field values contain ASCII double-quotes (`"`), escape them to prevent JSON parse failures.

---

## `content_spec`: Slide Content Summary

`content_spec` describes **what specific points this slide presents** — list the topical directions, but leave the actual copy and data to be drawn from `research.md` at generation time. Core principle: say clearly "what this slide is about and how it is divided", without hard-coding copy or specifying layout.

See [references/ppt-design.md](references/ppt-design.md) for writing conventions and examples.

---

## Workflow

> [!CAUTION]
> ## 🚨 Global Execution Discipline (MANDATORY)
>
> **The following rules have the highest priority. Violating any one of them constitutes execution failure:**
>
> 1. **SERIAL EXECUTION** — Steps MUST be executed in order. Non-BLOCKING adjacent steps may proceed continuously once prerequisites are met, without waiting for the user to say "continue".
> 2. **⛔ BLOCKING = HARD STOP** — Steps marked ⛔ BLOCKING require a full stop. The AI MUST wait for an explicit user response and MUST NOT make any decisions on the user's behalf.
> 3. **NO SPECULATIVE EXECUTION** — Pre-generating content for a later step while still executing an earlier step is FORBIDDEN (e.g. writing slide HTML during outline planning).
> 4. **GATE BEFORE ENTRY** — Each step lists its prerequisites (🚧 GATE). These MUST be verified before starting that step.
> 5. **DECLARE SUCCESS ONLY AFTER VERIFICATION** — Do not declare a step complete until its checkpoint conditions are confirmed.

---

### Core Workflow (Full Generation)

---

#### Step 1: Gather Requirements

🚧 **GATE**: User has initiated a PPT creation request.

Ask the user about: topic, purpose, target audience, desired page count, and style preference. Find out whether the user already has source material.

✅ **Checkpoint — Requirements understood. Proceed to Step 2.**

---

#### Step 2: Generate `research.md`

🚧 **GATE**: Step 1 complete; requirements are clear.

Read [references/research-collection.md](references/research-collection.md). Based on the user's situation, choose the appropriate mode and write all content material to `<project_dir>/research.md`:

| Mode | When to Use | Action |
|------|-------------|--------|
| **Web Research** | Topic involves recent data, industry trends, or specialized knowledge | Call search tools to gather information |
| **Organize User Material** | User has provided text, files, or documents | Format and structure only — do not add or remove content |
| **AI-Organized Content** | General or evergreen topic | AI organizes content from existing knowledge |

✅ **Checkpoint — `research.md` written. Proceed to Step 3.**

---

#### Step 3: Plan Outline

🚧 **GATE**: Step 2 complete; `research.md` exists and contains content.

Read [references/ppt-design.md](references/ppt-design.md). Using content from `research.md`, design each slide's `type` / `title` / `content_spec`. Write `content_spec` as topical direction only — no finalized copy, no layout prescriptions.

✅ **Checkpoint — Outline planned. Proceed to Step 4.**

---

#### Step 4: Present Plan to User

🚧 **GATE**: Step 3 complete; outline and theme are ready.

⛔ **BLOCKING**: Present the full outline (slide list + `theme`) to the user and wait for explicit confirmation.

> ❌ **NEVER proceed to Step 5 or any subsequent step before receiving explicit user confirmation.**

✅ **Checkpoint — User confirmed. Proceed to Step 5.**

---

#### Step 5: Initialize Project

🚧 **GATE**: Step 4 complete; user has confirmed the outline.

- Create `<project_dir>/slides/` directory.
- Write `<project_dir>/presentation.json` using a file write tool. Follow the format defined in the **`presentation.json` Format** section above.

✅ **Checkpoint — `presentation.json` written, `slides/` directory created. Proceed to Step 6.**

---

#### Step 6: Generate Slides (HTML)

🚧 **GATE**: Step 5 complete; `presentation.json` is valid and `slides/` directory exists.

Read [references/slide-generation.md](references/slide-generation.md) and select serial or parallel mode based on the current platform. Each slide MUST be generated with simultaneous reference to both `research.md` and `references/html-spec.md`.

✅ **Checkpoint — All slide HTML files generated. Proceed to Step 7.**

---

#### Step 7: Validate & Fix Icons

🚧 **GATE**: Step 6 complete; all HTML files exist in `slides/`.

Run the icon validation script to automatically fix invalid Lucide icon names:

```bash
node "$SKILL_DIR/scripts/validate-icons.mjs" "<project_dir>"
```

The script fetches the latest Lucide icon list from CDN, scans all `data-lucide` references across all HTML files, and fuzzy-replaces invalid icon names with the closest valid match.

> ⚠️ If the output contains items flagged "⚠ recommend manual review" (large edit distance), inspect those icons for semantic correctness and manually replace with a more appropriate icon if needed.

✅ **Checkpoint — Icon validation complete, all invalid icons fixed. Proceed to Step 8.**

---

#### Step 8: Export .pptx

🚧 **GATE**: Step 7 complete; all HTML files are icon-validated.

See the **Export to .pptx** section below.

✅ **Checkpoint — .pptx exported successfully. Workflow complete.**

---

### Incremental Edit Rules

When making partial changes to an existing project (modifying a slide, adding or removing pages, changing the theme, etc.), select the relevant steps from the core workflow based on user intent and follow these constraints:

**Filenames are immutable**: HTML filenames on disk are never renamed after creation. Indices are monotonically increasing and never reused. When adding a new slide, take the current maximum index + 1. To remove a slide, delete its entry from the `slides` array; the disk file may be kept or deleted.

**Minimum-change principle**: Choose the least-invasive operation for the type of change:

| Change Type | Operation |
|-------------|-----------|
| Text / numbers only | Direct string replacement |
| Style / color / layout only | Local edit — do not touch copy |
| Keep visuals, change copy | Replace text nodes only — preserve all `class` / style attributes |
| Keep copy, change visuals | Preserve text content — freely restyle color and layout |
| Full redesign | Rewrite the entire slide — may re-extract content from `research.md` |

**Theme change**: Update the `theme` field in `presentation.json`, then rewrite all slides in "keep copy, change visuals" mode (use the same execution mode as Step 6 — serial by default).

After any modification: sync `content_spec` in `presentation.json` for affected slides, re-run icon validation, then re-export the .pptx.

---

## Export to .pptx

**Before first export**, check and install fonts (one-time only — missing fonts cause layout calculation errors):

```bash
bash "$SKILL_DIR/scripts/check-fonts.sh"           # check
bash "$SKILL_DIR/scripts/check-fonts.sh" --install  # install if not found
```

**Run export** (must run outside sandbox — export depends on Playwright headless Chromium):

```bash
node "$SKILL_DIR/dist/export-pptx.mjs" <project_dir> --output <output_path>
```

The export tool automatically validates `presentation.json`. If validation fails, it outputs specific errors and fix suggestions.

If export fails due to missing Playwright, install it first:

```bash
npm init -y && npm install playwright && npx playwright install chromium
```

Export after every operation. Output filename format: `<title>_YYYYMMDD_HHmmss.pptx`.
