# Reviewing this repository

Three paths through the code, depending on why you opened it. Each one is self-contained;
none of them requires reading the others first.

---

## Path 1 — Evaluating in five minutes

Run the proof in the [README](../README.md#prove-it-in-five-minutes), then open exactly three
files. They are chosen because each answers a question the others cannot.

**1. `products/example-app/content/content_catalog.json`** — the input. Eight slots across
three uneven groups. This is the entire state that the expansion heuristic reasons about, and
you can hold all of it in your head at once.

**2. `products/example-app/content/next_expansion.json`** — the output. Note that it carries
its own `rationale`, a `basis` list enumerating the evidence used, a `confidence` value, an
`explicit_non_targets` array saying what it declined and why, and a `disclaimer` stating that
it is a heuristic rather than a plan. The claim being made is that a recommendation should be
auditable from its own artifact, without re-running the tool.

**3. `runs/portfolio/latest/summary.txt`** — the portfolio view. One ranked action per product,
derived from signals through findings to decisions.

If you want to see the reasoning rather than the result,
`argus/builder/next_expansion_generate.py` is about 310 lines and is the whole heuristic.

**What to be skeptical about.** The example product is a fixture written to demonstrate the
system, so it is a friendly case by construction. The interesting question is not whether the
tool handles it, but whether the *contracts* would survive a hostile one. `docs/` is where
that is argued.

---

## Path 2 — Assessing the engineering

Read in this order:

1. **[`docs/architecture.md`](architecture.md)** — the subsystem map.
2. **[`docs/architecture/state-of-the-system.md`](architecture/state-of-the-system.md)** — the
   code-grounded assessment, including what is thin. This is the most honest document here and
   the best single measure of whether the project's self-knowledge is real.
3. **[`docs/model-contracts.md`](model-contracts.md)** — IDs, canonical types, and the shared
   vocabulary. The distinction between confidence and novelty, and between observation and
   interpretation, is enforced here rather than left to prose.
4. **[`docs/builder-execution-contract.md`](builder-execution-contract.md)** — the scope model
   for agent-driven changes: allowed paths, breach detection, and outcome classification.
5. **[`docs/temporal-intelligence.md`](temporal-intelligence.md)** — why freshness is separated
   from meaning.

### The load-bearing design decisions

These are the choices worth arguing with, stated plainly so you do not have to infer them:

- **Determinism is the default; LLMs are additive.** Canonical scoring and ranking are
  mechanical. LLM paths exist, are off by default, and are explicitly non-authoritative. The
  reasoning is that a portfolio tool whose priorities change between runs cannot be audited.

- **Interpretation never becomes telemetry.** Findings infer meaning from signals; they are
  not permitted to stand in for observation. Decisions consume both but keep them separate.
  The failure mode being designed against is a narrative sentence acquiring the authority of a
  measurement.

- **Staleness is data, not an error.** Freshness is scored and carried forward rather than
  hidden. A consequence is that some pipeline outputs are clock-dependent by design.

- **Execution is gated, and the gates are separate from the reasoning.** Approval, capability,
  and sandboxing sit outside the analytical spine, so making the analysis smarter cannot widen
  what the system is permitted to do.

- **Builder orchestrates an agent; it does not generate code.** It selects a target, declares
  a path scope, invokes an external agent under containment, and then checks what came back
  against the contract. The verification is the contribution, not the generation.

### Where to push

Honest weak points, so you can go straight to them:

- Contract coverage is three kinds (`content_slot`, `bug_fix`, `signal_instrumentation`), not a
  general surface. Ad-hoc tasks can have a vacuous scope check.
- The expansion heuristic assumes a content catalog and a conventional static-site layout. It
  does not generalize to arbitrary products.
- Containment is materially stronger on Linux with bubblewrap present, and degrades explicitly
  elsewhere. The degradation is reported rather than silent, but it is still degradation.
- There is one example product. Multi-product portfolio behavior is exercised by tests rather
  than by lived use.

---

## Path 3 — Changing something

Start with [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the mechanics and the constraints on
what changes are likely to be accepted.

Orientation that will save you time:

- **The test suite is hermetic and fast.** `uv run pytest -q` runs about 2100 tests in roughly
  40 seconds with no network, no global git identity, and no external agent binary. If it is
  slow or fails on a clean machine, that is a bug worth reporting. `tests/conftest.py` explains
  what ambient state is deliberately excluded and why.
- **Lint is enforced.** `uv run ruff check .` must be clean. CI runs it as a separate job.
- **Artifacts are contracts.** Anything written under `runs/` or into a product tree has a
  `schema` field. Changing a payload shape is an interface change, so bump the schema and
  update the consumers rather than editing a key in place.
- **`products/` is data, not code.** The example product is a fixture. Tests should not depend
  on the real tree; they build their own under `tmp_path`.
