# HTML 幻灯片技术规范

生成每页幻灯片 HTML 时，MUST 严格遵循本文件中的所有规范。

本文档使用以下约束级别：

- **MUST** / **MUST NOT**：强制规则，违反即为错误
- **SHOULD**：强烈推荐，除非有充分理由
- **MAY**：可选，视场景决定

---

## 一、HTML 文档结构

1. 输出完整的 HTML 文档（包含 `<!DOCTYPE html>`、`<head>`、`<body>`）
2. `<head>` 中 MUST 包含，且 MUST 按如下顺序：
  - `<meta charset="UTF-8">`
  - `<meta name="viewport" content="width=device-width, initial-scale=1.0">`
  - 按需引入的 Google Fonts `<link>`
  - `<script src="https://cdn.tailwindcss.com"></script>`
  - Tailwind CSS v3 配置 script（MUST 放在 Tailwind CDN **之后**，否则 `tailwind` 对象尚未存在）
  - `<script src="https://unpkg.com/lucide@latest"></script>`
3. body 标签画布固定写法：`<body class="w-[1280px] h-[720px] overflow-hidden m-0 p-0 relative">`
4. MUST NOT 编写任何重置 DOM 样式的方法
5. 所有内容 MUST 在画布范围内，MUST NOT 撑高页面
6. MUST 使用 Lucide CDN 图标，MUST NOT 手写 `<svg>` 路径作为图标，MUST 在 script 中添加 `lucide.createIcons();`
7. 内容页 MUST NOT 添加页脚（如页码、品牌标注等），因为多页幻灯片之间难以保证页脚格式统一；封面页和结尾页不受此限制

---

## 二、设计系统变量

MUST 在 Tailwind CDN **之后**通过 `tailwind.config` script 声明配色和字体变量，然后通过 Tailwind 类名引用，确保全页配色字体一致。

```html
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = {
    theme: {
      extend: {
        colors: {
          primary: '#值',
          secondary: '#值',
          accent: '#值',
          'text-primary': '#值',
        },
        fontFamily: {
          heading: ['Microsoft YaHei', 'PingFang SC', 'sans-serif'],
          body: ['Noto Sans SC', 'Arial', 'Inter', 'sans-serif'],
        },
      },
    },
  }
</script>
```

- `primary`（主色）、`secondary`（辅色）、`accent`（点缀色）三个颜色 MUST 在 `colors` 中声明，通过 `bg-primary`、`text-secondary`、`border-accent` 等类名引用
- 深色背景时 MUST 额外声明 `text-primary` 颜色用于指定文字色，通过 `text-text-primary` 引用
- 字体 MUST 声明 `heading` 和 `body`，通过 `font-heading`、`font-body` 引用

---

## 三、字体规范

### 字体选择

1. **标题字体**：MUST 使用微软雅黑（Microsoft YaHei）或 PingFang SC
2. **正文字体**：MUST 使用思源黑体（Noto Sans SC）、Arial 或 Inter
3. 仅当用户明确要求不同风格时，MAY 从 Google Fonts 引入其他字体，并在 `@theme` 中声明
4. MUST NOT 在同一演示文稿中使用超过 3 种字体族

### 字号层级

严格使用 16px 以上的字号，确保后排观众也能看清每一个字。

| 用途   | Tailwind 类                                | 说明                   |
| ---- | ----------------------------------------- | -------------------- |
| 大标题  | `text-6xl` / `text-7xl` + `font-bold`     | 仅用于封面/结尾页主标题、章节过渡页   |
| 节标题  | `text-4xl` / `text-5xl` + `font-semibold` | 页面内分区标题，引用金句         |
| 内容标题 | `text-2xl` / `text-3xl` + `font-semibold` | 卡片标题、列表项标题           |
| 正文   | `text-lg` / `text-xl` + `font-normal`     | 内容页主体文字，≥ 16px       |


**MUST NOT**：正文字号小于 `text-base`（16px）。

---

## 四、颜色规范

### 60-30-10 黄金法则

1. **主色调 — 60% 面积**：用于全局背景色（背景层 div），奠定页面基调
2. **辅色调 — 30% 面积**：用于卡片容器、模块边框、区域划分，构建空间层次与结构
3. **点缀色 — 10% 面积**：用于强调关键数据、按钮、图标、核心行动点，引导视觉焦点

### 颜色使用规则

1. MUST 在 `tailwind.config` 的 `theme.extend.colors` 中声明语义化颜色（`primary` / `secondary` / `accent`），通过 Tailwind 类名引用（如 `bg-primary`、`text-secondary`、`border-accent`）
2. MUST NOT 在同一页面使用超过三种高饱和度颜色
3. MUST 遵循 WCAG 2.1 AA 无障碍标准：正文文字与直接背景的对比度比例 MUST ≥ 4.5:1
4. 大字（≥ 24px 或 ≥ 18.67px bold）与背景对比度 MUST ≥ 3:1
5. 深色背景页面中，正文文字 SHOULD 使用 `#E2E8F0` 以上亮度的浅色；浅色背景页面中，正文文字 SHOULD 使用 `#334155` 以下亮度的深色

