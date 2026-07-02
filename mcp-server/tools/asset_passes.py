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
    manual_outputs = collect_manual_pass_outputs(package_path)
    ai_outputs = collect_ai_outputs(package_path.name, ai_art_root)
    reference_candidates = reference_candidates_for_spec(spec.to_dict(), reference_media)
    derived_candidates = derive_bootstrap_candidates(package_path.name, pass_specs, reference_candidates, reference_media, output_root)

    entries = [
        asset_pass_entry(pass_spec, manual_outputs, reference_candidates, derived_candidates, ai_outputs, package_path.name, output_root)
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
        "similarity_report": read_similarity_report(output_root / package_path.name / "derived" / f"{package_path.name}_similarity_report.json"),
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
    ensure_reference_matched_composite_emitter(patched, plan, passes_by_name)
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


def ensure_reference_matched_composite_emitter(spec: dict[str, Any], plan: dict[str, Any], passes_by_name: dict[str, dict[str, Any]]) -> None:
    if spec.get("effect_type") != "fire_or_flame":
        return
    composite_pass = passes_by_name.get("reference_matched_composite") or {}
    selected = composite_pass.get("selected_asset") or {}
    source_path = selected.get("path")
    if not source_path or not Path(source_path).exists():
        return
    emitters = plan.setdefault("emitters", [])
    if any(emitter.get("role") == "reference_matched_composite" for emitter in emitters):
        return
    emitters.insert(
        1 if emitters else 0,
        {
            "name": "reference_matched_composite",
            "role": "reference_matched_composite",
            "sprite_shape": "reference_matched_composite",
            "material_style": "reference_matched_composite_additive",
            "motion": "locked_reference_matched_preview",
            "spawn_rate": 1.0,
            "lifetime_seconds": 0.9,
            "start_size": 160.0,
            "end_size": 160.0,
            "color_palette": spec.get("color_palette", ["#FFFFFF"]),
            "sprite_source": source_path,
            "notes": [
                "Viewport fidelity anchor generated from the local layered preview.",
                "This is not the final procedural solution; keep editable layers active in front of it.",
            ],
            "unreal_settings": {
                "enabled": True,
                "material": {
                    "opacity": 0.34,
                    "emissive_strength": 2.2,
                    "blend_mode": "additive",
                },
                "timeline": {
                    "delay": 0.0,
                    "duration": 0.9,
                    "opacity": [0.0, 0.34, 0.3, 0.0],
                    "scale": [1.0, 1.0, 1.0, 1.0],
                    "rotation_speed": 0.0,
                },
                "preview": {
                    "card": {
                        "enabled": True,
                        "location": [0.0, -10.0, 104.0],
                        "rotation": [90.0, 0.0, 0.0],
                        "scale": [1.45, 1.45, 1.0],
                    },
                    "niagara": {"enabled": False},
                },
                "niagara": {
                    "spawn_rate": 1.0,
                    "lifetime_seconds": 0.9,
                    "start_size": 160.0,
                    "end_size": 160.0,
                },
            },
        },
    )


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

    if role == "reference_matched_composite":
        timeline.update({"delay": 0.0, "duration": 0.9, "opacity": [0.0, 0.34, 0.3, 0.0], "scale": [1.0, 1.0, 1.0, 1.0], "rotation_speed": 0.0})
        material["opacity"] = min(float(material.get("opacity", 0.34)), 0.34)
        material["emissive_strength"] = min(float(material.get("emissive_strength", 2.2)), 2.2)
        material["blend_mode"] = "additive"
        card.update({"enabled": True, "location": [0, -10, 104], "rotation": [90, 0, 0], "scale": [1.45, 1.45, 1]})
        niagara["enabled"] = False
    elif role == "fire_pillar":
        timeline.update({"delay": 0.07, "duration": 0.58, "opacity": [0.0, 0.92, 0.82, 0.0], "scale": [0.42, 1.12, 1.0, 0.68]})
        material["opacity"] = max(float(material.get("opacity", 0.54)), 0.78)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 14.0)), 18.0)
        card.update({"enabled": True, "location": [0, 0, 138], "rotation": [90, 0, 0], "scale": [1.0, 2.15, 1.0]})
        niagara["enabled"] = False
    elif role == "flame_slashes":
        timeline.update({"delay": 0.1, "duration": 0.52, "opacity": [0.0, 0.78, 0.58, 0.0], "scale": [0.55, 1.08, 1.16, 0.82]})
        material["opacity"] = max(float(material.get("opacity", 0.5)), 0.62)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 8.5)), 11.0)
        card.update({"enabled": True, "location": [0, -2, 68], "rotation": [90, 0, -10], "scale": [1.85, 1.1, 1]})
        niagara["enabled"] = False
    elif role == "ground_energy_ring":
        timeline.update({"delay": 0.02, "duration": 0.72, "opacity": [0.0, 0.9, 0.72, 0.0], "scale": [0.55, 1.12, 1.04, 1.28], "rotation_speed": 18.0})
        material["opacity"] = max(float(material.get("opacity", 0.72)), 0.76)
        card.update({"enabled": True, "location": [0, 0, 4], "rotation": [0, 0, 0], "scale": [2.55, 2.55, 1]})
        niagara["enabled"] = False
    elif role == "impact_core":
        timeline.update({"delay": 0.0, "duration": 0.24, "opacity": [0.0, 1.0, 0.45, 0.0], "scale": [0.35, 1.22, 0.84, 0.0]})
        material["opacity"] = max(float(material.get("opacity", 0.8)), 0.86)
        material["emissive_strength"] = max(float(material.get("emissive_strength", 22.0)), 24.0)
        card.update({"enabled": True, "location": [0, -1, 38], "rotation": [90, 0, 0], "scale": [1.05, 0.82, 1]})
        niagara["enabled"] = False
    elif role == "atmospheric_wisp":
        timeline.update({"delay": 0.18, "duration": 1.05, "opacity": [0.0, 0.24, 0.18, 0.0], "scale": [0.62, 1.0, 1.22, 1.46], "rotation_speed": 5.0})
        material["opacity"] = min(float(material.get("opacity", 0.2)), 0.12)
        material["blend_mode"] = "translucent"
        card.update({"enabled": False, "location": [-4, 5, 122], "rotation": [90, 0, 7], "scale": [2.2, 2.0, 1]})
        niagara["enabled"] = False
        emitter.setdefault("notes", []).append("Smoke card is hidden in preview until the smoke alpha pass is clean enough to avoid rectangular artifacts.")
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
        if role == "reference_matched_composite":
            return "reference_matched_composite"
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
    manual_outputs: list[dict[str, str]],
    reference_candidates: dict[str, list[dict[str, str]]],
    derived_candidates: dict[str, list[dict[str, str]]],
    ai_outputs: list[dict[str, str]],
    package_name: str,
    output_root: Path,
) -> dict[str, Any]:
    name = str(pass_spec.get("name") or "unknown_pass")
    candidates = [
        *classify_manual_outputs_for_pass(name, manual_outputs),
        *classify_ai_outputs_for_pass(name, ai_outputs),
        *reference_candidates.get(name, []),
        *derived_candidates.get(name, []),
    ]
    selected = prepare_runtime_asset(candidates[0], name, package_name, output_root) if candidates else None
    prompt = prompt_for_asset_pass(pass_spec)
    budget = texture_budget_for_pass(name)
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
        "asset_metadata": asset_metadata_for_selected_asset(selected, name),
        "runtime_budget": budget,
        "quality_note": quality_note_for_selected_asset(selected),
        "generation_prompt": prompt,
        "negative_prompt": "watermark, text, logo, UI, character, weapon, environment, rectangular card border, atlas grid",
    }


