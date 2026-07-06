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
METADATA_SUFFIXES = {".json"}
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
    ai_companion_candidates = derive_ai_companion_candidates(package_path.name, pass_specs, ai_outputs, output_root)
    derived_candidates = derive_bootstrap_candidates(package_path.name, pass_specs, reference_candidates, reference_media, output_root, spec.to_dict())

    entries = [
        asset_pass_entry(pass_spec, manual_outputs, reference_candidates, ai_companion_candidates, derived_candidates, ai_outputs, package_path.name, output_root)
        for pass_spec in pass_specs
    ]
    required_entries = [entry for entry in entries if entry.get("required")]
    ready_required = [entry for entry in required_entries if entry.get("status") == "ready"]
    missing_required = [entry for entry in required_entries if entry.get("status") != "ready"]
    production_contract = production_contract_summary(entries)

    manifest = {
        "schema_version": 1,
        "package": package_path.name,
        "package_path": str(package_path),
        "effect_type": spec.effect_type,
        "motion": spec.motion,
        "reference_understanding": (spec.visual_profile or {}).get("reference_understanding", {}),
        "quality_tier": (plan.quality_target or {}).get("tier") if plan else None,
        "summary": {
            "total_passes": len(entries),
            "required_passes": len(required_entries),
            "ready_required_passes": len(ready_required),
            "missing_required_passes": len(missing_required),
            "unreal_ready": not missing_required,
            "production_contract_ready": production_contract["status"] == "pass",
        },
        "production_contract": production_contract,
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
    depth_path = ((passes_by_name.get("depth_or_thickness") or {}).get("selected_asset") or {}).get("path")
    normal_path = ((passes_by_name.get("normal_or_lighting") or {}).get("selected_asset") or {}).get("path")
    layer_mask_path = ((passes_by_name.get("layer_mask_pack") or {}).get("selected_asset") or {}).get("path")
    for emitter in plan.get("emitters") or []:
        pass_name = asset_pass_for_emitter(patched.get("effect_type"), emitter)
        if not pass_name:
            continue
        asset_pass = passes_by_name.get(pass_name)
        selected = (asset_pass or {}).get("selected_asset")
        if not selected:
            continue
        selected_path = sprite_path_for_emitter(pass_name, selected, emitter)
        if not selected_path or not Path(selected_path).exists():
            continue
        emitter["sprite_source"] = selected_path
        emitter.setdefault("notes", []).append(f"Using asset pass '{pass_name}' from {selected.get('source')}.")
        material = emitter.setdefault("unreal_settings", {}).setdefault("material", {})
        atlas = asset_pass.get("asset_metadata", {}).get("atlas")
        if atlas and selected_path == selected.get("path"):
            material["flipbook"] = atlas
            material["preview_playback"] = "material_flipbook"
        elif "flipbook" in material:
            material.pop("flipbook", None)
            material.pop("preview_playback", None)
        if should_apply_shared_alpha(pass_name, selected_path, alpha_path):
            material["alpha_source"] = alpha_path
            material["alpha_usage"] = "multiply_texture_alpha"
        if should_apply_distortion(emitter, distortion_path):
            material["distortion_source"] = distortion_path
            material["distortion_strength"] = material.get("distortion_strength", 0.075)
        if should_apply_volume_material_passes(emitter):
            if depth_path and Path(depth_path).exists():
                material["depth_thickness_source"] = depth_path
                material["depth_thickness_usage"] = "opacity_and_soft_volume_modulation"
            if normal_path and Path(normal_path).exists():
                material["normal_lighting_source"] = normal_path
                material["normal_lighting_usage"] = "emissive_lighting_modulation"
            if layer_mask_path and Path(layer_mask_path).exists():
                material["layer_mask_source"] = layer_mask_path
                material["layer_mask_usage"] = "core_edge_smoke_ground_channel_masks"
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
        plan["playback"] = {
            "mode": "material_flipbook",
            "duration_seconds": (spec.get("timing") or {}).get("duration_seconds", spec.get("duration_seconds", 1.25)),
            "looping": bool((spec.get("timing") or {}).get("looping", spec.get("looping", True))),
            "preview_instruction": "Open the preview Blueprint, enable Realtime, or press Play/Simulate to see flipbook-driven layer playback.",
        }
        for emitter in emitters:
            apply_fire_production_preview(emitter)
    elif effect_type == "electric_arc":
        plan["preview_mode"] = "production_layers"
        for emitter in emitters:
            apply_electric_production_preview(emitter)


def apply_fire_production_preview(emitter: dict[str, Any]) -> None:
    role = emitter.get("role")
    name = str(emitter.get("name") or "")
    settings = emitter.setdefault("unreal_settings", {})
    material = settings.setdefault("material", {})
    timeline = settings.setdefault("timeline", {})
    preview = settings.setdefault("preview", {})
    card = preview.setdefault("card", {})
    mesh = preview.setdefault("mesh", {})
    niagara = preview.setdefault("niagara", {})
    firestorm_markers = ("firestorm", "fire_ice", "magic_tornado", "tornado_vortex")
    marker_text = " ".join(
        str(value or "")
        for value in (
            name,
            emitter.get("motion"),
            emitter.get("material_style"),
            emitter.get("sprite_shape"),
            material.get("style"),
        )
    ).lower()
    is_firestorm = (
        any(marker in marker_text for marker in firestorm_markers)
        or name == "back_spiral_flame_wall"
    )
    is_short_burst = role == "fire_pillar" and float(emitter.get("end_size") or 0.0) <= 170.0

    if role == "reference_motion":
        material["opacity"] = min(float(material.get("opacity", 0.72)), 0.16)
        material["emissive_strength"] = min(float(material.get("emissive_strength", 5.5)), 1.8)
        card["enabled"] = False
        niagara["enabled"] = False
        emitter.setdefault("notes", []).append("Production preview hides the reference flipbook so the editable layers drive the look.")
        return

    if role == "reference_matched_composite":
        timeline.update({"delay": 0.0, "duration": 0.9, "opacity": [0.0, 0.24, 0.2, 0.0], "scale": [1.0, 1.0, 1.0, 1.0], "rotation_speed": 0.0})
        material["opacity"] = min(float(material.get("opacity", 0.24)), 0.24)
        material["emissive_strength"] = min(float(material.get("emissive_strength", 1.4)), 1.4)
        material["blend_mode"] = "additive"
        card.update({"enabled": False, "location": [0, -42, 96], "rotation": [90, 0, 0], "scale": [1.2, 1.2, 1]})
        niagara["enabled"] = False
        emitter.setdefault("notes", []).append("Production preview hides the reference-matched anchor so it cannot be mistaken for the authored effect.")
    elif role == "fire_pillar":
        if is_firestorm:
            timeline.update({"delay": 0.06, "duration": 0.92, "opacity": [0.0, 0.5, 0.4, 0.0], "scale": [0.62, 1.0, 1.04, 0.82], "rotation_speed": 32.0})
            material["opacity"] = 0.5
            material["emissive_strength"] = 9.4
            card.update({"enabled": True, "location": [0, 0, 76], "rotation": [87, 0, 0], "scale": [0.72, 1.22, 1.0]})
            card["volume_mode"] = "tapered_fire_tornado_funnel"
            card["instances"] = [
                {"location": [0, 0, 62], "rotation": [87, 54, -10], "scale": [0.56, 1.0, 1.0]},
                {"location": [0, 0, 90], "rotation": [87, 128, 12], "scale": [0.78, 1.18, 1.0]},
            ]
            mesh.update(
                {
                    "enabled": True,
                    "mesh": "cylinder",
                    "instances": [
                        {"mesh": "cylinder", "location": [0, 0, 26], "rotation": [0, 0, 0], "scale": [0.26, 0.26, 0.28]},
                        {"mesh": "cylinder", "location": [0, 0, 52], "rotation": [0, 0, 28], "scale": [0.38, 0.38, 0.46]},
                        {"mesh": "cylinder", "location": [0, 0, 80], "rotation": [0, 0, 58], "scale": [0.58, 0.58, 0.52]},
                        {"mesh": "cylinder", "location": [0, 0, 108], "rotation": [0, 0, 92], "scale": [0.76, 0.76, 0.28]},
                        {"mesh": "sphere", "location": [0, 0, 112], "rotation": [0, 0, 0], "scale": [0.92, 0.92, 0.2]},
                        {"mesh": "torus", "location": [0, 0, 96], "rotation": [0, 0, 12], "scale": [0.78, 0.78, 0.08]},
                        {"mesh": "torus", "location": [0, 0, 108], "rotation": [0, 0, 36], "scale": [0.98, 0.98, 0.09]},
                        {"mesh": "torus", "location": [0, 0, 120], "rotation": [0, 0, 68], "scale": [1.18, 1.18, 0.1]},
                    ],
                }
            )
            emitter.setdefault("notes", []).append("Firestorm core uses a tapered 3D funnel: narrow ground contact, widening upper vortex, and a smoky crown.")
        else:
            if is_short_burst:
                timeline.update({"delay": 0.02, "duration": 0.46, "opacity": [0.0, 0.9, 0.48, 0.0], "scale": [0.56, 1.0, 0.78, 0.28], "rotation_speed": 6.0})
                niagara_transform = {"enabled": True, "location": [0, 0, 48], "rotation": [0, 0, 0], "scale": [0.42, 0.42, 0.42]}
            else:
                timeline.update({"delay": 0.03, "duration": 0.78, "opacity": [0.0, 0.86, 0.62, 0.0], "scale": [0.42, 1.08, 1.0, 0.58], "rotation_speed": 10.0})
                niagara_transform = {"enabled": True, "location": [0, 0, 78], "rotation": [0, 0, 0], "scale": [0.62, 0.62, 0.62]}
            material["opacity"] = max(float(material.get("opacity", 0.54)), 0.72)
            material["emissive_strength"] = max(float(material.get("emissive_strength", 14.0)), 15.0)
            card["enabled"] = False
            card.pop("instances", None)
            mesh["enabled"] = False
            niagara.update(niagara_transform)
            emitter.setdefault("notes", []).append("Regular fire core is rendered through Niagara, not Blueprint static mesh cards.")
            if is_short_burst:
                emitter.setdefault("notes", []).append("Reference contract classifies this as a short gameplay burst, so the core stays compact instead of becoming a tall fire column.")
        niagara["enabled"] = bool(niagara.get("enabled")) if not is_firestorm else False
    elif role == "flame_slashes":
        if not is_firestorm:
            timeline.update({"delay": 0.08, "duration": 0.62, "opacity": [0.0, 0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0, 1.0], "rotation_speed": 0.0})
            material["opacity"] = 0.0
            material["emissive_strength"] = 0.0
            card["enabled"] = False
            card.pop("instances", None)
            mesh["enabled"] = False
            niagara["enabled"] = False
            emitter.setdefault("notes", []).append("Regular fire hides standalone flame slash cards; large side flame textures read as floating pasted images. Rebuild this layer as ribbons or small SubUV particles.")
            return
        if "rear_flame" in name:
            timeline.update({"delay": 0.16, "duration": 0.68, "opacity": [0.0, 0.48, 0.34, 0.0], "scale": [0.46, 1.04, 1.16, 0.7], "rotation_speed": 18.0})
            material["opacity"] = 0.46
            material["emissive_strength"] = 7.4
            card.update({"enabled": True, "location": [0, 12, 64], "rotation": [87, 0, 22], "scale": [0.88, 0.82, 1]})
            card["volume_mode"] = "tutorial_rear_outer_tongues"
            card["instances"] = [
                {"location": [-8, 12, 52], "rotation": [87, 48, 24], "scale": [0.58, 0.62, 1.0]},
                {"location": [9, 11, 78], "rotation": [87, -42, -18], "scale": [0.72, 0.7, 1.0]},
            ]
            mesh.update(
                {
                    "enabled": True,
                    "mesh": "sphere",
                    "instances": [
                        {"mesh": "sphere", "location": [-8, 13, 54], "rotation": [0, 0, 0], "scale": [0.42, 0.18, 0.16]},
                        {"mesh": "sphere", "location": [9, 12, 80], "rotation": [0, 0, 0], "scale": [0.48, 0.18, 0.16]},
                    ],
                }
            )
        elif "back_spiral" in name:
            timeline.update({"delay": 0.16, "duration": 0.78, "opacity": [0.0, 0.52, 0.42, 0.0], "scale": [0.48, 1.18, 1.24, 0.72], "rotation_speed": 22.0})
            material["opacity"] = max(float(material.get("opacity", 0.48)), 0.5)
            material["emissive_strength"] = max(float(material.get("emissive_strength", 7.5)), 9.0)
            card.update({"enabled": True, "location": [0, 7, 76], "rotation": [88, 0, 18], "scale": [1.55, 1.1, 1]})
            if is_firestorm or "back_spiral" in name:
                timeline.update({"delay": 0.1, "duration": 0.94, "opacity": [0.0, 0.38, 0.32, 0.0], "scale": [0.56, 1.0, 1.05, 0.82], "rotation_speed": 38.0})
                material["opacity"] = 0.42
                material["emissive_strength"] = 7.2
                card.update({"enabled": True, "location": [0, 10, 78], "rotation": [86, 0, 26], "scale": [0.95, 1.08, 1]})
                card["volume_mode"] = "rear_spiral_shell"
                card["instances"] = [
                    {"location": [-7, 12, 58], "rotation": [86, 42, 34], "scale": [0.72, 0.84, 1.0]},
                    {"location": [10, 12, 94], "rotation": [86, -46, -20], "scale": [1.02, 0.96, 1.0]},
                ]
                mesh.update(
                    {
                        "enabled": True,
                        "mesh": "cylinder",
                        "instances": [
                            {"mesh": "cylinder", "location": [-8, 12, 58], "rotation": [0, 0, 42], "scale": [0.52, 0.12, 0.34]},
                            {"mesh": "cylinder", "location": [10, 11, 92], "rotation": [0, 0, -34], "scale": [0.82, 0.14, 0.3]},
                            {"mesh": "sphere", "location": [0, 16, 108], "rotation": [0, 0, 0], "scale": [0.72, 0.24, 0.2]},
                        ],
                    }
                )
        else:
            timeline.update({"delay": 0.08, "duration": 0.62, "opacity": [0.0, 0.76, 0.52, 0.0], "scale": [0.44, 1.08, 1.16, 0.7], "rotation_speed": -18.0})
            material["opacity"] = max(float(material.get("opacity", 0.5)), 0.56)
            material["emissive_strength"] = max(float(material.get("emissive_strength", 8.5)), 9.2)
            card.update({"enabled": True, "location": [0, -9, 52], "rotation": [88, 0, -18], "scale": [0.96, 0.62, 1]})
            card["volume_mode"] = "tutorial_front_outer_tongues"
            card["instances"] = [
                {"location": [8, -10, 48], "rotation": [87, 44, -28], "scale": [0.62, 0.48, 1.0]},
                {"location": [-9, -8, 62], "rotation": [87, -48, 22], "scale": [0.58, 0.44, 1.0]},
            ]
            if is_firestorm:
                timeline.update({"delay": 0.08, "duration": 0.92, "opacity": [0.0, 0.42, 0.34, 0.0], "scale": [0.58, 1.0, 1.04, 0.8], "rotation_speed": -42.0})
                material["opacity"] = 0.46
                material["emissive_strength"] = 7.8
                card.update({"enabled": True, "location": [0, -12, 66], "rotation": [86, 0, -28], "scale": [0.9, 1.02, 1]})
                card["volume_mode"] = "front_spiral_shell"
                card["instances"] = [
                    {"location": [7, -13, 46], "rotation": [86, 44, -34], "scale": [0.66, 0.8, 1.0]},
                    {"location": [-11, -10, 84], "rotation": [86, -52, 20], "scale": [0.96, 0.94, 1.0]},
                ]
                mesh.update(
                    {
                        "enabled": True,
                        "mesh": "cylinder",
                        "instances": [
                            {"mesh": "cylinder", "location": [8, -13, 46], "rotation": [0, 0, -44], "scale": [0.48, 0.12, 0.3]},
                            {"mesh": "cylinder", "location": [-11, -10, 84], "rotation": [0, 0, 36], "scale": [0.78, 0.13, 0.3]},
                            {"mesh": "sphere", "location": [0, -18, 102], "rotation": [0, 0, 0], "scale": [0.72, 0.24, 0.2]},
                        ],
                    }
                )
                emitter.setdefault("notes", []).append("Firestorm side flames use offset spiral shell cards instead of a single flat ribbon.")
        niagara["enabled"] = False
    elif role == "ground_energy_ring":
        if is_firestorm:
            timeline.update({"delay": 0.02, "duration": 0.9, "opacity": [0.0, 0.28, 0.22, 0.0], "scale": [0.62, 0.86, 0.82, 0.9], "rotation_speed": 5.0})
            material["opacity"] = 0.32
            material["emissive_strength"] = 3.4
            material["blend_mode"] = "additive"
            card.update({"enabled": True, "location": [0, 0, 1.0], "rotation": [0, 0, 0], "scale": [1.28, 1.28, 1]})
            mesh.update(
                {
                    "enabled": True,
                        "mesh": "cylinder",
                        "instances": [
                            {"mesh": "cylinder", "location": [0, 0, 1.5], "rotation": [0, 0, 0], "scale": [0.62, 0.62, 0.016]},
                            {"mesh": "cylinder", "location": [0, 0, 5], "rotation": [0, 0, 28], "scale": [0.48, 0.48, 0.022]},
                            {"mesh": "sphere", "location": [0, 0, 10], "rotation": [0, 0, 0], "scale": [0.2, 0.2, 0.06]},
                            {"mesh": "torus", "location": [0, 0, 2.5], "rotation": [0, 0, 10], "scale": [0.72, 0.72, 0.035]},
                            {"mesh": "torus", "location": [0, 0, 6.5], "rotation": [0, 0, 42], "scale": [0.54, 0.54, 0.035]},
                        ],
                    }
                )
        else:
            is_small_contact = "contact" in name
            if is_small_contact:
                timeline.update({"delay": 0.01, "duration": 0.38, "opacity": [0.0, 0.58, 0.28, 0.0], "scale": [0.48, 0.86, 0.72, 0.5], "rotation_speed": 4.0})
                material["opacity"] = max(float(material.get("opacity", 0.5)), 0.56)
                material["emissive_strength"] = material.get("emissive_strength", 7.5)
                card.update({"enabled": True, "location": [0, 0, 2], "rotation": [0, 0, 0], "scale": [0.92, 0.92, 1]})
            else:
                timeline.update({"delay": 0.02, "duration": 0.72, "opacity": [0.0, 0.9, 0.72, 0.0], "scale": [0.55, 1.12, 1.04, 1.28], "rotation_speed": 18.0})
                material["opacity"] = max(float(material.get("opacity", 0.72)), 0.76)
                material["emissive_strength"] = material.get("emissive_strength", 8.0)
                card.update({"enabled": True, "location": [0, 0, 2], "rotation": [0, 0, 0], "scale": [2.15, 2.15, 1]})
            mesh["enabled"] = False
        niagara["enabled"] = False
    elif role == "impact_core":
        short_impact = float(emitter.get("end_size") or 0.0) <= 130.0
        timeline.update(
            {"delay": 0.0, "duration": 0.18, "opacity": [0.0, 0.62, 0.18, 0.0], "scale": [0.34, 0.72, 0.42, 0.0]}
            if short_impact
            else {"delay": 0.0, "duration": 0.24, "opacity": [0.0, 0.58, 0.2, 0.0], "scale": [0.32, 0.82, 0.58, 0.0]}
        )
        material["opacity"] = 0.58 if is_firestorm else max(float(material.get("opacity", 0.8)), 0.86)
        material["emissive_strength"] = 9.5 if is_firestorm else max(float(material.get("emissive_strength", 22.0)), 24.0)
        card.update({"enabled": True, "location": [0, -1, 16] if short_impact else [0, -1, 22], "rotation": [88, 0, 0], "scale": [0.38, 0.28, 1] if short_impact else [0.54, 0.38, 1]})
        if is_firestorm:
            card["instances"] = [
                {"location": [0, 0, 22], "rotation": [88, 90, 0], "scale": [0.42, 0.28, 1.0]},
            ]
            mesh.update(
                {
                    "enabled": True,
                    "mesh": "sphere",
                    "instances": [
                        {"mesh": "sphere", "location": [0, 0, 22], "rotation": [0, 0, 0], "scale": [0.32, 0.32, 0.18]},
                    ],
                }
            )
        niagara["enabled"] = False
    elif role == "atmospheric_wisp":
        if is_firestorm:
            timeline.update({"delay": 0.18, "duration": 0.98, "opacity": [0.0, 0.12, 0.09, 0.0], "scale": [0.64, 1.02, 1.18, 1.34], "rotation_speed": 14.0})
            material["opacity"] = 0.1
            material["emissive_strength"] = 0.16
            material["blend_mode"] = "translucent"
            card.update({"enabled": True, "location": [-4, 8, 108], "rotation": [86, 0, 14], "scale": [1.36, 0.72, 1.0]})
            mesh.update(
                {
                    "enabled": True,
                    "mesh": "sphere",
                    "instances": [
                        {"mesh": "sphere", "location": [-8, 10, 106], "rotation": [0, 0, 0], "scale": [1.08, 0.66, 0.2]},
                        {"mesh": "sphere", "location": [10, 6, 118], "rotation": [0, 0, 0], "scale": [0.92, 0.56, 0.18]},
                        {"mesh": "cylinder", "location": [0, 7, 100], "rotation": [0, 0, 16], "scale": [1.12, 0.68, 0.09]},
                    ],
                }
            )
            niagara["enabled"] = False
            emitter.setdefault("notes", []).append("Firestorm smoke crown is visible at very low opacity to break the bright spike silhouette without creating black cards.")
            return
        if "heat_distortion" in name:
            short_wisp = float(emitter.get("end_size") or 0.0) <= 130.0
            timeline.update(
                {"delay": 0.05, "duration": 0.5, "opacity": [0.0, 0.06, 0.035, 0.0], "scale": [0.48, 0.88, 0.9, 0.72], "rotation_speed": 5.0}
                if short_wisp
                else {"delay": 0.08, "duration": 0.82, "opacity": [0.0, 0.08, 0.06, 0.0], "scale": [0.52, 1.0, 1.12, 1.28], "rotation_speed": 9.0}
            )
            material["opacity"] = 0.055
            material["emissive_strength"] = 0.04
            material["distortion_strength"] = max(float(material.get("distortion_strength", 0.075)), 0.13)
            material["blend_mode"] = "translucent"
            card["enabled"] = False
            card.pop("instances", None)
            mesh["enabled"] = False
            niagara.update({"enabled": True, "location": [0, 2, 54] if short_wisp else [0, 2, 76], "rotation": [0, 0, 0], "scale": [0.38, 0.38, 0.38] if short_wisp else [0.52, 0.52, 0.52]})
            emitter.setdefault("notes", []).append("Tutorial heat haze is a low-opacity Niagara distortion carrier, not a visible smoke card.")
            return
        short_smoke = float(emitter.get("end_size") or 0.0) <= 130.0
        timeline.update(
            {"delay": 0.1, "duration": 0.58, "opacity": [0.0, 0.12, 0.06, 0.0], "scale": [0.58, 0.9, 0.84, 0.52], "rotation_speed": 3.0}
            if short_smoke
            else {"delay": 0.18, "duration": 1.05, "opacity": [0.0, 0.2, 0.14, 0.0], "scale": [0.62, 1.0, 1.22, 1.46], "rotation_speed": 5.0}
        )
        material["opacity"] = min(float(material.get("opacity", 0.2)), 0.085)
        material["emissive_strength"] = min(float(material.get("emissive_strength", 0.35)), 0.18)
        material["blend_mode"] = "translucent"
        card["enabled"] = False
        card.pop("instances", None)
        mesh["enabled"] = False
        niagara.update({"enabled": True, "location": [-2, 4, 48] if short_smoke else [-2, 4, 76], "rotation": [0, 0, 0], "scale": [0.36, 0.36, 0.36] if short_smoke else [0.54, 0.54, 0.54]})
        emitter.setdefault("notes", []).append("Smoke preview renders through Niagara so it does not expose a large static atlas/card.")
    elif role == "detail_particles":
        if is_firestorm:
            timeline.update({"delay": 0.12, "duration": 0.45, "opacity": [0.0, 0.0, 0.0, 0.0], "scale": [1.0, 1.0, 1.0, 1.0], "rotation_speed": 0.0})
            material["opacity"] = 0.0
            material["emissive_strength"] = 0.0
            card["enabled"] = False
            niagara["enabled"] = False
            emitter.setdefault("notes", []).append("Firestorm preview bakes spark accents into the authored flame/ground atlases and hides the unstable standalone ember component.")
            return
        short_sparks = float(emitter.get("spawn_rate") or 0.0) <= 10.0
        timeline.update(
            {"delay": 0.1, "duration": 0.24, "opacity": [0.0, 0.45, 0.18, 0.0], "scale": [0.42, 0.58, 0.28, 0.08], "rotation_speed": 110.0}
            if short_sparks
            else {"delay": 0.12, "duration": 0.32, "opacity": [0.0, 0.55, 0.32, 0.0], "scale": [0.55, 0.72, 0.44, 0.15], "rotation_speed": 130.0}
        )
        material["opacity"] = min(float(material.get("opacity", 0.78)), 0.46)
        material["emissive_strength"] = min(float(material.get("emissive_strength", 10.0)), 6.0)
        card["enabled"] = False
        niagara.update({"enabled": True, "location": [0, 0, 48] if short_sparks else [0, 0, 72], "rotation": [0, 0, 0], "scale": [0.16, 0.16, 0.16] if short_sparks else [0.22, 0.22, 0.22]})


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


def sprite_path_for_emitter(pass_name: str, selected: dict[str, str], emitter: dict[str, Any]) -> str | None:
    role = emitter.get("role")
    preview_path = selected.get("preview_frame_path")
    if playable_material_flipbook(pass_name, selected, emitter):
        return selected.get("path")
    if preview_path and role in {"fire_pillar", "flame_slashes", "ground_energy_ring", "impact_core", "atmospheric_wisp", "detail_particles"}:
        return preview_path
    return selected.get("path")


def playable_material_flipbook(pass_name: str, selected: dict[str, str], emitter: dict[str, Any]) -> bool:
    if not selected.get("atlas_columns") or not selected.get("atlas_rows"):
        return False
    if playable_firestorm_atlas(selected, emitter):
        return True
    if emitter.get("role") == "detail_particles":
        return False
    return pass_name in {
        "core_flame_flipbook",
        "flame_slash_flipbook",
        "ground_ring_mask",
        "impact_flash_mask",
        "smoke_heat_flipbook",
    }


def playable_firestorm_atlas(selected: dict[str, str], emitter: dict[str, Any]) -> bool:
    if not selected.get("atlas_columns") or not selected.get("atlas_rows"):
        return False
    text = " ".join(
        str(value or "")
        for value in (
            emitter.get("name"),
            emitter.get("motion"),
            emitter.get("material_style"),
            emitter.get("sprite_shape"),
            selected.get("role"),
            selected.get("source"),
            selected.get("path"),
        )
    ).lower()
    return "firestorm" in text and emitter.get("role") in {"fire_pillar", "flame_slashes", "ground_energy_ring", "impact_core", "detail_particles"}


def asset_pass_entry(
    pass_spec: dict[str, Any],
    manual_outputs: list[dict[str, str]],
    reference_candidates: dict[str, list[dict[str, str]]],
    ai_companion_candidates: dict[str, list[dict[str, str]]],
    derived_candidates: dict[str, list[dict[str, str]]],
    ai_outputs: list[dict[str, str]],
    package_name: str,
    output_root: Path,
) -> dict[str, Any]:
    name = str(pass_spec.get("name") or "unknown_pass")
    candidates = [
        *classify_manual_outputs_for_pass(name, manual_outputs),
        *classify_ai_outputs_for_pass(name, ai_outputs),
        *ai_companion_candidates.get(name, []),
        *reference_candidates.get(name, []),
        *derived_candidates.get(name, []),
    ]
    selected = prepare_runtime_asset(candidates[0], name, package_name, output_root) if candidates else None
    prompt = prompt_for_asset_pass(pass_spec)
    budget = texture_budget_for_pass(name)
    metadata = asset_metadata_for_selected_asset(selected, name)
    validation = validate_asset_pass_candidate(name, selected, metadata, pass_spec)
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
        "asset_metadata": metadata,
        "validation": validation,
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
    runtime_dir = output_root / package_name / "runtime"
    result = dict(selected)
    try:
        with Image.open(path) as image:
            width, height = image.size
            atlas = atlas_metadata_for_dimensions(pass_name, selected, width, height)
            if atlas:
                result.update(
                    {
                        "atlas_columns": str(atlas["columns"]),
                        "atlas_rows": str(atlas["rows"]),
                        "atlas_frame_count": str(atlas["frame_count"]),
                        "atlas_fps": str(atlas["fps"]),
                    }
                )
            if max(width, height) > max_edge:
                resized = image.convert("RGBA")
                ratio = max_edge / max(width, height)
                target_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
                resized = resized.resize(target_size, Image.Resampling.LANCZOS)
                runtime_dir.mkdir(parents=True, exist_ok=True)
                runtime_path = runtime_dir / f"{package_name}_{safe_file_token(pass_name)}_{safe_file_token(path.stem)}_rt.png"
                resized.save(runtime_path)
                result.update(
                    {
                        "path": str(runtime_path),
                        "original_path": str(path),
                        "runtime_resized": "true",
                        "runtime_max_edge": str(max_edge),
                    }
                )
            else:
                result.update({"runtime_resized": "false", "runtime_max_edge": str(max_edge)})
    except Exception:
        return result

    preview_path = create_preview_frame_for_asset(result, pass_name, package_name, output_root)
    if preview_path:
        result["preview_frame_path"] = str(preview_path)
    return result


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
        "depth_or_thickness": {"max_import_edge": 512, "max_preview_scale": 1.4, "max_card_area": 2.0, "usage": "depth_thickness_data"},
        "layer_mask_pack": {"max_import_edge": 1024, "max_preview_scale": 1.6, "max_card_area": 2.6, "usage": "packed_layer_masks"},
        "sdf_or_vector_field": {"max_import_edge": 512, "max_preview_scale": 1.2, "max_card_area": 1.8, "usage": "field_data"},
        "renderer_layout_metadata": {"max_import_edge": 0, "max_preview_scale": 0.0, "max_card_area": 0.0, "usage": "metadata"},
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
        "channels": channel_statistics(path),
    }


