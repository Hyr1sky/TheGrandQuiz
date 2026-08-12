# TheGrandQuiz Web Visual Language

Status: selected and synchronized（2026-07-28）

## Direction

The selected Local Web direction is **墨迹星图** with a **three-column learning workspace**.
It treats document structure, reading evidence, assessment state, and Agent orchestration as
one evidence constellation without turning the product into a science-fiction dashboard.

- [Dark reference](assets/article-workspace-dark.webp)
- [Light reference](assets/article-workspace-light.webp)

The two references are one design system. Light mode is not a color inversion, and dark mode
is not a decorative alternate page.

## Product hierarchy

1. The middle column is the dominant evidence surface: article while reading, question sheet
   while assessing.
2. The left column is the compact document outline or assessment-progress rail.
3. The right column is a first-class, material-scoped Agent conversation rail. It may navigate
   the middle surface, but never replaces exact citations or assessment state.
4. The three columns form one task workspace, not a generic support-chat split.
5. A citation visibly connects an answer to `section_path`, quote, and context.
6. Evidence is initially masked by frosted glass and can be deliberately revealed after the
   approved three-second hover, or immediately by click/keyboard.
7. Observability is a live drawer projected from the event spine, not a dashboard home page.
8. Acquisition is a right-side glass sheet: input, deterministic progress, candidate evidence,
   approval, and recovery live in one management state instead of scattered modal dialogs.

No screen may default to a KPI grid or a collection of nested cards. Chat bubbles remain
restrained; long-form evidence keeps editorial hierarchy.

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

The approved direction is Anthropic-like in tone—humanist, editorial, calm—not a brand clone.

- Reading: `"Iowan Old Style"`, `Charter`, `"Songti SC"`, `"Noto Serif CJK SC"`,
  `"Source Han Serif SC"`, serif.
- Interface: `system-ui`, `-apple-system`, `"Segoe UI"`, `"PingFang SC"`,
  `"Noto Sans CJK SC"`, `"Microsoft YaHei"`, sans-serif. The system stack is deliberately
  identical in light and dark themes; theme changes never switch typography.
- Long-form body: 15–17px, line height 1.8–2.0, comfortable line length around 60 Chinese
  characters.
- No more than these two families appear on a screen.

## Surfaces and controls

- Separation order: whitespace → alignment → fine rule → subtle tint → border → shadow.
- Colors, elevations, inset states, and status halos consume semantic theme tokens; component
  CSS does not introduce raw shadow recipes.
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
- At narrow widths the persistent outline rail collapses out, article/assessment and Chat stack
  vertically, and the current-material control remains visible without horizontal page scrolling.

## Responsive intent

- `>= 1180px`: outline/progress / article-or-assessment / Chat three-column workspace.
- On desktop, the outline and Chat rails are independently resizable and collapsible. Their
  last expanded widths are local display preferences, not learning-domain state.
- Reading renders the current revision as one continuous document. The outline navigates exact
  section anchors and follows the viewport; selecting an outline item does not replace the
  article with a node excerpt.
- The current article title is centered on the same reading measure as the paper body, so rail
  resizing does not visually attach the title to either side.
- `760–1179px`: compact outline rail above a full-width Chat row.
- `< 760px`: article-or-assessment and Chat stack vertically; the outline rail is not persistent,
  and no header, table, code block, or composer may widen the page viewport.

## Header and conversation hierarchy

- The header has three groups: identity, current material plus acquisition, and secondary
  management controls. Settings, tutorial, Eval, and theme are not presented as equal primary
  actions; management-only destinations live behind the management menu or settings sheet.
- Header secondary controls are 36px icon buttons with concise Chinese hover/focus tooltips and
  the same focus treatment. Popover menus close on outside click or `Escape`.
- Drawer close controls are fixed square icon buttons. Hover may change semantic color or surface,
  but must not rotate the icon or distort the circular hit target.
- Chat uses one integrated composer surface. The textarea grows with content up to a bounded
  height, then becomes internally scrollable; pointer focus does not add a second border around
  the composer. Context label and budget estimate form a disclosure control in its lower utility
  row, while send/stop is a prominent round action inside the composer.
- `/status` is a local interface command. It reads the persisted session usage plus the runtime's
  deterministic context estimate and must not create a model turn, SSE stream, or learning fact.
- Token usage is labelled as actual provider usage; context occupancy and remaining capacity are
  explicitly labelled heuristic estimates until the provider exposes an authoritative measure.

## Interaction states

- Loading: retain the reading layout and show a quiet textual loading state.
- Empty library: explain how to ingest a material; do not display fake sample data.
- No evidence: successful fail-safe state with a clear “材料中没有足够证据” message.
- Provider failure: redacted message plus `trace_id`, with retry as the primary recovery.
- Disconnected SSE: keep received stages, state that the live connection is interrupted, and
  resume from the last event sequence.
- Cancelled: terminal run state; keep trace link and question visible.
- Acquisition `needs_input`: keep concept, summary, confidence, and short exact-evidence previews
  together; selection is explicit, and no candidate appears as already committed.
- Acquisition failure: state that the formal knowledge base was not changed, expose the redacted
  failure class, and make retry the next clear action.
