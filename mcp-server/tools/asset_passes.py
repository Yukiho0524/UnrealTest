from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFile, ImageFilter

from tools.analyze_packages import analyze_effect_package, find_package_media


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ASSET_PASS_ROOT = WORKSPACE_ROOT / "generated" / "asset-passes"
DEFAULT_AI_ART_ROOT = WORKSPACE_ROOT / "generated" / "ai-art"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".exr", ".hdr"}
ANIMATED_SUFFIXES = {".gif", ".mp4", ".mov", ".webm"}
ImageFile.LOAD_TRUNCATED_IMAGES = True


def build_asset_pass_manifest(
    package_path: Path,
    output_root: Path = DEFAULT_ASSET_PASS_ROOT,
    ai_art_root: Path = DEFAULT_AI_ART_ROOT,
) -> dict[str, Any]:
    package_path = resolve_from_workspace(package_path)
    output_root = resolve_from_workspace(output_root)
    ai_art_root = resolve_from_workspace(ai_art_root)

    spec = analyze_effect_package(package_path)
    plan = spec.vfx_plan
    pass_specs = list(plan.asset_passes if plan else [])
    reference_media = find_package_media(package_path)
    ai_outputs = collect_ai_outputs(package_path.name, ai_art_root)
    reference_candidates = reference_candidates_for_spec(spec.to_dict(), reference_media)
    derived_candidates = derive_bootstrap_candidates(package_path.name, pass_specs, reference_candidates, output_root)

    entries = [
        asset_pass_entry(pass_spec, reference_candidates, derived_candidates, ai_outputs)
        for pass_spec in pass_specs
    ]
    required_entries = [entry for entry in entries if entry.get("required")]
    ready_required = [entry for entry in required_entries if entry.get("status") == "ready"]
    missing_required = [entry for entry in required_entries if entry.get("status") != "ready"]

    manifest = {
        "schema_version": 1,
        "package": package_path.name,
        "package_path": str(package_path),
        "effect_type": spec.effect_type,
        "motion": spec.motion,
        "quality_tier": (plan.quality_target or {}).get("tier") if plan else None,
        "summary": {
            "total_passes": len(entries),
            "required_passes": len(required_entries),
            "ready_required_passes": len(ready_required),
            "missing_required_passes": len(missing_required),
            "unreal_ready": not missing_required,
        },
        "reference_media": [str(path) for path in reference_media],
        "ai_output_manifests": sorted({output["manifest"] for output in ai_outputs if output.get("manifest")}),
        "passes": entries,
        "next_actions": next_actions_for_entries(entries),
    }

    manifest_path = output_root / package_path.name / "asset_pass_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def apply_asset_pass_manifest_to_spec_dict(spec: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    patched = copy.deepcopy(spec)
    plan = patched.get("vfx_plan") or {}
    passes_by_name = {entry.get("name"): entry for entry in manifest.get("passes", [])}
    alpha_pass = passes_by_name.get("alpha_mask")
    alpha_selected = (alpha_pass or {}).get("selected_asset") or {}
    alpha_path = alpha_selected.get("path")
    distortion_pass = passes_by_name.get("distortion_flow")
    distortion_selected = (distortion_pass or {}).get("selected_asset") or {}
    distortion_path = distortion_selected.get("path")
    for emitter in plan.get("emitters") or []:
        pass_name = asset_pass_for_emitter(patched.get("effect_type"), emitter)
        if not pass_name:
            continue
        asset_pass = passes_by_name.get(pass_name)
        selected = (asset_pass or {}).get("selected_asset")
        if not selected:
            continue
        selected_path = selected.get("path")
        if not selected_path or not Path(selected_path).exists():
            continue
        emitter["sprite_source"] = selected_path
        emitter.setdefault("notes", []).append(f"Using asset pass '{pass_name}' from {selected.get('source')}.")
        material = emitter.setdefault("unreal_settings", {}).setdefault("material", {})
        atlas = asset_pass.get("asset_metadata", {}).get("atlas")
        if atlas:
            material["flipbook"] = atlas
        if should_apply_shared_alpha(pass_name, selected_path, alpha_path):
            material["alpha_source"] = alpha_path
            material["alpha_usage"] = "multiply_texture_alpha"
        if should_apply_distortion(emitter, distortion_path):
            material["distortion_source"] = distortion_path
            material["distortion_strength"] = material.get("distortion_strength", 0.075)
    apply_production_preview_layers(patched, manifest)
    patched["vfx_plan"] = plan
    patched.setdefault("notes", []).append(
        f"Asset pass manifest applied: {manifest.get('manifest_path') or manifest.get('package')}"
    )
    return patched