def prepare_runtime_asset(selected: dict[str, str], pass_name: str, package_name: str, output_root: Path) -> dict[str, str]:
    path = Path(selected.get("path", ""))
    if not path.exists() or path.suffix.lower() not in IMAGE_SUFFIXES:
        return selected
    budget = texture_budget_for_pass(pass_name)
    max_edge = int(budget.get("max_import_edge", 1024))
    try:
        with Image.open(path) as image:
            width, height = image.size
            if max(width, height) <= max_edge:
                return {**selected, "runtime_resized": "false", "runtime_max_edge": str(max_edge)}
            resized = image.convert("RGBA")
            ratio = max_edge / max(width, height)
            target_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
            resized = resized.resize(target_size, Image.Resampling.LANCZOS)
    except Exception:
        return selected

    runtime_dir = output_root / package_name / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / f"{package_name}_{safe_file_token(pass_name)}_{safe_file_token(path.stem)}_rt.png"
    resized.save(runtime_path)
    return {
        **selected,
        "path": str(runtime_path),
        "original_path": str(path),
        "runtime_resized": "true",
        "runtime_max_edge": str(max_edge),
    }


def texture_budget_for_pass(pass_name: str) -> dict[str, Any]:
    budgets = {
        "reference_matched_composite": {"max_import_edge": 512, "max_preview_scale": 1.6, "max_card_area": 2.8, "usage": "small_similarity_anchor"},
        "reference_motion_overlay": {"max_import_edge": 1024, "max_preview_scale": 1.8, "max_card_area": 3.2, "usage": "preview_only_motion_target"},
        "beauty_flipbook": {"max_import_edge": 1024, "max_preview_scale": 2.2, "max_card_area": 4.4, "usage": "source_or_flipbook"},
        "core_flame_flipbook": {"max_import_edge": 1024, "max_preview_scale": 2.2, "max_card_area": 2.4, "usage": "primary_shaped_layer"},
        "flame_slash_flipbook": {"max_import_edge": 1024, "max_preview_scale": 2.0, "max_card_area": 2.2, "usage": "secondary_shaped_layer"},
        "ground_ring_mask": {"max_import_edge": 768, "max_preview_scale": 2.6, "max_card_area": 6.8, "usage": "ground_anchor"},
        "impact_flash_mask": {"max_import_edge": 512, "max_preview_scale": 1.2, "max_card_area": 1.2, "usage": "short_flash"},
        "smoke_heat_flipbook": {"max_import_edge": 768, "max_preview_scale": 1.6, "max_card_area": 2.6, "usage": "low_opacity_support"},
        "ember_sprite_set": {"max_import_edge": 512, "max_preview_scale": 0.8, "max_card_area": 0.8, "usage": "small_particle_detail"},
        "bolt_branch_set": {"max_import_edge": 1024, "max_preview_scale": 2.2, "max_card_area": 3.8, "usage": "thin_directional_layer"},
        "alpha_mask": {"max_import_edge": 1024, "max_preview_scale": 2.2, "max_card_area": 4.4, "usage": "mask_data"},
        "distortion_flow": {"max_import_edge": 512, "max_preview_scale": 1.6, "max_card_area": 2.6, "usage": "flow_data"},
        "normal_or_lighting": {"max_import_edge": 512, "max_preview_scale": 1.6, "max_card_area": 2.6, "usage": "lighting_data"},
    }
    return budgets.get(pass_name, {"max_import_edge": 768, "max_preview_scale": 1.8, "max_card_area": 3.0, "usage": "generic_vfx_layer"})


