# Design QA — Observatory Navigation background

## Evidence

- Source visual truth:
  `/Users/hyriskyhe/.codex/generated_images/019f6e0d-c288-7dc0-9805-08c0d709fc90/call_OuJ0HFmJVxloIh5TKvO94Hwr.png`
- Figma archive: file `35RgNvOh8VCIfivFd7jU6z`, section
  `03 · Selected · Observatory Navigation` (`14:2`)
- Rendered vector asset:
  `/private/tmp/grandquiz-svg-dark-crop.png`
- Full-view comparison:
  `/private/tmp/grandquiz-observatory-comparison.png`
- Browser-rendered reading state:
  `/private/tmp/grandquiz-real-reading-light.png`
- Browser-rendered grounded-answer state:
  `/private/tmp/grandquiz-real-answer-light.png`
- Browser-rendered assessment state:
  `/private/tmp/grandquiz-real-dogfood-dark.png`
- Intended CSS viewport: `1440 × 1024`
- Source pixels: `1487 × 1058`, normalized to `1440 × 1024`
- Vector render pixels: `1440 × 1024`, density `1`
- State: reading workspace, dark theme
- Browser viewport: `1280 × 720`, device density `1`

The source and vector render were placed in one side-by-side comparison at the
same pixel dimensions. The generated background itself was also inspected in
dark and light themes. The React shell was then exercised against the production
`~/.grandquiz/learning.db` and `.env` provider in the in-app browser.

## Findings

- No P0/P1/P2 issue was found in the vector asset comparison.
- Typography: the background labels stay monospaced, small and subordinate to
  the reading typography used by the application.
- Spacing and layout: the polar projection remains centered behind the reading
  area; the armillary is biased to the right edge and the bearing ring remains
  at the bottom, matching the selected composition without competing with text.
- Colors: dark ink, warm brass and restrained cyan map to the existing semantic
  theme tokens. The light version preserves the same geometry with reduced
  contrast.
- Image quality: both backgrounds are native SVG generated from real J2000 star
  positions and constellation lines. They remain sharp at arbitrary viewport
  density and do not depend on a runtime astronomy or network package.
- Copy: constellation abbreviations and degree labels are decorative; no
  product copy was changed.
- Resolved P1 — Multi-turn SSE now keeps a session-level cursor and the second
  EventSource resumes after the first turn's terminal sequence.
- Resolved P1 — `AssessmentPanel` now reuses one keyed start promise under React
  Strict Mode; the real question appears without issuing a duplicate assessment.
- Resolved P2 — Each Chat message now carries the selected `resource_id`; the
  backend validates it and injects only that exact id into trusted turn context.
- P2 — A long completed assistant response leaves the chat scrolled to the end
  of the new response instead of its beginning, so the user sees the citation
  footer before the answer.
- No browser console warnings or errors were emitted during the tested flow.

Focused-region comparison was not required for the background asset because its
only fine-detail elements are decorative star, grid and instrument paths. The
source application's controls and reading content were intentionally not copied
into the asset.

## Comparison history

1. The selected visual used one continuous star field but the existing product
   used two raster WebP backgrounds and repeated the image inside evidence
   veils.
2. The implementation replaced that layer with theme-specific SVG files
   generated from one source geometry, introduced one non-interactive global
   backdrop, and removed nested background repetition.
3. Static comparison confirmed the polar chart, right-edge armillary and bottom
   compass/bearing composition. Frontend tests, TypeScript checks and the
   production build pass.

## Browser interactions tested

- Load four resources from the production learning database
- Select and read a bounded document node
- Ask a grounded question and render Markdown/table content over SSE
- Send a second chat turn
- Request a three-question selected-resource assessment
- Switch from light to dark theme
- Inspect browser console warnings and errors

The visual direction itself is acceptable in both themes: the continuous star
map, translucent surfaces, typography and focus states remain legible. The QA
gate stays blocked by the interaction defects above, not by the selected visual
direction.

## Implementation checklist

- [x] One global decorative backdrop outside the interaction layer
- [x] Dark and light SVGs generated from the same source geometry
- [x] Real star/constellation data with retained BSD-3-Clause notice
- [x] Header, sidebar, chat and composer surfaces use coordinated translucent
      theme tokens
- [x] Nested repeated background images removed
- [x] Automated frontend test, typecheck and production build pass
- [x] Capture the React shell in dark and light themes
- [x] Check focus state and browser console
- [x] Preserve the last SSE sequence between turns
- [x] Pass selected `resource_id` into Chat session context
- [x] Make assessment initialization Strict-Mode-safe and resume polling
- [ ] Scroll completed long answers to the beginning of the new assistant block
- [ ] Check mobile stacking after interaction fixes

final result: blocked