def apply_production_preview_layers(spec: dict[str, Any], manifest: dict[str, Any]) -> None:
    if not (manifest.get("summary") or {}).get("unreal_ready"):
        return
    effect_type = spec.get("effect_type")
    plan = spec.get("vfx_plan") or {}
    emitters = plan.get("emitters") or []
    if effect_type == "fire_or_flame":
        plan["preview_mode"] = "production_layers"
        plan["primary_emitter"] = "central_fire_pillar"
        for emitter in emitters:
            apply_fire_production_preview(emitter)
    elif effect_type == "electric_arc":
        plan["preview_mode"] = "production_layers"
        for emitter in emitters:
            apply_electric_production_preview(emitter)


def apply_fire_production_preview(emitter: dict[str, Any]) -> None:
    role = emitter.get("role")
    settings = emitter.setdefault("unreal_settings", {})
    material = settings.setdefault("material", {})
    timeline = settings.setdefault("timeline", {})
    preview = settings.setdefault("preview", {})
    card = preview.setdefault("card", {})
    niagara = preview.setdefault("niagara", {})

    if role == "reference_motion":
        material["opacity"] = min(float(material.get("opacity", 0.72)), 0.16)
        material["emissive_strength"] = min(float(material.get("emissive_strength", 5.5)), 1.8)
        card["enabled"] = False
        niagara["enabled"] = False
        emitter.setdefault("notes", []).append("Production preview hides the reference flipbook so the editable layers drive the look.")
        return

    if role == "fire_pillar":
        timeline.update({"delay": 0.07, "duration": 0.58, "opacity": [0.0, 0.92, 0.82, 0.0], "scale": [0.42, 1.12, 1.0, 0.68]})
        material["opacity"] = max(float(material.get("opacity", 0.54)), 0.78)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 14.0)), 18.0)
        card.update({"enabled": True, "location": [0, 0, 150], "rotation": [90, 0, 0], "scale": [1.25, 2.65, 1.15]})
        niagara["enabled"] = False
    elif role == "flame_slashes":
        timeline.update({"delay": 0.1, "duration": 0.52, "opacity": [0.0, 0.78, 0.58, 0.0], "scale": [0.55, 1.08, 1.16, 0.82]})
        material["opacity"] = max(float(material.get("opacity", 0.5)), 0.62)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 8.5)), 11.0)
        card.update({"enabled": True, "location": [0, -2, 78], "rotation": [90, 0, -10], "scale": [2.55, 1.5, 1]})
        niagara["enabled"] = False
    elif role == "ground_energy_ring":
        timeline.update({"delay": 0.02, "duration": 0.72, "opacity": [0.0, 0.9, 0.72, 0.0], "scale": [0.55, 1.12, 1.04, 1.28], "rotation_speed": 18.0})
        material["opacity"] = max(float(material.get("opacity", 0.72)), 0.76)
        card.update({"enabled": True, "location": [0, 0, 4], "rotation": [0, 0, 0], "scale": [3.65, 3.65, 1]})
        niagara["enabled"] = False
    elif role == "impact_core":
        timeline.update({"delay": 0.0, "duration": 0.24, "opacity": [0.0, 1.0, 0.45, 0.0], "scale": [0.35, 1.22, 0.84, 0.0]})
        material["opacity"] = max(float(material.get("opacity", 0.8)), 0.86)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 22.0)), 24.0)
        card.update({"enabled": True, "location": [0, -1, 44], "rotation": [90, 0, 0], "scale": [1.55, 1.1, 1]})
        niagara["enabled"] = False
    elif role == "atmospheric_wisp":
        timeline.update({"delay": 0.18, "duration": 1.05, "opacity": [0.0, 0.24, 0.18, 0.0], "scale": [0.62, 1.0, 1.22, 1.46], "rotation_speed": 5.0})
        material["opacity"] = max(float(material.get("opacity", 0.2)), 0.26)
        material["blend_mode"] = "translucent"
        card.update({"enabled": True, "location": [-4, 5, 122], "rotation": [90, 0, 7], "scale": [2.2, 2.0, 1]})
        niagara["enabled"] = False
    elif role == "detail_particles":
        timeline.update({"delay": 0.12, "duration": 0.32, "opacity": [0.0, 0.9, 0.55, 0.0], "scale": [0.8, 1.0, 0.65, 0.25], "rotation_speed": 160.0})
        card["enabled"] = False
        niagara.update({"enabled": True, "location": [0, 0, 92], "rotation": [0, 0, 0], "scale": [0.65, 0.65, 0.65]})


