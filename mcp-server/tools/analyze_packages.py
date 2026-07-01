from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from schemas import VFXParticles, VFXSource, VFXSpec, VFXTiming
from tools.analyze_images import IMAGE_EXTENSIONS, _classify_from_filename


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

    effect_type, motion, palette, notes = infer_package_defaults(package_dir, media_files, prompt)
    effect_type = config.get("effect_type", effect_type)
    motion = config.get("motion", motion)
    palette = config.get("color_palette", palette)
    render_mode = config.get("render_mode", "ribbon" if effect_type == "electric_arc" else "sprite")
    duration_seconds = float(config.get("duration_seconds", 1.25))
    looping = bool(config.get("looping", False))

    notes.extend(package_notes(package_dir, prompt, media_files, config))

    return VFXSpec(
        name=config.get("name", package_dir.name),
        source=VFXSource(kind="folder", uri=str(package_dir)),
        effect_type=effect_type,
        motion=motion,
        color_palette=palette,
        render_mode=render_mode,
        timing=VFXTiming(duration_seconds=duration_seconds, looping=looping),
        particles=VFXParticles(
            spawn_rate=float(config.get("spawn_rate", 90.0)),
            lifetime_seconds=float(config.get("lifetime_seconds", 0.8)),
            start_size=float(config.get("start_size", 18.0)),
            end_size=float(config.get("end_size", 96.0)),
        ),
        notes=notes,
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
