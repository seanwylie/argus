"""Structured diagnostics for dashboard builds (warnings, errors, integrity hints)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("argus.dashboard")


Severity = Literal["info", "warning", "error"]


@dataclass
class DashboardDiagnostics:
    """Collects issues during payload construction; supports strict mode."""

    info: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def _append(
        self,
        bucket: list[dict[str, Any]],
        *,
        code: str,
        message: str,
        path: Path | str | None,
        severity: Severity,
    ) -> None:
        rec: dict[str, Any] = {
            "code": code,
            "message": message,
            "severity": severity,
        }
        if path is not None:
            rec["path"] = str(path)
        bucket.append(rec)
        log_fn = logger.info if severity == "info" else logger.warning
        if severity == "error":
            log_fn = logger.error
        log_fn("dashboard [%s] %s", code, message)

    def info_msg(self, code: str, message: str, path: Path | str | None = None) -> None:
        self._append(self.info, code=code, message=message, path=path, severity="info")

    def warn(self, code: str, message: str, path: Path | str | None = None) -> None:
        self._append(self.warnings, code=code, message=message, path=path, severity="warning")

    def error(self, code: str, message: str, path: Path | str | None = None) -> None:
        self._append(self.errors, code=code, message=message, path=path, severity="error")

    def json_failure(
        self,
        path: Path | str,
        exc: BaseException,
        *,
        strict: bool,
        label: str = "json_decode",
    ) -> None:
        """Invalid JSON in an on-disk artifact: warn always; in strict, also record as error."""
        p = Path(path) if isinstance(path, str) else path
        msg = f"{p}: {exc}"
        self.warn(label, msg, path=p)
        if strict:
            self.error("strict_json", msg, path=p)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "argus.dashboard_diagnostics.v1",
            "info": list(self.info),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "strict_issue_count": len(self.errors),
        }


def empty_integrity() -> dict[str, Any]:
    return {
        "schema": "argus.dashboard_integrity.v1",
        "history_snapshots": {"attempted": 0, "loaded": 0, "skipped_invalid": 0},
        "artifacts": {},
    }