def apply_electric_production_preview(emitter: dict[str, Any]) -> None:
    role = emitter.get("role")
    settings = emitter.setdefault("unreal_settings", {})
    preview = settings.setdefault("preview", {})
    card = preview.setdefault("card", {})
    niagara = preview.setdefault("niagara", {})
    if role in {"primary_bolt", "secondary_bolts", "impact_core", "supporting_glow"}:
        card["enabled"] = True
        niagara["enabled"] = False
    elif role == "detail_particles":
        card["enabled"] = False
        niagara["enabled"] = True


def asset_pass_for_emitter(effect_type: str | None, emitter: dict[str, Any]) -> str | None:
    role = emitter.get("role")
    if effect_type == "fire_or_flame":
        if role == "fire_pillar":
            return "core_flame_flipbook"
        if role == "flame_slashes":
            return "flame_slash_flipbook"
        if role == "ground_energy_ring":
            return "ground_ring_mask"
        if role == "impact_core":
            return "impact_flash_mask"
        if role == "atmospheric_wisp":
            return "smoke_heat_flipbook"
        if role == "detail_particles":
            return "ember_sprite_set"
    if effect_type == "electric_arc":
        if role in {"primary_bolt", "secondary_bolts"}:
            return "bolt_branch_set"
        if role == "impact_core":
            return "impact_flash_mask"
    return None


def asset_pass_entry(
    pass_spec: dict[str, Any],
    reference_candidates: dict[str, list[dict[str, str]]],
    derived_candidates: dict[str, list[dict[str, str]]],
    ai_outputs: list[dict[str, str]],
) -> dict[str, Any]:
    name = str(pass_spec.get("name") or "unknown_pass")
    candidates = [
        *reference_candidates.get(name, []),
        *derived_candidates.get(name, []),
        *classify_ai_outputs_for_pass(name, ai_outputs),
    ]
    selected = candidates[0] if candidates else None
    prompt = prompt_for_asset_pass(pass_spec)
    return {
        "name": name,
        "required": bool(pass_spec.get("required")),
        "status": "ready" if selected else "missing",
        "source": pass_spec.get("source"),
        "format": pass_spec.get("format"),
        "purpose": pass_spec.get("purpose"),
        "unreal_usage": pass_spec.get("unreal_usage"),
        "selected_asset": selected,
        "candidates": candidates,
        "asset_metadata": asset_metadata_for_selected_asset(selected),
        "quality_note": quality_note_for_selected_asset(selected),
        "generation_prompt": prompt,
        "negative_prompt": "watermark, text, logo, UI, character, weapon, environment, rectangular card border, atlas grid",
    }


