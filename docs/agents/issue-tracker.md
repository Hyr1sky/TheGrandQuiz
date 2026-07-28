# Issue tracker: GitHub Issues

GitHub Issues is the public backlog and the only issue state that contributors should rely on.

## Public planning boundaries

- One independently acceptable behavior maps to one issue and, normally, one pull request.
- Use the repository issue templates for bugs and scoped improvements.
- Put stable product direction and release slices in `docs/roadmap.md`.
- Put irreversible architecture decisions in `docs/adr/`.
- Put completed implementation narratives and evidence in `docs/devrecords/`.
- Keep exploratory notes local. They must not become dependencies of code, tests, or public documentation.

## When a skill says "publish to the issue tracker"

Create or update a GitHub Issue. Record the user-visible outcome, acceptance criteria, scope boundaries, and verification
evidence. Apply one triage role from `triage-labels.md` when the issue needs routing.

## When a skill says "fetch the relevant ticket"

Read the referenced GitHub Issue. If only a local draft exists, treat it as working context and publish the stable,
actionable part before asking contributors to depend on it.
