"""Versioned semantic location references and the initial nine-grid resolver."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional


LOCATION_SCHEMA_VERSION = "1.0"


class LocationKind(str, Enum):
    NAMED = "named"
    POSE_REF = "pose_ref"
    REGION = "region"
    OBJECT_RELATIVE = "object_relative"


@dataclass(frozen=True)
class LocationRef:
    kind: LocationKind
    namespace: str
    location_id: str
    scene_id: str
    scene_version: str
    schema_version: str = LOCATION_SCHEMA_VERSION
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != LOCATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported LocationRef schema_version: {self.schema_version}")
        for name, value in (
            ("namespace", self.namespace),
            ("location_id", self.location_id),
            ("scene_id", self.scene_id),
            ("scene_version", self.scene_version),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")

    @property
    def qualified_id(self) -> str:
        return f"{self.namespace}/{self.location_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "namespace": self.namespace,
            "location_id": self.location_id,
            "scene_id": self.scene_id,
            "scene_version": self.scene_version,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LocationRef":
        return cls(
            schema_version=str(data.get("schema_version", LOCATION_SCHEMA_VERSION)),
            kind=LocationKind(str(data["kind"])),
            namespace=str(data["namespace"]),
            location_id=str(data["location_id"]),
            scene_id=str(data["scene_id"]),
            scene_version=str(data["scene_version"]),
            attributes=dict(data.get("attributes") or {}),
        )


@dataclass(frozen=True)
class ResolvedLocation:
    location_ref: LocationRef
    pose: Mapping[str, float]
    frame_id: str
    map_version: str
    resolver_version: str
    resolution_id: str
    provenance: Mapping[str, Any]
    resolved_at: float
    valid_until: Optional[float] = None
    schema_version: str = LOCATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LOCATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported ResolvedLocation schema_version: {self.schema_version}")
        if set(self.pose) != {"x", "y", "z", "theta"}:
            raise ValueError("resolved pose requires exactly x, y, z, and theta")
        for value in self.pose.values():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("resolved pose values must be numbers")
        if not self.frame_id or not self.resolution_id or not self.resolver_version:
            raise ValueError("resolved location identity fields must be non-empty")

    def is_valid(self, *, now: Optional[float] = None) -> bool:
        return self.valid_until is None or self.valid_until >= (now if now is not None else time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "location_ref": self.location_ref.to_dict(),
            "pose": {name: float(value) for name, value in self.pose.items()},
            "frame_id": self.frame_id,
            "map_version": self.map_version,
            "resolver_version": self.resolver_version,
            "resolution_id": self.resolution_id,
            "provenance": dict(self.provenance),
            "resolved_at": self.resolved_at,
            "valid_until": self.valid_until,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResolvedLocation":
        return cls(
            schema_version=str(data.get("schema_version", LOCATION_SCHEMA_VERSION)),
            location_ref=LocationRef.from_dict(data["location_ref"]),
            pose={name: float(value) for name, value in dict(data["pose"]).items()},
            frame_id=str(data["frame_id"]),
            map_version=str(data["map_version"]),
            resolver_version=str(data["resolver_version"]),
            resolution_id=str(data["resolution_id"]),
            provenance=dict(data.get("provenance") or {}),
            resolved_at=float(data["resolved_at"]),
            valid_until=(float(data["valid_until"]) if data.get("valid_until") is not None else None),
        )


class LocationResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class NineGridLocationResolver:
    GRID_IDS = frozenset({"nw", "n", "ne", "w", "c", "e", "sw", "s", "se"})

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        scene_id: str,
        scene_version: str,
        map_version: str,
        resolver_version: str = "nine-grid-v1",
        config_provenance: Optional[Mapping[str, Any]] = None,
    ) -> None:
        cells = dict(config.get("grid_cells") or {})
        if set(cells) != self.GRID_IDS:
            raise ValueError("nine-grid resolver config must define exactly nine grid cells")
        self._cells = cells
        self.frame_id = str(config.get("frame") or "")
        if not self.frame_id:
            raise ValueError("location resolver config requires frame")
        self.scene_id = scene_id
        self.scene_version = scene_version
        self.map_version = map_version
        self.resolver_version = resolver_version
        self.config_provenance = dict(config_provenance or {})

    @classmethod
    def from_json_file(
        cls,
        path: str | Path,
        *,
        scene_id: str,
        scene_version: str,
        map_version: str,
        resolver_version: str = "nine-grid-v1",
    ) -> "NineGridLocationResolver":
        resolved_path = Path(path).resolve()
        config = json.loads(resolved_path.read_text(encoding="utf-8"))
        return cls(
            config,
            scene_id=scene_id,
            scene_version=scene_version,
            map_version=map_version,
            resolver_version=resolver_version,
            config_provenance={"config_path": str(resolved_path)},
        )

    def resolve(self, location: LocationRef, *, now: Optional[float] = None) -> ResolvedLocation:
        if location.kind != LocationKind.NAMED:
            raise LocationResolutionError(
                "UNSUPPORTED_LOCATION_KIND",
                f"initial resolver cannot execute {location.kind.value} locations",
            )
        if location.namespace != "grid":
            raise LocationResolutionError(
                "UNSUPPORTED_LOCATION_NAMESPACE",
                f"initial resolver cannot execute namespace {location.namespace}",
            )
        if location.location_id not in self.GRID_IDS:
            raise LocationResolutionError(
                "UNKNOWN_LOCATION_ID", f"unknown nine-grid location: {location.location_id}"
            )
        if location.scene_id != self.scene_id:
            raise LocationResolutionError(
                "SCENE_ID_MISMATCH",
                f"location scene {location.scene_id} does not match {self.scene_id}",
            )
        if location.scene_version != self.scene_version:
            raise LocationResolutionError(
                "SCENE_VERSION_MISMATCH",
                f"location scene version {location.scene_version} does not match {self.scene_version}",
            )
        resolved_at = now if now is not None else time.time()
        cell = dict(self._cells[location.location_id])
        return ResolvedLocation(
            location_ref=location,
            pose={
                "x": float(cell["x"]),
                "y": float(cell["y"]),
                "z": float(cell.get("z", 0.0)),
                "theta": float(cell.get("theta", 0.0)),
            },
            frame_id=self.frame_id,
            map_version=self.map_version,
            resolver_version=self.resolver_version,
            resolution_id=f"location-resolution-{uuid.uuid4().hex}",
            provenance={
                **self.config_provenance,
                "qualified_location_id": location.qualified_id,
                "scene_id": self.scene_id,
                "scene_version": self.scene_version,
            },
            resolved_at=resolved_at,
        )


def legacy_grid_location(
    value: str,
    *,
    scene_id: str,
    scene_version: str,
) -> LocationRef:
    raw = str(value).strip().lower()
    location_id = raw.removeprefix("grid/")
    if location_id not in NineGridLocationResolver.GRID_IDS:
        raise LocationResolutionError("UNKNOWN_LOCATION_ID", f"invalid legacy grid value: {value}")
    return LocationRef(
        kind=LocationKind.NAMED,
        namespace="grid",
        location_id=location_id,
        scene_id=scene_id,
        scene_version=scene_version,
        attributes={"translated_from_legacy": value},
    )
