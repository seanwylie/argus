"""Structural tests for the refactored Argus CLI parser (no behavior change intended)."""

from __future__ import annotations

import unittest

from argus.cli.dispatch import dispatch
from argus.cli.main import build_parser


def _top_level_command_names(parser) -> set[str]:
    for action in parser._actions:
        if getattr(action, "dest", None) == "command" and getattr(action, "choices", None):
            return set(action.choices.keys())
    raise AssertionError("expected subparsers action with dest=command")


class TestCliParserStructure(unittest.TestCase):
    def test_build_parser_exposes_major_commands(self) -> None:
        names = _top_level_command_names(build_parser())
        for cmd in (
            "products",
            "signals",
            "findings",
            "decisions",
            "orchestration",
            "loop",
            "experiments",
            "doctor",
            "reset",
            "scan",
        ):
            self.assertIn(cmd, names, msg=f"missing top-level command {cmd!r}")

    def test_nested_parse_products_list(self) -> None:
        args = build_parser().parse_args(["products", "list"])
        self.assertEqual(args.command, "products")
        self.assertEqual(args.products_command, "list")

    def test_nested_parse_orchestration_state(self) -> None:
        args = build_parser().parse_args(
            ["orchestration", "state", "--product-id", "myproduct", "--no-write"]
        )
        self.assertEqual(args.command, "orchestration")
        self.assertEqual(args.orchestration_command, "state")
        self.assertEqual(args.product_ids, ["myproduct"])

    def test_dispatch_returns_int_for_doctor_json(self) -> None:
        args = build_parser().parse_args(["doctor", "--json"])
        code = dispatch(args)
        self.assertIsInstance(code, int)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
