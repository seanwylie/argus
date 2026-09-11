"""Doctor ``spine`` checks (adapters, loop harness, blocked_actions, signals light parse)."""

from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from argus.cli.doctor_cmd import cmd_doctor


def _args(**kwargs: object) -> SimpleNamespace:
    base = {"products_dir": None, "json": False, "strict": False}
    base.update(kwargs)
    return SimpleNamespace(**base)


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: {pid}
owner:
  team: t
lifecycle:
  stage: validate
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 0
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")


class TestDoctorSpine(unittest.TestCase):
    def test_spine_json_keys_and_chain_hint(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertIn("spine", out)
            sp = out["spine"]
            self.assertIn("adapter_warnings", sp)
            self.assertIn("loop_harness_notes", sp)
            self.assertIn("chain_hint", sp)
            self.assertIn("escalation generate", sp["chain_hint"])
            infos = "\n".join(out.get("info") or [])
            self.assertIn("escalation generate", infos)

    def test_unknown_adapter_id_in_config(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            cfg = root / "config" / "adapters.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(
                json.dumps(
                    {
                        "schema": "argus.adapters_config.v1",
                        "enabled_ids": ["execution", "not_a_registered_adapter_id"],
                    }
                ),
                encoding="utf-8",
            )
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            aw = out["spine"]["adapter_warnings"]
            self.assertTrue(any("not_a_registered_adapter_id" in x for x in aw))
            self.assertTrue(any("not_a_registered_adapter_id" in x for x in out["warnings"]))

    def test_blocked_actions_parse_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            p = root / "runs" / "autonomy" / "blocked_actions.json"
            p.parent.mkdir(parents=True)
            p.write_text("{", encoding="utf-8")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            notes = out["spine"]["loop_harness_notes"]
            self.assertTrue(any("blocked_actions" in x for x in notes))

    def test_latest_loop_failed_stage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            run_dir = root / "runs" / "loop" / "20260110T120000Z_deadbeef"
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.loop_run.v1",
                        "run_id": "20260110T120000Z_deadbeef",
                        "stages": [
                            {"stage": "signals", "ok": False, "error": "boom"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            notes = "\n".join(out["spine"]["loop_harness_notes"])
            self.assertIn("signals", notes)
            self.assertIn("failed", notes)

    def test_signals_latest_light_parse(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            sp = root / "runs" / "signals" / "latest" / "x.json"
            sp.parent.mkdir(parents=True)
            sp.write_text("not json", encoding="utf-8")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            notes = "\n".join(out["spine"]["loop_harness_notes"])
            self.assertIn("signals/latest", notes)


if __name__ == "__main__":
    unittest.main()
