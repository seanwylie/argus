"""Strict validation for ``products/<id>/argus.policy.yaml`` (Phase 2 Track B)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from argus.project_permissions.defaults import DEFAULT_PHASE1
from argus.project_permissions.errors import ProjectPermissionPolicyError
from argus.project_permissions.gate import evaluate_phase1_for_keys
from argus.project_permissions.load import (
    default_policy_yaml_text,
    load_project_permission_policy,
    write_default_policy_file,
)
from argus.project_permissions.schema import PHASE1_KEYS
from argus.project_permissions.value_parse import parse_strict_permission_value


def _product_dir(root: Path, pid: str) -> Path:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
owner:
  team: test
lifecycle:
  stage: idea
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
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 1
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    return pr


def test_quoted_yes_no_confirm_pass(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            schema: argus.project_permission_policy.v1
            observe_prod_signals: "yes"
            mutate_nonprod: "no"
            mutate_prod: "no"
            commit_local: "confirm"
            push_remote: "no"
            deploy: "no"
            change_experiments: "no"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    pol = load_project_permission_policy(tmp_path, "p1")
    assert pol.get("mutate_nonprod") == "no"
    assert pol.get("commit_local") == "confirm"


def test_unquoted_yes_fails_boolean(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        "mutate_nonprod: yes\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectPermissionPolicyError) as ei:
        load_project_permission_policy(tmp_path, "p1")
    assert "boolean" in str(ei.value).lower()


def test_unquoted_no_fails_boolean(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        "mutate_nonprod: no\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectPermissionPolicyError) as ei:
        load_project_permission_policy(tmp_path, "p1")
    assert "boolean" in str(ei.value).lower()


def test_yaml_true_false_fail(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        "mutate_nonprod: true\ndeploy: false\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectPermissionPolicyError) as ei:
        load_project_permission_policy(tmp_path, "p1")
    msg = str(ei.value).lower()
    assert "boolean" in msg


def test_invalid_string_allow(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        'mutate_nonprod: "allow"\n',
        encoding="utf-8",
    )
    with pytest.raises(ProjectPermissionPolicyError) as ei:
        load_project_permission_policy(tmp_path, "p1")
    assert "allow" in str(ei.value) or "invalid" in str(ei.value).lower()


def test_null_explicit_fails(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        "mutate_nonprod: null\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectPermissionPolicyError):
        load_project_permission_policy(tmp_path, "p1")


def test_mixed_valid_invalid_fails_entire_policy(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        textwrap.dedent(
            """
            deploy: "no"
            mutate_nonprod: yes
            observe_prod_signals: "yes"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectPermissionPolicyError) as ei:
        load_project_permission_policy(tmp_path, "p1")
    err = str(ei.value)
    assert "mutate_nonprod" in err


def test_missing_file_uses_defaults_all_keys(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    pol = load_project_permission_policy(tmp_path, "p1")
    assert pol.load_warnings
    for k in PHASE1_KEYS:
        assert pol.get(k) == DEFAULT_PHASE1[k]


def test_default_policy_roundtrip_strict(tmp_path: Path) -> None:
    pr = _product_dir(tmp_path, "p1")
    write_default_policy_file(pr)
    pol = load_project_permission_policy(tmp_path, "p1")
    assert not pol.load_warnings
    for k in PHASE1_KEYS:
        assert pol.get(k) == DEFAULT_PHASE1[k]


def test_default_policy_yaml_text_only_quoted_values() -> None:
    text = default_policy_yaml_text()
    assert ': "yes"' in text or ': "confirm"' in text
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("schema:") or s.startswith("#"):
            continue
        _, rhs = s.split(":", 1)
        rhs = rhs.strip()
        assert rhs.startswith('"') and rhs.endswith('"'), ln


def test_evaluate_returns_error_invalid_policy(tmp_path: Path) -> None:
    _product_dir(tmp_path, "p1")
    (tmp_path / "products" / "p1" / "argus.policy.yaml").write_text(
        "mutate_nonprod: yes\n",
        encoding="utf-8",
    )
    ev = evaluate_phase1_for_keys(
        tmp_path,
        "p1",
        ("mutate_nonprod",),
        check_environment=False,
        execution_path="orchestration_step_executor",
        action_description="t",
    )
    assert ev.get("aggregate_decision") == "error_invalid_policy"
    assert ev.get("execution_proceeds") is False


def test_parse_strict_rejects_bool() -> None:
    with pytest.raises(ValueError, match="boolean"):
        parse_strict_permission_value("mutate_nonprod", True)


def test_parse_strict_accepts_string_variants() -> None:
    assert parse_strict_permission_value("k", "YES") == "yes"
    assert parse_strict_permission_value("k", " Confirm ") == "confirm"
