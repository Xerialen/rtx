#!/usr/bin/env python3
"""Production PlanLink / replant entry. Never send a plant around the gate."""

from __future__ import annotations

from typing import Any

from d_failclosed import FailClosed, guard_plant, send_plan_link


def plant(ctl: Any, payload: dict[str, Any], recipe: dict[str, Any] | None = None) -> Any:
    """The only tools-side PlanLink send. Recipe/sigill is required."""
    return send_plan_link(ctl, payload, recipe)


def preflight_replant(recipe: dict[str, Any] | None = None) -> None:
    """Call before any replant_kanon / PlanLink subprocess."""
    guard_plant(recipe)


__all__ = ["FailClosed", "plant", "preflight_replant", "guard_plant"]
