from __future__ import annotations

import json
import copy
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from tools.analyze_packages import analyze_effect_package, find_package_media


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ASSET_PASS_ROOT = WORKSPACE_ROOT / "generated" / "asset-passes"
DEFAULT_AI_ART_ROOT = WORKSPACE_ROOT / "generated" / "ai-art"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".exr", ".hdr"}
ANIMATED_SUFFIXES = {".gif", ".mp4", ".mov", ".webm"}


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
        material["opacity"] = max(float(material.get("opacity", 0.54)), 0.78)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 14.0)), 18.0)
        card.update({"enabled": True, "location": [0, 0, 150], "rotation": [90, 0, 0], "scale": [1.25, 2.65, 1.15]})
        niagara["enabled"] = False
    elif role == "flame_slashes":
        material["opacity"] = max(float(material.get("opacity", 0.5)), 0.62)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 8.5)), 11.0)
        card.update({"enabled": True, "location": [0, -2, 78], "rotation": [90, 0, -10], "scale": [2.55, 1.5, 1]})
        niagara["enabled"] = False
    elif role == "ground_energy_ring":
        material["opacity"] = max(float(material.get("opacity", 0.72)), 0.76)
        card.update({"enabled": True, "location": [0, 0, 4], "rotation": [0, 0, 0], "scale": [3.65, 3.65, 1]})
        niagara["enabled"] = False
    elif role == "impact_core":
        material["opacity"] = max(float(material.get("opacity", 0.8)), 0.86)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 22.0)), 24.0)
        card.update({"enabled": True, "location": [0, -1, 44], "rotation": [90, 0, 0], "scale": [1.55, 1.1, 1]})
        niagara["enabled"] = False
    elif role == "atmospheric_wisp":
        material["opacity"] = max(float(material.get("opacity", 0.2)), 0.26)
        material["blend_mode"] = "translucent"
        card.update({"enabled": True, "location": [-4, 5, 122], "rotation": [90, 0, 7], "scale": [2.2, 2.0, 1]})
        niagara["enabled"] = False
    elif role == "detail_particles":
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
        if role == "atmospheric_wisp":
            return "smoke_heat_flipbook"
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
        if any(keyword in filename for keyword in keywords):
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
