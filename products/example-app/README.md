# Example App

A fictional product node used to demonstrate Argus end to end. Everything here is invented:
the guide describes no real place, the metrics are hand-written fixtures rather than measured
values, and the cost estimate is a placeholder rather than a bill.

It exists because Argus is a tool for observing products, and a tool like that is much easier
to evaluate against a product you can read in full than against a description of one.

## What it represents

A small static content site — a field guide — organised as a catalog:

- **Groups** are regions of the guide. There are three.
- **Slots** are individual entries within a group, in reading order. There are eight.
- A slot's `status` moves through `planned` → `outlined` → `seeded` as it gains substance.

Three slots have entries on disk (`content/slots/`), each with a matching page under
`app/site/slot/`. The rest exist only as reserved rows in the catalog. That gap between
"declared in the catalog" and "present on disk" is the thing most of the Argus signals and
findings are actually reasoning about.

## Layout

| Path | What it is |
| --- | --- |
| `product.yaml` | The product node: identity, lifecycle stage, enabled signals, cost, constraints |
| `argus.policy.yaml` | Per-product permission policy (what Argus may do without asking) |
| `content/content_catalog.json` | The catalog: groups, slots, and each slot's status |
| `content/slots/*.json` | Structured entries for slots that exist on disk |
| `app/site/` | The rendered static site (index, group listing, one group hub, three entries) |
| `metrics/` | Local fixtures: content evidence plus two activity logs |
| `scripts/` | `start` / `stop` / `analyze` hooks. All no-ops that touch nothing |

## Why the catalog looks lopsided

The three groups are deliberately uneven, so that Builder's expansion heuristic has a
non-obvious choice to make and a visible reason for making it:

| Group | Slots | Embodied (seeded or outlined) | Hub page |
| --- | --- | --- | --- |
| 1. Coast Path | 4 | 2 | yes |
| 2. Ridge Loop | 2 | 1 | no |
| 3. Marsh Boardwalk | 2 | 0 | no |

Given that shape, `argus builder next-expansion example-app` selects **`group_01_slot_03`**
(Gull Point). It prefers Coast Path because that group has the most embodied slots and a hub
page already exists, then walks the group in order from slot 1 while slots are embodied
(slots 1 and 2 are), and targets the first `planned` slot after that contiguous prefix.

It also records Ridge Loop as an *explicit non-target*, with the reason that breadth is
secondary to finishing the strongest in-order spine. The heuristic is deliberately simple
and says so in its own output: it is not a planner, and its `disclaimer` field says as much.

## Running against it

```sh
argus products validate                        # confirm the node parses
argus builder next-expansion example-app --json --no-save
argus portfolio refresh                        # signals -> findings -> decisions
```

None of these reach the network, and none of them modify this product.