def asset_metadata_for_selected_asset(selected: dict[str, str] | None) -> dict[str, Any]:
    if not selected:
        return {}
    path = Path(selected.get("path", ""))
    if not path.exists() or path.suffix.lower() not in IMAGE_SUFFIXES:
        return {}
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return {}
    if width < 2 or height < 2:
        return {}
    columns = max(1, round(width / 256)) if width >= 256 else 1
    rows = max(1, round(height / 256)) if height >= 256 else 1
    atlas = {
        "columns": columns,
        "rows": rows,
        "frame_count": max(1, columns * rows),
        "fps": 12.0,
    }
    return {
        "width": width,
        "height": height,
        "atlas": atlas if columns > 1 or rows > 1 else None,
    }


def derive_bootstrap_candidates(
    package_name: str,
    pass_specs: list[dict[str, Any]],
    reference_candidates: dict[str, list[dict[str, str]]],
    output_root: Path,
) -> dict[str, list[dict[str, str]]]:
    beauty = first_existing_candidate(reference_candidates.get("beauty_flipbook", []))
    reference_motion = first_existing_candidate(reference_candidates.get("reference_motion_overlay", []))
    source = beauty or reference_motion
    if not source:
        return {}

    source_path = Path(source["path"])
    if not source_path.suffix.lower() in IMAGE_SUFFIXES:
        return {}

    target_names = {str(pass_spec.get("name") or "") for pass_spec in pass_specs}
    output_dir = output_root / package_name / "derived"
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates: dict[str, list[dict[str, str]]] = {}
    if "alpha_mask" in target_names:
        alpha_path = output_dir / f"{package_name}_alpha_mask.png"
        create_alpha_mask(source_path, alpha_path)
        candidates.setdefault("alpha_mask", []).append(derived_candidate(alpha_path, "alpha_from_reference_luminance"))

    if "core_flame_flipbook" in target_names:
        core_path = output_dir / f"{package_name}_core_flame_flipbook.png"
        create_core_flame_pass(source_path, core_path)
        candidates.setdefault("core_flame_flipbook", []).append(derived_candidate(core_path, "hot_core_from_reference"))

    if "smoke_heat_flipbook" in target_names:
        smoke_path = output_dir / f"{package_name}_smoke_heat_flipbook.png"
        create_smoke_heat_pass(source_path, smoke_path)
        candidates.setdefault("smoke_heat_flipbook", []).append(derived_candidate(smoke_path, "soft_heat_haze_from_reference_alpha"))

    if "flame_slash_flipbook" in target_names:
        slash_path = output_dir / f"{package_name}_flame_slash_flipbook.png"
        create_fire_atlas_pass(slash_path, "flame_slashes")
        candidates.setdefault("flame_slash_flipbook", []).append(derived_candidate(slash_path, "procedural_side_flame_atlas"))

    if "ground_ring_mask" in target_names:
        ring_path = output_dir / f"{package_name}_ground_ring_mask.png"
        create_fire_atlas_pass(ring_path, "ground_ring")
        candidates.setdefault("ground_ring_mask", []).append(derived_candidate(ring_path, "procedural_molten_ring_atlas"))

    if "impact_flash_mask" in target_names:
        flash_path = output_dir / f"{package_name}_impact_flash_mask.png"
        create_fire_atlas_pass(flash_path, "impact_flash")
        candidates.setdefault("impact_flash_mask", []).append(derived_candidate(flash_path, "procedural_impact_flash_atlas"))

    if "ember_sprite_set" in target_names:
        ember_path = output_dir / f"{package_name}_ember_sprite_set.png"
        create_fire_atlas_pass(ember_path, "embers")
        candidates.setdefault("ember_sprite_set", []).append(derived_candidate(ember_path, "procedural_ember_sprite_set"))

    if "distortion_flow" in target_names:
        flow_path = output_dir / f"{package_name}_distortion_flow.png"
        create_distortion_flow_pass(flow_path)
        candidates.setdefault("distortion_flow", []).append(derived_candidate(flow_path, "procedural_heat_distortion_flow"))

    return candidates


