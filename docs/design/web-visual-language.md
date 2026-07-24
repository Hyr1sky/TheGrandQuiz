# TheGrandQuiz Web Visual Language

Status: selected（2026-07-24）

## Direction

The selected Article Workspace direction is **墨迹星图**. It treats document structure,
search progress, and citations as a navigable evidence constellation without turning the
product into a science-fiction dashboard.

- [Dark reference](assets/article-workspace-dark.webp)
- [Light reference](assets/article-workspace-light.webp)

The two references are one design system. Light mode is not a color inversion, and dark mode
is not a decorative alternate page.

## Product hierarchy

1. The article is the dominant reading surface.
2. The document outline is a compact constellation map at the paper edge.
3. The user question and grounded answer are editorial margin annotations, not chat bubbles.
4. A citation visibly connects the answer to `section_path`, quote, and context.
5. The Agentic Search projection is a restrained four-stage trail, not a KPI panel.
6. Evidence is initially masked by frosted glass and can be deliberately revealed.

No screen may default to a dashboard grid, a generic chat split, or a collection of nested
cards.

## Semantic theme tokens

Components consume semantic tokens only. Theme-specific raw colors stay in the theme layer.

| Role | Dark: night ink | Light: mineral paper |
| --- | --- | --- |
| canvas | deep blue-black paper | warm ivory mineral paper |
| text | warm parchment | deep blue-black ink |
| muted text | desaturated parchment | slate ink |
| brass | antique gold | muted ochre |
| evidence | electric cyan | mineral blue |
| citation | parchment gold | antique brass |
| glass | smoky translucent ink | milky translucent vellum |
| focus | high-contrast cyan ring | deep mineral-blue ring |

State meaning cannot depend on color alone. Complete, active, failed, and masked states also
use copy and iconography.

## Typography

- Reading: `"Songti SC"`, `"Noto Serif CJK SC"`, `"Source Han Serif SC"`, serif.
- Interface: `"PingFang SC"`, `"Noto Sans CJK SC"`, `"Microsoft YaHei"`, sans-serif.
- Long-form body: 15–17px, line height 1.8–2.0, comfortable line length around 60 Chinese
  characters.
- No more than these two families appear on a screen.

## Surfaces and controls

- Separation order: whitespace → alignment → fine rule → subtle tint → border → shadow.
- Embossed or inset treatment is reserved for ask, reveal, theme, and compact run controls.
- Decorative constellation lines remain low-contrast and never cross long-form body text.
- Use a maintained icon library for functional icons. Do not encode meaning with emoji or
  improvised glyphs.

## Motion and accessibility

- Theme changes use color transitions under 180ms; `prefers-reduced-motion` disables them.
- Citation focus scrolls the exact node into view and moves keyboard focus to its evidence.
- Evidence reveal is a real button, supports `Enter`/`Space`, and exposes `aria-expanded`.
- Every control has a visible `:focus-visible` ring.
- Body and control text target WCAG AA contrast in both themes.
- At narrow widths the outline becomes a horizontal section map and the annotation follows the
  article; persistent controls must remain visible without horizontal page scrolling.

## Responsive intent

- `>= 1180px`: outline / article / annotation three-track editorial composition.
- `760–1179px`: compact outline rail, article and annotation remain related.
- `< 760px`: single reading column; outline becomes a scrollable section map, answer follows the
  current section, and the run trail may wrap vertically.

## Interaction states

- Loading: retain the reading layout and show a quiet textual loading state.
- Empty library: explain how to ingest a material; do not display fake sample data.
- No evidence: successful fail-safe state with a clear “材料中没有足够证据” message.
- Provider failure: redacted message plus `trace_id`, with retry as the primary recovery.
- Disconnected SSE: keep received stages, state that the live connection is interrupted, and
  resume from the last event sequence.
- Cancelled: terminal run state; keep trace link and question visible.