def channel_statistics(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            rgba.thumbnail((128, 128), Image.Resampling.BILINEAR)
            pixels = list(rgba.getdata())
    except Exception:
        return {}
    if not pixels:
        return {}
    count = len(pixels)
    alpha_values = [pixel[3] for pixel in pixels]
    opaque = sum(1 for value in alpha_values if value >= 250) / count
    transparent = sum(1 for value in alpha_values if value <= 5) / count
    alpha_coverage = sum(1 for value in alpha_values if value > 8) / count
    luminance_values = [(0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0 for r, g, b, _ in pixels]
    warm_values = [warm_score01(r, g, b) for r, g, b, _ in pixels]
    channel_means = [
        sum(pixel[index] for pixel in pixels) / (255.0 * count)
        for index in range(4)
    ]
    return {
        "alpha_coverage": round(alpha_coverage, 4),
        "opaque_ratio": round(opaque, 4),
        "transparent_ratio": round(transparent, 4),
        "mean_luminance": round(sum(luminance_values) / count, 4),
        "mean_warmth": round(sum(warm_values) / count, 4),
        "mean_rgba": [round(value, 4) for value in channel_means],
    }


def validate_asset_pass_candidate(
    pass_name: str,
    selected: dict[str, str] | None,
    metadata: dict[str, Any],
    pass_spec: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not selected:
        issue = {"type": "missing_asset", "message": "No candidate asset was selected for this pass."}
        (issues if pass_spec.get("required") else warnings).append(issue)
        return validation_result(issues, warnings)

    path = Path(str(selected.get("path") or ""))
    if pass_name == "renderer_layout_metadata":
        validate_renderer_metadata(path, issues, warnings)
        return validation_result(issues, warnings)

    if not path.exists():
        issues.append({"type": "path_missing", "path": str(path)})
        return validation_result(issues, warnings)
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        issues.append({"type": "unsupported_image_format", "path": str(path), "suffix": path.suffix})
        return validation_result(issues, warnings)
    if not metadata:
        issues.append({"type": "image_metadata_unreadable", "path": str(path)})
        return validation_result(issues, warnings)

    width = int(metadata.get("width") or 0)
    height = int(metadata.get("height") or 0)
    if width < 16 or height < 16:
        issues.append({"type": "image_too_small", "size": [width, height]})

    channels = metadata.get("channels") or {}
    alpha_coverage = float(channels.get("alpha_coverage") or 0.0)
    opaque_ratio = float(channels.get("opaque_ratio") or 0.0)
    transparent_ratio = float(channels.get("transparent_ratio") or 0.0)
    mean_luminance = float(channels.get("mean_luminance") or 0.0)
    mean_warmth = float(channels.get("mean_warmth") or 0.0)
    source = str(selected.get("source") or "")

    if pass_name in passes_requiring_atlas() and not metadata.get("atlas"):
        warnings.append({"type": "missing_atlas_metadata", "message": "Flipbook pass has no atlas columns/rows/fps metadata."})
    if pass_name in passes_requiring_alpha() and opaque_ratio > 0.94 and transparent_ratio < 0.02:
        issues.append({"type": "opaque_card_risk", "opaque_ratio": opaque_ratio, "transparent_ratio": transparent_ratio})
    if pass_name in {"alpha_mask", "impact_flash_mask", "ground_ring_mask"} and mean_warmth > 0.2:
        warnings.append({"type": "mask_pass_contains_beauty_color", "mean_warmth": mean_warmth})
    if pass_name in data_pass_names() and mean_luminance > 0.65 and mean_warmth > 0.25:
        warnings.append({"type": "data_pass_looks_like_beauty", "mean_luminance": mean_luminance, "mean_warmth": mean_warmth})
    if pass_name == "smoke_heat_flipbook" and alpha_coverage > 0.9 and opaque_ratio > 0.75:
        warnings.append({"type": "smoke_pass_may_render_as_sheet", "alpha_coverage": alpha_coverage, "opaque_ratio": opaque_ratio})
    if source in {"derived_reference_bootstrap", "reference_layer_extraction", "procedural_layer_synthesis", "reference_matched_composite"}:
        warnings.append({"type": "bootstrap_or_reference_source", "source": source})
    if source == "ai_output_derivative":
        warnings.append({"type": "ai_derived_companion_pass", "message": "Useful AI-derived companion data, but replace with provider-native or simulation-baked passes for final quality."})

    return validation_result(issues, warnings)


def validate_renderer_metadata(path: Path, issues: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> None:
    if not path.exists():
        warnings.append({"type": "metadata_missing", "path": str(path)})
        return
    if path.suffix.lower() != ".json":
        issues.append({"type": "metadata_not_json", "path": str(path)})
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append({"type": "metadata_invalid_json", "path": str(path), "error": str(exc)})
        return
    required = {"columns", "rows", "frame_count", "fps", "pivot"}
    default_atlas = payload.get("default_atlas") or payload
    missing = sorted(key for key in required if key not in default_atlas)
    if missing:
        warnings.append({"type": "metadata_missing_fields", "fields": missing})
    if not (payload.get("intended_renderers") or default_atlas.get("intended_renderers")):
        warnings.append({"type": "metadata_missing_renderer_targets"})


def validation_result(issues: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    if issues:
        status = "fail"
    elif warnings:
        status = "warning"
    else:
        status = "pass"
    return {"status": status, "issues": issues, "warnings": warnings}


def passes_requiring_atlas() -> set[str]:
    return {
        "beauty_flipbook",
        "core_flame_flipbook",
        "flame_slash_flipbook",
        "smoke_heat_flipbook",
        "ember_sprite_set",
        "reference_motion_overlay",
        "bolt_branch_set",
    }


def passes_requiring_alpha() -> set[str]:
    return {
        "beauty_flipbook",
        "core_flame_flipbook",
        "flame_slash_flipbook",
        "smoke_heat_flipbook",
        "impact_flash_mask",
        "ember_sprite_set",
        "reference_matched_composite",
    }


def data_pass_names() -> set[str]:
    return {
        "motion_vectors",
        "distortion_flow",
        "normal_or_lighting",
        "depth_or_thickness",
        "layer_mask_pack",
        "sdf_or_vector_field",
    }


def production_contract_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    failing_required = [
        entry.get("name")
        for entry in entries
        if entry.get("required") and (entry.get("validation") or {}).get("status") == "fail"
    ]
    warning_required = [
        entry.get("name")
        for entry in entries
        if entry.get("required") and (entry.get("validation") or {}).get("status") == "warning"
    ]
    advanced_passes = {"motion_vectors", "distortion_flow", "depth_or_thickness", "normal_or_lighting", "layer_mask_pack", "sdf_or_vector_field"}
    production_ready_advanced = sorted(
        entry.get("name")
        for entry in entries
        if entry.get("name") in advanced_passes
        and entry.get("status") == "ready"
        and (entry.get("validation") or {}).get("status") in {"pass", "warning"}
        and (entry.get("selected_asset") or {}).get("source") not in {"derived_reference_bootstrap", "reference_layer_extraction", "procedural_layer_synthesis"}
    )
    bootstrap_selected = sorted(
        entry.get("name")
        for entry in entries
        if (entry.get("selected_asset") or {}).get("source") in {"derived_reference_bootstrap", "reference_layer_extraction", "procedural_layer_synthesis", "reference_matched_composite"}
    )
    ai_derived_selected = sorted(
        entry.get("name")
        for entry in entries
        if (entry.get("selected_asset") or {}).get("source") == "ai_output_derivative"
    )
    if failing_required:
        status = "fail"
    elif warning_required or bootstrap_selected or ai_derived_selected or len(production_ready_advanced) < 3:
        status = "warning"
    else:
        status = "pass"
    return {
        "status": status,
        "failing_required_passes": failing_required,
        "warning_required_passes": warning_required,
        "production_ready_advanced_passes": production_ready_advanced,
        "bootstrap_or_reference_passes": bootstrap_selected,
        "ai_derived_companion_passes": ai_derived_selected,
        "minimum_advanced_pass_count": 3,
    }


def atlas_metadata_for_asset(pass_name: str | None, selected: dict[str, str], width: int, height: int) -> dict[str, Any] | None:
    explicit = explicit_atlas_metadata(selected)
    if explicit:
        return explicit
    return atlas_metadata_for_dimensions(pass_name, selected, width, height)


def explicit_atlas_metadata(selected: dict[str, str]) -> dict[str, Any] | None:
    try:
        columns = int(selected.get("atlas_columns") or 0)
        rows = int(selected.get("atlas_rows") or 0)
        frame_count = int(selected.get("atlas_frame_count") or columns * rows)
        fps = float(selected.get("atlas_fps") or 12.0)
    except (TypeError, ValueError):
        return None
    if columns <= 1 and rows <= 1:
        return None
    return {
        "columns": columns,
        "rows": rows,
        "frame_count": max(1, frame_count),
        "fps": fps,
    }


def atlas_metadata_for_dimensions(pass_name: str | None, selected: dict[str, str], width: int, height: int) -> dict[str, Any] | None:
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
        "normal_or_lighting",
        "depth_or_thickness",
        "layer_mask_pack",
        "sdf_or_vector_field",
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


def create_preview_frame_for_asset(selected: dict[str, str], pass_name: str, package_name: str, output_root: Path) -> Path | None:
    if pass_name not in {"core_flame_flipbook", "flame_slash_flipbook", "ground_ring_mask", "impact_flash_mask", "smoke_heat_flipbook", "ember_sprite_set"}:
        return None
    atlas = explicit_atlas_metadata(selected)
    if not atlas:
        return None
    path = Path(selected.get("path", ""))
    if not path.exists() or path.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    try:
        with Image.open(path) as image:
            atlas_image = image.convert("RGBA")
            columns = max(1, int(atlas["columns"]))
            rows = max(1, int(atlas["rows"]))
            frame_count = min(max(1, int(atlas["frame_count"])), columns * rows)
            frame_width = atlas_image.width // columns
            frame_height = atlas_image.height // rows
            best_score = -1.0
            best_frame = None
            for index in range(frame_count):
                x = (index % columns) * frame_width
                y = (index // columns) * frame_height
                frame = atlas_image.crop((x, y, x + frame_width, y + frame_height))
                score = frame_energy_score(frame)
                if score > best_score:
                    best_score = score
                    best_frame = frame
            if best_frame is None:
                return None
            runtime_dir = output_root / package_name / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            preview_path = runtime_dir / f"{package_name}_{safe_file_token(pass_name)}_preview_frame.png"
            if pass_name == "ember_sprite_set":
                best_frame = isolate_single_ember_sprite(best_frame)
            best_frame.save(preview_path)
            return preview_path
    except Exception:
        return None


def isolate_single_ember_sprite(frame: Image.Image, output_size: int = 128) -> Image.Image:
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    width, height = rgba.size
    visited: set[tuple[int, int]] = set()
    best: tuple[float, tuple[int, int, int, int]] | None = None
    pixels = rgba.load()
    alpha_pixels = alpha.load()
    for y in range(height):
        for x in range(width):
            if (x, y) in visited or alpha_pixels[x, y] <= 12:
                continue
            stack = [(x, y)]
            visited.add((x, y))
            left = right = x
            top = bottom = y
            energy = 0.0
            count = 0
            while stack:
                px, py = stack.pop()
                r, g, b, a = pixels[px, py]
                lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
                energy += (a / 255.0) * (0.4 + lum * 0.6)
                count += 1
                left = min(left, px)
                right = max(right, px)
                top = min(top, py)
                bottom = max(bottom, py)
                for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height or (nx, ny) in visited:
                        continue
                    if alpha_pixels[nx, ny] <= 12:
                        continue
                    visited.add((nx, ny))
                    stack.append((nx, ny))
            if count >= 2 and (best is None or energy > best[0]):
                best = (energy, (left, top, right + 1, bottom + 1))
    if not best:
        return rgba.resize((output_size, output_size), Image.Resampling.LANCZOS)
    left, top, right, bottom = best[1]
    pad = max(4, int(max(right - left, bottom - top) * 0.75))
    crop = rgba.crop((max(0, left - pad), max(0, top - pad), min(width, right + pad), min(height, bottom + pad)))
    crop.thumbnail((output_size - 16, output_size - 16), Image.Resampling.LANCZOS)
    output = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
    output.alpha_composite(crop, ((output_size - crop.width) // 2, (output_size - crop.height) // 2))
    return output


def frame_energy_score(frame: Image.Image) -> float:
    rgba = frame.convert("RGBA")
    score = 0.0
    for r, g, b, a in rgba.getdata():
        if a <= 4:
            continue
        lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
        score += (a / 255.0) * (0.35 + lum * 0.65)
    return score


def derive_ai_companion_candidates(
    package_name: str,
    pass_specs: list[dict[str, Any]],
    ai_outputs: list[dict[str, str]],
    output_root: Path,
) -> dict[str, list[dict[str, str]]]:
    target_names = {str(pass_spec.get("name") or "") for pass_spec in pass_specs}
    source = best_ai_companion_source(ai_outputs)
    if not source:
        return {}
    source_path = Path(str(source.get("path") or ""))
    if not source_path.exists() or source_path.suffix.lower() not in IMAGE_SUFFIXES:
        return {}

    output_dir = output_root / package_name / "ai-derived"
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates: dict[str, list[dict[str, str]]] = {}
    provenance = f"from_{safe_file_token(source_path.stem)}"

    if "alpha_mask" in target_names and not has_ai_candidate(ai_outputs, "alpha_mask"):
        alpha_path = output_dir / f"{package_name}_alpha_mask_from_ai.png"
        create_alpha_mask_from_source(source_path, alpha_path)
        candidates.setdefault("alpha_mask", []).append(
            derived_candidate(alpha_path, f"ai_alpha_mask_{provenance}", source="ai_output_derivative", confidence="medium")
        )

    if "distortion_flow" in target_names and not has_ai_candidate(ai_outputs, "distortion_flow"):
        flow_path = output_dir / f"{package_name}_distortion_flow_from_ai.png"
        create_distortion_flow_pass(flow_path)
        candidates.setdefault("distortion_flow", []).append(
            derived_candidate(flow_path, f"ai_companion_distortion_{provenance}", source="ai_output_derivative", confidence="medium")
        )

    if "normal_or_lighting" in target_names and not has_ai_candidate(ai_outputs, "normal_or_lighting"):
        normal_path = output_dir / f"{package_name}_normal_or_lighting_from_ai.png"
        create_normal_or_lighting_pass(source_path, normal_path)
        candidates.setdefault("normal_or_lighting", []).append(
            derived_candidate(normal_path, f"ai_normal_lighting_{provenance}", source="ai_output_derivative", confidence="medium")
        )

    if "depth_or_thickness" in target_names and not has_ai_candidate(ai_outputs, "depth_or_thickness"):
        depth_path = output_dir / f"{package_name}_depth_or_thickness_from_ai.png"
        create_depth_or_thickness_pass(source_path, depth_path)
        candidates.setdefault("depth_or_thickness", []).append(
            derived_candidate(depth_path, f"ai_depth_thickness_{provenance}", source="ai_output_derivative", confidence="medium")
        )

    if "layer_mask_pack" in target_names and not has_ai_candidate(ai_outputs, "layer_mask_pack"):
        mask_pack_path = output_dir / f"{package_name}_layer_mask_pack_from_ai.png"
        create_layer_mask_pack_pass(source_path, mask_pack_path)
        candidates.setdefault("layer_mask_pack", []).append(
            derived_candidate(mask_pack_path, f"ai_layer_masks_{provenance}", source="ai_output_derivative", confidence="medium")
        )

    if "sdf_or_vector_field" in target_names and not has_ai_candidate(ai_outputs, "sdf_or_vector_field"):
        field_path = output_dir / f"{package_name}_sdf_or_vector_field_from_ai.png"
        create_sdf_or_vector_field_pass(source_path, field_path)
        candidates.setdefault("sdf_or_vector_field", []).append(
            derived_candidate(field_path, f"ai_vector_field_{provenance}", source="ai_output_derivative", confidence="medium")
        )

    if "renderer_layout_metadata" in target_names and not has_ai_candidate(ai_outputs, "renderer_layout_metadata"):
        metadata_path = output_dir / f"{package_name}_renderer_layout_metadata_from_ai.json"
        create_ai_renderer_layout_metadata(metadata_path, package_name, source_path)
        candidates.setdefault("renderer_layout_metadata", []).append(
            derived_candidate(metadata_path, f"ai_layout_metadata_{provenance}", source="ai_output_derivative", confidence="medium")
        )
    return candidates


def best_ai_companion_source(ai_outputs: list[dict[str, str]]) -> dict[str, str] | None:
    priorities = [
        "core_flame_flipbook",
        "beauty_flipbook",
        "flame_slash_flipbook",
        "smoke_heat_flipbook",
        "impact_flash_mask",
    ]
    image_outputs = [
        output for output in ai_outputs
        if Path(str(output.get("path") or "")).suffix.lower() in IMAGE_SUFFIXES and Path(str(output.get("path") or "")).exists()
    ]
    for pass_name in priorities:
        for output in image_outputs:
            if pass_name in (output.get("candidate_passes") or []):
                return output
    return image_outputs[0] if image_outputs else None


def has_ai_candidate(ai_outputs: list[dict[str, str]], pass_name: str) -> bool:
    return any(pass_name in (output.get("candidate_passes") or []) for output in ai_outputs)


def create_alpha_mask_from_source(source_path: Path, output_path: Path, size: int = 512) -> None:
    alpha = reference_foreground_alpha(source_path, size)
    output = Image.merge("RGBA", (alpha, alpha, alpha, alpha))
    output.save(output_path)


def create_ai_renderer_layout_metadata(output_path: Path, package_name: str, source_path: Path) -> None:
    payload = {
        "schema_version": 1,
        "package": package_name,
        "source_asset": str(source_path),
        "default_atlas": {
            "columns": 1,
            "rows": 1,
            "frame_count": 1,
            "fps": 12,
            "frame_order": "single_frame_or_provider_atlas",
            "color_space": "srgb_for_beauty_linear_for_data",
            "pivot": [0.5, 0.5],
            "bounds": "match_ai_source_bounds",
        },
        "intended_renderers": ["sprite", "mesh_card", "ground_card"],
        "notes": [
            "AI-derived metadata. Replace with provider-exported atlas metadata for final production flipbooks.",
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def derive_bootstrap_candidates(
    package_name: str,
    pass_specs: list[dict[str, Any]],
    reference_candidates: dict[str, list[dict[str, str]]],
    reference_media: list[Path],
    output_root: Path,
    spec: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, str]]]:
    beauty = first_existing_candidate(reference_candidates.get("beauty_flipbook", []))
    reference_motion = first_existing_candidate(reference_candidates.get("reference_motion_overlay", []))
    source = beauty or reference_motion
    shape_contract = ((((spec or {}).get("visual_profile") or {}).get("reference_understanding") or {}).get("vfx_structure") or {}).get("shape_contract") or {}
    procedural_short_burst = shape_contract.get("height_class") == "short_burst"
    procedural_only = "firestorm" in package_name.lower()
    if not source and not procedural_only:
        return {}

    source_path = Path(source["path"]) if source else None
    if source_path and not source_path.suffix.lower() in IMAGE_SUFFIXES:
        return {}

    static_references = [path for path in reference_media if path.suffix.lower() in IMAGE_SUFFIXES]
    target_reference = best_reference_for_layer(static_references, "target_fire") or (static_references[0] if static_references else source_path)
    core_source = best_reference_for_layer(static_references, "core_flame") or target_reference
    side_source = best_reference_for_layer(static_references, "side_flames") or source_path or target_reference
    ring_source = best_reference_for_layer(static_references, "ground_ring") or side_source
    smoke_source = best_reference_for_layer(static_references, "smoke") or side_source

    target_names = {str(pass_spec.get("name") or "") for pass_spec in pass_specs}
    output_dir = output_root / package_name / "derived"
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates: dict[str, list[dict[str, str]]] = {}
    if "beauty_flipbook" in target_names and procedural_only:
        beauty_path = output_dir / f"{package_name}_beauty_flipbook.png"
        create_fire_atlas_pass(beauty_path, "firestorm_core")
        candidates.setdefault("beauty_flipbook", []).append(
            derived_candidate(beauty_path, "procedural_firestorm_beauty", source="procedural_layer_synthesis", confidence="medium")
        )

    if "alpha_mask" in target_names:
        alpha_path = output_dir / f"{package_name}_alpha_mask.png"
        if target_reference:
            create_reference_extracted_fire_atlas(target_reference, alpha_path, "alpha_mask")
            candidates.setdefault("alpha_mask", []).append(derived_candidate(alpha_path, "alpha_from_reference_layers", source="reference_layer_extraction", confidence="medium"))
        else:
            create_fire_atlas_pass(alpha_path, "alpha_mask")
            candidates.setdefault("alpha_mask", []).append(derived_candidate(alpha_path, "procedural_firestorm_alpha", source="procedural_layer_synthesis", confidence="medium"))

    if "core_flame_flipbook" in target_names:
        core_path = output_dir / f"{package_name}_core_flame_flipbook.png"
        if procedural_short_burst:
            create_fire_atlas_pass(core_path, "short_burst_core")
            candidates.setdefault("core_flame_flipbook", []).append(derived_candidate(core_path, "procedural_short_burst_core", source="procedural_layer_synthesis", confidence="medium"))
        elif core_source:
            create_reference_extracted_fire_atlas(core_source, core_path, "core_flame")
            candidates.setdefault("core_flame_flipbook", []).append(derived_candidate(core_path, "core_flame_from_reference_layer", source="reference_layer_extraction", confidence="medium"))
        else:
            create_fire_atlas_pass(core_path, "firestorm_core")
            candidates.setdefault("core_flame_flipbook", []).append(derived_candidate(core_path, "procedural_firestorm_core", source="procedural_layer_synthesis", confidence="medium"))

    if "smoke_heat_flipbook" in target_names:
        smoke_path = output_dir / f"{package_name}_smoke_heat_flipbook.png"
        create_fire_atlas_pass(smoke_path, "smoke_heat")
        candidates.setdefault("smoke_heat_flipbook", []).append(derived_candidate(smoke_path, "procedural_smoke_heat_support", source="procedural_layer_synthesis", confidence="medium"))

    if "flame_slash_flipbook" in target_names:
        slash_path = output_dir / f"{package_name}_flame_slash_flipbook.png"
        if procedural_short_burst:
            create_fire_atlas_pass(slash_path, "short_burst_lobes")
            candidates.setdefault("flame_slash_flipbook", []).append(derived_candidate(slash_path, "procedural_short_burst_lobes", source="procedural_layer_synthesis", confidence="medium"))
        elif side_source:
            create_reference_extracted_fire_atlas(side_source, slash_path, "flame_slashes")
            candidates.setdefault("flame_slash_flipbook", []).append(derived_candidate(slash_path, "side_flames_from_reference_layer", source="reference_layer_extraction", confidence="medium"))
        else:
            create_fire_atlas_pass(slash_path, "firestorm_slashes")
            candidates.setdefault("flame_slash_flipbook", []).append(derived_candidate(slash_path, "procedural_firestorm_spiral_slashes", source="procedural_layer_synthesis", confidence="medium"))

    if "ground_ring_mask" in target_names:
        ring_path = output_dir / f"{package_name}_ground_ring_mask.png"
        create_fire_atlas_pass(ring_path, "firestorm_ground_ring" if procedural_only else "ground_ring")
        candidates.setdefault("ground_ring_mask", []).append(derived_candidate(ring_path, "procedural_ground_ring_anchor", source="procedural_layer_synthesis", confidence="medium"))

    if "impact_flash_mask" in target_names:
        flash_path = output_dir / f"{package_name}_impact_flash_mask.png"
        if procedural_short_burst:
            create_fire_atlas_pass(flash_path, "short_burst_impact")
            candidates.setdefault("impact_flash_mask", []).append(derived_candidate(flash_path, "procedural_short_burst_impact", source="procedural_layer_synthesis", confidence="medium"))
        elif ring_source:
            create_reference_extracted_fire_atlas(ring_source, flash_path, "impact_flash")
            candidates.setdefault("impact_flash_mask", []).append(derived_candidate(flash_path, "impact_flash_from_reference_layer", source="reference_layer_extraction", confidence="medium"))
        else:
            create_fire_atlas_pass(flash_path, "impact_flash")
            candidates.setdefault("impact_flash_mask", []).append(derived_candidate(flash_path, "procedural_firestorm_impact_flash", source="procedural_layer_synthesis", confidence="medium"))

    if "ember_sprite_set" in target_names:
        ember_path = output_dir / f"{package_name}_ember_sprite_set.png"
        if procedural_short_burst:
            create_fire_atlas_pass(ember_path, "short_burst_embers")
            candidates.setdefault("ember_sprite_set", []).append(derived_candidate(ember_path, "procedural_short_burst_embers", source="procedural_layer_synthesis", confidence="medium"))
        elif target_reference:
            create_reference_extracted_fire_atlas(target_reference, ember_path, "embers")
            candidates.setdefault("ember_sprite_set", []).append(derived_candidate(ember_path, "embers_from_reference_layer", source="reference_layer_extraction", confidence="medium"))
        else:
            create_fire_atlas_pass(ember_path, "embers")
            candidates.setdefault("ember_sprite_set", []).append(derived_candidate(ember_path, "procedural_firestorm_embers", source="procedural_layer_synthesis", confidence="medium"))

    if "distortion_flow" in target_names:
        flow_path = output_dir / f"{package_name}_distortion_flow.png"
        create_distortion_flow_pass(flow_path)
        candidates.setdefault("distortion_flow", []).append(derived_candidate(flow_path, "procedural_heat_distortion_flow"))

    if "normal_or_lighting" in target_names:
        normal_path = output_dir / f"{package_name}_normal_or_lighting.png"
        if target_reference:
            create_normal_or_lighting_pass(target_reference, normal_path)
        else:
            create_fire_atlas_pass(normal_path, "normal_or_lighting", columns=2, rows=2, frame_size=256)
        candidates.setdefault("normal_or_lighting", []).append(
            derived_candidate(normal_path, "ai_ready_normal_lighting_bootstrap")
        )

    if "depth_or_thickness" in target_names:
        depth_path = output_dir / f"{package_name}_depth_or_thickness.png"
        if target_reference:
            create_depth_or_thickness_pass(target_reference, depth_path)
        else:
            create_fire_atlas_pass(depth_path, "depth_or_thickness", columns=2, rows=2, frame_size=256)
        candidates.setdefault("depth_or_thickness", []).append(
            derived_candidate(depth_path, "ai_ready_depth_thickness_bootstrap")
        )

    if "layer_mask_pack" in target_names:
        mask_pack_path = output_dir / f"{package_name}_layer_mask_pack.png"
        if target_reference:
            create_layer_mask_pack_pass(target_reference, mask_pack_path)
        else:
            create_fire_atlas_pass(mask_pack_path, "layer_mask_pack", columns=2, rows=2, frame_size=256)
        candidates.setdefault("layer_mask_pack", []).append(
            derived_candidate(mask_pack_path, "ai_ready_layer_mask_pack_bootstrap")
        )

    if "sdf_or_vector_field" in target_names:
        field_path = output_dir / f"{package_name}_sdf_or_vector_field.png"
        if target_reference:
            create_sdf_or_vector_field_pass(target_reference, field_path)
        else:
            create_fire_atlas_pass(field_path, "sdf_or_vector_field", columns=1, rows=1, frame_size=256)
        candidates.setdefault("sdf_or_vector_field", []).append(
            derived_candidate(field_path, "ai_ready_sdf_vector_field_bootstrap")
        )

    if "renderer_layout_metadata" in target_names:
        metadata_path = output_dir / f"{package_name}_renderer_layout_metadata.json"
        create_renderer_layout_metadata(metadata_path, package_name)
        candidates.setdefault("renderer_layout_metadata", []).append(
            derived_candidate(metadata_path, "renderer_layout_metadata_bootstrap")
        )

    similarity_report = create_similarity_report(package_name, target_reference, output_dir) if target_reference else {}
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


def signed_noise(value: float, seed: float = 0.0) -> float:
    raw = math.sin(value * 12.9898 + seed * 78.233) * 43758.5453
    return (raw - math.floor(raw)) * 2.0 - 1.0


def draw_flame_tongue(
    draw: ImageDraw.ImageDraw,
    size: int,
    center_x: float,
    base_y: float,
    height: float,
    base_width: float,
    lean: float,
    phase: float,
    fill: tuple[int, int, int, int],
    seed: float,
    steps: int = 10,
) -> None:
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for step in range(steps + 1):
        t = step / steps
        y = base_y - height * t
        taper = (1.0 - t) ** 0.72
        width = base_width * (0.08 + taper * 0.92)
        sway = math.sin(t * 5.8 + phase * math.tau * 1.35 + seed) * size * (0.016 + t * 0.034)
        jitter = signed_noise(t * 4.3 + phase * 2.0, seed) * size * 0.018 * taper
        x = center_x + lean * t + sway + jitter
        edge = signed_noise(t * 9.0 + phase * 3.0, seed + 4.0) * width * 0.2
        left.append((x - width + edge, y))
        right.append((x + width + edge * 0.5, y))
    tip = right[-1]
    polygon = [*left, tip, *reversed(right[:-1])]
    draw.polygon(polygon, fill=fill)


def draw_flame_ribbon(
    draw: ImageDraw.ImageDraw,
    size: int,
    origin_x: float,
    origin_y: float,
    length: float,
    width: float,
    angle: float,
    curvature: float,
    phase: float,
    fill: tuple[int, int, int, int],
    seed: float,
    steps: int = 12,
) -> None:
    centers: list[tuple[float, float]] = []
    for step in range(steps + 1):
        t = step / steps
        curl = math.sin(t * math.pi) * curvature
        local_angle = angle + curl + math.sin(phase * math.tau + t * 5.0 + seed) * 0.12
        distance = length * t
        x = origin_x + math.cos(local_angle) * distance
        y = origin_y - math.sin(local_angle) * distance * 0.58
        y += math.sin(t * 8.0 + phase * math.tau + seed) * size * 0.018
        centers.append((x, y))

    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for index, (x, y) in enumerate(centers):
        t = index / steps
        if index < len(centers) - 1:
            nx = centers[index + 1][0] - x
            ny = centers[index + 1][1] - y
        else:
            nx = x - centers[index - 1][0]
            ny = y - centers[index - 1][1]
        length_norm = max(0.001, math.hypot(nx, ny))
        px = -ny / length_norm
        py = nx / length_norm
        local_width = width * (1.0 - t) ** 0.68 + size * 0.006
        rough = 1.0 + signed_noise(t * 6.2 + phase * 3.0, seed) * 0.24
        left.append((x + px * local_width * rough, y + py * local_width * rough))
        right.append((x - px * local_width * (2.0 - rough), y - py * local_width * (2.0 - rough)))
    draw.polygon([*left, *reversed(right)], fill=fill)


def create_fire_atlas_pass(output_path: Path, pass_kind: str, columns: int = 4, rows: int = 4, frame_size: int = 256) -> None:
    atlas = Image.new("RGBA", (columns * frame_size, rows * frame_size), (0, 0, 0, 0))
    frame_count = columns * rows
    for index in range(frame_count):
        phase = index / max(frame_count - 1, 1)
        frame = Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame, "RGBA")
        if pass_kind == "firestorm_core":
            draw_firestorm_core_frame(draw, frame_size, phase)
        elif pass_kind == "firestorm_slashes":
            draw_firestorm_slash_frame(draw, frame_size, phase)
        elif pass_kind == "firestorm_ground_ring":
            draw_firestorm_ground_ring_frame(draw, frame_size, phase)
        elif pass_kind == "short_burst_core":
            draw_short_burst_core_frame(draw, frame_size, phase)
        elif pass_kind == "short_burst_lobes":
            draw_short_burst_lobes_frame(draw, frame_size, phase)
        elif pass_kind == "short_burst_impact":
            draw_short_burst_impact_frame(draw, frame_size, phase)
        elif pass_kind == "short_burst_embers":
            draw_short_burst_ember_frame(draw, frame_size, phase, index)
        elif pass_kind == "alpha_mask":
            draw_firestorm_alpha_frame(draw, frame_size, phase)
        elif pass_kind == "normal_or_lighting":
            draw_firestorm_normal_frame(draw, frame_size, phase)
        elif pass_kind == "depth_or_thickness":
            draw_firestorm_depth_frame(draw, frame_size, phase)
        elif pass_kind == "layer_mask_pack":
            draw_firestorm_layer_mask_frame(draw, frame_size, phase)
        elif pass_kind == "sdf_or_vector_field":
            draw_firestorm_field_frame(draw, frame_size, phase)
        elif pass_kind == "flame_slashes":
            draw_flame_slash_frame(draw, frame_size, phase)
        elif pass_kind == "ground_ring":
            draw_ground_ring_frame(draw, frame_size, phase)
        elif pass_kind == "impact_flash":
            draw_impact_flash_frame(draw, frame_size, phase)
        elif pass_kind == "smoke_heat":
            draw_smoke_heat_frame(draw, frame_size, phase, index)
        elif pass_kind == "embers":
            draw_ember_frame(draw, frame_size, phase, index)
        frame = frame.filter(ImageFilter.GaussianBlur(radius=blur_radius_for_fire_pass(pass_kind)))
        x = (index % columns) * frame_size
        y = (index // columns) * frame_size
        atlas.alpha_composite(frame, (x, y))
    atlas.save(output_path)


def blur_radius_for_fire_pass(pass_kind: str) -> float:
    if pass_kind == "smoke_heat":
        return 2.6
    if pass_kind == "firestorm_core":
        return 1.45
    if pass_kind == "firestorm_slashes":
        return 1.15
    if pass_kind == "short_burst_core":
        return 2.4
    if pass_kind == "short_burst_lobes":
        return 1.35
    if pass_kind == "short_burst_impact":
        return 0.8
    if pass_kind == "short_burst_embers":
        return 0.05
    if pass_kind == "firestorm_ground_ring":
        return 0.35
    if pass_kind in {"normal_or_lighting", "depth_or_thickness", "layer_mask_pack", "sdf_or_vector_field"}:
        return 0.0
    if pass_kind == "embers":
        return 0.05
    return 0.22


def draw_short_burst_core_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = math.sin(phase * math.pi)
    cx = size * (0.5 + math.sin(phase * math.tau * 0.8) * 0.03)
    cy = size * (0.59 - pulse * 0.045)

    # This is a flame volume cell, not a complete flame drawing. Repeated
    # Niagara sprites should blend into one connected burst instead of reading
    # as many pasted fire icons.
    for index in range(16):
        t = index / 15
        band = (index % 5) / 4
        rx = size * (0.058 + 0.05 * (1.0 - band) + pulse * 0.01)
        ry = size * (0.07 + 0.062 * (1.0 - t) + pulse * 0.014)
        x = cx + signed_noise(index * 1.1, phase) * size * 0.13
        y = cy - t * size * 0.2 + signed_noise(index * 1.6, phase + 1.0) * size * 0.046
        alpha = int((24 + 46 * (1.0 - t)) * (0.72 + pulse * 0.28))
        draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(255, int(78 + 116 * (1.0 - t)), 20, alpha))

    for index in range(7):
        t = index / 6
        rx = size * (0.036 + pulse * 0.006)
        ry = size * (0.08 + pulse * 0.012)
        x = cx + signed_noise(index * 2.3, phase + 4.0) * size * 0.082
        y = cy - size * (0.015 + t * 0.18)
        draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(255, 218, 96, int((22 + 28 * (1.0 - t)) * (0.78 + pulse * 0.22))))

    for index in range(6):
        side = -1 if index % 2 == 0 else 1
        origin_x = cx + side * size * (0.08 + index * 0.018)
        origin_y = cy + size * 0.08 - index * size * 0.019
        draw_flame_ribbon(
            draw,
            size,
            origin_x,
            origin_y,
            size * (0.17 + pulse * 0.035),
            size * (0.014 + index * 0.0015),
            math.radians(72 + index * 8) * side,
            side * 0.28,
            phase,
            (255, 76 + index * 22, 16, int(28 + 34 * pulse)),
            11.0 + index,
            steps=8,
        )


def draw_short_burst_lobes_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = math.sin(phase * math.pi)
    cx = size * 0.5
    cy = size * (0.7 - pulse * 0.08)
    for index in range(7):
        side = -1 if index % 2 == 0 else 1
        distance = size * (0.05 + index * 0.018)
        origin_x = cx + side * distance
        origin_y = cy + signed_noise(index, phase) * size * 0.018
        angle = math.radians(64 + index * 7) * side
        length = size * (0.16 + pulse * 0.09 + index * 0.006)
        width = size * (0.026 + (6 - index) * 0.004)
        color = (255, 82 + index * 14, 18, int(96 + 78 * pulse))
        draw_flame_ribbon(draw, size, origin_x, origin_y, length, width, angle, side * 0.42, phase, color, 3.1 + index)
    draw.ellipse((cx - size * 0.18, cy - size * 0.1, cx + size * 0.18, cy + size * 0.08), fill=(255, 142, 24, int(70 + 52 * pulse)))


def draw_short_burst_impact_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = math.sin(phase * math.pi)
    cx = cy = size * 0.5
    for index in range(5):
        radius_x = size * (0.09 + index * 0.05 + pulse * 0.025)
        radius_y = size * (0.035 + index * 0.016)
        alpha = int((155 - index * 28) * (0.72 + pulse * 0.28))
        draw.ellipse((cx - radius_x, cy - radius_y, cx + radius_x, cy + radius_y), fill=(255, 130 + index * 16, 28, alpha))
    draw.ellipse((cx - size * 0.055, cy - size * 0.055, cx + size * 0.055, cy + size * 0.055), fill=(255, 252, 208, int(220 * pulse)))


def draw_short_burst_ember_frame(draw: ImageDraw.ImageDraw, size: int, phase: float, seed: int) -> None:
    pulse = 0.65 + math.sin(phase * math.pi) * 0.35
    cx = size * (0.5 + signed_noise(seed * 1.2, phase) * 0.08)
    cy = size * (0.52 + signed_noise(seed * 1.7, phase + 2.0) * 0.08)
    radius = size * (0.016 + (seed % 3) * 0.004)
    angle = phase * math.tau + seed * 0.9
    trail = size * (0.04 + (seed % 4) * 0.012)
    tx = math.cos(angle) * trail
    ty = math.sin(angle) * trail
    draw.line((cx - tx, cy - ty, cx + tx * 0.2, cy + ty * 0.2), fill=(255, 82, 18, int(86 * pulse)), width=max(1, int(size * 0.012)))
    draw.ellipse((cx - radius * 2.0, cy - radius * 2.0, cx + radius * 2.0, cy + radius * 2.0), fill=(255, 72, 14, int(58 * pulse)))
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(255, 174, 42, int(170 * pulse)))
    draw.ellipse((cx - radius * 0.42, cy - radius * 0.42, cx + radius * 0.42, cy + radius * 0.42), fill=(255, 246, 198, int(230 * pulse)))


def draw_firestorm_core_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = math.sin(phase * math.pi)
    center_x = size * (0.5 + math.sin(phase * math.tau * 0.7) * 0.025)
    bottom_y = size * 0.86
    top_y = size * 0.26

    for band in range(9):
        t = band / 8
        y = bottom_y + (top_y - bottom_y) * t
        center = center_x + math.sin(t * 5.2 + phase * math.tau) * size * 0.028
        radius = size * (0.035 + t * 0.16 + pulse * 0.006)
        height = radius * (0.34 + t * 0.1)
        start = phase * 360 + band * 54
        width = max(2, int(size * (0.008 + t * 0.008)))
        box = (center - radius, y - height, center + radius, y + height)
        outer, core, hot = fire_ice_tornado_palette(t, pulse)
        draw.arc(box, start=start, end=start + 185, fill=outer, width=width + 4)
        draw.arc(box, start=start + 18, end=start + 138, fill=core, width=width)
        if band % 2 == 0:
            draw.arc(box, start=start + 38, end=start + 88, fill=hot, width=max(1, width // 2))

    for tongue in range(3):
        t = tongue / 2
        side = -1 if tongue % 2 else 1
        y = size * (0.62 - t * 0.25)
        radius = size * (0.05 + t * 0.11)
        x = center_x + side * radius
        draw_flame_tongue(
            draw,
            size,
            x,
            y,
            size * (0.042 - t * 0.01),
            size * (0.014 + t * 0.004),
            side * size * (0.032 + t * 0.02),
            phase + t * 0.23,
            (130, 240, 255, int(16 + 14 * pulse)) if t < 0.55 else (255, 112, 18, int(16 + 14 * pulse)),
            34.0 + tongue,
            steps=6,
        )

    hollow_top = size * 0.29
    hollow_bottom = size * 0.78
    for band in range(7):
        t = band / 6
        y = hollow_bottom + (hollow_top - hollow_bottom) * t
        rx = size * (0.018 + t * 0.075)
        ry = size * (0.024 + t * 0.026)
        x = center_x + math.sin(t * 5.2 + phase * math.tau) * size * 0.02
        draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(6, 3, 2, int(70 - t * 18)))


def draw_firestorm_slash_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = math.sin(phase * math.pi)
    cx = size * 0.5
    for band in range(7):
        t = band / 6
        y = size * (0.78 - t * 0.48)
        radius = size * (0.05 + t * 0.16)
        height = radius * 0.42
        x = cx + math.sin(t * 4.4 + phase * math.tau) * size * 0.035
        start = phase * 300 + band * 58
        width = max(2, int(size * (0.006 + t * 0.006)))
        box = (x - radius, y - height, x + radius, y + height)
        outer, core, hot = fire_ice_tornado_palette(t, pulse)
        draw.arc(box, start=start, end=start + 150, fill=outer, width=width + 3)
        draw.arc(box, start=start + 16, end=start + 106, fill=core, width=width)
        if band % 2 == 1:
            draw.arc(box, start=start + 34, end=start + 68, fill=hot, width=max(1, width // 2))


def fire_ice_tornado_palette(height01: float, pulse: float) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int]]:
    if height01 < 0.58:
        blend = smoothstep01(0.0, 0.58, height01)
        outer = (18, int(118 + 40 * blend), 160, int(52 + 18 * pulse))
        core = (62, int(222 + 18 * blend), 255, int(72 + 24 * pulse))
        hot = (214, 252, 255, int(54 + 22 * pulse))
        return outer, core, hot
    blend = smoothstep01(0.58, 1.0, height01)
    outer = (118, int(18 + 18 * blend), 3, int(48 + 18 * pulse))
    core = (255, int(66 + 54 * blend), 8, int(70 + 24 * pulse))
    hot = (255, int(190 + 34 * blend), 86, int(58 + 20 * pulse))
    return outer, core, hot


def draw_firestorm_ground_ring_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    pulse = math.sin(phase * math.pi)
    cx = cy = size / 2
    glow_layers = (
        (0.34, 0.2, (8, 74, 94, 30)),
        (0.24, 0.14, (26, 164, 188, 22)),
    )
    for rx_scale, ry_scale, color in glow_layers:
        rx = size * (rx_scale + 0.01 * pulse)
        ry = size * (ry_scale + 0.006 * pulse)
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=color)

    for radius_index, radius_scale in enumerate((0.2, 0.28, 0.34)):
        radius = size * (radius_scale + 0.008 * pulse)
        height = radius * (0.5 + radius_index * 0.025)
        width = max(2, int(size * (0.006 + 0.0025 * radius_index)))
        segment_count = 4 + radius_index
        for segment in range(segment_count):
            start = segment * (360 / segment_count) + phase * 58 * (1 if radius_index % 2 == 0 else -1) + radius_index * 27
            length = 16 + 13 * ((segment * 31 + radius_index * 17) % 100) / 100
            box = (cx - radius, cy - height, cx + radius, cy + height)
            draw.arc(box, start=start, end=start + length, fill=(20, 154, 184, 58), width=width + 1)
            draw.arc(box, start=start + 4, end=start + length * 0.72, fill=(118, 238, 255, 64), width=width)
            if radius_index == 0:
                draw.arc(box, start=start + 10, end=start + length * 0.45, fill=(226, 255, 255, 36), width=max(1, width // 2))

    for streamer in range(8):
        seed = streamer / 8
        angle = seed * math.tau + phase * math.tau * 0.5
        length = size * (0.12 + 0.09 * ((streamer * 29) % 100) / 100)
        start_radius = size * (0.08 + 0.18 * ((streamer * 17) % 100) / 100)
        points = []
        for step in range(4):
            t = step / 3
            local_radius = start_radius + length * t
            local_angle = angle + t * 0.42
            x = cx + math.cos(local_angle) * local_radius
            y = cy + math.sin(local_angle) * local_radius * 0.58
            points.append((x, y))
        draw.line(points, fill=(98, 232, 255, 34), width=max(1, int(size * 0.004)), joint="curve")

    for mote in range(10):
        local = mote / 10
        angle = local * math.tau + phase * math.tau * 0.42
        radius = size * (0.1 + 0.22 * ((mote * 23) % 100) / 100)
        x = cx + math.cos(angle) * radius
        y = cy + math.sin(angle) * radius * 0.58
        r = size * (0.0025 + 0.003 * ((mote * 11) % 100) / 100)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(180, 250, 255, int(18 + 12 * pulse)))


def draw_firestorm_alpha_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    draw_firestorm_core_frame(draw, size, phase)
    draw_firestorm_slash_frame(draw, size, phase)
    cx = cy = size / 2
    draw.ellipse((cx - size * 0.42, cy + size * 0.16, cx + size * 0.42, cy + size * 0.54), fill=(255, 255, 255, 138))


def draw_firestorm_normal_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    for y in range(size):
        ny = (y / max(size - 1, 1) - 0.5) * 2
        for x in range(size):
            nx = (x / max(size - 1, 1) - 0.5) * 2
            distance = min(1.0, math.hypot(nx * 0.9, ny * 1.2))
            angle = math.atan2(ny, nx) + phase * math.tau
            r = int(128 + math.cos(angle) * (1.0 - distance) * 86)
            g = int(128 + math.sin(angle) * (1.0 - distance) * 86)
            b = int(170 + 72 * (1.0 - distance))
            a = int(255 * (1.0 - smoothstep01(0.72, 1.0, distance)))
            draw.point((x, y), fill=(r, g, min(255, b), a))


def draw_firestorm_depth_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    for y in range(size):
        y01 = y / max(size - 1, 1)
        for x in range(size):
            x01 = x / max(size - 1, 1)
            radial = 1.0 - min(1.0, math.hypot((x01 - 0.5) * 1.55, (y01 - 0.58) * 1.8))
            column = math.exp(-abs(x01 - 0.5 - math.sin(y01 * 9.0 + phase * math.tau) * 0.07) * 8.0)
            value = int(255 * clamp01(radial * 0.44 + column * 0.42))
            draw.point((x, y), fill=(value, value, value, value))


def draw_firestorm_layer_mask_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    for y in range(size):
        y01 = y / max(size - 1, 1)
        for x in range(size):
            x01 = x / max(size - 1, 1)
            dx = x01 - 0.5
            dy = y01 - 0.58
            distance = min(1.0, math.hypot(dx * 1.3, dy * 1.8))
            angle = math.atan2(dy, dx) + phase * math.tau
            core = int(255 * clamp01((1.0 - abs(dx) * 5.0) * (1.0 - smoothstep01(0.05, 0.94, y01))))
            edge = int(255 * clamp01((1.0 - distance) * (0.45 + 0.55 * math.sin(angle * 3.0) ** 2)))
            smoke = int(255 * clamp01(smoothstep01(0.1, 0.78, y01) * (1.0 - core / 255.0) * 0.62))
            sparks = int(255 * clamp01((1.0 - distance) * smoothstep01(0.18, 0.66, abs(math.sin(angle * 5.0)))))
            draw.point((x, y), fill=(core, edge, smoke, sparks))


def draw_firestorm_field_frame(draw: ImageDraw.ImageDraw, size: int, phase: float) -> None:
    for y in range(size):
        y01 = y / max(size - 1, 1)
        for x in range(size):
            x01 = x / max(size - 1, 1)
            dx = x01 - 0.5
            dy = y01 - 0.58
            angle = math.atan2(dy, dx) + math.pi / 2
            radius = min(1.0, math.hypot(dx, dy) * 2.0)
            u = int(128 + math.cos(angle + phase * math.tau) * (1.0 - radius) * 92)
            v = int(128 + math.sin(angle + phase * math.tau) * (1.0 - radius) * 92)
            sdf = int(255 * (1.0 - smoothstep01(0.18, 0.72, radius)))
            draw.point((x, y), fill=(u, v, sdf, 255))


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


def draw_smoke_heat_frame(draw: ImageDraw.ImageDraw, size: int, phase: float, seed: int) -> None:
    pulse = math.sin(phase * math.pi)
    base_y = size * (0.62 - 0.1 * phase)
    for index in range(10):
        local = ((seed + 1) * 19 + index * 31) % 100 / 100
        drift = math.sin(phase * math.tau + local * 5.0)
        tier = index / 9.0
        cx = size * (0.5 + (local - 0.5) * (0.42 - tier * 0.18) + drift * 0.045)
        cy = base_y - size * (0.28 * tier + 0.08 * phase)
        rx = size * (0.12 + 0.09 * pulse + 0.04 * local) * (1.0 - tier * 0.32)
        ry = size * (0.06 + 0.05 * pulse + 0.02 * (1.0 - local))
        alpha = int((10 + 28 * pulse * (0.55 + local * 0.45)) * (1.0 - tier * 0.42))
        color = (78, 58, 46, alpha)
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=color)
    for index in range(4):
        local = index / 3.0
        cx = size * (0.45 + local * 0.1 + math.sin(phase * math.tau + index) * 0.025)
        cy = size * (0.46 - local * 0.18 - phase * 0.06)
        rx = size * (0.035 + local * 0.025)
        ry = size * (0.16 - local * 0.04)
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(96, 70, 46, int(10 + 12 * pulse)))


def draw_ember_frame(draw: ImageDraw.ImageDraw, size: int, phase: float, seed: int) -> None:
    cols = 4
    rows = 4
    cell = size / cols
    for index in range(cols * rows):
        x0 = (index % cols) * cell
        y0 = (index // rows) * cell
        local = (seed * 17 + index * 29) % 100 / 100
        angle = local * math.tau + phase * 1.8
        cx = x0 + cell * (0.48 + 0.16 * math.sin(local * 9.0))
        cy = y0 + cell * (0.48 + 0.14 * math.cos(local * 7.0))
        radius = cell * (0.035 + 0.055 * ((index + seed) % 5) / 4)
        tail = cell * (0.08 + 0.12 * local)
        tx = math.cos(angle) * tail
        ty = math.sin(angle) * tail * 0.55
        width = max(1, int(radius * 0.75))
        draw.line((cx - tx, cy - ty, cx + tx * 0.25, cy + ty * 0.25), fill=(255, 92, 18, 110), width=width + 2)
        draw.ellipse((cx - radius * 1.9, cy - radius * 1.9, cx + radius * 1.9, cy + radius * 1.9), fill=(255, 92, 18, 64))
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(255, 172, 40, 174))
        draw.ellipse((cx - radius * 0.42, cy - radius * 0.42, cx + radius * 0.42, cy + radius * 0.42), fill=(255, 246, 198, 220))


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


def create_normal_or_lighting_pass(source_path: Path, output_path: Path, size: int = 512) -> None:
    alpha = reference_foreground_alpha(source_path, size)
    blurred = alpha.filter(ImageFilter.GaussianBlur(radius=2.0))
    pixels = []
    for y in range(size):
        for x in range(size):
            left = blurred.getpixel((max(0, x - 1), y))
            right = blurred.getpixel((min(size - 1, x + 1), y))
            up = blurred.getpixel((x, max(0, y - 1)))
            down = blurred.getpixel((x, min(size - 1, y + 1)))
            dx = (left - right) / 255.0
            dy = (up - down) / 255.0
            strength = 0.85
            nz = 1.0
            length = math.sqrt(dx * dx * strength + dy * dy * strength + nz * nz) or 1.0
            nx = dx * strength / length
            ny = dy * strength / length
            normal_r = int((nx * 0.5 + 0.5) * 255)
            normal_g = int((ny * 0.5 + 0.5) * 255)
            lighting = int(max(32, min(255, blurred.getpixel((x, y)))))
            pixels.append((normal_r, normal_g, lighting, 255))
    output = Image.new("RGBA", (size, size))
    output.putdata(pixels)
    output.save(output_path)


def create_depth_or_thickness_pass(source_path: Path, output_path: Path, size: int = 512) -> None:
    alpha = reference_foreground_alpha(source_path, size)
    center_glow = Image.new("L", (size, size), 0)
    pixels = []
    cx = cy = (size - 1) / 2.0
    for y in range(size):
        y01 = y / max(size - 1, 1)
        for x in range(size):
            x01 = x / max(size - 1, 1)
            radial = 1.0 - min(1.0, math.hypot((x - cx) / cx, (y - cy) / cy))
            vertical = 1.0 - abs(y01 - 0.58) * 1.35
            value = int(255 * clamp01(radial * 0.55 + vertical * 0.28))
            pixels.append(value)
    center_glow.putdata(pixels)
    thickness = Image.composite(center_glow, Image.new("L", (size, size), 0), alpha)
    thickness = thickness.filter(ImageFilter.GaussianBlur(radius=3.0))
    output = Image.merge("RGBA", (thickness, thickness, thickness, alpha))
    output.save(output_path)


def create_layer_mask_pack_pass(source_path: Path, output_path: Path, size: int = 512) -> None:
    with Image.open(source_path) as source_image:
        image = source_image.convert("RGBA")
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))

    channels: list[tuple[int, int, int, int]] = []
    for r, g, b, a in canvas.getdata():
        if a <= 4:
            channels.append((0, 0, 0, 0))
            continue
        lum = luminance01(r, g, b)
        warm = warm_score01(r, g, b)
        core = int(255 * clamp01(lum * warm * 1.35))
        edge = int(255 * clamp01(warm * (1.0 - abs(lum - 0.55) * 1.15)))
        smoke = int(255 * clamp01((1.0 - lum) * (0.45 + warm * 0.2)))
        sparks = int(255 * clamp01(smoothstep01(0.82, 1.0, lum) * (0.5 + warm * 0.5)))
        channels.append((core, edge, smoke, sparks))
    output = Image.new("RGBA", (size, size))
    output.putdata(channels)
    output.save(output_path)


def create_sdf_or_vector_field_pass(source_path: Path, output_path: Path, size: int = 256) -> None:
    alpha = reference_foreground_alpha(source_path, size).filter(ImageFilter.GaussianBlur(radius=1.2))
    pixels = []
    cx = cy = (size - 1) / 2.0
    for y in range(size):
        for x in range(size):
            left = alpha.getpixel((max(0, x - 1), y)) / 255.0
            right = alpha.getpixel((min(size - 1, x + 1), y)) / 255.0
            up = alpha.getpixel((x, max(0, y - 1))) / 255.0
            down = alpha.getpixel((x, min(size - 1, y + 1))) / 255.0
            grad_x = right - left
            grad_y = down - up
            swirl_x = -(y - cy) / max(cy, 1.0)
            swirl_y = (x - cx) / max(cx, 1.0)
            u = int(128 + 78 * clamp01_signed(grad_x * 1.7 + swirl_x * 0.24))
            v = int(128 + 78 * clamp01_signed(grad_y * 1.7 + swirl_y * 0.24))
            sdf = int(alpha.getpixel((x, y)))
            pixels.append((max(0, min(255, u)), max(0, min(255, v)), sdf, 255))
    output = Image.new("RGBA", (size, size))
    output.putdata(pixels)
    output.save(output_path)


def create_renderer_layout_metadata(output_path: Path, package_name: str) -> None:
    payload = {
        "schema_version": 1,
        "package": package_name,
        "default_atlas": {
            "columns": 4,
            "rows": 4,
            "frame_count": 16,
            "fps": 12,
            "frame_order": "row_major",
            "color_space": "linear_for_data_srgb_for_beauty",
            "pivot": [0.5, 0.5],
        },
        "intended_renderers": ["sprite_subuv", "ribbon", "mesh_card", "ground_card"],
        "notes": [
            "Bootstrap metadata only. Replace with provider-exported per-pass metadata for final production.",
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def reference_foreground_alpha(source_path: Path, size: int) -> Image.Image:
    with Image.open(source_path) as source_image:
        image = source_image.convert("RGBA")
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    values = []
    for r, g, b, a in canvas.getdata():
        if a > 8:
            foreground = max(a, int(255 * effect_foreground_score(r, g, b)))
        else:
            foreground = int(255 * effect_foreground_score(r, g, b))
        values.append(max(0, min(255, foreground)))
    alpha = Image.new("L", (size, size))
    alpha.putdata(values)
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius=0.8))
    alpha = alpha.point(lambda value: 0 if value < 12 else min(255, int(value * 1.22)))
    return alpha


def clamp01_signed(value: float) -> float:
    return max(-1.0, min(1.0, value))


def should_apply_shared_alpha(pass_name: str | None, selected_path: str | None, alpha_path: str | None) -> bool:
    if pass_name not in {"beauty_flipbook", "reference_motion_overlay"}:
        return False
    if not selected_path or not alpha_path:
        return False
    selected = Path(selected_path)
    alpha = Path(alpha_path)
    if not selected.exists() or not alpha.exists():
        return False
    # Role-specific runtime frames already carry their own shaped alpha. Reusing
    # the package-wide reference mask on those layers has mismatched UVs and
    # produces visible holes or floating patch textures in the Blueprint preview.
    return True


def should_apply_distortion(emitter: dict[str, Any], distortion_path: str | None) -> bool:
    if not distortion_path or not Path(distortion_path).exists():
        return False
    return emitter.get("role") in {"fire_pillar", "flame_slashes", "atmospheric_wisp", "primary_bolt", "secondary_bolts"}


def should_apply_volume_material_passes(emitter: dict[str, Any]) -> bool:
    return emitter.get("role") in {"fire_pillar", "flame_slashes", "atmospheric_wisp", "primary_body", "secondary_body"}


def quality_note_for_selected_asset(selected: dict[str, str] | None) -> str:
    if not selected:
        return "missing_required_generation_or_assignment"
    if selected.get("source") == "derived_reference_bootstrap":
        return "bootstrap_only_replace_with_ai_or_simulation_for_final_aaa_quality"
    if selected.get("source") in {"reference_extraction", "reference_layer_extraction"}:
        return "reference_extraction_useful_for_match_but_should_be_rebuilt_as_editable_layers"
    if str(selected.get("role") or "").startswith("procedural_short_burst_"):
        return "short_burst_procedural_cell_available_replace_with_sim_or_ai_for_final_quality"
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
    suffixes = IMAGE_SUFFIXES | ANIMATED_SUFFIXES | METADATA_SUFFIXES
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
        "depth_or_thickness": ["depth", "thickness", "volume", "height"],
        "layer_mask_pack": ["layer_mask", "mask_pack", "packed_mask", "core_edge", "masks"],
        "sdf_or_vector_field": ["sdf", "distance", "vector_field", "field", "curl"],
        "renderer_layout_metadata": ["metadata", "layout", "atlas_meta", "renderer"],
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
    if "layer" in name and "mask" in name:
        return ["layer", "mask_pack", "packed_mask", "core_edge", "rgba_mask"]
    if "metadata" in name or "layout" in name:
        return ["metadata", "layout", "atlas_meta", "renderer"]
    if "alpha" in name or "mask" in name:
        return ["alpha", "mask", "matte", "opacity"]
    if "motion" in name:
        return ["motion", "vector", "velocity", "mv"]
    if "distortion" in name or "flow" in name:
        return ["distort", "flow", "heat", "normal"]
    if "depth" in name or "thickness" in name:
        return ["depth", "thickness", "height", "volume"]
    if "normal" in name or "lighting" in name:
        return ["normal", "lighting", "light", "sixpoint"]
    if "sdf" in name or "vector_field" in name or "field" in name:
        return ["sdf", "distance", "vector_field", "field", "curl"]
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
        "Name the output with this exact pass name so the importer can classify it. "
        "If this is a data pass, keep it clean and non-beauty: no baked background, no text, no watermark, no atlas grid lines. "
        "Use clean alpha, centered composition, and consistent pivot/bounds across all passes. "
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