def first_existing_candidate(candidates: list[dict[str, str]] | None) -> dict[str, str] | None:
    for candidate in candidates or []:
        path = Path(candidate.get("path", ""))
        if path.exists():
            return candidate
    return None


def derived_candidate(path: Path, role: str) -> dict[str, str]:
    return {
        "path": str(path),
        "source": "derived_reference_bootstrap",
        "role": role,
        "confidence": "bootstrap",
    }


def create_alpha_mask(source_path: Path, output_path: Path) -> None:
    with Image.open(source_path) as source_image:
        image = source_image.convert("RGBA")
        alpha = image.getchannel("A")
        if alpha.getextrema()[1] <= 0:
            alpha = image.convert("L")
        alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.35))
        alpha = alpha.point(lambda value: 0 if value < 8 else min(255, int(value * 1.18)))
        output = Image.merge("RGBA", (alpha, alpha, alpha, alpha))
        output.save(output_path)


def create_core_flame_pass(source_path: Path, output_path: Path) -> None:
    with Image.open(source_path) as source_image:
        image = source_image.convert("RGBA")
        pixels = []
        for r, g, b, a in image.getdata():
            if a <= 4:
                pixels.append((0, 0, 0, 0))
                continue
            lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
            warm = r > 110 and g > 35 and r > b * 1.08
            hot = max(0.0, min(1.0, (lum - 0.28) / 0.62))
            alpha = int(min(255, a * (hot ** 0.58) * (1.15 if warm else 0.42)))
            if alpha < 10:
                pixels.append((0, 0, 0, 0))
                continue
            edge = max(0.0, min(1.0, hot))
            red = 255
            green = int(92 + 162 * edge)
            blue = int(10 + 130 * edge)
            pixels.append((red, green, blue, alpha))
        output = Image.new("RGBA", image.size)
        output.putdata(pixels)
        output = output.filter(ImageFilter.GaussianBlur(radius=0.25))
        output.save(output_path)


def create_smoke_heat_pass(source_path: Path, output_path: Path) -> None:
    with Image.open(source_path) as source_image:
        image = source_image.convert("RGBA")
        alpha = image.getchannel("A").filter(ImageFilter.GaussianBlur(radius=4.0))
        pixels = []
        for a in alpha.getdata():
            value = int(min(130, max(0, a * 0.42)))
            if value < 7:
                pixels.append((0, 0, 0, 0))
            else:
                pixels.append((54, 42, 34, value))
        output = Image.new("RGBA", image.size)
        output.putdata(pixels)
        output = output.filter(ImageFilter.GaussianBlur(radius=1.4))
        output.save(output_path)