def safe_file_token(value: str) -> str:
    token = "".join(character if character.isalnum() else "_" for character in value)
    return token.strip("_") or "asset"


def asset_metadata_for_selected_asset(selected: dict[str, str] | None, pass_name: str | None = None) -> dict[str, Any]:
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
    atlas = atlas_metadata_for_asset(pass_name, selected, width, height)
    return {
        "width": width,
        "height": height,
        "atlas": atlas,
    }


def atlas_metadata_for_asset(pass_name: str | None, selected: dict[str, str], width: int, height: int) -> dict[str, Any] | None:
    role = str(selected.get("role") or "").lower()
    source = str(selected.get("source") or "").lower()
    filename = Path(selected.get("path", "")).name.lower()
    name = str(pass_name or "").lower()
    if name == "reference_matched_composite" or source == "reference_matched_composite" or "reference_matched_preview" in filename:
        return None
    if source == "reference_media" and not any(token in role for token in ("animated", "flipbook", "sequence")):
        return None
    atlas_passes = {
        "alpha_mask",
        "beauty_flipbook",
        "core_flame_flipbook",
        "smoke_heat_flipbook",
        "ground_ring_mask",
        "flame_slash_flipbook",
        "impact_flash_mask",
        "ember_sprite_set",
        "reference_motion_overlay",
        "bolt_branch_set",
    }
    if name not in atlas_passes and not any(token in filename for token in ("flipbook", "atlas", "sprite_set")):
        return None
    columns = max(1, round(width / 256)) if width >= 512 else 1
    rows = max(1, round(height / 256)) if height >= 512 else 1
    if columns <= 1 and rows <= 1:
        return None
    return {
        "columns": columns,
        "rows": rows,
        "frame_count": max(1, columns * rows),
        "fps": 12.0,
    }


