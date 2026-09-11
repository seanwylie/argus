"""Audit MVP: deterministic scans, artifacts, context slice, idea gating."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.audit.agent_lens import PROVENANCE_DETERMINISTIC_STUB
from argus.audit.agent_prompt import (
    build_cursor_scan_prompt,
    build_cursor_scan_prompt_for_angle,
    normalize_angle_id,
)
from argus.audit.angles.stub import STUB_ANGLE_IDS
from argus.audit.bundle import ANGLE_IDS, BUNDLE_SCHEMA, bundle_audit_path, load_audit_bundle
from argus.audit.cache import audit_product_dir, latest_audit_path, load_latest_audit, run_audit
from argus.audit.capability_map import build_capability_entries
from argus.audit.cursor_scan import CURSOR_SCAN_SCHEMA, PROVENANCE_CURSOR_SCAN
from argus.audit.doctor import check_audit_artifacts
from argus.audit.ingest_agent import ingest_agent_angles
from argus.audit.models import AuditStatus
from argus.audit.scanner import bounded_walk, resolve_product_paths
from argus.context import ContextPurpose, assemble_context_bundle
from argus.idea_generation.audit_gating import apply_audit_to_ideas
from argus.idea_generation.models import Idea, IdeaSource, IdeaType
from argus.products.inventory import build_inventory


def _minimal_product(root: Path, product_id: str = "aud_p1") -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: AudTest
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths:
    - metrics/
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(exist_ok=True)
    for name, body in (
        ("start.sh", "#!/bin/sh\necho start\n" + "x" * 50),
        ("stop.sh", "#!/bin/sh\necho stop\n" + "y" * 50),
        ("analyze.sh", "#!/bin/sh\necho analyze\n" + "z" * 50),
    ):
        (pr / "scripts" / name).write_text(body, encoding="utf-8")
    (pr / "metrics").mkdir(exist_ok=True)
    (pr / "metrics" / "dummy.txt").write_text("1", encoding="utf-8")


class TestAuditRun(unittest.TestCase):
    def test_run_audit_writes_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            s = run_audit(root, "p1")
            self.assertEqual(s.product_id, "p1")
            self.assertEqual(s.scan_depth, "quick")
            p = latest_audit_path(root, "p1")
            self.assertTrue(p.is_file())
            raw = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(raw.get("schema"), "argus.audit_summary.v1")
            self.assertIn("implemented", raw.get("counts_by_status", {}))
            bp = bundle_audit_path(root, "p1")
            self.assertTrue(bp.is_file())
            bundle = load_audit_bundle(root, "p1")
            self.assertIsNotNone(bundle)
            self.assertEqual(bundle.get("schema"), BUNDLE_SCHEMA)
            self.assertEqual(len(bundle.get("angles", {})), len(ANGLE_IDS))
            for aid in ANGLE_IDS:
                ang = bundle["angles"][aid]
                self.assertGreaterEqual(len(ang.get("summary_lines", [])), 1)
            self.assertIn("inputs_fingerprint_bundle", bundle)
            self.assertEqual(bundle["angles"]["security"].get("schema"), "argus.audit_angle.security.v1")
            self.assertIn(
                bundle["angles"]["security"].get("angle_status"),
                ("active", "partial"),
            )
            self.assertNotEqual(
                bundle["angles"]["security"].get("summary_lines", [""])[0],
                "security: not implemented (stub)",
            )
            perf = bundle["angles"]["performance"]
            self.assertEqual(perf.get("schema"), "argus.audit_angle.performance.v1")
            self.assertNotIn("provenance", perf)
            sb = bundle["angles"]["store_business"]
            self.assertEqual(sb.get("schema"), "argus.audit_angle.store_business.v1")
            self.assertIn(sb.get("angle_status"), ("active", "partial"))
            self.assertEqual(sb.get("sources"), {"deterministic": True, "cursor_scan": False})
            ux = bundle["angles"]["ux"]
            self.assertEqual(ux.get("schema"), "argus.audit_angle.ux.v1")
            self.assertIn(ux.get("angle_status"), ("active", "partial"))
            self.assertEqual(ux.get("sources"), {"deterministic": True, "cursor_scan": False})
            for aid in STUB_ANGLE_IDS:
                self.assertEqual(
                    bundle["angles"][aid].get("provenance"),
                    PROVENANCE_DETERMINISTIC_STUB,
                )

    def test_agent_ingest_preserved_across_audit_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            ingest_agent_angles(
                root,
                "p1",
                {
                    "schema": "argus.audit_agent_batch.v1",
                    "angles": {
                        "security": {
                            "schema": "argus.audit_angle.security.v1",
                            "angle_status": "active",
                            "summary_lines": ["security: reviewed deps (static)"],
                            "priorities": [{"rank": 1, "title": "Pin deps", "detail": "", "evidence_refs": []}],
                        }
                    },
                },
            )
            b1 = load_audit_bundle(root, "p1")
            self.assertIsNotNone(b1)
            sec1 = b1["angles"]["security"]
            cs1 = sec1.get("cursor_scan")
            self.assertIsInstance(cs1, dict)
            self.assertEqual(cs1.get("schema"), CURSOR_SCAN_SCHEMA)
            self.assertEqual(cs1.get("provenance"), PROVENANCE_CURSOR_SCAN)
            self.assertTrue(sec1.get("sources", {}).get("cursor_scan"))
            self.assertIn("reviewed deps", " ".join(sec1["summary_lines"]))
            run_audit(root, "p1")
            b2 = load_audit_bundle(root, "p1")
            sec2 = b2["angles"]["security"]
            cs2 = sec2.get("cursor_scan")
            self.assertIsInstance(cs2, dict)
            self.assertEqual(cs2.get("summary_lines"), cs1.get("summary_lines"))
            self.assertEqual(sec2["summary_lines"], sec1["summary_lines"])

    def test_reliability_agent_ingest_preserved_across_audit_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            ingest_agent_angles(
                root,
                "p1",
                {
                    "schema": "argus.audit_agent_batch.v1",
                    "angles": {
                        "reliability": {
                            "schema": "argus.audit_angle.reliability.v1",
                            "angle_status": "active",
                            "summary_lines": ["reliability: agent-reviewed runbooks (static)"],
                        }
                    },
                },
            )
            b1 = load_audit_bundle(root, "p1")
            self.assertIsNotNone(b1)
            r1 = b1["angles"]["reliability"]
            self.assertEqual(r1.get("cursor_scan", {}).get("provenance"), PROVENANCE_CURSOR_SCAN)
            self.assertIn("agent-reviewed", " ".join(r1["summary_lines"]))
            run_audit(root, "p1")
            b2 = load_audit_bundle(root, "p1")
            r2 = b2["angles"]["reliability"]
            self.assertEqual(r2.get("cursor_scan", {}).get("summary_lines"), r1["cursor_scan"]["summary_lines"])
            self.assertEqual(r2["summary_lines"], r1["summary_lines"])

    def test_unknown_not_missing_for_empty_metrics_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "nomets"
            pr = root / "products" / pid
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: nomets
name: X
owner: { team: t }
lifecycle: { stage: idea }
metrics:
  local_paths: []
  primary: []
cost: { monthly_usd: 0, notes: "" }
signals:
  - { type: filesystem, enabled: true }
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints: { max_monthly_cost_usd: 1, min_activity_threshold: 0 }
""",
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(exist_ok=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho ok\n" + "p" * 60, encoding="utf-8")
            inv = build_inventory(root)
            node = inv.valid[pid].node
            product_root, _ = resolve_product_paths(root, inv.valid[pid])
            entries = build_capability_entries(pid, product_root, node)
            metrics = [e for e in entries if e.capability_id == "audit.cap.metrics.local_paths"]
            self.assertTrue(metrics)
            self.assertEqual(metrics[0].status, AuditStatus.UNKNOWN)

    def test_malformed_audit_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = audit_product_dir(root, "bad")
            d.mkdir(parents=True)
            (d / "latest.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_latest_audit(root, "bad"))


class TestDoctorAudit(unittest.TestCase):
    def test_malformed_latest_json_warns(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "doc_p")
            bad = root / "runs" / "audit" / "doc_p" / "latest.json"
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_text("not{json", encoding="utf-8")
            err, warn, info = check_audit_artifacts(root)
            self.assertFalse(err)
            self.assertTrue(any("invalid JSON" in w for w in warn))

    def test_missing_audit_is_info_not_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "only_prod")
            (root / "runs" / "audit").mkdir(parents=True)
            err, warn, info = check_audit_artifacts(root)
            self.assertFalse(err)
            self.assertTrue(any("no audit yet" in m for m in info))


class TestContextAuditSlice(unittest.TestCase):
    def test_bundle_includes_audit_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            b = assemble_context_bundle(root, ContextPurpose.REFINEMENT_GROUNDED, "p1", draft=None)
            self.assertIn("audit", b)
            sm = b["audit"]["summary"]
            self.assertIn("already_exists", sm)
            self.assertIn("coverage", sm)
            self.assertEqual(sm["coverage"]["depth"], "quick")
            aud = b["audit"]
            self.assertIn("audit_coverage", aud)
            self.assertIn("implemented_angles", aud["audit_coverage"])
            self.assertIn("angles", aud)
            self.assertIn("product_gap", aud["angles"])


class TestIdeaAuditGating(unittest.TestCase):
    def test_implemented_overlap_nudges_down(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            idea = Idea(
                idea_id="i1",
                title="audit cap action start script",
                description="we already have audit cap action start for product",
                type=IdeaType.INVENT,
                source=IdeaSource.SYNTHESIS,
                novelty_score=0.8,
                adjacency_score=0.5,
                expected_value_score=0.7,
                confidence_score=0.6,
                cost_estimate="small",
                channel_type="hybrid",
                monetization_type="hybrid",
                rationale="test",
                product_id="p1",
            )
            before = idea.expected_value_score
            meta = apply_audit_to_ideas([idea], root, "p1")
            self.assertTrue(meta.get("applied"))
            self.assertLess(idea.expected_value_score, before)
            self.assertIsNotNone(idea.audit_adjustment)


class TestReliabilityAngle(unittest.TestCase):
    def test_reliability_partial_when_only_script_hints(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            rel = b["angles"]["reliability"]
            self.assertEqual(rel.get("schema"), "argus.audit_angle.reliability.v1")
            self.assertEqual(rel.get("angle_status"), "partial")
            self.assertNotEqual(
                rel.get("summary_lines", [""])[0],
                "reliability: not implemented (stub)",
            )
            self.assertTrue(any("ops_script_name_hints" in line for line in rel["summary_lines"]))

    def test_reliability_active_with_readme_and_ci(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            (root / "products" / "p1" / "README.md").write_text(
                "## Operations\nRunbook for local dev.\n",
                encoding="utf-8",
            )
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "ci.yml").write_text(
                "name: ci\non: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n",
                encoding="utf-8",
            )
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            rel = b["angles"]["reliability"]
            self.assertEqual(rel.get("angle_status"), "active")


class TestComplianceAngle(unittest.TestCase):
    def test_compliance_partial_when_only_yaml_markers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            comp = b["angles"]["compliance"]
            self.assertEqual(comp.get("schema"), "argus.audit_angle.compliance.v1")
            self.assertEqual(comp.get("angle_status"), "partial")
            self.assertTrue(
                any("not legal/regulatory" in line for line in comp.get("summary_lines", [])),
            )

    def test_compliance_active_when_multiple_signal_groups(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "ci.yml").write_text("on: push\njobs: {}\n", encoding="utf-8")
            (root / "CODEOWNERS").write_text("* @team\n", encoding="utf-8")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            comp = b["angles"]["compliance"]
            self.assertEqual(comp.get("angle_status"), "active")


class TestSecurityAngle(unittest.TestCase):
    def test_security_partial_when_no_dep_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            sec = b["angles"]["security"]
            self.assertEqual(sec["angle_status"], "partial")
            self.assertTrue(any("no known lockfiles" in line for line in sec["summary_lines"]))

    def test_security_active_when_lockfile_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            (root / "package-lock.json").write_text('{"lockfileVersion":2}', encoding="utf-8")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            sec = b["angles"]["security"]
            self.assertEqual(sec["angle_status"], "active")
            self.assertTrue(any("root:package-lock.json" in line for line in sec["summary_lines"]))
            self.assertIn("root:package-lock.json", sec["lockfiles_found"])


class TestStoreBusinessAngle(unittest.TestCase):
    def test_store_business_partial_when_manifest_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            sb = b["angles"]["store_business"]
            self.assertEqual(sb.get("schema"), "argus.audit_angle.store_business.v1")
            self.assertEqual(sb.get("angle_status"), "partial")
            self.assertTrue(any("evidence_score~0" in line for line in sb["summary_lines"]))

    def test_store_business_active_when_pricing_block_declared(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            py = root / "products" / "p1" / "product.yaml"
            py.write_text(
                py.read_text(encoding="utf-8")
                + "\npricing:\n  tier: test\n  note: \"declarative only\"\n",
                encoding="utf-8",
            )
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            sb = b["angles"]["store_business"]
            self.assertEqual(sb.get("angle_status"), "active")
            self.assertEqual(sb.get("manifest", {}).get("optional_yaml_blocks"), ["pricing"])
            self.assertTrue(any("optional_yaml_blocks=pricing" in line for line in sb["summary_lines"]))


class TestPerformanceAngle(unittest.TestCase):
    def test_performance_partial_when_no_perf_markers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            perf = b["angles"]["performance"]
            self.assertEqual(perf.get("schema"), "argus.audit_angle.performance.v1")
            self.assertEqual(perf.get("angle_status"), "partial")
            self.assertTrue(any("not measured" in line for line in perf["summary_lines"]))

    def test_performance_active_when_docs_contain_marker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            dd = root / "docs"
            dd.mkdir(parents=True)
            (dd / "perf.md").write_text("# SLO\nDeclared latency and timeout budgets.\n", encoding="utf-8")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            perf = b["angles"]["performance"]
            self.assertEqual(perf.get("angle_status"), "active")
            self.assertTrue(any("docs_md" in line for line in perf["summary_lines"]))


class TestUxAngle(unittest.TestCase):
    def test_ux_partial_when_no_doc_or_structure_signals(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            ux = b["angles"]["ux"]
            self.assertEqual(ux.get("schema"), "argus.audit_angle.ux.v1")
            self.assertEqual(ux.get("angle_status"), "partial")
            self.assertTrue(
                any("bounded local inventory" in line for line in ux.get("summary_lines", [])),
            )

    def test_ux_active_when_repo_readme_has_topic_marker(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            (root / "README.md").write_text("# X\nOnboarding and navigation for developers.\n", encoding="utf-8")
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            ux = b["angles"]["ux"]
            self.assertEqual(ux.get("angle_status"), "active")
            self.assertTrue(ux.get("repo_readme_topic_hit"))

    def test_ux_active_when_audit_scope_roots_declared(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            pr = root / "products" / "p1"
            py = pr / "product.yaml"
            py.write_text(
                py.read_text(encoding="utf-8")
                + "\naudit:\n  ux:\n    scope_roots:\n      - app\n",
                encoding="utf-8",
            )
            (pr / "app").mkdir()
            run_audit(root, "p1")
            b = load_audit_bundle(root, "p1")
            assert b is not None
            ux = b["angles"]["ux"]
            self.assertEqual(ux.get("angle_status"), "active")
            self.assertEqual(ux.get("declared_scope_roots"), ["app"])

    def test_ux_agent_ingest_preserved_across_audit_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            ingest_agent_angles(
                root,
                "p1",
                {
                    "schema": "argus.audit_angle.ux.v1",
                    "angle_status": "active",
                    "summary_lines": ["ux: agent-reviewed static notes"],
                },
            )
            b1 = load_audit_bundle(root, "p1")
            assert b1 is not None
            u1 = b1["angles"]["ux"]
            self.assertEqual(u1.get("cursor_scan", {}).get("provenance"), PROVENANCE_CURSOR_SCAN)
            run_audit(root, "p1")
            b2 = load_audit_bundle(root, "p1")
            assert b2 is not None
            u2 = b2["angles"]["ux"]
            self.assertEqual(u2.get("cursor_scan", {}).get("summary_lines"), u1["cursor_scan"]["summary_lines"])
            self.assertEqual(u2["summary_lines"], u1["summary_lines"])


class TestCursorScanAudit(unittest.TestCase):
    def test_build_cursor_scan_prompt_covers_requested_angles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            text = build_cursor_scan_prompt(root, "p1", angle_ids=["security", "cost"])
            self.assertIn("argus.audit_cursor_scan_batch.v1", text)
            self.assertIn("angle_id", text)
            self.assertIn("enhancements", text)
            self.assertIn("cursor_codebase_scan", text)
            lens_part = text.split("### Lenses", 1)[1]
            self.assertIn("## `security`", lens_part)
            self.assertIn("## `cost`", lens_part)
            self.assertNotIn("## `ux`", lens_part)

    def test_build_cursor_scan_prompt_for_angle_single_contract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            text = build_cursor_scan_prompt_for_angle(root, "p1", "security")
            self.assertIn("single angle `security`", text)
            self.assertIn("product_id", text)
            self.assertIn("`p1`", text)
            self.assertIn("argus.audit_cursor_scan.v1", text)
            self.assertIn("bundle.angles.security.cursor_scan", text)
            self.assertIn("Security posture", text)  # lens
            self.assertIn("lockfiles", text.lower())
            self.assertNotIn("audit_cursor_scan_batch", text)
            self.assertIn("```json", text)
            self.assertIn("angle_status", text)

    def test_normalize_angle_id_unknown(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            normalize_angle_id("not_an_angle")
        self.assertIn("Unknown angle_id", str(ctx.exception))

    def test_build_cursor_scan_prompt_for_angle_all_nine(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            for aid in ANGLE_IDS:
                text = build_cursor_scan_prompt_for_angle(root, "p1", aid)
                self.assertIn(f"single angle `{aid}`", text)
                self.assertIn(f'"angle_id": "{aid}"', text)
                self.assertIn(CURSOR_SCAN_SCHEMA, text)

    def test_cursor_scan_valid_batch_ingest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            ingest_agent_angles(
                root,
                "p1",
                {
                    "schema": "argus.audit_cursor_scan_batch.v1",
                    "angles": {
                        "cost": {
                            "schema": CURSOR_SCAN_SCHEMA,
                            "angle_id": "cost",
                            "summary_lines": ["cost: cursor noted registry drift"],
                            "findings": [
                                {
                                    "title": "Registry drift",
                                    "detail": "",
                                    "severity": "warn",
                                    "evidence_refs": ["config/economics/resources.yaml"],
                                }
                            ],
                            "risks": [{"statement": "misaligned billing id", "evidence_refs": []}],
                            "enhancements": [
                                {
                                    "title": "reconcile economics yaml",
                                    "detail": "",
                                    "severity": "info",
                                    "evidence_refs": [],
                                }
                            ],
                            "confidence": 0.7,
                            "repo_evidence_refs": ["config/economics/resources.yaml"],
                            "limitations": ["no cloud API calls"],
                            "provenance": PROVENANCE_CURSOR_SCAN,
                        }
                    },
                },
            )
            b = load_audit_bundle(root, "p1")
            assert b is not None
            c = b["angles"]["cost"].get("cursor_scan")
            self.assertIsInstance(c, dict)
            self.assertEqual(c.get("confidence"), 0.7)
            self.assertEqual(c.get("angle_id"), "cost")
            self.assertIn("deterministic_summary_lines", b["angles"]["cost"])

    def test_cursor_scan_invalid_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            with self.assertRaises(ValueError) as ctx:
                ingest_agent_angles(
                    root,
                    "p1",
                    {
                        "schema": "argus.audit_cursor_scan_batch.v1",
                        "angles": {
                            "quality": {
                                "schema": CURSOR_SCAN_SCHEMA,
                                "summary_lines": [],
                            }
                        },
                    },
                )
            self.assertIn("required keys", str(ctx.exception))

    def test_cursor_scan_batch_all_nine_angles(self) -> None:
        """Every bundle angle accepts a minimal valid cursor_scan in one batch ingest."""

        def _scan(aid: str) -> dict[str, object]:
            return {
                "schema": CURSOR_SCAN_SCHEMA,
                "angle_id": aid,
                "summary_lines": [f"{aid}: cursor layer ok"],
                "findings": [
                    {
                        "title": "spot check",
                        "detail": "",
                        "severity": "info",
                        "evidence_refs": ["products/p1/product.yaml"],
                    }
                ],
                "risks": [],
                "enhancements": [],
                "confidence": 0.55,
                "repo_evidence_refs": ["products/p1/product.yaml"],
                "limitations": ["unit test payload"],
                "provenance": PROVENANCE_CURSOR_SCAN,
            }

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            batch = {
                "schema": "argus.audit_cursor_scan_batch.v1",
                "angles": {aid: _scan(aid) for aid in ANGLE_IDS},
            }
            ingest_agent_angles(root, "p1", batch)
            b = load_audit_bundle(root, "p1")
            assert b is not None
            for aid in ANGLE_IDS:
                cs = b["angles"][aid].get("cursor_scan")
                self.assertIsInstance(cs, dict)
                self.assertEqual(cs.get("angle_id"), aid)
                self.assertEqual(cs.get("provenance"), PROVENANCE_CURSOR_SCAN)
            run_audit(root, "p1")
            b2 = load_audit_bundle(root, "p1")
            assert b2 is not None
            for aid in ANGLE_IDS:
                self.assertEqual(
                    b2["angles"][aid].get("cursor_scan", {}).get("summary_lines"),
                    b["angles"][aid].get("cursor_scan", {}).get("summary_lines"),
                )

    def test_audit_show_cursor_scan_view(self) -> None:
        from types import SimpleNamespace

        from argus.audit.cli import run_audit_command

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            run_audit(root, "p1")
            ingest_agent_angles(
                root,
                "p1",
                {
                    "schema": "argus.audit_cursor_scan_batch.v1",
                    "angles": {
                        "cost": {
                            "schema": CURSOR_SCAN_SCHEMA,
                            "angle_id": "cost",
                            "summary_lines": ["cost: x"],
                            "findings": [
                                {
                                    "title": "t",
                                    "detail": "",
                                    "severity": "info",
                                    "evidence_refs": [],
                                }
                            ],
                            "risks": [],
                            "enhancements": [],
                            "confidence": 0.5,
                            "repo_evidence_refs": [],
                            "limitations": ["t"],
                            "provenance": PROVENANCE_CURSOR_SCAN,
                        }
                    },
                },
            )
            args = SimpleNamespace(
                audit_command="show",
                product_id="p1",
                legacy_product_gap=False,
                merged_text=False,
                cursor_scan=True,
                json=False,
            )
            # run_audit_command prints; capture via stdout
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_audit_command(args, root)
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertEqual(out.get("schema"), "argus.audit_cursor_scan_view.v1")
            self.assertEqual(out.get("product_id"), "p1")
            self.assertIsInstance(out["angles"]["cost"], dict)
            self.assertIsNone(out["angles"]["security"])


class TestScanner(unittest.TestCase):
    def test_bounded_walk(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            pr = root / "products" / "p1"
            scanned, excl = bounded_walk(pr)
            self.assertTrue(any("product.yaml" in s for s in scanned))
            self.assertIsInstance(excl, list)
