# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

## TheGrandQuiz visual decision

- Selected direction: “墨迹星图”.
- Maintain one component tree with semantic light/dark tokens; do not fork theme-specific markup.
- Dark reference: `../docs/design/assets/article-workspace-dark.webp`.
- Light reference: `../docs/design/assets/article-workspace-light.webp`.
- The article is the primary canvas. Question and answer are margin annotations, never a chat column.
- Citation is a visible evidence path between an answer, section path, and exact quote.
- Neumorphism is restricted to tactile controls; it is not a page-wide surface treatment.
- Theme selection starts from the system preference, persists the first explicit light/dark
  choice, and must remain keyboard accessible.
- All active asynchronous work uses the shared orbital activity indicator with explicit stage
  copy. Preserve stable content and layout while waiting; use a block status only when the target
  region has no meaningful content yet. Never use a full-screen loading overlay for Chat,
  Assessment, Acquisition, Settings, Eval, or Observatory work.
- The star map may move only as ambient context: compositor-only transforms, no layout animation,
  at most a few pixels of pointer parallax, and a very slow sub-degree orbital drift. Disable
  parallax on coarse pointers, stop ambient motion below the desktop breakpoint, honor
  `prefers-reduced-motion`, and pause the animation while the document is hidden.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
