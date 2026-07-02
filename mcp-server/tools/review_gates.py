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
        gate_alpha_mask_applied(patched_spec, manifest),
        gate_reference_overlay_not_primary(patched_spec, unreal_result),
        gate_texture_card_budget(patched_spec, manifest, unreal_result),
        gate_unreal_generation(unreal_result),
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
    for emitter in emitters:
        pass_name = asset_pass_for_emitter(spec.get("effect_type"), emitter)
        material = ((emitter.get("unreal_settings") or {}).get("material") or {})
        preview_card = (((emitter.get("unreal_settings") or {}).get("preview") or {}).get("card") or {})
        if preview_card.get("enabled") is not False and material.get("flipbook"):
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


def preview_components(unreal_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not unreal_result:
        return []
    bundle = unreal_result.get("bundle") or {}
    preview = bundle.get("preview") or {}
    return preview.get("components") or []


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
