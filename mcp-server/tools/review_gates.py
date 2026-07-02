from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.analyze_packages import analyze_effect_package
from tools.asset_passes import apply_asset_pass_manifest_to_spec_dict, build_asset_pass_manifest


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def review_effect_package(package_path: Path, destination_path: str | None = None) -> dict[str, Any]:
    package_path = resolve_from_workspace(package_path)
    spec = analyze_effect_package(package_path)
    manifest = build_asset_pass_manifest(package_path)
    patched_spec = apply_asset_pass_manifest_to_spec_dict(spec.to_dict(), manifest)
    unreal_result = read_latest_unreal_result(spec.name)
    gates = [
        gate_required_passes(manifest),
        gate_similarity_target(manifest),
        gate_fire_pass_coverage(spec.effect_type, manifest),
        gate_layer_timing(patched_spec, unreal_result),
        gate_distortion_pass_link(patched_spec, manifest),
        gate_reference_matched_anchor(patched_spec, manifest, unreal_result),
        gate_production_preview(patched_spec, unreal_result),
        gate_preview_component_contract(patched_spec, unreal_result),
        gate_unreal_material_creation(unreal_result),
        gate_fire_spatial_design(patched_spec, unreal_result),
        gate_firestorm_3d_volume_preview(patched_spec, unreal_result),
        gate_firestorm_visual_balance(patched_spec),
        gate_alpha_mask_applied(patched_spec, manifest),
        gate_reference_overlay_not_primary(patched_spec, unreal_result),
        gate_texture_card_budget(patched_spec, manifest, unreal_result),
        gate_unreal_generation(unreal_result),
        gate_source_asset_contract(manifest),
        gate_asset_pass_validation(manifest),
        gate_bootstrap_quality(manifest),
    ]
    passed = [gate for gate in gates if gate["status"] == "pass"]
    warnings = [gate for gate in gates if gate["status"] == "warning"]
    failed = [gate for gate in gates if gate["status"] == "fail"]
    return {
        "package": package_path.name,
        "destinationPath": destination_path or f"/Game/VFX/Generated/{package_path.name}",
        "summary": {
            "score": round((len(passed) + len(warnings) * 0.5) / max(len(gates), 1), 2),
            "passed": len(passed),
            "warnings": len(warnings),
            "failed": len(failed),
            "status": "pass" if not failed else "needs_iteration",
        },
        "gates": gates,
        "assetPassManifest": {
            "manifest_path": manifest.get("manifest_path"),
            "summary": manifest.get("summary"),
        },
        "unrealResultFile": str(latest_unreal_result_path(spec.name)) if latest_unreal_result_path(spec.name).exists() else None,
    }


def gate_required_passes(manifest: dict[str, Any]) -> dict[str, Any]:
    summary = manifest.get("summary") or {}
    missing = int(summary.get("missing_required_passes") or 0)
    return {
        "name": "required_asset_passes",
        "status": "pass" if missing == 0 else "fail",
        "message": "All required asset passes have candidates." if missing == 0 else f"{missing} required asset pass(es) are missing.",
        "data": summary,
    }


def gate_similarity_target(manifest: dict[str, Any]) -> dict[str, Any]:
    report = manifest.get("similarity_report") or {}
    if not manifest.get("reference_media") and not report:
        return {
            "name": "reference_similarity_80",
            "status": "pass",
            "message": "No reference target was provided; procedural showcase packages skip the similarity gate.",
            "data": {"target": 0.8, "score": None, "report_status": "skipped_no_reference"},
        }
    score = (report.get("score") or {}).get("overall")
    alpha = report.get("alpha") or {}
    opaque_card_risk = bool(alpha.get("opaque_card_risk"))
    ok = isinstance(score, (int, float)) and float(score) >= 0.8 and not opaque_card_risk
    return {
        "name": "reference_similarity_80",
        "status": "pass" if ok else "fail",
        "message": "Local composited preview reached the 0.80 similarity target without opaque-card risk." if ok else "Local composited preview is below target or risks rendering as an opaque card.",
        "data": {
            "target": report.get("target", 0.8),
            "score": report.get("score"),
            "alpha": alpha,
            "preview": report.get("preview"),
            "target_reference": report.get("target_reference"),
            "report_status": report.get("status"),
        },
    }


def gate_fire_pass_coverage(effect_type: str, manifest: dict[str, Any]) -> dict[str, Any]:
    if effect_type != "fire_or_flame":
        return {
            "name": "fire_production_pass_coverage",
            "status": "pass",
            "message": "Not a fire package.",
            "data": {},
        }
    required = {
        "core_flame_flipbook",
        "smoke_heat_flipbook",
        "ground_ring_mask",
        "flame_slash_flipbook",
        "impact_flash_mask",
        "ember_sprite_set",
    }
    ready = {
        entry.get("name")
        for entry in manifest.get("passes", [])
        if entry.get("name") in required and entry.get("status") == "ready"
    }
    missing = sorted(required - ready)
    return {
        "name": "fire_production_pass_coverage",
        "status": "pass" if not missing else "fail",
        "message": "Fire package has the required production layer passes." if not missing else f"Fire package is missing production layer passes: {', '.join(missing)}",
        "data": {"ready": sorted(ready), "missing": missing},
    }


