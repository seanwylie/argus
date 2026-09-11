"""Builder: deterministic next_expansion.json from a product's content catalog."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.builder.next_expansion_generate import (
    CONTENT_CATALOG_SCHEMA,
    NextExpansionGenerateError,
    generate_and_write_next_expansion,
    generate_next_expansion_payload,
)
from argus.builder.next_expansion_prepare import build_prepare_result
from argus.cli.builder_cmd import run_builder_subcommand

PRODUCT_ID = "demo-product"


def _minimal_catalog(groups: list[dict]) -> dict:
    return {
        "schema": CONTENT_CATALOG_SCHEMA,
        "groups": groups,
    }


def _write_catalog(root: Path, catalog: dict, *, product_id: str = PRODUCT_ID) -> Path:
    content_dir = root / "products" / product_id / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    path = content_dir / "content_catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def _group(ordinal: int, statuses: list[str], *, title: str | None = None) -> dict:
    return {
        "id": f"group_{ordinal:02d}",
        "title": title or f"Group {ordinal}",
        "ordinal": ordinal,
        "slots": [
            {"id": f"group_{ordinal:02d}_slot_{i:02d}", "ordinal": i, "status": status}
            for i, status in enumerate(statuses, start=1)
        ],
    }


def test_any_product_with_a_catalog_is_eligible(tmp_path: Path) -> None:
    """The catalog is the opt-in: no product identity is consulted."""
    _write_catalog(
        tmp_path,
        _minimal_catalog([_group(1, ["seeded", "planned"])]),
        product_id="some-other-product",
    )
    payload = generate_next_expansion_payload(tmp_path, "some-other-product")
    assert payload["primary_target"]["id"] == "group_01_slot_02"


def test_unsupported_catalog_schema_raises(tmp_path: Path) -> None:
    catalog = _minimal_catalog([_group(1, ["seeded", "planned"])])
    catalog["schema"] = "some.other.schema.v1"
    _write_catalog(tmp_path, catalog)
    with pytest.raises(NextExpansionGenerateError, match="Unsupported catalog schema"):
        generate_next_expansion_payload(tmp_path, PRODUCT_ID)


def test_picks_next_slot_after_chain(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        _minimal_catalog([_group(1, ["seeded", "outlined", "planned"])]),
    )
    payload = generate_next_expansion_payload(tmp_path, PRODUCT_ID)
    assert payload["primary_target"]["id"] == "group_01_slot_03"
    assert payload["primary_target"]["target_type"] == "content_slot"
    assert payload["schema"] == "argus.next_expansion.v1"


def test_prefers_richer_group(tmp_path: Path) -> None:
    _write_catalog(
        tmp_path,
        _minimal_catalog(
            [
                _group(1, ["seeded", "planned"]),
                _group(2, ["seeded", "outlined", "outlined", "planned"]),
            ]
        ),
    )
    payload = generate_next_expansion_payload(tmp_path, PRODUCT_ID)
    assert payload["primary_target"]["group_id"] == "group_02"
    assert payload["primary_target"]["group_ordinal"] == 2
    assert payload["primary_target"]["id"] == "group_02_slot_04"


def test_no_planned_slot_raises(tmp_path: Path) -> None:
    _write_catalog(tmp_path, _minimal_catalog([_group(1, ["outlined"] * 9)]))
    with pytest.raises(NextExpansionGenerateError, match="No planned slot"):
        generate_next_expansion_payload(tmp_path, PRODUCT_ID)


def test_missing_catalog_raises(tmp_path: Path) -> None:
    (tmp_path / "products" / PRODUCT_ID / "content").mkdir(parents=True)
    with pytest.raises(NextExpansionGenerateError, match="Missing content catalog"):
        generate_next_expansion_payload(tmp_path, PRODUCT_ID)


def test_write_and_prepare_compatible(tmp_path: Path) -> None:
    _write_catalog(tmp_path, _minimal_catalog([_group(1, ["seeded", "planned"])]))
    _p, path = generate_and_write_next_expansion(tmp_path, PRODUCT_ID)
    assert path.is_file()
    res = build_prepare_result(tmp_path, PRODUCT_ID)
    assert res.task["resolved_target"]["id"] == "group_01_slot_02"


def test_cli_next_expansion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_catalog(tmp_path, _minimal_catalog([_group(1, ["seeded", "planned"])]))
    monkeypatch.setattr("argus.cli.builder_cmd.repo_root", lambda: tmp_path)
    args = Namespace(
        builder_command="next-expansion",
        product_id=PRODUCT_ID,
        json=True,
        no_save=True,
        products_dir=None,
    )
    assert run_builder_subcommand(args) == 0
