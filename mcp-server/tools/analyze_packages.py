from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from schemas import VFXParticles, VFXSource, VFXSpec, VFXTiming
from tools.analyze_images import IMAGE_EXTENSIONS, _classify_from_filename
from tools.image_features import analyze_media_files


CONFIG_FILE = "config.json"
PROMPT_FILE = "prompt.md"
IMAGES_DIR = "images"


def list_effect_packages(root: Path) -> list[dict[str, str]]:
    if not root.exists():
        return []

    packages: list[dict[str, str]] = []
    for package_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        media_files = find_package_media(package_dir)
        packages.append(
            {
                "name": package_dir.name,
                "path": str(package_dir),
                "media_count": str(len(media_files)),
            }
        )
    return packages


def analyze_effect_package(package_dir: Path) -> VFXSpec:
    if not package_dir.exists():
        raise FileNotFoundError(f"Effect package does not exist: {package_dir}")
    if not package_dir.is_dir():
        raise NotADirectoryError(f"Effect package path is not a folder: {package_dir}")

    config = read_package_config(package_dir)
    prompt = read_package_prompt(package_dir)
    media_files = find_package_media(package_dir)
    visual_profile = analyze_media_files(media_files)

    effect_type, motion, palette, notes = infer_package_defaults(package_dir, media_files, prompt)
    if visual_profile.get("palette"):
        palette = visual_profile["palette"]
    if visual_profile.get("motion_hint") == "vertical_column_rise":
        motion = "rise_and_fade"
    if visual_profile.get("shape_hint") in {"bright_core_column_with_outer_flames", "ground_ring_with_upward_flare"}:
        effect_type = "fire_or_flame"

    effect_type = config.get("effect_type", effect_type)
    motion = config.get("motion", motion)
    if config.get("lock_color_palette"):
        palette = config.get("color_palette", palette)
    render_mode = config.get("render_mode", "ribbon" if effect_type == "electric_arc" else "sprite")
    duration_seconds = float(config.get("duration_seconds", 1.25))
    looping = bool(config.get("looping", False))

    notes.extend(package_notes(package_dir, prompt, media_files, config))
    notes.extend(visual_profile_notes(visual_profile))

    return VFXSpec(
        name=config.get("name", package_dir.name),
        source=VFXSource(kind="folder", uri=str(package_dir)),
        effect_type=effect_type,
        motion=motion,
        color_palette=palette,
        render_mode=render_mode,
        timing=VFXTiming(duration_seconds=duration_seconds, looping=looping),
        particles=VFXParticles(
            spawn_rate=float(config.get("spawn_rate", inferred_spawn_rate(visual_profile)) if config.get("lock_particles") else inferred_spawn_rate(visual_profile)),
            lifetime_seconds=float(config.get("lifetime_seconds", inferred_lifetime(visual_profile)) if config.get("lock_particles") else inferred_lifetime(visual_profile)),
            start_size=float(config.get("start_size", inferred_start_size(visual_profile)) if config.get("lock_particles") else inferred_start_size(visual_profile)),
            end_size=float(config.get("end_size", inferred_end_size(visual_profile)) if config.get("lock_particles") else inferred_end_size(visual_profile)),
        ),
        notes=notes,
        visual_profile=visual_profile,
    )


def read_package_config(package_dir: Path) -> dict[str, Any]:
    config_path = package_dir / CONFIG_FILE
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def read_package_prompt(package_dir: Path) -> str:
    prompt_path = package_dir / PROMPT_FILE
    if not prompt_path.exists():
        return ""
    return prompt_path.read_text(encoding="utf-8").strip()


def find_package_media(package_dir: Path) -> list[Path]:
    media_roots = [package_dir / IMAGES_DIR, package_dir]
    media_files: list[Path] = []
    for root in media_roots:
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                media_files.append(path)
    return media_files


def infer_package_defaults(package_dir: Path, media_files: list[Path], prompt: str) -> tuple[str, str, list[str], list[str]]:
    candidate_names = [package_dir.name, *[path.stem for path in media_files]]
    prompt_lower = prompt.lower()
    if any(token in prompt_lower for token in ("fire", "flame", "burn", "lava", "火", "火焰")):
        candidate_names.insert(0, "fire")
    if any(token in prompt_lower for token in ("smoke", "mist", "fog", "煙", "霧")):
        candidate_names.insert(0, "smoke")
    if any(token in prompt_lower for token in ("electric", "lightning", "spark", "雷", "電")):
        candidate_names.insert(0, "electric")
    if any(token in prompt_lower for token in ("magic", "aura", "energy", "spell", "魔法", "能量")):
        candidate_names.insert(0, "magic")

    for name in candidate_names:
        effect_type, motion, palette, notes = _classify_from_filename(Path(name))
        if effect_type != "unknown":
            notes.append(f"Package heuristic matched candidate: {name}")
            return effect_type, motion, palette, notes

    return _classify_from_filename(package_dir)


def package_notes(package_dir: Path, prompt: str, media_files: list[Path], config: dict[str, Any]) -> list[str]:
    notes = [f"Effect package: {package_dir.name}", f"Media files found: {len(media_files)}"]
    if prompt:
        notes.append("prompt.md provided designer intent.")
    if config:
        notes.append("config.json provided explicit overrides.")
    if any(path.suffix.lower() == ".gif" for path in media_files):
        notes.append("Animated GIF reference detected; future pass should sample timing and motion.")
    return notes


def visual_profile_notes(visual_profile: dict[str, Any]) -> list[str]:
    if not visual_profile:
        return []
    return [
        f"Image analysis shape hint: {visual_profile.get('shape_hint', 'unknown')}",
        f"Image analysis motion hint: {visual_profile.get('motion_hint', 'unknown')}",
        f"Image analysis style hint: {visual_profile.get('style_hint', 'unknown')}",
        f"Image analysis palette: {', '.join(visual_profile.get('palette', []))}",
    ]


def inferred_spawn_rate(visual_profile: dict[str, Any]) -> float:
    if visual_profile.get("style_hint") == "high_intensity_stylized_fire":
        return 170.0
    if visual_profile.get("bright_pixel_ratio", 0) > 0.12:
        return 160.0
    return 90.0


def inferred_lifetime(visual_profile: dict[str, Any]) -> float:
    if visual_profile.get("motion_hint") == "vertical_column_rise":
        return 0.72
    return 0.8


def inferred_start_size(visual_profile: dict[str, Any]) -> float:
    if visual_profile.get("base_energy", 0) > 0.34:
        return 28.0
    return 18.0


def inferred_end_size(visual_profile: dict[str, Any]) -> float:
    if visual_profile.get("shape_hint") == "bright_core_column_with_outer_flames":
        return 150.0
    return 96.0