def gate_layer_timing(spec: dict[str, Any], unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    plan = spec.get("vfx_plan") or {}
    emitters = [
        emitter for emitter in plan.get("emitters", [])
        if emitter.get("role") != "reference_motion"
    ]
    timed_emitters = [
        emitter.get("name")
        for emitter in emitters
        if isinstance(((emitter.get("unreal_settings") or {}).get("timeline")), dict)
        and ((emitter.get("unreal_settings") or {}).get("timeline") or {}).get("duration")
    ]
    component_timelines = [
        component.get("name")
        for component in preview_components(unreal_result)
        if (component.get("timeline") or {}).get("duration")
    ]
    ok = len(timed_emitters) >= min(len(emitters), 5) and len(component_timelines) >= min(len(emitters), 4)
    return {
        "name": "layer_timing_design",
        "status": "pass" if ok else "fail",
        "message": "Production layers have explicit timing metadata." if ok else "Production layers are still missing timing metadata.",
        "data": {"timed_emitters": timed_emitters, "component_timelines": component_timelines},
    }


def gate_distortion_pass_link(spec: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    distortion_ready = any(entry.get("name") == "distortion_flow" and entry.get("status") == "ready" for entry in manifest.get("passes", []))
    distortion_emitters = []
    for emitter in ((spec.get("vfx_plan") or {}).get("emitters") or []):
        material = ((emitter.get("unreal_settings") or {}).get("material") or {})
        if material.get("distortion_source"):
            distortion_emitters.append(emitter.get("name"))
    ok = distortion_ready and bool(distortion_emitters)
    return {
        "name": "distortion_flow_material_link",
        "status": "pass" if ok else "warning",
        "message": "Distortion flow is available and linked into material settings." if ok else "Distortion flow is missing or not linked into material settings.",
        "data": {"distortion_ready": distortion_ready, "distortion_emitters": distortion_emitters},
    }


def gate_reference_matched_anchor(spec: dict[str, Any], manifest: dict[str, Any], unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    if not manifest.get("reference_media"):
        return {
            "name": "reference_matched_viewport_anchor",
            "status": "pass",
            "message": "No reference media was provided; procedural showcase preview does not require a reference-matched anchor.",
            "data": {"has_emitter": False, "components": [], "caveat": "Use visual review instead of reference matching for this package."},
        }
    emitters = ((spec.get("vfx_plan") or {}).get("emitters") or [])
    has_emitter = any(emitter.get("role") == "reference_matched_composite" for emitter in emitters)
    composite_entry = next((entry for entry in manifest.get("passes", []) if entry.get("name") == "reference_matched_composite"), {})
    composite_atlas = (composite_entry.get("asset_metadata") or {}).get("atlas")
    components = [
        component.get("name")
        for component in preview_components(unreal_result)
        if "reference_matched_composite" in str(component.get("name", ""))
    ]
    ok = has_emitter and not composite_atlas
    return {
        "name": "reference_matched_viewport_anchor",
        "status": "warning" if ok else "fail",
        "message": (
            "Viewport includes a reference-matched fidelity anchor; use it as a temporary visual target while improving procedural layers."
            if ok
            else "Viewport fidelity anchor is missing or incorrectly configured as a flipbook atlas."
        ),
        "data": {
            "has_emitter": has_emitter,
            "composite_atlas": composite_atlas,
            "components": components,
            "caveat": "This improves visual similarity but is not a final fully procedural AAA effect.",
        },
    }


def gate_production_preview(spec: dict[str, Any], unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    plan = spec.get("vfx_plan") or {}
    components = preview_components(unreal_result)
    production_components = [
        component for component in components
        if "reference_motion" not in str(component.get("name", ""))
    ]
    ok = plan.get("preview_mode") == "production_layers" and len(production_components) >= 3
    return {
        "name": "production_layer_preview",
        "status": "pass" if ok else "fail",
        "message": "Blueprint preview is using production layers." if ok else "Preview is still not a production-layer composite.",
        "data": {
            "preview_mode": plan.get("preview_mode"),
            "component_count": len(components),
            "production_component_count": len(production_components),
            "component_names": [component.get("name") for component in components],
        },
    }


def gate_preview_component_contract(spec: dict[str, Any], unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    components = preview_components(unreal_result)
    issues = []
    checked = []
    for emitter in ((spec.get("vfx_plan") or {}).get("emitters") or []):
        name = str(emitter.get("name") or "")
        if not name:
            continue
        settings = emitter.get("unreal_settings") or {}
        preview = settings.get("preview") or {}
        card = preview.get("card") or {}
        niagara = preview.get("niagara") or {}
        if card.get("enabled") is not False and card:
            component = matching_component(components, name, "StaticMeshComponent")
            if not component:
                issues.append({"emitter": name, "type": "missing_card_component"})
            else:
                issues.extend(compare_transform(name, "card", card, component.get("transform") or {}))
                issues.extend(compare_timeline(name, emitter, component.get("timeline") or {}))
                issues.extend(check_role_material_expectations(emitter))
                checked.append({"emitter": name, "component": component.get("name"), "kind": "card"})
        if niagara.get("enabled") is True:
            component = matching_component(components, name, "NiagaraComponent")
            if not component:
                issues.append({"emitter": name, "type": "missing_niagara_component"})
            else:
                issues.extend(compare_transform(name, "niagara", niagara, component.get("transform") or {}))
                issues.extend(compare_timeline(name, emitter, component.get("timeline") or {}))
                checked.append({"emitter": name, "component": component.get("name"), "kind": "niagara"})
    ok = not issues
    return {
        "name": "preview_component_contract",
        "status": "pass" if ok else "fail",
        "message": "Preview components are present and match the expected transforms/material ranges." if ok else "Preview components do not match the expected placement or parameter contract.",
        "data": {"checked": checked, "issues": issues},
    }


def gate_unreal_material_creation(unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    systems = (((unreal_result or {}).get("bundle") or {}).get("systems") or [])
    issues = []
    for system in systems:
        materials = system.get("materials") or {}
        if not materials:
            continue
        if materials.get("created") is not True or materials.get("errors"):
            issues.append(
                {
                    "system": system.get("asset_path"),
                    "material_path": materials.get("material_path"),
                    "material_instance_path": materials.get("material_instance_path"),
                    "created": materials.get("created"),
                    "errors": materials.get("errors") or [],
                }
            )
    preview_errors = (((unreal_result or {}).get("bundle") or {}).get("preview") or {}).get("errors") or []
    ok = not issues and not preview_errors
    return {
        "name": "unreal_material_creation",
        "status": "pass" if ok else "fail",
        "message": "Unreal material assets were created and preview components have valid material instances." if ok else "One or more Unreal materials failed, which can produce grey checker preview geometry.",
        "data": {
            "material_issues": issues,
            "preview_errors": preview_errors,
        },
    }


def gate_fire_spatial_design(spec: dict[str, Any], unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    if spec.get("effect_type") != "fire_or_flame":
        return {
            "name": "fire_spatial_design",
            "status": "pass",
            "message": "Not a fire package.",
            "data": {},
        }
    components = preview_components(unreal_result)
    issues = []
    emitters = ((spec.get("vfx_plan") or {}).get("emitters") or [])
    hidden_emitters = {
        str(emitter.get("name") or "")
        for emitter in emitters
        if (((emitter.get("unreal_settings") or {}).get("preview") or {}).get("card") or {}).get("enabled") is False
        and (((emitter.get("unreal_settings") or {}).get("preview") or {}).get("niagara") or {}).get("enabled") is not True
    }
    firestorm_preview = str(spec.get("name", "")).lower() == "firestorm" or any(
        "firestorm" in str(emitter.get("motion", "")).lower()
        or "firestorm" in str(((emitter.get("unreal_settings") or {}).get("material") or {}).get("style", "")).lower()
        for emitter in emitters
    )
    expected_bands = {
        "ground_rune_ring": {"z": (0, 8), "scale_xy_max": 2.6, "component_type": "StaticMeshComponent"},
        "impact_flash": {"z": (10, 34), "scale_xy_max": 1.2, "component_type": "StaticMeshComponent"},
        "side_flame_slashes": {"z": (34, 70), "scale_xy_max": 1.9, "component_type": "StaticMeshComponent"},
        "smoke_dust_crown": {"z": (36, 76), "scale_xy_max": 1.6, "component_type": "StaticMeshComponent"},
        "central_fire_pillar": {"z": (82, 126), "scale_xy_max": 1.8, "component_type": "StaticMeshComponent"},
        "ember_sparks": {"z": (56, 110), "scale_xy_max": 0.8, "component_type": "NiagaraComponent"},
    }
    if firestorm_preview:
        expected_bands["ember_sparks"] = {"z": (56, 110), "scale_xy_max": 0.9, "component_type": "StaticMeshComponent"}
    for emitter_name, rule in expected_bands.items():
        if firestorm_preview and emitter_name in hidden_emitters:
            continue
        component = matching_component(components, emitter_name, str(rule["component_type"]))
        if not component:
            issues.append({"emitter": emitter_name, "type": "missing_component_for_fire_spatial_design"})
            continue
        transform = component.get("transform") or {}
        location = transform.get("location") or []
        scale = transform.get("scale") or []
        z_value = safe_float(location[2]) if len(location) >= 3 else None
        minimum, maximum = rule["z"]
        if z_value is None or z_value < minimum or z_value > maximum:
            issues.append({"emitter": emitter_name, "type": "z_band_mismatch", "expected": [minimum, maximum], "actual": z_value})
        if len(scale) >= 2:
            max_scale = max(abs(float(scale[0])), abs(float(scale[1])))
            if max_scale > float(rule["scale_xy_max"]):
                issues.append({"emitter": emitter_name, "type": "scale_too_large_for_fire_spatial_design", "expected_max": rule["scale_xy_max"], "actual": round(max_scale, 3)})
    reference_component = next((component for component in components if "reference_matched_composite" in str(component.get("name") or "")), None)
    if reference_component:
        issues.append(
            {
                "emitter": "reference_matched_composite",
                "type": "reference_anchor_visible_in_production_preview",
                "reason": "The similarity anchor should be hidden or debug-only so it cannot shift the authored effect read.",
            }
        )
    return {
        "name": "fire_spatial_design",
        "status": "pass" if not issues else "fail",
        "message": "Fire preview layers sit in the expected ground, impact, flame, smoke, and ember height bands." if not issues else "Fire preview layers are not arranged in the expected spatial bands.",
        "data": {"issues": issues, "expected_bands": expected_bands},
    }


def gate_firestorm_3d_volume_preview(spec: dict[str, Any], unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    emitters = ((spec.get("vfx_plan") or {}).get("emitters") or [])
    is_firestorm = str(spec.get("name", "")).lower() == "firestorm" or any(
        "firestorm" in str(emitter.get("motion", "")).lower()
        or "firestorm" in str(((emitter.get("unreal_settings") or {}).get("material") or {}).get("style", "")).lower()
        for emitter in emitters
    )
    if spec.get("effect_type") != "fire_or_flame" or not is_firestorm:
        return {
            "name": "firestorm_3d_volume_preview",
            "status": "pass",
            "message": "Not a firestorm package.",
            "data": {},
        }

    components = preview_components(unreal_result)
    volume_components = [
        component for component in components
        if component.get("type") == "StaticMeshComponent" and str(component.get("name") or "").startswith("VolumeMesh_")
    ]
    required_emitters = {
        "ground_rune_ring",
        "central_fire_pillar",
        "side_flame_slashes",
        "back_spiral_flame_wall",
        "smoke_dust_crown",
    }
    covered_emitters = {
        emitter_name
        for emitter_name in required_emitters
        if any(emitter_name in str(component.get("name") or "") for component in volume_components)
    }
    missing = sorted(required_emitters - covered_emitters)
    ok = len(volume_components) >= 12 and not missing
    return {
        "name": "firestorm_3d_volume_preview",
        "status": "pass" if ok else "fail",
        "message": "Firestorm preview includes 3D volume mesh shells for the vortex, core, flame walls, and smoke crown." if ok else "Firestorm preview is still too dependent on 2D cards.",
        "data": {
            "volume_mesh_count": len(volume_components),
            "required_emitters": sorted(required_emitters),
            "covered_emitters": sorted(covered_emitters),
            "missing_emitters": missing,
            "volume_component_names": [component.get("name") for component in volume_components],
        },
    }


def gate_firestorm_visual_balance(spec: dict[str, Any]) -> dict[str, Any]:
    emitters = ((spec.get("vfx_plan") or {}).get("emitters") or [])
    if spec.get("effect_type") != "fire_or_flame" or not any(is_firestorm_emitter(emitter) for emitter in emitters):
        return {
            "name": "firestorm_visual_balance",
            "status": "pass",
            "message": "Not a firestorm package.",
            "data": {},
        }

    issues = []
    expected = {
        "central_fire_pillar": {"max_card_instances": 2, "opacity": (0.32, 0.56), "emissive": (5.0, 11.0), "max_scale_xy": 1.15},
        "side_flame_slashes": {"max_card_instances": 2, "opacity": (0.32, 0.58), "emissive": (5.0, 10.5), "max_scale_xy": 1.35},
        "back_spiral_flame_wall": {"max_card_instances": 2, "opacity": (0.32, 0.58), "emissive": (5.0, 10.5), "max_scale_xy": 1.35},
        "ground_rune_ring": {"max_card_instances": 0, "opacity": (0.3, 0.62), "emissive": (3.0, 8.0), "max_scale_xy": 1.9},
        "impact_flash": {"max_card_instances": 1, "opacity": (0.25, 0.7), "emissive": (4.0, 12.0), "max_scale_xy": 0.9},
    }
    by_name = {str(emitter.get("name") or ""): emitter for emitter in emitters}
    for emitter_name, rule in expected.items():
        emitter = by_name.get(emitter_name)
        if not emitter:
            issues.append({"emitter": emitter_name, "type": "missing_firestorm_balance_emitter"})
            continue
        settings = emitter.get("unreal_settings") or {}
        material = settings.get("material") or {}
        card = ((settings.get("preview") or {}).get("card") or {})
        opacity = safe_float(material.get("opacity"))
        emissive = safe_float(material.get("emissive_strength"))
        issues.extend(expect_range(emitter_name, "opacity", opacity, *rule["opacity"]))
        issues.extend(expect_range(emitter_name, "emissive_strength", emissive, *rule["emissive"]))
        instance_count = len(card.get("instances") or [])
        if instance_count > int(rule["max_card_instances"]):
            issues.append({"emitter": emitter_name, "type": "too_many_firestorm_card_instances", "expected_max": rule["max_card_instances"], "actual": instance_count})
        scale = card.get("scale") or []
        if len(scale) >= 2:
            max_scale = max(abs(float(scale[0])), abs(float(scale[1])))
            if max_scale > float(rule["max_scale_xy"]):
                issues.append({"emitter": emitter_name, "type": "firestorm_card_scale_too_large", "expected_max": rule["max_scale_xy"], "actual": round(max_scale, 3)})

    return {
        "name": "firestorm_visual_balance",
        "status": "pass" if not issues else "fail",
        "message": "Firestorm preview keeps emissive intensity, card count, and ground footprint restrained." if not issues else "Firestorm preview is likely to read as an overbright card pile or spike burst.",
        "data": {"issues": issues},
    }


def gate_alpha_mask_applied(spec: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    alpha_ready = any(entry.get("name") == "alpha_mask" and entry.get("status") == "ready" for entry in manifest.get("passes", []))
    alpha_emitters = []
    for emitter in ((spec.get("vfx_plan") or {}).get("emitters") or []):
        material = ((emitter.get("unreal_settings") or {}).get("material") or {})
        if material.get("alpha_source"):
            alpha_emitters.append(emitter.get("name"))
    ok = alpha_ready and bool(alpha_emitters)
    return {
        "name": "alpha_mask_material_link",
        "status": "pass" if ok else "warning",
        "message": "Alpha mask is linked into material settings." if ok else "Alpha mask exists but is not linked into material settings.",
        "data": {"alpha_ready": alpha_ready, "alpha_emitters": alpha_emitters},
    }


def gate_reference_overlay_not_primary(spec: dict[str, Any], unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    plan = spec.get("vfx_plan") or {}
    components = preview_components(unreal_result)
    reference_components = [component.get("name") for component in components if "reference_motion" in str(component.get("name", ""))]
    ok = plan.get("primary_emitter") != "reference_motion_flipbook" and not reference_components
    return {
        "name": "reference_overlay_not_primary",
        "status": "pass" if ok else "fail",
        "message": "Reference flipbook is not driving the preview." if ok else "Reference flipbook still appears to be the primary preview read.",
        "data": {"primary_emitter": plan.get("primary_emitter"), "reference_components": reference_components},
    }


def gate_texture_card_budget(spec: dict[str, Any], manifest: dict[str, Any], unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    pass_issues = []
    budgets_by_pass = {
        entry.get("name"): entry.get("runtime_budget") or {}
        for entry in manifest.get("passes", [])
    }
    for entry in manifest.get("passes", []):
        metadata = entry.get("asset_metadata") or {}
        budget = entry.get("runtime_budget") or {}
        max_edge = int(budget.get("max_import_edge") or 4096)
        width = int(metadata.get("width") or 0)
        height = int(metadata.get("height") or 0)
        if width and height and max(width, height) > max_edge:
            pass_issues.append(
                {
                    "type": "texture_edge",
                    "pass": entry.get("name"),
                    "size": [width, height],
                    "max_import_edge": max_edge,
                    "asset": (entry.get("selected_asset") or {}).get("path"),
                }
            )

    component_issues = []
    components = preview_components(unreal_result)
    emitters = ((spec.get("vfx_plan") or {}).get("emitters") or [])
    playback_mode = (((spec.get("vfx_plan") or {}).get("playback") or {}).get("mode") or "")
    for emitter in emitters:
        pass_name = asset_pass_for_emitter(spec.get("effect_type"), emitter)
        material = ((emitter.get("unreal_settings") or {}).get("material") or {})
        preview_card = (((emitter.get("unreal_settings") or {}).get("preview") or {}).get("card") or {})
        if preview_card.get("enabled") is not False and material.get("flipbook") and playback_mode != "material_flipbook":
            component_issues.append(
                {
                    "type": "preview_card_uses_flipbook_atlas",
                    "emitter": emitter.get("name"),
                    "pass": pass_name,
                    "reason": "Blueprint preview cards must use a single clean frame; atlas playback belongs in Niagara/material animation only.",
                }
            )
        budget = budgets_by_pass.get(pass_name) or role_budget_for_emitter(emitter)
        max_scale = float(budget.get("max_preview_scale") or 99.0)
        max_area = float(budget.get("max_card_area") or 999.0)
        emitter_name = str(emitter.get("name") or "")
        for component in components:
            component_name = str(component.get("name") or "")
            if emitter_name not in component_name:
                continue
            transform = component.get("transform") or {}
            scale = transform.get("scale") or []
            if len(scale) < 2:
                continue
            sx = abs(float(scale[0]))
            sy = abs(float(scale[1]))
            area = sx * sy
            if max(sx, sy) > max_scale or area > max_area:
                component_issues.append(
                    {
                        "type": "preview_card_scale",
                        "component": component_name,
                        "pass": pass_name,
                        "scale": [round(sx, 3), round(sy, 3)],
                        "area": round(area, 3),
                        "max_preview_scale": max_scale,
                        "max_card_area": max_area,
                    }
                )

    ok = not pass_issues and not component_issues
    return {
        "name": "texture_card_budget",
        "status": "pass" if ok else "fail",
        "message": (
            "Runtime textures and preview cards stay within the VFX size budget."
            if ok
            else "One or more textures/cards are too large and will read as ugly billboard sheets."
        ),
        "data": {
            "texture_issues": pass_issues,
            "component_issues": component_issues,
        },
    }


def gate_unreal_generation(unreal_result: dict[str, Any] | None) -> dict[str, Any]:
    ok = bool(unreal_result and unreal_result.get("status") == "created_bundle")
    return {
        "name": "unreal_generation_result",
        "status": "pass" if ok else "warning",
        "message": "Latest Unreal generation result exists and created a bundle." if ok else "No successful Unreal generation result was found.",
        "data": {"status": unreal_result.get("status") if unreal_result else None, "asset_path": unreal_result.get("asset_path") if unreal_result else None},
    }


def gate_bootstrap_quality(manifest: dict[str, Any]) -> dict[str, Any]:
    bootstrap = [
        entry.get("name")
        for entry in manifest.get("passes", [])
        if entry.get("required") and (entry.get("selected_asset") or {}).get("source") == "derived_reference_bootstrap"
    ]
    optional_bootstrap = [
        entry.get("name")
        for entry in manifest.get("passes", [])
        if not entry.get("required") and (entry.get("selected_asset") or {}).get("source") == "derived_reference_bootstrap"
    ]
    status = "fail" if bootstrap else ("warning" if optional_bootstrap else "pass")
    return {
        "name": "final_quality_assets",
        "status": status,
        "message": (
            "Required passes are still bootstrap derivations; replace them with manual, AI, or simulation passes for final AAA quality."
            if bootstrap
            else ("Only optional passes are bootstrap derivations." if optional_bootstrap else "No bootstrap pass is selected.")
        ),
        "data": {"required_bootstrap_passes": bootstrap, "optional_bootstrap_passes": optional_bootstrap},
    }


def gate_source_asset_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    entries = {entry.get("name"): entry for entry in manifest.get("passes", [])}
    minimum = {"beauty_flipbook", "alpha_mask", "layer_mask_pack", "renderer_layout_metadata"}
    production = {
        "motion_vectors",
        "distortion_flow",
        "depth_or_thickness",
        "normal_or_lighting",
        "sdf_or_vector_field",
    }
    ready_minimum = sorted(name for name in minimum if (entries.get(name) or {}).get("status") == "ready")
    missing_minimum = sorted(minimum - set(ready_minimum))
    ready_production = sorted(name for name in production if (entries.get(name) or {}).get("status") == "ready")
    missing_production = sorted(production - set(ready_production))
    bootstrap_sources = {
        name: (entries.get(name, {}).get("selected_asset") or {}).get("source")
        for name in sorted(minimum | production)
        if (entries.get(name, {}).get("selected_asset") or {}).get("source")
        in {"derived_reference_bootstrap", "reference_layer_extraction", "procedural_layer_synthesis", "reference_matched_composite"}
    }
    if missing_minimum:
        status = "fail"
        message = "The source asset contract is missing minimum passes; the effect will collapse into beauty-card/blockout quality."
    elif len(ready_production) < 3 or bootstrap_sources:
        status = "warning"
        message = "The source asset contract is structurally present, but production data passes still need AI/simulation-quality replacements."
    else:
        status = "pass"
        message = "The source asset contract includes minimum and advanced production data passes."
    return {
        "name": "source_asset_contract",
        "status": status,
        "message": message,
        "data": {
            "minimum": sorted(minimum),
            "ready_minimum": ready_minimum,
            "missing_minimum": missing_minimum,
            "production_quality": sorted(production),
            "ready_production": ready_production,
            "missing_production": missing_production,
            "bootstrap_or_reference_sources": bootstrap_sources,
            "expectation": "Final AAA quality needs authored or simulated pass bundles, not only derived bootstrap maps.",
        },
    }


def gate_asset_pass_validation(manifest: dict[str, Any]) -> dict[str, Any]:
    contract = manifest.get("production_contract") or {}
    failed = []
    warnings = []
    for entry in manifest.get("passes", []):
        validation = entry.get("validation") or {}
        if validation.get("status") == "fail":
            failed.append(
                {
                    "pass": entry.get("name"),
                    "issues": validation.get("issues") or [],
                    "asset": (entry.get("selected_asset") or {}).get("path"),
                }
            )
        elif validation.get("status") == "warning":
            warnings.append(
                {
                    "pass": entry.get("name"),
                    "warnings": validation.get("warnings") or [],
                    "asset": (entry.get("selected_asset") or {}).get("path"),
                }
            )
    if failed:
        status = "fail"
        message = "One or more selected asset passes violate the production pass contract."
    elif contract.get("status") != "pass" or warnings:
        status = "warning"
        message = "Selected passes are usable for preview, but still need production-quality AI/simulation replacements or metadata."
    else:
        status = "pass"
        message = "Selected asset passes satisfy the production pass contract."
    return {
        "name": "asset_pass_validation",
        "status": status,
        "message": message,
        "data": {
            "production_contract": contract,
            "failed": failed,
            "warnings": warnings,
        },
    }


def preview_components(unreal_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not unreal_result:
        return []
    bundle = unreal_result.get("bundle") or {}
    preview = bundle.get("preview") or {}
    return preview.get("components") or []


def matching_component(components: list[dict[str, Any]], emitter_name: str, component_type: str) -> dict[str, Any] | None:
    return next(
        (
            component
            for component in components
            if component.get("type") == component_type and emitter_name in str(component.get("name") or "")
        ),
        None,
    )


def compare_transform(emitter_name: str, kind: str, expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    issues = []
    for key, tolerance in {"location": 0.01, "rotation": 0.01, "scale": 0.01}.items():
        expected_value = expected.get(key)
        actual_value = actual.get(key)
        if expected_value is None:
            continue
        if not vector_close(expected_value, actual_value, tolerance):
            issues.append(
                {
                    "emitter": emitter_name,
                    "type": f"{kind}_{key}_mismatch",
                    "expected": list(expected_value) if isinstance(expected_value, (list, tuple)) else expected_value,
                    "actual": list(actual_value) if isinstance(actual_value, (list, tuple)) else actual_value,
                }
            )
    return issues


def compare_timeline(emitter_name: str, emitter: dict[str, Any], actual_timeline: dict[str, Any]) -> list[dict[str, Any]]:
    expected = ((emitter.get("unreal_settings") or {}).get("timeline") or {})
    issues = []
    if not expected:
        return issues
    for key in ("delay", "duration", "rotation_speed"):
        if key in expected and not scalar_close(expected.get(key), actual_timeline.get(key), 0.001):
            issues.append({"emitter": emitter_name, "type": f"timeline_{key}_mismatch", "expected": expected.get(key), "actual": actual_timeline.get(key)})
    for key in ("opacity", "scale"):
        if key in expected and not vector_close(expected.get(key), actual_timeline.get(key), 0.001):
            issues.append({"emitter": emitter_name, "type": f"timeline_{key}_mismatch", "expected": expected.get(key), "actual": actual_timeline.get(key)})
    return issues


def check_role_material_expectations(emitter: dict[str, Any]) -> list[dict[str, Any]]:
    role = emitter.get("role")
    name = emitter.get("name")
    material = ((emitter.get("unreal_settings") or {}).get("material") or {})
    issues = []
    opacity = safe_float(material.get("opacity"))
    emissive = safe_float(material.get("emissive_strength"))
    blend = material.get("blend_mode")
    is_firestorm = is_firestorm_emitter(emitter)
    if role == "reference_matched_composite":
        issues.extend(expect_range(name, "opacity", opacity, 0.0, 0.4))
        issues.extend(expect_range(name, "emissive_strength", emissive, 0.5, 3.0))
    elif role == "fire_pillar":
        if is_firestorm:
            issues.extend(expect_range(name, "opacity", opacity, 0.32, 0.62))
            issues.extend(expect_range(name, "emissive_strength", emissive, 5.0, 12.0))
        else:
            issues.extend(expect_range(name, "opacity", opacity, 0.7, 0.95))
            issues.extend(expect_range(name, "emissive_strength", emissive, 14.0, 28.0))
    elif role == "flame_slashes":
        if is_firestorm:
            issues.extend(expect_range(name, "opacity", opacity, 0.32, 0.62))
            issues.extend(expect_range(name, "emissive_strength", emissive, 5.0, 12.0))
        else:
            issues.extend(expect_range(name, "opacity", opacity, 0.45, 0.75))
            issues.extend(expect_range(name, "emissive_strength", emissive, 7.0, 16.0))
    elif role == "ground_energy_ring":
        issues.extend(expect_range(name, "opacity", opacity, 0.3, 0.85))
    elif role == "impact_core":
        if is_firestorm:
            issues.extend(expect_range(name, "opacity", opacity, 0.25, 0.7))
            issues.extend(expect_range(name, "emissive_strength", emissive, 4.0, 12.0))
        else:
            issues.extend(expect_range(name, "opacity", opacity, 0.75, 1.0))
            issues.extend(expect_range(name, "emissive_strength", emissive, 16.0, 32.0))
    elif role == "atmospheric_wisp":
        issues.extend(expect_range(name, "opacity", opacity, 0.0, 0.12))
        issues.extend(expect_range(name, "emissive_strength", emissive, 0.0, 0.35))
        if blend != "translucent":
            issues.append({"emitter": name, "type": "material_blend_mismatch", "expected": "translucent", "actual": blend})
    if role not in {"atmospheric_wisp"} and material and blend not in {None, "additive"}:
        issues.append({"emitter": name, "type": "material_blend_mismatch", "expected": "additive", "actual": blend})
    return issues


def is_firestorm_emitter(emitter: dict[str, Any]) -> bool:
    material = ((emitter.get("unreal_settings") or {}).get("material") or {})
    text = " ".join(
        str(value or "")
        for value in (
            emitter.get("name"),
            emitter.get("motion"),
            emitter.get("material_style"),
            emitter.get("sprite_shape"),
            material.get("style"),
        )
    ).lower()
    return "firestorm" in text or str(emitter.get("name") or "") in {
        "central_fire_pillar",
        "side_flame_slashes",
        "back_spiral_flame_wall",
        "ground_rune_ring",
        "impact_flash",
        "smoke_dust_crown",
        "ember_sparks",
    }


def expect_range(emitter_name: str, field: str, value: float | None, minimum: float, maximum: float) -> list[dict[str, Any]]:
    if value is None or value < minimum or value > maximum:
        return [{"emitter": emitter_name, "type": f"material_{field}_out_of_range", "expected": [minimum, maximum], "actual": value}]
    return []


def scalar_close(expected: Any, actual: Any, tolerance: float) -> bool:
    try:
        return abs(float(expected) - float(actual)) <= tolerance
    except (TypeError, ValueError):
        return False


def vector_close(expected: Any, actual: Any, tolerance: float) -> bool:
    if not isinstance(expected, (list, tuple)) or not isinstance(actual, (list, tuple)):
        return False
    if len(expected) != len(actual):
        return False
    return all(scalar_close(left, right, tolerance) for left, right in zip(expected, actual))


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def asset_pass_for_emitter(effect_type: str | None, emitter: dict[str, Any]) -> str | None:
    role = emitter.get("role")
    if effect_type == "fire_or_flame":
        mapping = {
            "reference_matched_composite": "reference_matched_composite",
            "reference_motion": "reference_motion_overlay",
            "fire_pillar": "core_flame_flipbook",
            "flame_slashes": "flame_slash_flipbook",
            "ground_energy_ring": "ground_ring_mask",
            "impact_core": "impact_flash_mask",
            "atmospheric_wisp": "smoke_heat_flipbook",
            "detail_particles": "ember_sprite_set",
        }
        return mapping.get(role)
    if effect_type == "electric_arc":
        if role in {"primary_bolt", "secondary_bolts"}:
            return "bolt_branch_set"
        if role == "impact_core":
            return "impact_flash_mask"
    return None


def role_budget_for_emitter(emitter: dict[str, Any]) -> dict[str, Any]:
    role = emitter.get("role")
    if role in {"reference_motion", "reference_matched_composite"}:
        return {"max_preview_scale": 1.6, "max_card_area": 2.8}
    if role in {"fire_pillar", "primary_bolt"}:
        return {"max_preview_scale": 2.2, "max_card_area": 3.8}
    if role in {"flame_slashes", "secondary_bolts"}:
        return {"max_preview_scale": 2.0, "max_card_area": 3.2}
    if role in {"ground_energy_ring", "supporting_glow"}:
        return {"max_preview_scale": 2.6, "max_card_area": 6.8}
    if role == "impact_core":
        return {"max_preview_scale": 1.4, "max_card_area": 1.8}
    return {"max_preview_scale": 1.8, "max_card_area": 3.0}


def read_latest_unreal_result(effect_name: str) -> dict[str, Any] | None:
    path = latest_unreal_result_path(effect_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def latest_unreal_result_path(effect_name: str) -> Path:
    return WORKSPACE_ROOT / "generated" / "unreal-results" / f"{effect_name}.vfxspec.result.json"


def resolve_from_workspace(path: Path) -> Path:
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path
