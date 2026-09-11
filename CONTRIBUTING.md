# Contributing

Thanks for looking. Please read the next paragraph before investing effort.

This is an experimental research system, released so the approach and the code can be studied
and adapted. It is **maintained as time permits, with no production-support commitment**. That
has a practical consequence worth stating up front: issues and pull requests are welcome, but
may not be reviewed promptly, and large unsolicited changes are unlikely to be merged. If you
need this to move at your pace, forking is a legitimate and expected outcome.

## Getting set up

```sh
uv sync --group dev
uv run pytest -q          # ~2100 tests, roughly 40 seconds
uv run ruff check .       # must be clean
```

Python 3.11 and 3.12 are both supported and both run in CI. There is nothing else to
configure: the suite needs no network, no API keys, no global git identity, and no external
agent binary.

If the suite is slow, or fails on a clean machine, that is a bug and worth an issue on its own.
`tests/conftest.py` documents the ambient state deliberately excluded to keep runs hermetic.

## What is likely to be accepted

- Bug fixes with a test that fails before the change and passes after.
- Corrections to documentation that overstates what the code does. Accuracy about capability is
  treated as a correctness issue here, not a stylistic one.
- Tests that pin down behavior that is currently only implied.
- Portability fixes, particularly for containment on non-Linux hosts.

## What is unlikely to be accepted

- New subsystems or capability expansion. The gap between what this does and what it might do
  is intentional, and documented.
- Making LLM output authoritative anywhere in the scoring path.
- Changes that make execution easier to trigger, or that route around the approval, capability,
  or sandbox gates.
- Adding a dependency for something the standard library already does adequately.

## Conventions that matter

**Artifacts are interfaces.** Anything written under `runs/` or into a product tree carries a
`schema` field. If you change a payload's shape, treat it as an interface change: bump the
schema, update every consumer, and update the doc that describes it. Do not quietly rename a
key.

**Determinism is a feature.** If a change makes the same inputs produce different outputs, that
needs to be deliberate and explained. Where output legitimately varies with the clock — the
freshness and staleness paths — keep that variance visible rather than smoothing it away.

**Don't let interpretation become observation.** Findings infer; signals observe. Keep inferred
meaning out of the places that record what was measured.

**Tests build their own fixtures.** Use `tmp_path`. Do not depend on the contents of
`products/`, which is example data and may change.

**Say what the code cannot say.** Comments should record a constraint or a reason that is not
visible from the code itself. Comments restating the next line, or explaining a change to a
reviewer, are noise once merged.

## Commit and PR notes

- Explain *why* in the commit message; the diff already shows what.
- Keep unrelated changes in separate commits.
- Do not commit anything under `runs/` except its tracked `README.md`, and nothing from `tmp/`.
- CI must be green: lint plus the suite on 3.11 and 3.12.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## Conduct

Be straightforward and civil; assume competence. See
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
