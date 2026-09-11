"""Unit tests for GitHub URL parsing and exclude list composition."""

from __future__ import annotations

import json

import pytest

from argus.importer.constants import build_exclude_list
from argus.importer.evaluate import (
    FirstPassMetrics,
    classify_first_pass_status,
    load_first_pass_run_anchors,
    write_first_pass_summary,
)
from argus.importer.import_state import extract_import_state
from argus.importer.url import parse_github_repo


def test_parse_github_repo() -> None:
    r = parse_github_repo("https://github.com/acme/widget.git")
    assert r.owner == "acme"
    assert r.repo == "widget"
    assert r.normalized_url == "https://github.com/acme/widget"
    assert r.slug == "acme_widget"


def test_parse_github_repo_trailing_slash() -> None:
    r = parse_github_repo("https://github.com/acme/widget/")
    assert r.repo == "widget"


def test_parse_github_repo_rejects_non_github() -> None:
    with pytest.raises(ValueError, match="github"):
        parse_github_repo("https://gitlab.com/acme/widget")


def test_build_exclude_list_defaults() -> None:
    ex = build_exclude_list(
        include_cursor=False,
        include_local_db=False,
        exclude_node_artifacts=False,
        extra=[],
    )
    assert ".git/" not in ex
    assert ".cursor/" in ex
    assert "*.db" in ex


def test_build_exclude_list_legacy_flat_copy_excludes_git() -> None:
    ex = build_exclude_list(
        include_cursor=False,
        include_local_db=False,
        exclude_node_artifacts=False,
        extra=[],
        preserve_product_git=False,
    )
    assert ".git/" in ex


def test_build_exclude_list_opt_ins() -> None:
    ex = build_exclude_list(
        include_cursor=True,
        include_local_db=True,
        exclude_node_artifacts=True,
        extra=["custom/"],
    )
    assert ".cursor/" not in ex
    assert "*.db" not in ex
    assert "custom/" in ex
    assert "node_modules/" in ex


def test_classify_first_pass_status() -> None:
    assert classify_first_pass_status(skipped=True, metrics=None) == "skipped"
    assert classify_first_pass_status(skipped=False, metrics=None) == "failed"
    m_ok = FirstPassMetrics()
    m_ok.command_log = [(f"c{i}", 0) for i in range(6)]
    assert classify_first_pass_status(skipped=False, metrics=m_ok) == "success"
    m_bad = FirstPassMetrics()
    m_bad.command_log = [(f"c{i}", 0 if i < 5 else 1) for i in range(6)]
    assert classify_first_pass_status(skipped=False, metrics=m_bad) == "partial"
    m_short = FirstPassMetrics()
    m_short.command_log = [("a", 0)]
    assert classify_first_pass_status(skipped=False, metrics=m_short) == "failed"
    m_err = FirstPassMetrics()
    m_err.evaluation_error = "boom"
    m_err.command_log = [("x", 0)] * 6
    assert classify_first_pass_status(skipped=False, metrics=m_err) == "failed"


def test_extract_import_state() -> None:
    data = {
        "raw_extensions": {
            "import_state": {"schema": "argus.import_state.v1", "import_mode": "cache_sync"},
        },
    }
    st = extract_import_state(data)
    assert st is not None
    assert st.get("import_mode") == "cache_sync"
    assert extract_import_state({}) is None


def test_load_first_pass_run_anchors_reads_bundles(tmp_path) -> None:
    pid = "demo_prod"
    (tmp_path / "runs" / "signals" / "latest").mkdir(parents=True)
    (tmp_path / "runs" / "ideas").mkdir(parents=True)
    (tmp_path / "runs" / "orchestration" / "latest").mkdir(parents=True)
    (tmp_path / "runs" / "signals" / "latest" / f"{pid}.json").write_text(
        json.dumps({"collected_at_utc": "2026-01-02T00:00:00+00:00", "record_count": 3}),
        encoding="utf-8",
    )
    (tmp_path / "runs" / "ideas" / "latest.json").write_text(
        json.dumps(
            {
                "meta": {
                    "timestamp_slug": "20260102T000000Z",
                    "selection_summary": {
                        "stopped_early_for_quality": True,
                        "rejected_below_quality_threshold": 2,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runs" / "orchestration" / "latest" / f"{pid}.json").write_text(
        json.dumps(
            {
                "orchestration_status": "eligible",
                "eligibility_facts": {
                    "temporal_worst_freshness_status": "fresh",
                    "signals_refresh_needed": False,
                },
            }
        ),
        encoding="utf-8",
    )
    a = load_first_pass_run_anchors(tmp_path, pid)
    assert a["signals_latest"]["record_count"] == 3
    assert a["ideas_latest"]["meta_timestamp_slug"] == "20260102T000000Z"
    assert a["orchestration_latest"]["orchestration_status"] == "eligible"


def test_write_first_pass_summary_includes_anchors_and_confidence(tmp_path) -> None:
    pid = "demo_prod"
    prod = tmp_path / "products" / pid
    prod.mkdir(parents=True)
    (tmp_path / "runs" / "signals" / "latest").mkdir(parents=True)
    (tmp_path / "runs" / "ideas").mkdir(parents=True)
    (tmp_path / "runs" / "signals" / "latest" / f"{pid}.json").write_text(
        json.dumps({"collected_at_utc": "2026-01-01T00:00:00+00:00", "record_count": 10}),
        encoding="utf-8",
    )
    (tmp_path / "runs" / "ideas" / "latest.json").write_text(
        json.dumps({"meta": {"timestamp_slug": "20260101T000000Z", "selection_summary": {}}}),
        encoding="utf-8",
    )
    m = FirstPassMetrics()
    m.command_log = [(f"c{i}", 0) for i in range(6)]
    m.signals_record_count = 10
    m.manifest_declaration_rows = 5
    m.non_manifest_rows = 5
    m.findings_count = 1
    m.decisions_candidates = 0
    m.ideas_count = 2
    inst = {
        "product_type": "saas_app",
        "top_level": ["README.md", "src"],
        "first_pass": {"first_pass_status": "success"},
        "repo_snapshot": {
            "test_path": "tests/test_x.py",
            "doc_path": "docs/",
            "has_security_md": False,
            "notable_layout_dirs": ["src"],
        },
    }
    write_first_pass_summary(
        prod,
        tmp_path,
        pid,
        m,
        import_instrumentation=inst,
        use_uv=True,
    )
    text = (prod / "first_pass_argus_summary.md").read_text(encoding="utf-8")
    assert "Deterministic run anchors" in text
    assert "meta.timestamp_slug" in text
    assert "saas_app" in text
    assert "manifest_declaration" in text
    assert "tests/test_x.py" in text
    assert "SECURITY.md present" in text


def test_status_cmd_minimal_product(tmp_path) -> None:
    from argus.importer.status_cmd import run_status

    prod = tmp_path / "products" / "probe"
    prod.mkdir(parents=True)
    (prod / "product.yaml").write_text(
        """
id: probe
name: Probe
raw_extensions:
  import_state:
    schema: argus.import_state.v1
    import_mode: cache_sync
    source_repo_url: https://github.com/o/r
    cache_slug: o_r
    imported_at_utc: "2026-01-01T00:00:00Z"
    sync_excludes: [".git/"]
    include_cursor: false
    include_local_db_artifacts: false
    exclude_node_artifacts: true
    extra_excludes: []
    first_pass_ran: false
    first_pass_status: skipped
    first_pass_summary_path: null
""",
        encoding="utf-8",
    )
    assert run_status(tmp_path, "probe", json_out=False) == 0
