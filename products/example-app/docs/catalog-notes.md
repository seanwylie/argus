# Catalog notes

Notes on how this fixture was constructed, kept alongside it so the numbers in
`metrics/content_evidence.json` can be checked against the catalog by hand.

## Provenance

Written by hand for the Argus example product. There is no upstream source, no import, and
no generated prose. The place names are invented; the guide describes no real location.

## Counts

Derived from `content/content_catalog.json`:

| Measure | Value | Where it comes from |
| --- | --- | --- |
| Groups | 3 | `groups[]` |
| Slots | 8 | sum of `groups[].slots[]` |
| Seeded | 2 | `group_01_slot_01`, `group_02_slot_01` |
| Outlined | 1 | `group_01_slot_02` |
| Planned | 5 | the remainder |
| Entries on disk | 3 | files under `content/slots/` |

`metrics/content_evidence.json` restates these. They are intended to agree, and a
disagreement is a legitimate finding rather than a bug in the fixture — the point of the
metric is that it can drift from the catalog.

## Status vocabulary

- `planned` — the slot is reserved in the catalog and nothing else exists.
- `outlined` — structure and intent are recorded; the body is not written.
- `seeded` — a usable first version exists on disk.

Only `seeded` and `outlined` count as *embodied* for the expansion heuristic. This is
defined in `argus/builder/next_expansion_generate.py` as `EMBODIED_STATUSES`.

## Deliberate imperfections

Two things are left incomplete on purpose, because a fixture where everything is finished
cannot demonstrate a tool that looks for gaps:

1. `group_01_slot_02` is outlined rather than seeded, so more than one fidelity level is
   present in the catalog.
2. Ridge Loop and Marsh Boardwalk have no hub pages, so the heuristic's hub check has both
   a positive and a negative case to distinguish.
