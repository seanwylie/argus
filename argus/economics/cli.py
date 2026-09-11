"""CLI: ``argus economics analyze`` and ``argus economics portfolio``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.economics.analyze import analyze_inventory, economics_to_jsonable
from argus.economics.resources_report import build_resource_report, resource_report_to_jsonable


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def _write_resources_latest(repo: Path, payload: dict[str, Any]) -> Path:
    base = repo / "runs" / "economics"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "resources_latest.json"
    path.write_text(dumps_json(payload), encoding="utf-8")
    return path


def _write_latest(repo: Path, payload: dict[str, Any]) -> Path:
    base = repo / "runs" / "economics"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "latest.json"
    path.write_text(dumps_json(payload), encoding="utf-8")
    return path


def _print_product_line(p: Any) -> None:
    roi = f"{p.roi_estimate:.3f}" if p.roi_estimate is not None else "n/a"
    print(
        f"{p.product_id:20}  cost=${p.monthly_cost:8.2f}  rev~=${p.estimated_revenue:8.2f}  "
        f"ROI={roi:>8}  burn=${p.burn_rate:8.2f}  growth={p.growth_signal.value}"
    )


def cmd_economics_analyze(repo: Path, args: Any) -> int:
    products, portfolio = analyze_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    payload = economics_to_jsonable(products, portfolio)
    out_path = _write_latest(repo, payload)

    if args.json:
        print(dumps_json(payload))
    else:
        print("Product economics (declared cost + signal-derived MRR when present)\n")
        for p in products:
            _print_product_line(p)
        print()
        print(
            f"Totals: cost ${portfolio.total_monthly_cost:.2f}  "
            f"revenue~ ${portfolio.total_estimated_revenue:.2f}  "
            f"net ${portfolio.net_monthly_margin:.2f}",
            end="",
        )
        if portfolio.portfolio_roi is not None:
            print(f"  portfolio ROI {portfolio.portfolio_roi:.3f}")
        else:
            print("  portfolio ROI n/a (zero total cost)")
        print(f"\nWrote {out_path.relative_to(repo.resolve())}", file=sys.stderr)

    return 0


def cmd_economics_portfolio(repo: Path, args: Any) -> int:
    products, portfolio = analyze_inventory(repo, products_dir=_products_dir(repo, args.products_dir))
    payload = economics_to_jsonable(products, portfolio)
    out_path = _write_latest(repo, payload)

    if args.json:
        print(dumps_json({"portfolio": payload["portfolio"], "products": payload["products"]}))
    else:
        print("Portfolio economics\n")
        print(
            f"  Total monthly cost:     ${portfolio.total_monthly_cost:.2f}\n"
            f"  Total estimated revenue: ${portfolio.total_estimated_revenue:.2f}\n"
            f"  Net monthly margin:      ${portfolio.net_monthly_margin:.2f}"
        )
        pr = portfolio.portfolio_roi
        print(f"  Portfolio ROI:           {pr:.3f}\n" if pr is not None else "  Portfolio ROI:           n/a\n")
        print(f"  Top performers:    {', '.join(portfolio.top_performers) or '(none)'}")
        print(f"  Worst performers:  {', '.join(portfolio.worst_performers) or '(none)'}\n")
        if portfolio.signals:
            print("  Signals:")
            for s in portfolio.signals:
                print(f"    [{s.kind.value}] {s.product_id}: {s.message}")
        else:
            print("  Signals: (none)")
        print(f"\nWrote {out_path.relative_to(repo.resolve())}", file=sys.stderr)

    return 0


def cmd_economics_resources(repo: Path, args: Any) -> int:
    report = build_resource_report(repo, products_dir=_products_dir(repo, args.products_dir))
    payload = resource_report_to_jsonable(report)
    out_path = _write_resources_latest(repo, payload)

    if args.json:
        print(dumps_json(payload))
    else:
        print("Cost resources (registry + ingest + signal-derived observed costs)\n")
        print(f"  Mapped cost (valid products): ${report.total_mapped_cost_usd:.2f}/mo")
        print(f"  Orphan / unmapped cost:      ${report.total_orphan_cost_usd:.2f}/mo")
        print(f"  Thresholds (high-cost/low-value): {report.thresholds}\n")
        if report.resources:
            print("Resources:")
            for r in report.resources:
                pid = r.product_id or "—"
                print(f"  {r.id:40}  {r.kind:20}  ${r.monthly_cost_usd:10.2f}  product={pid}  [{r.source}]")
        else:
            print("Resources: (none — add config/economics/resources.json or runs/economics/cost_ingest.json)")
        print()
        if report.orphan_resources:
            print("Unused / unmapped resources:")
            for o in report.orphan_resources:
                print(f"  {o.resource_id:40}  ${o.monthly_cost_usd:10.2f}  {o.reason}")
        else:
            print("Unused / unmapped resources: (none)")
        print()
        if report.high_cost_low_value:
            print("High-cost, low-value products (heuristic):")
            for h in report.high_cost_low_value:
                roi = f"{h.roi_estimate:.3f}" if h.roi_estimate is not None else "n/a"
                print(
                    f"  {h.product_id:24}  cost=${h.monthly_cost:8.2f}  rev~=${h.estimated_revenue:8.2f}  "
                    f"ROI={roi:>8}  — {h.reason}"
                )
        else:
            print("High-cost, low-value products: (none)")
        print(f"\nWrote {out_path.relative_to(repo.resolve())}", file=sys.stderr)

    return 0


def run_economics_subcommand(args: Any) -> int:
    repo = repo_root()
    cmd = getattr(args, "economics_command", None)
    if cmd == "analyze":
        return cmd_economics_analyze(repo, args)
    if cmd == "portfolio":
        return cmd_economics_portfolio(repo, args)
    if cmd == "resources":
        return cmd_economics_resources(repo, args)
    print("Unknown economics subcommand.", file=sys.stderr)
    return 2
