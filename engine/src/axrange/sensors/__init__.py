"""Sensor-type registry — the open catalog of sensor `type`s.

A `sensor` (docs/design/06-taps-sensors.md) is a typed capture/analysis node the
mirror plane feeds. This package is the single source of truth for the known
types; Phase A consumes it for validation (a `type` must be known), Phase B/C
attach each type's base image + provisioning recipe + export contract. Adding a
type is additive — a new entry here, no schema/planner change (§10).
"""
from __future__ import annotations

from axrange.sensors.registry import (
    DEFAULT_SENSOR_TYPE,
    SENSOR_TYPES,
    SensorType,
    default_image_for,
    is_known_type,
    resolve_type,
)

__all__ = [
    "DEFAULT_SENSOR_TYPE",
    "SENSOR_TYPES",
    "SensorType",
    "default_image_for",
    "is_known_type",
    "resolve_type",
]