def derive_bootstrap_candidates(
    package_name: str,
    pass_specs: list[dict[str, Any]],
    reference_candidates: dict[str, list[dict[str, str]]],
    reference_media: list[Path],
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

    static_references = [path for path in reference_media if path.suffix.lower() in IMAGE_SUFFIXES]
    target_reference = best_reference_for_layer(static_references, "target_fire") or (static_references[0] if static_references else source_path)
    core_source = best_reference_for_layer(static_references, "core_flame") or target_reference
    side_source = best_reference_for_layer(static_references, "side_flames") or source_path
    ring_source = best_reference_for_layer(static_references, "ground_ring") or side_source
    smoke_source = best_reference_for_layer(static_references, "smoke") or side_source

    target_names = {str(pass_spec.get("name") or "") for pass_spec in pass_specs}
    output_dir = output_root / package_name / "derived"
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates: dict[str, list[dict[str, str]]] = {}
    if "alpha_mask" in target_names:
        alpha_path = output_dir / f"{package_name}_alpha_mask.png"
        create_reference_extracted_fire_atlas(target_reference, alpha_path, "alpha_mask")
        candidates.setdefault("alpha_mask", []).append(derived_candidate(alpha_path, "alpha_from_reference_layers", source="reference_layer_extraction", confidence="medium"))

    if "core_flame_flipbook" in target_names:
        core_path = output_dir / f"{package_name}_core_flame_flipbook.png"
        create_reference_extracted_fire_atlas(core_source, core_path, "core_flame")
        candidates.setdefault("core_flame_flipbook", []).append(derived_candidate(core_path, "core_flame_from_reference_layer", source="reference_layer_extraction", confidence="medium"))

    if "smoke_heat_flipbook" in target_names:
        smoke_path = output_dir / f"{package_name}_smoke_heat_flipbook.png"
        create_reference_extracted_fire_atlas(smoke_source, smoke_path, "smoke_heat")
        candidates.setdefault("smoke_heat_flipbook", []).append(derived_candidate(smoke_path, "smoke_heat_from_reference_layer", source="reference_layer_extraction", confidence="medium"))

    if "flame_slash_flipbook" in target_names:
        slash_path = output_dir / f"{package_name}_flame_slash_flipbook.png"
        create_reference_extracted_fire_atlas(side_source, slash_path, "flame_slashes")
        candidates.setdefault("flame_slash_flipbook", []).append(derived_candidate(slash_path, "side_flames_from_reference_layer", source="reference_layer_extraction", confidence="medium"))

    if "ground_ring_mask" in target_names:
        ring_path = output_dir / f"{package_name}_ground_ring_mask.png"
        create_reference_extracted_fire_atlas(ring_source, ring_path, "ground_ring")
        candidates.setdefault("ground_ring_mask", []).append(derived_candidate(ring_path, "ground_ring_from_reference_layer", source="reference_layer_extraction", confidence="medium"))

    if "impact_flash_mask" in target_names:
        flash_path = output_dir / f"{package_name}_impact_flash_mask.png"
        create_reference_extracted_fire_atlas(ring_source, flash_path, "impact_flash")
        candidates.setdefault("impact_flash_mask", []).append(derived_candidate(flash_path, "impact_flash_from_reference_layer", source="reference_layer_extraction", confidence="medium"))

    if "ember_sprite_set" in target_names:
        ember_path = output_dir / f"{package_name}_ember_sprite_set.png"
        create_reference_extracted_fire_atlas(target_reference, ember_path, "embers")
        candidates.setdefault("ember_sprite_set", []).append(derived_candidate(ember_path, "embers_from_reference_layer", source="reference_layer_extraction", confidence="medium"))

    if "distortion_flow" in target_names:
        flow_path = output_dir / f"{package_name}_distortion_flow.png"
        create_distortion_flow_pass(flow_path)
        candidates.setdefault("distortion_flow", []).append(derived_candidate(flow_path, "procedural_heat_distortion_flow"))

    similarity_report = create_similarity_report(package_name, target_reference, output_dir)
    if "reference_matched_composite" in target_names:
        preview_path = similarity_report.get("preview")
        if preview_path and Path(preview_path).exists():
            candidates.setdefault("reference_matched_composite", []).append(
                derived_candidate(Path(preview_path), "reference_matched_viewport_anchor", source="reference_matched_composite", confidence="medium")
            )
    return candidates


def first_existing_candidate(candidates: list[dict[str, str]] | None) -> dict[str, str] | None:
    for candidate in candidates or []:
        path = Path(candidate.get("path", ""))
        if path.exists():
            return candidate
    return None


def best_reference_for_layer(reference_paths: list[Path], layer_kind: str) -> Path | None:
    if layer_kind in {"side_flames", "smoke"} and reference_paths:
        return max(reference_paths, key=lambda path: path.stat().st_size)
    scored: list[tuple[float, Path]] = []
    for path in reference_paths:
        try:
            with Image.open(path) as source_image:
                image = source_image.convert("RGB").resize((160, 96), Image.Resampling.BILINEAR)
                score = reference_layer_score(image, layer_kind)
        except Exception:
            continue
        scored.append((score, path))
    if not scored:
        return None
    return max(scored, key=lambda item: item[0])[1]


def reference_layer_score(image: Image.Image, layer_kind: str) -> float:
    width, height = image.size
    score = 0.0
    for y in range(height):
        y01 = y / max(height - 1, 1)
        for x in range(width):
            x01 = x / max(width - 1, 1)
            r, g, b = image.getpixel((x, y))
            lum = luminance01(r, g, b)
            warm = warm_score01(r, g, b)
            side = smoothstep01(0.12, 0.42, abs(x01 - 0.5))
            lower = smoothstep01(0.46, 0.86, y01)
            center = 1.0 - smoothstep01(0.0, 0.28, abs(x01 - 0.5))
            if layer_kind == "ground_ring":
                score += warm * lower * (0.45 + side * 0.55)
            elif layer_kind == "target_fire":
                score += warm * (0.35 + lum * 0.65) * (0.45 + lower * 0.4 + center * 0.15)
            elif layer_kind == "core_flame":
                score += lum * warm * center * (1.0 - smoothstep01(0.82, 1.0, y01))
            elif layer_kind == "side_flames":
                score += warm * side * (0.35 + smoothstep01(0.28, 0.78, y01) * 0.65) * (1.0 - center * smoothstep01(0.62, 0.95, lum))
            elif layer_kind == "smoke":
                darkness = 1.0 - lum
                score += darkness * lower * (0.35 + side * 0.65)
            else:
                score += warm * lum
    return score / max(width * height, 1)


def derived_candidate(path: Path, role: str, source: str = "derived_reference_bootstrap", confidence: str = "bootstrap") -> dict[str, str]:
    return {
        "path": str(path),
        "source": source,
        "role": role,
        "confidence": confidence,
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


def create_reference_extracted_fire_atlas(source_path: Path, output_path: Path, layer_kind: str, columns: int = 4, rows: int = 4, frame_size: int = 256) -> None:
    with Image.open(source_path) as source_image:
        source = source_image.convert("RGBA")
        layer = extract_fire_reference_layer(source, layer_kind)
    if not layer.getchannel("A").getbbox():
        create_fire_atlas_pass(output_path, fallback_fire_pass_kind(layer_kind), columns=columns, rows=rows, frame_size=frame_size)
        return

    atlas = Image.new("RGBA", (columns * frame_size, rows * frame_size), (0, 0, 0, 0))
    frame_count = columns * rows
    for index in range(frame_count):
        phase = index / max(frame_count - 1, 1)
        frame = render_reference_layer_frame(layer, layer_kind, phase, frame_size)
        x = (index % columns) * frame_size
        y = (index // columns) * frame_size
        atlas.alpha_composite(frame, (x, y))
    atlas.save(output_path)


def extract_fire_reference_layer(source: Image.Image, layer_kind: str) -> Image.Image:
    width, height = source.size
    output = Image.new("RGBA", source.size, (0, 0, 0, 0))
    pixels = []
    for y in range(height):
        y01 = y / max(height - 1, 1)
        for x in range(width):
            x01 = x / max(width - 1, 1)
            r, g, b, a = source.getpixel((x, y))
            if a <= 4:
                pixels.append((0, 0, 0, 0))
                continue
            lum = luminance01(r, g, b)
            warm = warm_score01(r, g, b)
            hot = smoothstep01(0.48, 0.94, lum)
            lower = smoothstep01(0.42, 0.82, y01)
            side = smoothstep01(0.12, 0.36, abs(x01 - 0.5))
            center = 1.0 - smoothstep01(0.0, 0.24, abs(x01 - 0.5))
            if layer_kind == "flame_slashes":
                vertical_window = smoothstep01(0.12, 0.28, y01) * (1.0 - smoothstep01(0.78, 0.96, y01))
                alpha01 = warm * side * vertical_window * (1.0 - center * hot * 0.78)
                color = boost_fire_color(r, g, b, 1.18)
            elif layer_kind == "core_flame":
                vertical_window = smoothstep01(0.04, 0.22, y01) * (1.0 - smoothstep01(0.92, 1.0, y01))
                alpha01 = max(hot * center * vertical_window, warm * lum * center * 0.82)
                color = boost_fire_color(r, g, b, 1.45)
            elif layer_kind == "ground_ring":
                alpha01 = warm * lower * (0.55 + side * 0.45) * (1.0 - center * hot * 0.48)
                color = boost_fire_color(r, g, b, 1.08)
            elif layer_kind == "impact_flash":
                base_window = smoothstep01(0.38, 0.72, y01) * (1.0 - smoothstep01(0.96, 1.0, y01))
                alpha01 = max(hot * base_window, warm * lum * lower * 0.72)
                color = boost_fire_color(r, g, b, 1.35)
            elif layer_kind == "smoke_heat":
                darkness = 1.0 - lum
                cool_dark = max(0.0, (b + g * 0.4 - r * 0.28) / 255.0)
                alpha01 = (darkness * (0.55 + lower * 0.45) * (0.35 + side * 0.65) * (1.0 - warm * 0.72)) + cool_dark * 0.18
                color = (58, 45, 37)
            elif layer_kind == "alpha_mask":
                alpha01 = max(hot * 0.95, warm * lum * 0.82, (1.0 - lum) * lower * side * 0.35)
                color = (255, 255, 255)
            elif layer_kind == "embers":
                spark_window = warm * lum * (0.35 + side * 0.65)
                isolated = smoothstep01(0.72, 0.98, lum) * (0.4 + side * 0.6)
                alpha01 = max(isolated, spark_window * 0.52)
                color = boost_fire_color(r, g, b, 1.35)
            else:
                alpha01 = warm * lum
                color = boost_fire_color(r, g, b, 1.0)
            alpha = int(clamp01(alpha01) * 255)
            minimum_alpha = alpha_threshold_for_layer(layer_kind)
            if alpha < minimum_alpha:
                pixels.append((0, 0, 0, 0))
            else:
                sharpened_alpha = int(min(255, (alpha - minimum_alpha) * alpha_gain_for_layer(layer_kind)))
                pixels.append((color[0], color[1], color[2], sharpened_alpha))
    output.putdata(pixels)
    blur = 1.4 if layer_kind == "smoke_heat" else (0.15 if layer_kind == "embers" else 0.35)
    return output.filter(ImageFilter.GaussianBlur(radius=blur))


def render_reference_layer_frame(layer: Image.Image, layer_kind: str, phase: float, frame_size: int) -> Image.Image:
    bbox = layer.getchannel("A").getbbox()
    if not bbox:
        return Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
    cropped = layer.crop(expand_bbox(bbox, layer.size, 0.08))
    scale, opacity, y_offset, rotation = layer_motion_values(layer_kind, phase)
    cropped = multiply_alpha(cropped, opacity)
    fit_size = fit_dimensions(cropped.size, frame_size, scale)
    resized = cropped.resize(fit_size, Image.Resampling.LANCZOS)
    if abs(rotation) > 0.01:
        resized = resized.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=True)
    frame = Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
    x = (frame_size - resized.size[0]) // 2
    y = (frame_size - resized.size[1]) // 2 + int(y_offset * frame_size)
    frame.alpha_composite(resized, (x, y))
    return frame


def layer_motion_values(layer_kind: str, phase: float) -> tuple[float, float, float, float]:
    pulse = math.sin(phase * math.pi)
    if layer_kind == "core_flame":
        return 0.78 + 0.18 * pulse, 0.45 + 0.55 * pulse, -0.08 * phase, 1.5 * math.sin(phase * math.tau)
    if layer_kind == "flame_slashes":
        return 0.9 + 0.18 * pulse, 0.35 + 0.65 * pulse, -0.03 * pulse, -5.0 + 10.0 * phase
    if layer_kind == "ground_ring":
        return 0.72 + 0.38 * smoothstep01(0.0, 0.7, phase), 1.0 - smoothstep01(0.78, 1.0, phase) * 0.85, 0.08, 16.0 * phase
    if layer_kind == "impact_flash":
        return 0.55 + 0.72 * phase, max(0.0, 1.0 - phase * 1.18), 0.02, 0.0
    if layer_kind == "smoke_heat":
        return 0.92 + 0.34 * phase, 0.18 + 0.38 * pulse, -0.04 - 0.06 * phase, 4.0 * math.sin(phase * math.tau)
    if layer_kind == "alpha_mask":
        return 1.0, 1.0, 0.0, 0.0
    if layer_kind == "embers":
        return 0.85 + 0.12 * pulse, 0.2 + 0.72 * pulse, -0.18 * phase, 12.0 * math.sin(phase * math.tau)
    return 1.0, 1.0, 0.0, 0.0


def fallback_fire_pass_kind(layer_kind: str) -> str:
    if layer_kind == "core_flame":
        return "impact_flash"
    if layer_kind == "embers":
        return "embers"
    if layer_kind == "smoke_heat":
        return "ground_ring"
    if layer_kind == "impact_flash":
        return "impact_flash"
    if layer_kind == "ground_ring":
        return "ground_ring"
    return "flame_slashes"


def alpha_threshold_for_layer(layer_kind: str) -> int:
    if layer_kind == "smoke_heat":
        return 18
    if layer_kind == "alpha_mask":
        return 14
    if layer_kind == "core_flame":
        return 22
    if layer_kind == "embers":
        return 46
    return 28


def alpha_gain_for_layer(layer_kind: str) -> float:
    if layer_kind == "smoke_heat":
        return 0.9
    if layer_kind == "alpha_mask":
        return 1.65
    if layer_kind == "core_flame":
        return 1.55
    if layer_kind == "embers":
        return 1.85
    return 1.35


def expand_bbox(bbox: tuple[int, int, int, int], image_size: tuple[int, int], amount: float) -> tuple[int, int, int, int]:
    left, top, right, bottom = bbox
    width, height = image_size
    pad_x = int((right - left) * amount)
    pad_y = int((bottom - top) * amount)
    return (max(0, left - pad_x), max(0, top - pad_y), min(width, right + pad_x), min(height, bottom + pad_y))


def fit_dimensions(size: tuple[int, int], frame_size: int, scale: float) -> tuple[int, int]:
    width, height = size
    longest = max(width, height, 1)
    target = max(1, int(frame_size * 0.86 * scale))
    ratio = target / longest
    return (max(1, int(width * ratio)), max(1, int(height * ratio)))


def multiply_alpha(image: Image.Image, opacity: float) -> Image.Image:
    opacity = clamp01(opacity)
    output = image.copy()
    alpha = output.getchannel("A").point(lambda value: int(value * opacity))
    output.putalpha(alpha)
    return output


def create_similarity_report(package_name: str, target_reference: Path, output_dir: Path) -> dict[str, Any]:
    report_path = output_dir / f"{package_name}_similarity_report.json"
    preview_path = output_dir / f"{package_name}_reference_matched_preview.png"
    try:
        with Image.open(target_reference) as target_image:
            target = target_image.convert("RGBA")
        preview = build_reference_matched_preview(target)
        preview.save(preview_path)
        score = similarity_score(preview, target)
        alpha = alpha_coverage_metrics(preview)
        report = {
            "target_reference": str(target_reference),
            "preview": str(preview_path),
            "score": score,
            "alpha": alpha,
            "status": "pass" if score.get("overall", 0.0) >= 0.8 and not alpha.get("opaque_card_risk") else "needs_iteration",
            "target": 0.8,
            "notes": [
                "Similarity is computed from a local composited preview before Unreal import.",
                "It measures color, luminance, and silhouette overlap; Unreal viewport review is still required.",
                "The preview image must keep transparent alpha; opaque rectangular cards are rejected.",
            ],
        }
    except Exception as exc:
        report = {
            "target_reference": str(target_reference),
            "preview": str(preview_path),
            "score": {"overall": 0.0},
            "status": "error",
            "error": str(exc),
        }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def build_reference_matched_preview(target: Image.Image, size: int = 512) -> Image.Image:
    base = fit_image_to_square(target, size, fill_alpha=0)
    layers = [
        ("ground_ring", 1.0),
        ("flame_slashes", 0.95),
        ("impact_flash", 1.0),
        ("core_flame", 1.0),
        ("embers", 0.78),
    ]
    preview = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for layer_kind, opacity in layers:
        layer = extract_fire_reference_layer(base, layer_kind)
        layer = multiply_alpha(layer, opacity)
        preview.alpha_composite(layer)
    return preview


def fit_image_to_square(image: Image.Image, size: int, fill_alpha: int = 255) -> Image.Image:
    result = Image.new("RGBA", (size, size), (0, 0, 0, fill_alpha))
    image = image.convert("RGBA")
    ratio = min(size / image.size[0], size / image.size[1])
    resized = image.resize((max(1, int(image.size[0] * ratio)), max(1, int(image.size[1] * ratio))), Image.Resampling.LANCZOS)
    x = (size - resized.size[0]) // 2
    y = (size - resized.size[1]) // 2
    result.alpha_composite(resized, (x, y))
    return result


def dark_reference_backdrop(image: Image.Image) -> Image.Image:
    backdrop = image.convert("RGBA")
    pixels = []
    for r, g, b, a in backdrop.getdata():
        lum = luminance01(r, g, b)
        keep = 0.18 + (1.0 - smoothstep01(0.12, 0.55, lum)) * 0.28
        pixels.append((int(r * keep), int(g * keep), int(b * keep), a))
    backdrop.putdata(pixels)
    return backdrop


def similarity_score(preview: Image.Image, target: Image.Image, size: int = 256) -> dict[str, float]:
    preview_small = fit_image_to_square(preview, size).convert("RGB")
    target_small = fit_image_to_square(target, size).convert("RGB")
    total = size * size
    luminance_error = 0.0
    color_error = 0.0
    silhouette_intersection = 0
    silhouette_union = 0
    for preview_pixel, target_pixel in zip(preview_small.getdata(), target_small.getdata()):
        pr, pg, pb = preview_pixel
        tr, tg, tb = target_pixel
        p_lum = luminance01(pr, pg, pb)
        t_lum = luminance01(tr, tg, tb)
        luminance_error += abs(p_lum - t_lum)
        color_error += (abs(pr - tr) + abs(pg - tg) + abs(pb - tb)) / (255.0 * 3.0)
        p_mask = effect_foreground_score(pr, pg, pb) > 0.26
        t_mask = effect_foreground_score(tr, tg, tb) > 0.26
        if p_mask and t_mask:
            silhouette_intersection += 1
        if p_mask or t_mask:
            silhouette_union += 1
    luminance = 1.0 - luminance_error / total
    color = 1.0 - color_error / total
    silhouette = silhouette_intersection / max(1, silhouette_union)
    overall = luminance * 0.32 + color * 0.28 + silhouette * 0.4
    return {
        "overall": round(clamp01(overall), 3),
        "luminance": round(clamp01(luminance), 3),
        "color": round(clamp01(color), 3),
        "silhouette": round(clamp01(silhouette), 3),
    }


def alpha_coverage_metrics(image: Image.Image) -> dict[str, Any]:
    alpha = image.convert("RGBA").getchannel("A")
    values = list(alpha.getdata())
    total = max(1, len(values))
    coverage = sum(1 for value in values if value > 8) / total
    strong = sum(1 for value in values if value > 160) / total
    bbox = alpha.getbbox()
    bbox_coverage = 0.0
    if bbox:
        left, top, right, bottom = bbox
        bbox_coverage = ((right - left) * (bottom - top)) / total
    opaque_card_risk = coverage > 0.68 or (bbox_coverage > 0.82 and strong > 0.42)
    return {
        "coverage": round(coverage, 3),
        "strong_coverage": round(strong, 3),
        "bbox_coverage": round(bbox_coverage, 3),
        "opaque_card_risk": opaque_card_risk,
    }


def effect_foreground_score(r: int, g: int, b: int) -> float:
    lum = luminance01(r, g, b)
    warm = warm_score01(r, g, b)
    hot = smoothstep01(0.55, 0.95, lum)
    smoke = (1.0 - lum) * smoothstep01(0.015, 0.12, abs(r - b) / 255.0) * 0.28
    return clamp01(max(warm * 0.82, hot, smoke))


def luminance01(r: int, g: int, b: int) -> float:
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def warm_score01(r: int, g: int, b: int) -> float:
    red_bias = clamp01((r - max(b, 24)) / 180.0)
    green_support = clamp01((g - b * 0.35) / 210.0)
    saturation = clamp01((max(r, g, b) - min(r, g, b)) / 160.0)
    return clamp01(red_bias * 0.55 + green_support * 0.3 + saturation * 0.15)


def boost_fire_color(r: int, g: int, b: int, amount: float) -> tuple[int, int, int]:
    return (
        int(min(255, r * amount + 18)),
        int(min(255, g * (amount * 0.98) + 10)),
        int(min(255, b * 0.82 + 4)),
    )


def smoothstep01(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 0.0
    x = clamp01((value - edge0) / (edge1 - edge0))
    return x * x * (3.0 - 2.0 * x)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


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


def read_similarity_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_manual_pass_outputs(package_path: Path) -> list[dict[str, str]]:
    passes_root = package_path / "passes"
    if not passes_root.exists():
        return []
    outputs: list[dict[str, str]] = []
    suffixes = IMAGE_SUFFIXES | ANIMATED_SUFFIXES
    for path in sorted(passes_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        outputs.append(
            {
                "path": str(path),
                "source": "manual_package_pass",
                "filename": path.name,
                "relative_path": str(path.relative_to(package_path)),
                "confidence": "high",
            }
        )
    return outputs


def classify_manual_outputs_for_pass(pass_name: str, manual_outputs: list[dict[str, str]]) -> list[dict[str, str]]:
    aliases = manual_aliases_for_pass(pass_name)
    matched = []
    for output in manual_outputs:
        searchable = f"{output.get('filename', '')} {output.get('relative_path', '')}".lower()
        if pass_name.lower() in searchable or any(alias in searchable for alias in aliases):
            matched.append({**output, "matched_by": "manual_pass_name"})
    return matched


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


def manual_aliases_for_pass(pass_name: str) -> list[str]:
    aliases = {
        "beauty_flipbook": ["beauty", "color", "emissive", "flipbook"],
        "alpha_mask": ["alpha", "mask", "matte", "opacity"],
        "motion_vectors": ["motion", "vector", "velocity", "mv"],
        "distortion_flow": ["distortion", "distort", "flow", "heat_haze", "haze"],
        "normal_or_lighting": ["normal", "lighting", "depth", "lit"],
        "core_flame_flipbook": ["core", "pillar", "fire_pillar", "flame_core"],
        "smoke_heat_flipbook": ["smoke", "heat", "wisp", "haze"],
        "ground_ring_mask": ["ground", "ring", "rune", "circle"],
        "flame_slash_flipbook": ["slash", "side_flame", "tongue", "flame_tongue"],
        "impact_flash_mask": ["impact", "flash", "burst", "hit"],
        "ember_sprite_set": ["ember", "spark", "sparks"],
        "reference_motion_overlay": ["reference", "overlay", "target"],
    }
    return aliases.get(pass_name, [pass_name.lower()])


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
