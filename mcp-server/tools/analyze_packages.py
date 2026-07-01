from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from schemas import VFXEmitterPlan, VFXParticles, VFXPlan, VFXSource, VFXSpec, VFXTiming
from tools.analyze_images import IMAGE_EXTENSIONS, _classify_from_filename
from tools.image_features import analyze_media_files
from tools.reference_sprites import create_reference_card_source, create_reference_sprite_source
from tools.vfx_authoring import composition_layers_for_plan, production_notes_for_plan


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
    if effect_type == "electric_arc":
        motion = "branch_and_flicker"
        palette = electric_palette(palette)
    elif visual_profile.get("shape_hint") in {"glowing_square_particles", "glowing_shard_particles"}:
        effect_type = "glowing_particles"
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

    particles = VFXParticles(
        spawn_rate=float(config.get("spawn_rate", inferred_spawn_rate(visual_profile)) if config.get("lock_particles") else inferred_spawn_rate(visual_profile)),
        lifetime_seconds=float(config.get("lifetime_seconds", inferred_lifetime(visual_profile)) if config.get("lock_particles") else inferred_lifetime(visual_profile)),
        start_size=float(config.get("start_size", inferred_start_size(visual_profile)) if config.get("lock_particles") else inferred_start_size(visual_profile)),
        end_size=float(config.get("end_size", inferred_end_size(visual_profile)) if config.get("lock_particles") else inferred_end_size(visual_profile)),
    )

    reference_sprite_source = None
    if visual_profile.get("shape_hint") != "glowing_shard_particles":
        reference_sprite_source = create_reference_sprite_source(package_dir.name, media_files, effect_type, visual_profile)
    reference_card_source = create_reference_card_source(package_dir.name, media_files, effect_type, visual_profile)

    return VFXSpec(
        name=config.get("name", package_dir.name),
        source=VFXSource(kind="folder", uri=str(package_dir)),
        effect_type=effect_type,
        motion=motion,
        color_palette=palette,
        render_mode=render_mode,
        timing=VFXTiming(duration_seconds=duration_seconds, looping=looping),
        particles=particles,
        notes=notes,
        visual_profile=visual_profile,
        vfx_plan=build_vfx_plan(effect_type, motion, palette, particles, visual_profile, reference_sprite_source, reference_card_source, config),
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


def build_vfx_plan(
    effect_type: str,
    motion: str,
    palette: list[str],
    particles: VFXParticles,
    visual_profile: dict[str, Any],
    reference_sprite_source: str | None,
    reference_card_source: str | None,
    config: dict[str, Any] | None = None,
) -> VFXPlan:
    if visual_profile.get("shape_hint") == "glowing_shard_particles":
        emitters = [
            VFXEmitterPlan(
                name="glowing_shards",
                role="primary_particles",
                sprite_shape="shard",
                material_style="gold_white_emissive_shards",
                motion="rise_with_turbulence",
                spawn_rate=max(particles.spawn_rate, 150.0),
                lifetime_seconds=max(particles.lifetime_seconds, 0.95),
                start_size=max(particles.start_size, 8.0),
                end_size=max(particles.end_size, 22.0),
                color_palette=palette[:4],
                sprite_source=reference_sprite_source,
                notes=["Use a crisp triangular shard sprite, random rotation, and nonuniform sizes."],
            ),
            VFXEmitterPlan(
                name="overexposed_glints",
                role="accent_particles",
                sprite_shape="soft_disc",
                material_style="white_hot_glint",
                motion="quick_rise_and_fade",
                spawn_rate=36.0,
                lifetime_seconds=0.42,
                start_size=12.0,
                end_size=4.0,
                color_palette=["#FFFFFF", "#FFF0C8"],
                notes=["Adds small hot flashes between shard particles."],
            ),
        ]
        emitters = apply_unreal_settings(emitters, config)
        return VFXPlan(
            visual_intent="Gold-white glowing shard particles swirling upward with varied small triangular silhouettes and a faint bloom core.",
            primary_emitter="glowing_shards",
            emitters=emitters,
            reference_card_source=reference_card_source,
            composition_layers=composition_layers_for_plan(effect_type, [emitter.__dict__ for emitter in emitters], bool(reference_card_source)),
            production_notes=production_notes_for_plan(effect_type, visual_profile, [emitter.__dict__ for emitter in emitters]),
        )

    if effect_type == "glowing_particles" or (effect_type != "electric_arc" and visual_profile.get("shape_hint") == "glowing_square_particles"):
        emitters = [
            VFXEmitterPlan(
                name="glowing_squares",
                role="primary_particles",
                sprite_shape="square",
                material_style="white_emissive",
                motion="rise_with_turbulence",
                spawn_rate=max(particles.spawn_rate, 120.0),
                lifetime_seconds=max(particles.lifetime_seconds, 0.9),
                start_size=max(particles.start_size, 12.0),
                end_size=max(particles.end_size, 28.0),
                color_palette=palette[:3],
                sprite_source=reference_sprite_source,
                notes=["Use hard-edged square sprites, random rotation, and additive bloom."],
            ),
            VFXEmitterPlan(
                name="soft_bloom_core",
                role="supporting_glow",
                sprite_shape="soft_disc",
                material_style="warm_white_glow",
                motion="slow_vertical_drift",
                spawn_rate=24.0,
                lifetime_seconds=0.7,
                start_size=32.0,
                end_size=72.0,
                color_palette=["#FFFFFF", "#FFFCE8"],
                notes=["Adds the overexposed vertical glow visible behind the square particles."],
            ),
        ]
        emitters = apply_unreal_settings(emitters, config)
        return VFXPlan(
            visual_intent="White emissive square particles drifting upward in a loose vertical column with soft bloom.",
            primary_emitter="glowing_squares",
            emitters=emitters,
            reference_card_source=reference_card_source,
            composition_layers=composition_layers_for_plan(effect_type, [emitter.__dict__ for emitter in emitters], bool(reference_card_source)),
            production_notes=production_notes_for_plan(effect_type, visual_profile, [emitter.__dict__ for emitter in emitters]),
        )

    if effect_type == "fire_or_flame":
        emitters = [
            VFXEmitterPlan(
                name="core_flame",
                role="primary_body",
                sprite_shape="flame_tongue",
                material_style="additive_flame_gradient",
                motion=motion,
                spawn_rate=particles.spawn_rate,
                lifetime_seconds=particles.lifetime_seconds,
                start_size=particles.start_size,
                end_size=particles.end_size,
                color_palette=palette,
                sprite_source=reference_sprite_source,
                notes=["Use alpha-shaped flame sprites instead of full rectangular billboards."],
            ),
            VFXEmitterPlan(
                name="outer_flame_wisps",
                role="secondary_body",
                sprite_shape="flame_wisp",
                material_style="additive_outer_flame",
                motion="curl_up_and_fade",
                spawn_rate=round(max(36.0, particles.spawn_rate * 0.28), 2),
                lifetime_seconds=round(max(0.5, particles.lifetime_seconds * 1.1), 2),
                start_size=round(max(18.0, particles.start_size * 0.8), 2),
                end_size=round(max(96.0, particles.end_size * 1.05), 2),
                color_palette=palette[1:] or palette,
                notes=["Breaks the body silhouette with slower orange tongues around the core."],
            ),
            VFXEmitterPlan(
                name="base_glow",
                role="supporting_glow",
                sprite_shape="ground_glow",
                material_style="soft_base_glow",
                motion="static_pulse",
                spawn_rate=1.0,
                lifetime_seconds=max(1.0, particles.lifetime_seconds),
                start_size=round(max(96.0, particles.end_size * 0.9), 2),
                end_size=round(max(180.0, particles.end_size * 1.35), 2),
                color_palette=[palette[0], palette[1] if len(palette) > 1 else "#FF8A30", "#4A1408"],
                notes=["Provides the grounded hot base and broad value mass instead of relying on particles."],
            ),
            VFXEmitterPlan(
                name="smoke_heat_wisp",
                role="atmospheric_wisp",
                sprite_shape="smoke_wisp",
                material_style="translucent_smoke_heat",
                motion="slow_curl_up",
                spawn_rate=round(max(10.0, particles.spawn_rate * 0.08), 2),
                lifetime_seconds=round(max(0.9, particles.lifetime_seconds * 1.45), 2),
                start_size=round(max(30.0, particles.start_size * 1.2), 2),
                end_size=round(max(120.0, particles.end_size * 1.1), 2),
                color_palette=["#6A5A50", "#2A2420", palette[2] if len(palette) > 2 else "#D06030"],
                notes=["Adds low-opacity smoky/heat breakup above the flame so the core is not a flat card."],
            ),
            VFXEmitterPlan(
                name="ember_sparks",
                role="detail_particles",
                sprite_shape="small_disc",
                material_style="orange_emissive",
                motion="rise_and_scatter",
                spawn_rate=round(max(24.0, particles.spawn_rate * 0.18), 2),
                lifetime_seconds=round(max(0.35, particles.lifetime_seconds * 0.7), 2),
                start_size=round(max(3.0, particles.start_size * 0.18), 2),
                end_size=round(max(1.0, particles.start_size * 0.08), 2),
                color_palette=palette[1:3] or palette,
                notes=["Adds small hot particles separated from the main flame silhouette."],
            ),
        ]
        emitters = apply_unreal_settings(emitters, config)
        return VFXPlan(
            visual_intent="Layered stylized flame with a bright core, orange outer tongues, and small ember accents.",
            primary_emitter="core_flame",
            emitters=emitters,
            reference_card_source=reference_card_source,
            composition_layers=composition_layers_for_plan(effect_type, [emitter.__dict__ for emitter in emitters], bool(reference_card_source)),
            production_notes=production_notes_for_plan(effect_type, visual_profile, [emitter.__dict__ for emitter in emitters]),
        )

    if effect_type == "electric_arc":
        electric_palette_values = electric_palette(palette)
        emitters = [
            VFXEmitterPlan(
                name="main_bolt",
                role="primary_bolt",
                sprite_shape="lightning_bolt",
                material_style="electric_core_bolt",
                motion="branch_and_flicker",
                spawn_rate=1.0,
                lifetime_seconds=0.32,
                start_size=42.0,
                end_size=220.0,
                color_palette=electric_palette_values,
                sprite_source=None,
                notes=["Readable vertical bolt must dominate the effect; sparks are secondary."],
            ),
            VFXEmitterPlan(
                name="branch_arcs",
                role="secondary_bolts",
                sprite_shape="lightning_branch",
                material_style="electric_branch_arcs",
                motion="branch_and_flicker",
                spawn_rate=8.0,
                lifetime_seconds=0.26,
                start_size=26.0,
                end_size=175.0,
                color_palette=electric_palette_values,
                sprite_source=None,
                notes=["Adds side forks around the main bolt instead of random particle spray."],
            ),
            VFXEmitterPlan(
                name="impact_core",
                role="impact_core",
                sprite_shape="soft_disc",
                material_style="electric_impact_core",
                motion="pulse_loop",
                spawn_rate=1.0,
                lifetime_seconds=0.42,
                start_size=72.0,
                end_size=150.0,
                color_palette=electric_palette_values[:3],
                sprite_source=None,
                notes=["Bright contact point where the bolt hits the ground."],
            ),
            VFXEmitterPlan(
                name="ground_energy_ring",
                role="supporting_glow",
                sprite_shape="ground_glow",
                material_style="electric_ground_ring",
                motion="radial_expand_then_fade",
                spawn_rate=1.0,
                lifetime_seconds=0.55,
                start_size=130.0,
                end_size=240.0,
                color_palette=[electric_palette_values[1], electric_palette_values[2], "#171B4A"],
                sprite_source=None,
                notes=["Grounded blue/purple energy ring anchors the bolt."],
            ),
            VFXEmitterPlan(
                name="ion_sparks",
                role="detail_particles",
                sprite_shape="small_disc",
                material_style="blue_white_sparks",
                motion="quick_scatter_and_fade",
                spawn_rate=22.0,
                lifetime_seconds=0.28,
                start_size=4.0,
                end_size=1.4,
                color_palette=[electric_palette_values[0], electric_palette_values[1]],
                sprite_source=None,
                notes=["Sparse ion sparks only; do not let this layer become the main read."],
            ),
        ]
        emitters = apply_unreal_settings(emitters, config)
        return VFXPlan(
            visual_intent="Stylized lightning strike with a dominant vertical bolt, branching side arcs, bright impact core, blue ground energy ring, and sparse ion sparks.",
            primary_emitter="main_bolt",
            emitters=emitters,
            reference_card_source=reference_card_source,
            composition_layers=composition_layers_for_plan(effect_type, [emitter.__dict__ for emitter in emitters], bool(reference_card_source)),
            production_notes=production_notes_for_plan(effect_type, visual_profile, [emitter.__dict__ for emitter in emitters]),
        )

    emitters = [
        VFXEmitterPlan(
            name="primary_sprite",
            role="primary_body",
            sprite_shape="soft_disc",
            material_style="additive_energy",
            motion=motion,
            spawn_rate=particles.spawn_rate,
            lifetime_seconds=particles.lifetime_seconds,
            start_size=particles.start_size,
            end_size=particles.end_size,
            color_palette=palette,
            sprite_source=reference_sprite_source,
        )
    ]
    emitters = apply_unreal_settings(emitters, config)
    return VFXPlan(
        visual_intent="Single sprite-based energy effect inferred from the reference package.",
        primary_emitter="primary_sprite",
        emitters=emitters,
        reference_card_source=reference_card_source,
        composition_layers=composition_layers_for_plan(effect_type, [emitter.__dict__ for emitter in emitters], bool(reference_card_source)),
        production_notes=production_notes_for_plan(effect_type, visual_profile, [emitter.__dict__ for emitter in emitters]),
    )


def apply_unreal_settings(emitters: list[VFXEmitterPlan], config: dict[str, Any] | None) -> list[VFXEmitterPlan]:
    overrides = (config or {}).get("layer_overrides", {})
    result: list[VFXEmitterPlan] = []
    for index, emitter in enumerate(emitters, start=1):
        settings = default_unreal_settings_for_emitter(emitter, index)
        override = overrides.get(emitter.name, {}) if isinstance(overrides, dict) else {}
        settings = deep_merge(settings, override)
        result.append(replace(emitter, unreal_settings=settings))
    return result


def default_unreal_settings_for_emitter(emitter: VFXEmitterPlan, index: int) -> dict[str, Any]:
    material = default_material_settings_for_emitter(emitter)
    preview_card = default_preview_card_settings_for_emitter(emitter, index)
    niagara_transform = default_niagara_transform_for_emitter(emitter, index)
    return {
        "enabled": True,
        "material": material,
        "preview": {
            "card": preview_card,
            "niagara": niagara_transform,
        },
        "niagara": {
            "spawn_rate": emitter.spawn_rate,
            "lifetime_seconds": emitter.lifetime_seconds,
            "start_size": emitter.start_size,
            "end_size": emitter.end_size,
        },
    }


def default_material_settings_for_emitter(emitter: VFXEmitterPlan) -> dict[str, Any]:
    style = emitter.material_style
    if "electric_core_bolt" in style:
        return {"opacity": 0.9, "emissive_strength": 22.0, "blend_mode": "additive"}
    if "electric_branch" in style:
        return {"opacity": 0.72, "emissive_strength": 15.0, "blend_mode": "additive"}
    if "electric_impact" in style:
        return {"opacity": 0.56, "emissive_strength": 14.0, "blend_mode": "additive"}
    if "electric_ground" in style:
        return {"opacity": 0.34, "emissive_strength": 7.0, "blend_mode": "additive"}
    if "blue_white_sparks" in style:
        return {"opacity": 0.7, "emissive_strength": 13.0, "blend_mode": "additive"}
    if "smoke" in style:
        return {"opacity": 0.22, "emissive_strength": 0.65, "blend_mode": "translucent"}
    if "base_glow" in style:
        return {"opacity": 0.36, "emissive_strength": 5.25, "blend_mode": "additive"}
    if "reference_card" in style:
        return {"opacity": 0.38, "emissive_strength": 2.4, "blend_mode": "additive"}
    if "outer_flame" in style:
        return {"opacity": 0.55, "emissive_strength": 7.5, "blend_mode": "additive"}
    if emitter.role == "detail_particles":
        return {"opacity": 0.78, "emissive_strength": 10.0, "blend_mode": "additive"}
    return {"opacity": 0.82, "emissive_strength": 12.0, "blend_mode": "additive"}


def default_preview_card_settings_for_emitter(emitter: VFXEmitterPlan, index: int) -> dict[str, Any]:
    role = emitter.role
    if role == "primary_bolt":
        return {"enabled": True, "location": [0.0, 0.0, 152.0], "rotation": [90.0, 0.0, 0.0], "scale": [1.05, 2.25, 1.05]}
    if role == "secondary_bolts":
        return {"enabled": True, "location": [0.0, 0.0, 120.0], "rotation": [90.0, 0.0, -8.0], "scale": [1.85, 1.65, 1.0]}
    if role == "impact_core":
        return {"enabled": True, "location": [0.0, 0.0, 18.0], "rotation": [90.0, 0.0, 0.0], "scale": [1.45, 1.45, 1.45]}
    if role == "supporting_glow":
        return {"enabled": True, "location": [0.0, 0.0, 4.0], "rotation": [0.0, 0.0, 0.0], "scale": [3.0, 3.0, 1.0]}
    if role == "primary_body":
        return {"enabled": True, "location": [0.0, -1.0, 135.0], "rotation": [90.0, 0.0, 0.0], "scale": [1.55, 1.55, 1.55]}
    if role == "secondary_body":
        return {"enabled": True, "location": [5.0, 1.5, 128.0], "rotation": [90.0, 0.0, -7.0], "scale": [1.85, 1.85, 1.85]}
    if role == "atmospheric_wisp":
        return {"enabled": True, "location": [-6.0, 2.0, 178.0], "rotation": [90.0, 0.0, 8.0], "scale": [2.0, 2.0, 2.0]}
    if role == "primary_particles":
        return {"enabled": True, "location": [0.0, 0.0, 135.0 + index * 3.0], "rotation": [90.0, 0.0, 0.0], "scale": [1.2, 1.2, 1.2]}
    if role in {"detail_particles", "accent_particles"}:
        return {"enabled": False}
    return {"enabled": True, "location": [index * 3.0, 0.0, 145.0 + index * 5.0], "rotation": [90.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0]}


def default_niagara_transform_for_emitter(emitter: VFXEmitterPlan, index: int) -> dict[str, Any]:
    return {
        "location": [-36.0, (index - 1) * 34.0, 118.0],
        "rotation": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
    }


def electric_palette(palette: list[str]) -> list[str]:
    blue_or_purple = [color for color in palette if color.upper() not in {"#FFFFFF", "#FFFCE8", "#FFF8C8"}]
    if blue_or_purple:
        return ["#F2FFFF", "#4FDFFF", blue_or_purple[0], "#7F45FF"]
    return ["#F2FFFF", "#4FDFFF", "#2B76FF", "#7F45FF"]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def inferred_spawn_rate(visual_profile: dict[str, Any]) -> float:
    if visual_profile.get("shape_hint") == "glowing_shard_particles":
        return 155.0
    if visual_profile.get("shape_hint") == "glowing_square_particles":
        return 130.0
    if visual_profile.get("style_hint") == "high_intensity_stylized_fire":
        return 170.0
    if visual_profile.get("bright_pixel_ratio", 0) > 0.12:
        return 160.0
    return 90.0


def inferred_lifetime(visual_profile: dict[str, Any]) -> float:
    if visual_profile.get("shape_hint") == "glowing_shard_particles":
        return 0.95
    if visual_profile.get("shape_hint") == "glowing_square_particles":
        return 1.05
    if visual_profile.get("motion_hint") == "vertical_column_rise":
        return 0.72
    return 0.8


def inferred_start_size(visual_profile: dict[str, Any]) -> float:
    if visual_profile.get("shape_hint") == "glowing_shard_particles":
        return 8.0
    if visual_profile.get("shape_hint") == "glowing_square_particles":
        return 10.0
    if visual_profile.get("base_energy", 0) > 0.34:
        return 28.0
    return 18.0


def inferred_end_size(visual_profile: dict[str, Any]) -> float:
    if visual_profile.get("shape_hint") == "glowing_shard_particles":
        return 22.0
    if visual_profile.get("shape_hint") == "glowing_square_particles":
        return 28.0
    if visual_profile.get("shape_hint") == "bright_core_column_with_outer_flames":
        return 150.0
    return 96.0
