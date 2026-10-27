"""Shared finding type so validators report problems instead of silently repairing data."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    severity: str = "error"  # error | warning
    where: str = ""


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]
