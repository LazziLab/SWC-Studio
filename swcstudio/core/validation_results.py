"""Structured validation result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


Status = str
Severity = str


@dataclass
class PreCheckItem:
    key: str
    label: str
    source: str
    severity: Severity
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "severity": self.severity,
            "params": dict(self.params),
            "enabled": bool(self.enabled),
        }


@dataclass
class CheckResult:
    key: str
    label: str
    passed: bool
    severity: Severity
    message: str
    failing_node_ids: list[int] = field(default_factory=list)
    failing_section_ids: list[int] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    source: str = "native"
    params_used: dict[str, Any] = field(default_factory=dict)
    thresholds_used: dict[str, Any] = field(default_factory=dict)
    status: Status = "pass"

    @staticmethod
    def from_pass_fail(
        *,
        key: str,
        label: str,
        passed: bool,
        severity: Severity,
        message: str,
        source: str,
        params_used: dict[str, Any] | None = None,
        thresholds_used: dict[str, Any] | None = None,
        failing_node_ids: list[int] | None = None,
        failing_section_ids: list[int] | None = None,
        metrics: dict[str, Any] | None = None,
        error: bool = False,
    ) -> "CheckResult":
        if passed:
            status = "pass"
        elif str(severity).lower() == "warning":
            status = "warning"
        else:
            status = "fail"
        msg = str(message)
        if error and not msg.lower().startswith("validation error:"):
            msg = f"Validation error: {msg}"
        return CheckResult(
            key=key,
            label=label,
            passed=bool(passed),
            severity=str(severity),
            message=msg,
            failing_node_ids=list(failing_node_ids or []),
            failing_section_ids=list(failing_section_ids or []),
            metrics=dict(metrics or {}),
            source=str(source),
            params_used=dict(params_used or {}),
            thresholds_used=dict(thresholds_used or {}),
            status=status,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "passed": bool(self.passed),
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "failing_node_ids": list(self.failing_node_ids),
            "failing_section_ids": list(self.failing_section_ids),
            "metrics": dict(self.metrics),
            "source": self.source,
            "params_used": dict(self.params_used),
            "thresholds_used": dict(self.thresholds_used),
        }


@dataclass
class ValidationReport:
    profile: str
    precheck: list[PreCheckItem] = field(default_factory=list)
    results: list[CheckResult] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        out = {"pass": 0, "info": 0, "warning": 0, "fail": 0}
        for r in self.results:
            if r.status in out:
                out[r.status] += 1
            elif r.passed:
                out["pass"] += 1
            else:
                out["fail"] += 1
        out["total"] = len(self.results)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "precheck": [p.to_dict() for p in self.precheck],
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary(),
        }
