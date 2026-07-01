from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


EffectType = Literal[
    "fire_or_flame",
    "smoke_or_mist",
    "magic_energy",
    "electric_arc",
    "impact_burst",
    "unknown",
]

MotionType = Literal[
    "rise_and_fade",
    "drift_and_dissipate",
    "radial_expand_then_fade",
    "branch_and_flicker",
    "pulse_loop",
    "unknown",
]

RenderMode = Literal["sprite", "ribbon", "mesh", "flipbook"]
SourceKind = Literal["image", "folder", "url", "manual"]


@dataclass(frozen=True)
class VFXSource:
    kind: SourceKind
    uri: str


@dataclass(frozen=True)
class VFXTiming:
    duration_seconds: float
    looping: bool


@dataclass(frozen=True)
class VFXParticles:
    spawn_rate: float
    lifetime_seconds: float
    start_size: float
    end_size: float


@dataclass(frozen=True)
class VFXSpec:
    name: str
    source: VFXSource
    effect_type: EffectType
    motion: MotionType
    color_palette: list[str]
    render_mode: RenderMode
    timing: VFXTiming
    particles: VFXParticles
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