---

## 五、布局规范

### 12 列网格架构

在 1280×720 画布上采用标准化 12 列网格：

- 页面边距：左右 `px-16`（64px），上下 `py-12`（48px），内容区有效宽度 1152px
- 网格列数：`grid-cols-12`，列间距 `gap-6`（24px）
- 常用分栏：2 栏 = `col-span-6`，3 栏 = `col-span-4`，4 栏 = `col-span-3`

### 对齐原则

1. 所有内容元素 MUST 对齐到网格列边界，消除排版混乱
2. 文本块 MUST 使用统一的 `leading-`* 和 `gap-`* 保持基线对齐
3. 无论是文本块、图标还是图像容器，均 MUST 锚定在无形的对齐线上

### 留白系统（Negative Space）

科学运用宏观与微观留白，降低认知负荷，引导视觉焦点：

- **宏观留白**：页面边距 `px-16 py-12`（64px / 48px），分离页面边缘与内容
- **微观留白**：模块间距 `gap-6`（24px），元素内间距 `p-6` ~ `p-8`
- MUST：内容区块之间使用 ≥ 24px 间距
- MUST NOT：相邻元素紧贴无间距

### 视觉层级（信息层次）

通过以下四个维度的系统性差异，构建清晰的阅读路径，确保关键信息被第一时间捕获：

1. **字号梯度**：大标题 > 节标题 > 内容标题 > 正文，级差明显
2. **字重对比**：`font-black` / `font-bold` 用于标题，`font-semibold` 用于副标题，`font-normal` 用于正文
3. **色彩对比度**：标题使用高对比色，辅助文字降低对比度（如 `text-slate-400`）
4. **空间分布**：重要信息占更大面积，次要信息收紧聚拢

### 自适应内容填充

1. 内容容器 MUST 使用 `flex-grow` + `min-h-0` 构建自适应高度填充
2. 卡片网格 MUST 使用 `flex-1` 填充剩余空间
3. MUST NOT 出现大面积空白的"半空"页面：内容稀疏时容器 SHOULD 收紧聚拢，内容丰富时容器再适当展开
4. 释放出的多余空间交给装饰层处理，而非让内容容器空着大面积留白

---

## 六、视觉设计规范

### 设计原则

1. **让内容决定布局，而非布局等待内容**：先评估实际文案量，再选择合适的容器尺寸和布局方式。内容稀疏时容器应当收紧聚拢，内容丰富时容器再适当展开。
2. **填充留白，消除空旷感**：用低信息密度的视觉元素（装饰图形、纹理、渐变色块等）填充内容区之外的空白，让页面"满"而不"挤"。
3. **建立信息层级，引导视线**：通过放大、变色、高亮等手法强化层级感，让观众第一眼就知道先看哪里。
4. **引入视觉节奏，打破单调**：在保持整体结构统一的基础上，利用重复中的微小变奏制造节奏感。
5. **打磨细节，提升品质感**：圆角、阴影、渐变、间距一致性等细节是拉开业余与专业观感的关键。
6. MAY 添加 hover 动效，但 SHOULD 保持克制，不能喧宾夺主。

### 视觉层次（从下到上）

1. **背景层**：纯色 / 渐变 / 纹理（根据风格规范决定）
2. **装饰层**：几何图形、纹理叠加（绝对定位，z-index 低）
3. **内容层**：标题、文字、数据（`relative`，z-index 中）
4. **点缀层**：漂浮元素、高光装饰、漂浮贴纸气泡（`absolute`，z-index 高）

注意：装饰层与点缀层为可选，根据风格规范决定是否使用。

---

## 七、样式编写规范

**布局与通用样式（优先 Tailwind）**：flex、grid、gap、padding、margin、圆角、z-index、字体大小等优先使用 Tailwind 类名（如 `flex items-center gap-4 p-8 rounded-xl z-10`）。

**复杂视觉效果（允许常规 CSS）**：复杂渐变、背景图案、精确装饰定位、`-webkit-background-clip` 等特殊效果可使用内联 style 属性或自定义类名。

---

## 八、PPTX 导出规范

生成的 HTML 会被导出为可编辑的 PPTX 文件，以下规范影响导出质量，MUST 严格遵守。

### 背景层写法

- `body` 标签本身只设置尺寸与溢出，MUST NOT 在 body 上设置背景渐变或纹理
- 背景色/渐变/纹理 MUST 写在独立的背景 div 上：

```html
<div class="absolute inset-0 z-0" style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%)"></div>
```

### 文字容器

- 含有文字内容的 div/span/p 等元素，MUST NOT 同时添加 `clip-path` 样式
- `clip-path` 只用于纯装饰性的无文字容器
- 这样可确保文字在 PPTX 中保持可编辑状态