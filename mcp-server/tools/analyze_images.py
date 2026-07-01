from __future__ import annotations

from pathlib import Path
import re

from schemas import VFXParticles, VFXSource, VFXSpec, VFXTiming


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".exr", ".gif"}


def analyze_reference_folder(folder: Path) -> list[VFXSpec]:
    if not folder.exists():
        raise FileNotFoundError(f"Reference folder does not exist: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Reference path is not a folder: {folder}")

    specs: list[VFXSpec] = []
    for image_path in sorted(folder.iterdir()):
        if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        specs.append(analyze_reference_image(image_path))
    return specs


def analyze_reference_image(image_path: Path) -> VFXSpec:
    effect_type, motion, palette, notes = _classify_from_filename(image_path)
    render_mode = "ribbon" if effect_type == "electric_arc" else "sprite"

    return VFXSpec(
        name=_asset_name_from_path(image_path),
        source=VFXSource(kind="image", uri=str(image_path)),
        effect_type=effect_type,
        motion=motion,
        color_palette=palette,
        render_mode=render_mode,
        timing=VFXTiming(duration_seconds=1.25, looping=False),
        particles=VFXParticles(
            spawn_rate=90.0,
            lifetime_seconds=0.8,
            start_size=18.0,
            end_size=96.0,
        ),
        notes=notes,
    )


def _asset_name_from_path(path: Path) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in path.stem)
    return f"NS_{normalized.strip('_') or 'GeneratedVFX'}"


def _classify_from_filename(path: Path) -> tuple[str, str, list[str], list[str]]:
    name = path.stem.lower()

    if any_keyword(name, ("fire", "flame", "burn", "lava")):
        return (
            "fire_or_flame",
            "rise_and_fade",
            ["#FFB14A", "#FF5A1F", "#2A1208"],
            ["Filename heuristic matched flame-like keywords."],
        )
    if any_keyword(name, ("smoke", "mist", "fog", "cloud")):
        return (
            "smoke_or_mist",
            "drift_and_dissipate",
            ["#D8D7D2", "#8D918F", "#3C4142"],
            ["Filename heuristic matched smoke-like keywords."],
        )
    if any_keyword(name, ("electric", "bolt", "lightning", "spark")):
        return (
            "electric_arc",
            "branch_and_flicker",
            ["#DDF8FF", "#54D7FF", "#294DFF"],
            ["Filename heuristic matched electric-like keywords."],
        )
    if any_keyword(name, ("magic", "aura", "energy", "spell", "portal")):
        return (
            "magic_energy",
            "radial_expand_then_fade",
            ["#F7F3FF", "#7DE2FF", "#8B5CFF"],
            ["Filename heuristic matched magic or energy keywords."],
        )
    if any_keyword(name, ("impact", "hit", "burst", "shock")):
        return (
            "impact_burst",
            "radial_expand_then_fade",
            ["#FFFFFF", "#FFD36A", "#FF7A45"],
            ["Filename heuristic matched impact-like keywords."],
        )

    return (
        "unknown",
        "pulse_loop",
        ["#FFFFFF", "#89CFF0", "#6A7FDB"],
        ["No filename keyword matched. Replace this with vision-model analysis in the next phase."],
    )


def any_keyword(name: str, keywords: tuple[str, ...]) -> bool:
    tokens = [token for token in re.split(r"[^a-z0-9]+", name) if token]
    return any(keyword in tokens for keyword in keywords)