def create_fire_atlas_pass(output_path: Path, pass_kind: str, columns: int = 4, rows: int = 4, frame_size: int = 256) -> None:
    atlas = Image.new("RGBA", (columns * frame_size, rows * frame_size), (0, 0, 0, 0))
    frame_count = columns * rows
    for index in range(frame_count):
        phase = index / max(frame_count - 1, 1)
        frame = Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame, "RGBA")
        if pass_kind == "flame_slashes":
            draw_flame_slash_frame(draw, frame_size, phase)
        elif pass_kind == "ground_ring":
            draw_ground_ring_frame(draw, frame_size, phase)
        elif pass_kind == "impact_flash":
            draw_impact_flash_frame(draw, frame_size, phase)
        elif pass_kind == "embers":
            draw_ember_frame(draw, frame_size, phase, index)
        frame = frame.filter(ImageFilter.GaussianBlur(radius=0.22 if pass_kind != "embers" else 0.05))
        x = (index % columns) * frame_size
        y = (index // columns) * frame_size
        atlas.alpha_composite(frame, (x, y))
    atlas.save(output_path)


def draw_flame_slash_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = math.sin(phase * math.pi)
    for side, direction in enumerate((-1, 1)):
        base_y = size * (0.67 - 0.08 * pulse)
        points = []
        for step in range(9):
            t = step / 8
            x = size * (0.5 + direction * (0.08 + 0.42 * t))
            y = base_y - size * (0.18 * math.sin(t * math.pi) + 0.22 * t)
            x += direction * math.sin(t * 9.0 + phase * 6.0 + side) * size * 0.035
            y += math.cos(t * 7.0 + phase * 4.0) * size * 0.025
            width = size * (0.075 * (1.0 - t) + 0.02)
            points.append((x, y, width))
        for color, scale in [((255, 74, 18, 80), 1.55), ((255, 166, 35, 160), 0.95), ((255, 245, 190, 210), 0.42)]:
            polygon = ribbon_polygon(points, scale)
            draw.polygon(polygon, fill=color)
    draw.ellipse((size * 0.28, size * 0.58, size * 0.72, size * 0.82), fill=(255, 124, 25, int(78 * pulse)))


def draw_ground_ring_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = math.sin(phase * math.pi)
    cx = cy = size / 2
    radius = size * (0.27 + 0.08 * phase)
    for start in range(0, 360, 38):
        gap = 9 + int(8 * math.sin(phase * 6.0 + start))
        width = int(size * (0.022 + 0.018 * pulse))
        box = (cx - radius, cy - radius * 0.72, cx + radius, cy + radius * 0.72)
        draw.arc(box, start=start + gap, end=start + 27, fill=(255, 104, 18, 210), width=width)
        draw.arc(box, start=start + 4 + gap, end=start + 18, fill=(255, 236, 170, 185), width=max(1, width // 2))
    inner = size * (0.08 + 0.04 * pulse)
    draw.ellipse((cx - inner, cy - inner * 0.7, cx + inner, cy + inner * 0.7), outline=(255, 176, 48, 135), width=max(1, int(size * 0.01)))


def draw_impact_flash_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = max(0.0, 1.0 - phase * 1.3)
    cx = cy = size / 2
    for amount, color in [(0.55, (255, 92, 18, 90)), (0.34, (255, 178, 54, 150)), (0.18, (255, 250, 210, 230))]:
        radius = size * amount * (0.25 + 0.9 * phase)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(*color[:3], int(color[3] * pulse)))
    for blade in range(8):
        angle = blade * math.tau / 8 + phase * 0.6
        length = size * (0.18 + 0.28 * pulse)
        width = size * 0.035 * pulse
        tip = (cx + math.cos(angle) * length, cy + math.sin(angle) * length)
        left = (cx + math.cos(angle + 1.9) * width, cy + math.sin(angle + 1.9) * width)
        right = (cx + math.cos(angle - 1.9) * width, cy + math.sin(angle - 1.9) * width)
        draw.polygon([left, tip, right], fill=(255, 238, 190, int(150 * pulse)))


def draw_ember_frame(draw: ImageDraw.ImageDraw, size: int, phase: float, seed: int) -> None:
    cols = 4
    rows = 4
    cell = size / cols
    for index in range(cols * rows):
        x0 = (index % cols) * cell
        y0 = (index // rows) * cell
        local = (seed * 17 + index * 29) % 100 / 100
        angle = local * math.tau + phase * 0.5
        cx = x0 + cell * (0.48 + 0.16 * math.sin(local * 9.0))
        cy = y0 + cell * (0.48 + 0.14 * math.cos(local * 7.0))
        radius = cell * (0.08 + 0.08 * ((index + seed) % 5) / 4)
        points = []
        for vertex in range(3):
            a = angle + vertex * math.tau / 3
            points.append((cx + math.cos(a) * radius * 1.5, cy + math.sin(a) * radius))
        draw.polygon(points, fill=(255, 224, 174, 230))
        draw.polygon([(cx, cy), *points[:2]], fill=(255, 124, 26, 160))


def ribbon_polygon(points: list[tuple[float, float, float]], scale: float) -> list[tuple[float, float]]:
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for index, (x, y, width) in enumerate(points):
        if index == 0:
            nx, ny = points[index + 1][0] - x, points[index + 1][1] - y
        elif index == len(points) - 1:
            nx, ny = x - points[index - 1][0], y - points[index - 1][1]
        else:
            nx, ny = points[index + 1][0] - points[index - 1][0], points[index + 1][1] - points[index - 1][1]
        length = math.hypot(nx, ny) or 1.0
        px, py = -ny / length, nx / length
        scaled = width * scale
        left.append((x + px * scaled, y + py * scaled))
        right.append((x - px * scaled, y - py * scaled))
    return left + list(reversed(right))


def create_distortion_flow_pass(output_path: Path, size: int = 256) -> None:
    image = Image.new("RGBA", (size, size), (128, 128, 0, 255))
    pixels = []
    for y in range(size):
        ny = y / max(size - 1, 1)
        for x in range(size):
            nx = x / max(size - 1, 1)
            u = 128 + int(48 * math.sin(nx * 18.0 + ny * 7.0) + 22 * math.sin(ny * 31.0))
            v = 128 + int(44 * math.cos(ny * 16.0 - nx * 8.0) + 18 * math.sin(nx * 25.0))
            pixels.append((max(0, min(255, u)), max(0, min(255, v)), 128, 255))
    image.putdata(pixels)
    image.save(output_path)


def should_apply_shared_alpha(pass_name: str | None, selected_path: str | None, alpha_path: str | None) -> bool:
    if pass_name not in {"core_flame_flipbook", "smoke_heat_flipbook"}:
        return False
    if not selected_path or not alpha_path:
        return False
    selected = Path(selected_path)
    alpha = Path(alpha_path)
    if not selected.exists() or not alpha.exists():
        return False
    try:
        with Image.open(selected) as selected_image, Image.open(alpha) as alpha_image:
            return selected_image.size == alpha_image.size
    except Exception:
        return False


def should_apply_distortion(emitter: dict[str, Any], distortion_path: str | None) -> bool:
    if not distortion_path or not Path(distortion_path).exists():
        return False
    return emitter.get("role") in {"fire_pillar", "flame_slashes", "atmospheric_wisp", "primary_bolt", "secondary_bolts"}


def quality_note_for_selected_asset(selected: dict[str, str] | None) -> str:
    if not selected:
        return "missing_required_generation_or_assignment"
    if selected.get("source") == "derived_reference_bootstrap":
        return "bootstrap_only_replace_with_ai_or_simulation_for_final_aaa_quality"
    if selected.get("source") == "reference_extraction":
        return "reference_extraction_useful_for_match_but_should_be_rebuilt_as_editable_layers"
    return "candidate_available"


def reference_candidates_for_spec(spec: dict[str, Any], reference_media: list[Path]) -> dict[str, list[dict[str, str]]]:
    candidates: dict[str, list[dict[str, str]]] = {}
    plan = spec.get("vfx_plan") or {}
    for emitter in plan.get("emitters") or []:
        source = emitter.get("sprite_source")
        if not source:
            continue
        source_path = Path(source)
        if not source_path.exists():
            continue
        role = emitter.get("role")
        pass_names = []
        if role == "reference_motion":
            pass_names.extend(["reference_motion_overlay", "beauty_flipbook"])
        if role in {"primary_body", "fire_pillar", "primary_bolt"}:
            pass_names.append("beauty_flipbook")
        for pass_name in pass_names:
            candidates.setdefault(pass_name, []).append(
                {
                    "path": str(source_path),
                    "source": "reference_extraction",
                    "role": str(role),
                    "confidence": "high",
                }
            )

    animated_media = [path for path in reference_media if path.suffix.lower() in ANIMATED_SUFFIXES]
    static_media = [path for path in reference_media if path.suffix.lower() in IMAGE_SUFFIXES]
    if animated_media:
        for path in animated_media:
            candidates.setdefault("reference_motion_overlay", []).append(
                {"path": str(path), "source": "reference_media", "role": "animated_reference", "confidence": "medium"}
            )
    if static_media:
        largest = max(static_media, key=lambda path: path.stat().st_size)
        candidates.setdefault("beauty_flipbook", []).append(
            {"path": str(largest), "source": "reference_media", "role": "static_style_reference", "confidence": "low"}
        )
    return candidates


def collect_ai_outputs(package_name: str, ai_art_root: Path) -> list[dict[str, str]]:
    package_root = ai_art_root / package_name
    if not package_root.exists():
        return []
    outputs: list[dict[str, str]] = []
    for manifest_path in sorted(package_root.glob("**/manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in manifest.get("outputs") or []:
            path = Path(str(item.get("path") or ""))
            if path.exists():
                outputs.append(
                    {
                        "path": str(path),
                        "source": str(manifest.get("provider") or "ai_art"),
                        "manifest": str(manifest_path),
                        "filename": path.name,
                        "candidate_passes": item.get("candidate_passes") or [],
                        "confidence": "medium",
                    }
                )
    return outputs


def classify_ai_outputs_for_pass(pass_name: str, ai_outputs: list[dict[str, str]]) -> list[dict[str, str]]:
    keywords = keywords_for_pass(pass_name)
    matched = []
    fallback = []
    for output in ai_outputs:
        filename = output.get("filename", "").lower()
        candidate_passes = output.get("candidate_passes") or []
        if pass_name in candidate_passes:
            matched.append({**output, "matched_by": "provider_manifest"})
        elif any(keyword in filename for keyword in keywords):
            matched.append({**output, "matched_by": "filename_keyword"})
        elif pass_name == "beauty_flipbook":
            fallback.append({**output, "matched_by": "beauty_fallback"})
    return matched or fallback[:1]


def keywords_for_pass(pass_name: str) -> list[str]:
    name = pass_name.lower()
    if "alpha" in name or "mask" in name:
        return ["alpha", "mask", "matte", "opacity"]
    if "motion" in name:
        return ["motion", "vector", "velocity", "mv"]
    if "distortion" in name or "flow" in name:
        return ["distort", "flow", "heat", "normal"]
    if "normal" in name or "lighting" in name:
        return ["normal", "depth", "lighting", "light"]
    if "smoke" in name:
        return ["smoke", "heat", "wisp"]
    if "core" in name or "flame" in name:
        return ["core", "flame", "fire", "beauty"]
    if "bolt" in name:
        return ["bolt", "branch", "lightning", "arc"]
    if "impact" in name:
        return ["impact", "flash", "burst"]
    if "ring" in name:
        return ["ring", "rune", "ground"]
    if "reference" in name:
        return ["reference", "overlay"]
    return [name]


def prompt_for_asset_pass(pass_spec: dict[str, Any]) -> str:
    name = str(pass_spec.get("name") or "vfx_pass")
    purpose = str(pass_spec.get("purpose") or "game VFX asset pass")
    output_format = str(pass_spec.get("format") or "transparent PNG sequence or atlas")
    return (
        f"Create the {name} pass for a realtime AAA game VFX effect. "
        f"Purpose: {purpose}. "
        f"Output as {output_format}. "
        "Use clean alpha, centered composition, no background environment, no UI text, no watermark, no atlas grid lines. "
        "Preserve the reference silhouette, timing, color palette, and readable game-effect shape."
    )


def next_actions_for_entries(entries: list[dict[str, Any]]) -> list[str]:
    missing_required = [entry for entry in entries if entry.get("required") and entry.get("status") != "ready"]
    if not missing_required:
        return [
            "All required asset passes have candidates. Generate Unreal assets and inspect the Blueprint preview.",
            "Tune secondary Niagara layers against the reference overlay instead of increasing particle count.",
        ]
    actions = [
        f"Generate or assign required pass '{entry['name']}' ({entry.get('format')})"
        for entry in missing_required
    ]
    actions.append("Run the AI art provider or simulation tool, then rebuild the asset pass manifest.")
    return actions


def resolve_from_workspace(path: Path) -> Path:
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path
