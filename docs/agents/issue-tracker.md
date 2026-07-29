# Issue tracker: Local-first Markdown

Personal planning for this repository lives as Markdown under `.scratch/`. The directory is gitignored: local PRDs,
implementation tickets, review notes, and agent working context must not be committed or treated as public project state.

GitHub Issues becomes the shared tracker only when collaborators are involved, or when the repository owner explicitly
asks to publish an item for external coordination. A local draft does not need a matching GitHub Issue.

## Local conventions

- One feature per directory: `.scratch/<feature-slug>/`.
- The PRD is `.scratch/<feature-slug>/PRD.md`.
- Implementation tickets are `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`.
- One independently acceptable behavior maps to one ticket and, normally, one pull request.
- Triage state is recorded as a `Status:` line near the top of each ticket (see `triage-labels.md`).
- Comments and conversation history append under a `## Comments` heading when they are useful.

## Tracked planning boundaries

- Put stable product direction and release slices in `docs/roadmap.md`.
- Put irreversible architecture decisions in `docs/adr/`.
- Put completed implementation narratives and verification evidence in `docs/devrecords/`.
- Code and tests must not depend on `.scratch/` files.

## When a skill says "publish to the issue tracker"

By default, create or update a local Markdown ticket under `.scratch/<feature-slug>/`. Publish the stable, actionable
part to GitHub Issues only after the owner explicitly requests public tracking or collaborators need shared issue state.

## When a skill says "fetch the relevant ticket"

Read the referenced local Markdown file. If the user explicitly references a GitHub Issue, read that shared issue
instead; do not silently migrate local planning to GitHub.
