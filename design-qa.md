# Article Workspace Design QA

Date: 2026-07-24

Scope: LW-S3 “墨迹星图” Article Workspace, light and dark themes

## Evidence

| Theme | Source | Implementation | Combined comparison |
| --- | --- | --- | --- |
| Dark | `docs/design/assets/article-workspace-dark.webp` | `docs/design/qa/article-workspace-dark-implementation.png` | `docs/design/qa/article-workspace-dark-comparison.webp` |
| Light | `docs/design/assets/article-workspace-light.webp` | `docs/design/qa/article-workspace-light-implementation.png` | `docs/design/qa/article-workspace-light-comparison.webp` |

Implementation captures use a 1440 × 1024 desktop viewport with the same completed state:
`Durable processors` selected, grounded answer complete, citation resolved, and exact evidence
revealed. A second 1280 × 720 capture verifies the compact desktop composition. The in-app
browser enforced its 1280 minimum when a narrower override was requested, so the `<760px>`
layout is encoded as a dedicated 390 × 844 Playwright project and a single-column media query;
the standalone Playwright CLI was not invoked during this in-app-browser QA.

Full-view comparisons retain readable typography, controls, and evidence content, so no
separate crop was needed.

## Comparison history

### Pass 1

- P2 · layout/typography: the resource title wrapped into the picker column and weakened the
  editorial hierarchy. Fixed by aligning the header to the workspace subgrid and keeping the
  title in the reading track.
- P2 · content: bounded node output exposed its Markdown heading above the already visible
  `section_path`. Fixed by stripping only the leading heading for presentation; API content and
  citation offsets remain unchanged.

### Pass 2

- Dark and light implementations preserve the reference hierarchy: constellation outline,
  dominant reading paper, margin annotation, explicit evidence path, and bottom run trail.
- Semantic tokens retain warm brass structure plus cyan/mineral-blue evidence without relying
  on color alone. Focus rings, labels, icons, and status copy remain visible in both themes.
- All visible functional icons come from Phosphor; astronomical texture is a generated raster
  asset rather than CSS or inline-vector imitation.
- Keyboard controls use native button/select/textarea semantics, `aria-expanded` exposes the
  evidence state, and reduced-motion removes decorative transitions.
- No console warnings or errors were emitted during the real FastAPI/SSE fixture path.

## Accepted product-driven differences

- The reference shows several article sections at once; the implementation intentionally reads
  one bounded `DocumentNode` at a time to preserve progressive disclosure and API limits.
- The mock uses a decorative curved citation line. The implementation uses a stable citation
  control plus evidence panel, preserving keyboard access and exact `section_path`/quote/context.
- Export controls from the visual exploration are not part of LW-S3's accepted tracer bullet and
  were not added as non-functional decoration.

There are no remaining P0, P1, or P2 findings.

final result: passed
