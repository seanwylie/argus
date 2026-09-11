"""Doctor checks for economics resource linkage."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.cli.doctor_cmd import cmd_doctor


class _Args:
    products_dir = None
    json = True


class TestDoctorEconomics(unittest.TestCase):
    def test_invalid_config_resources_json_warns_via_stdout(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            cfg = root / "config" / "economics" / "resources.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text("{not json", encoding="utf-8")
            import io
            import sys

            buf = io.StringIO()
            old = sys.stdout
            try:
                sys.stdout = buf
                a = _Args()
                rc = cmd_doctor(root, a)
            finally:
                sys.stdout = old
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertTrue(any("resources.json" in w for w in out["warnings"]))

    def test_ingest_without_latest_info(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            ing = root / "runs" / "economics" / "cost_ingest.json"
            ing.parent.mkdir(parents=True)
            ing.write_text(json.dumps({"resources": []}), encoding="utf-8")
            import io
            import sys

            buf = io.StringIO()
            old = sys.stdout
            try:
                sys.stdout = buf
                a = _Args()
                rc = cmd_doctor(root, a)
            finally:
                sys.stdout = old
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertTrue(any("economics resources" in i.lower() for i in out["info"]))

    def test_orphan_spend_warns(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            latest = root / "runs" / "economics" / "resources_latest.json"
            latest.parent.mkdir(parents=True)
            latest.write_text(
                json.dumps(
                    {
                        "total_orphan_cost_usd": 12.5,
                        "orphan_resources": [{"resource_id": "x", "kind": "s3", "monthly_cost_usd": 12.5, "reason": "no_product_mapping"}],
                        "high_cost_low_value": [],
                    }
                ),
                encoding="utf-8",
            )
            import io
            import sys

            buf = io.StringIO()
            old = sys.stdout
            try:
                sys.stdout = buf
                a = _Args()
                rc = cmd_doctor(root, a)
            finally:
                sys.stdout = old
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertTrue(any("unmapped resource cost" in w for w in out["warnings"]))


if __name__ == "__main__":
    unittest.main()
