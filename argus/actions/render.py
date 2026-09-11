"""Human-readable formatting for action validation and dry-run."""

from __future__ import annotations

from argus.actions.models import ActionContract, DryRunResult, ExecuteResult
from argus.core.serialize import dumps_json, to_jsonable


def format_action_show(contract: ActionContract) -> str:
    """Full action contract for ``argus actions show``."""
    lines = [
        f"action_id:           {contract.action_id}",
        f"product_id:          {contract.product_id}",
        f"action_type:         {contract.action_type}",
        f"working_directory:   {contract.working_directory}",
        f"requires_approval:   {contract.requires_approval}",
        f"safe_to_auto_execute:{contract.safe_to_auto_execute}",
        f"estimated_duration:  {contract.estimated_duration or '(unset)'}",
        f"created_at:          {contract.created_at.isoformat() if contract.created_at else '(unset)'}",
        "",
        "command:",
        contract.command,
        "",
        "expected_outcome:",
        contract.expected_outcome or "(empty)",
        "",
        "rollback_notes:",
        contract.rollback_notes or "(empty)",
        "",
    ]
    if contract.lifecycle_transition is not None:
        lt = contract.lifecycle_transition
        lines.extend(
            [
                "lifecycle_transition:",
                f"  from: {lt.from_stage}",
                f"  to:   {lt.to_stage}",
                "",
            ]
        )
    return "\n".join(lines)


def format_validation_text(errors: list[str]) -> str:
    if not errors:
        return "OK — action contract is valid.\n"
    lines = ["Validation failed:"]
    lines.extend(f"  - {e}" for e in errors)
    lines.append("")
    return "\n".join(lines)


def format_dry_run_text(result: DryRunResult) -> str:
    lines: list[str] = [
        "=== Dry run (no command executed) ===",
        "",
        result.rendered_preview,
        "",
    ]
    if result.validation_errors:
        lines.append("Validation errors:")
        for e in result.validation_errors:
            lines.append(f"  - {e}")
        lines.append("")
    else:
        lines.append("Validation: OK")
        lines.append("")

    if result.file_checks:
        lines.append("Referenced paths:")
        for fc in result.file_checks:
            st = "exists" if fc.exists else "MISSING"
            lines.append(f"  [{fc.kind}] {fc.path} — {st}")
        lines.append("")
    else:
        lines.append("Referenced paths: (none detected)")
        lines.append("")

    if result.dangerous_flags:
        lines.append("Dangerous / high-risk patterns:")
        for d in result.dangerous_flags:
            lines.append(f"  ! {d}")
        lines.append("")
    else:
        lines.append("Dangerous patterns: none detected")
        lines.append("")

    if result.notes:
        lines.append("Notes:")
        for n in result.notes:
            lines.append(f"  - {n}")
        lines.append("")

    lines.append(f"safe_to_proceed (valid + no danger flags): {result.safe_to_proceed}")
    lines.append("")
    return "\n".join(lines)


def dry_run_to_jsonable(result: DryRunResult) -> object:
    return to_jsonable(result)


def dry_run_to_json(result: DryRunResult, *, indent: int = 2) -> str:
    return dumps_json(dry_run_to_jsonable(result), indent=indent)


def format_execute_text(result: ExecuteResult) -> str:
    """Human-readable output for ``argus actions execute``."""
    lines: list[str] = ["=== Execute ===", ""]
    if result.dry_run_snapshot is not None:
        lines.append("Dry-run snapshot (pre-execution):")
        lines.append(result.dry_run_snapshot.rendered_preview)
        lines.append("")
    lines.append(f"returncode: {result.returncode}")
    if result.stdout:
        lines.extend(["", "stdout:", result.stdout.rstrip()])
    if result.stderr:
        lines.extend(["", "stderr:", result.stderr.rstrip()])
    lines.append("")
    return "\n".join(lines)


def execute_to_json(result: ExecuteResult, *, indent: int = 2) -> str:
    return dumps_json(to_jsonable(result), indent=indent)
