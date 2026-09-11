"""Tests for :mod:`argus.portfolio.github_onboarding`."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.portfolio.github_onboarding import (
    GITHUB_ONBOARDING_PAYLOAD_SCHEMA,
    parse_github_https_url,
    parse_github_url,
    run_github_onboarding,
)


class TestParseGitHubUrl(unittest.TestCase):
    def test_ok(self) -> None:
        p = parse_github_https_url("https://github.com/acme/sample-service")
        self.assertEqual(p["owner"], "acme")
        self.assertEqual(p["repo"], "sample-service")
        self.assertEqual(p["default_product_id_hint"], "sample-service")
        self.assertIn("github.com/acme/sample-service.git", p["clone_url"])

    def test_optional_git_suffix(self) -> None:
        p = parse_github_https_url("https://github.com/acme/sample-service.git")
        self.assertEqual(p["repo"], "sample-service")

    def test_rejects_non_github(self) -> None:
        with self.assertRaises(ValueError):
            parse_github_https_url("https://gitlab.com/a/b")


class TestParseGitHubUrlSsh(unittest.TestCase):
    def test_ok(self) -> None:
        p = parse_github_url("git@github.com:acme/example-app.git")
        self.assertEqual(p["owner"], "acme")
        self.assertEqual(p["repo"], "example-app")
        self.assertEqual(p["default_product_id_hint"], "example-app")
        self.assertEqual(p["clone_url"], "git@github.com:acme/example-app.git")

    def test_optional_git_suffix(self) -> None:
        p = parse_github_url("git@github.com:acme/example-app")
        self.assertEqual(p["repo"], "example-app")
        self.assertEqual(p["clone_url"], "git@github.com:acme/example-app.git")

    def test_rejects_malformed_ssh(self) -> None:
        with self.assertRaises(ValueError):
            parse_github_url("git@gitlab.com:acme/example-app.git")


class TestGithubOnboardingFlow(unittest.TestCase):
    def test_dry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = run_github_onboarding(
                root,
                github_url="https://github.com/acme/demo",
                dry_run=True,
                write_artifacts=False,
            )
            self.assertEqual(pl["schema"], GITHUB_ONBOARDING_PAYLOAD_SCHEMA)
            self.assertTrue(pl.get("ok"))
            self.assertEqual(pl.get("product_id"), "demo")

    def test_end_to_end_mocked_git_and_bootstrap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if cmd[:3] == ["git", "clone", "--depth"]:
                    dest = Path(cmd[-1])
                    dest.mkdir(parents=True)
                    (dest / "README.md").write_text("# hi\n", encoding="utf-8")
                    (dest / ".git").mkdir()
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                if len(cmd) >= 3 and cmd[0] == "git" and cmd[2] == "rev-parse":
                    return subprocess.CompletedProcess(cmd, 0, "deadbeef\n", "")
                return subprocess.CompletedProcess(cmd, 1, "", "unexpected")

            boot = {
                "ok": True,
                "schema": "argus.product_creation_bootstrap.v1",
                "steps_executed": [{"step": "signals_collect", "status": "ok"}],
            }

            with patch("argus.portfolio.github_onboarding.subprocess.run", side_effect=fake_run):
                with patch(
                    "argus.portfolio.github_onboarding.evaluate_product_creation_bootstrap",
                    return_value=boot,
                ):
                    with patch("argus.portfolio.github_onboarding.write_orchestration_state") as wos:
                        wos.return_value = root / "runs" / "orchestration" / "latest" / "ghdemo.json"
                        with patch(
                            "argus.portfolio.github_onboarding.build_product_readiness_payload",
                            return_value={
                                "evidence_maturity_hint": "thin_bootstrap",
                                "thin_evidence_baseline": True,
                            },
                        ):
                            pl = run_github_onboarding(
                                root,
                                github_url="git@github.com:acme/example-app.git",
                                product_id="example-app",
                                write_artifacts=False,
                            )
            self.assertTrue(pl.get("ok"))
            self.assertEqual(pl.get("product_id"), "example-app")
            self.assertIn("argus_github_onboarding", (root / "products" / "example-app" / "product.yaml").read_text())
            self.assertEqual(pl.get("readiness_summary", {}).get("evidence_maturity_hint"), "thin_bootstrap")


if __name__ == "__main__":
    unittest.main()
