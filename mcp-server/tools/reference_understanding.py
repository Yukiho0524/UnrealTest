from __future__ import annotations

from pathlib import Path
from typing import Any


def build_reference_understanding(package_path: Path, media_files: list[Path], visual_profile: dict[str, Any], prompt: str = "") -> dict[str, Any]:
    package_name = package_path.name.lower()
    prompt_lower = prompt.lower()
    text = " ".join([package_name, prompt_lower, *[path.stem.lower() for path in media_files]])
    shape = str(visual_profile.get("shape_hint") or "unknown")
    motion = str(visual_profile.get("motion_hint") or "unknown")
    style = str(visual_profile.get("style_hint") or "unknown")

    category = infer_effect_category(text, shape, style)
    structure = infer_structure(category, shape, motion, style, visual_profile)
    generation_strategy = generation_strategy_for(category, structure)
    unreal_strategy = unreal_strategy_for(category, structure)
    failure_modes = failure_modes_for(category, structure)

    return {
        "schema_version": 1,
        "source": "local_reference_understanding_v1",
        "status": "ready",
        "effect_category": category,
        "confidence": confidence_for(category, visual_profile, media_files),
        "dominant_read": dominant_read_for(category, structure),
        "reference_evidence": {
            "media_count": visual_profile.get("media_count", len(media_files)),
            "animated_count": visual_profile.get("animated_count", 0),
            "shape_hint": shape,
            "motion_hint": motion,
            "style_hint": style,
            "palette": visual_profile.get("palette", []),
            "vertical_energy": visual_profile.get("vertical_energy"),
            "base_energy": visual_profile.get("base_energy"),
            "center_energy": visual_profile.get("center_energy"),
            "bright_pixel_ratio": visual_profile.get("bright_pixel_ratio"),
            "warm_pixel_ratio": visual_profile.get("warm_pixel_ratio"),
        },
        "vfx_structure": structure,
        "generation_strategy": generation_strategy,
        "unreal_strategy": unreal_strategy,
        "asset_pass_priorities": asset_pass_priorities_for(category, structure),
        "negative_requirements": failure_modes,
        "review_focus": review_focus_for(category, structure),
        "vision_model_prompt": vision_model_prompt_for(package_path.name, category, structure),
    }


def infer_effect_category(text: str, shape: str, style: str) -> str:
    if any(token in text for token in ("firestorm", "fire_ice", "tornado", "vortex", "cyclone")):
        return "fire_magic_vortex"
    if any(token in text for token in ("fire", "flame", "burn", "lava")):
        return "fire_plume"
    if "electric" in text or "lightning" in text or "bolt" in text:
        return "electric_arc"
    if "glowing_shard" in shape or "square_particles" in shape:
        return "glowing_fragment_field"
    if "fire" in style or "flame" in shape:
        return "fire_plume"
    return "stylized_energy"


def infer_structure(category: str, shape: str, motion: str, style: str, visual_profile: dict[str, Any]) -> dict[str, Any]:
    vertical = float(visual_profile.get("vertical_energy") or 0.0)
    base = float(visual_profile.get("base_energy") or 0.0)
    center = float(visual_profile.get("center_energy") or 0.0)
    animated = int(visual_profile.get("animated_count") or 0)
    if category == "fire_magic_vortex":
        return {
            "primary_form": "spiral_vortex_column",
            "silhouette": "hollow rotating tornado funnel with asymmetric fire tongues",
            "motion_model": "orbital ribbon flow plus vertical lift",
            "camera_read": "must hold from multiple angles; no single flat front card",
            "required_layers": ["core_volume", "spiral_ribbons", "outer_flame_sheets", "smoke_haze", "embers", "subtle_ground_contact"],
            "ground_role": "support_only",
            "renderer_bias": ["ribbon", "mesh_volume", "flipbook_cards"],
            "needs_motion_target": animated > 0,
        }
    if category == "fire_plume":
        ground_role = "small_contact_flash"
        if base > 0.5 and vertical < 0.22:
            ground_role = "impact_ring"
        return {
            "primary_form": "volumetric_flame_plume",
            "silhouette": "irregular rising fire mass with torn edges, hot inner core, and darker outer smoke",
            "motion_model": "fuel ignition, rolling flame tongues, heat lift, then smoke decay",
            "camera_read": "should read as a 3D flame volume; crossed cards are only scaffolding",
            "required_layers": ["hot_core", "outer_tongues", "low_smoke", "heat_distortion", "embers", ground_role],
            "ground_role": ground_role,
            "renderer_bias": ["volume_mesh_helpers", "cross_billboard_flipbooks", "small_particles"],
            "needs_motion_target": animated > 0,
        }
    if category == "electric_arc":
        return {
            "primary_form": "branching_bolt",
            "silhouette": "one readable main bolt with thinner branch forks",
            "motion_model": "instant strike, flicker, branch decay",
            "camera_read": "branch path must dominate before sparks",
            "required_layers": ["main_bolt", "branch_bolts", "impact_core", "ion_sparks", "small_ground_contact"],
            "ground_role": "small_contact_flash",
            "renderer_bias": ["ribbon", "branch_cards", "small_particles"],
            "needs_motion_target": animated > 0,
        }
    return {
        "primary_form": "stylized_energy_body",
        "silhouette": shape,
        "motion_model": motion,
        "camera_read": "match dominant reference read before adding detail particles",
        "required_layers": ["primary_body", "secondary_body", "support_glow", "detail_particles"],
        "ground_role": "none_unless_visible_in_reference",
        "renderer_bias": ["flipbook_cards", "particles"],
        "needs_motion_target": animated > 0,
    }


def generation_strategy_for(category: str, structure: dict[str, Any]) -> dict[str, Any]:
    return {
        "order": [
            "understand_reference_structure",
            "generate_beauty_and_alpha_from_the_same_silhouette",
            "generate_layer_masks_for_core_edge_smoke_ground",
            "generate_motion_depth_distortion_support_passes",
            "assemble_unreal_preview",
            "score_against_reference",
        ],
        "primary_prompt_focus": [
            structure.get("primary_form"),
            structure.get("silhouette"),
            structure.get("motion_model"),
        ],
        "must_generate_as_bundle": True,
        "beauty_only_quality": "blockout_only",
        "provider_recommendation": provider_recommendation_for(category),
    }


def unreal_strategy_for(category: str, structure: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_renderer": (structure.get("renderer_bias") or ["flipbook_cards"])[0],
        "renderer_stack": structure.get("renderer_bias") or [],
        "material_requirements": [
            "alpha erosion",
            "emissive core-edge separation",
            "depth/thickness opacity modulation",
            "heat distortion flow",
            "soft particle or depth fade at contact",
        ],
        "preview_requirements": [
            "playable loop or one-shot timing",
            "multi-angle readability",
            "no oversized single texture card as final read",
            "ground layer must not dominate unless the reference clearly does",
        ],
    }


def asset_pass_priorities_for(category: str, structure: dict[str, Any]) -> list[dict[str, str]]:
    priorities = [
        ("beauty_flipbook", "highest", "main animated look, generated from the understood structure"),
        ("alpha_mask", "highest", "prevents rectangular cards and preserves torn silhouettes"),
        ("layer_mask_pack", "high", "separates core, edge, smoke, ground, and sparks"),
        ("motion_vectors", "high", "sells flow direction and interpolation"),
        ("depth_or_thickness", "high", "reduces flat-card look with pseudo-volume shading"),
        ("distortion_flow", "medium", "heat haze and edge breakup"),
        ("normal_or_lighting", "medium", "adds lit volume response"),
    ]
    if structure.get("ground_role") in {"support_only", "small_contact_flash"}:
        priorities.append(("ground_ring_mask", "low", "small contact support only; do not create a large magic floor symbol"))
    return [{"pass": name, "priority": priority, "reason": reason} for name, priority, reason in priorities]


def failure_modes_for(category: str, structure: dict[str, Any]) -> list[str]:
    common = [
        "single flat billboard used as the whole effect",
        "uniform particle fountain as the main shape",
        "opaque rectangular texture cards",
        "detail particles replacing the primary silhouette",
        "beauty-only output without alpha, masks, motion, depth, and distortion passes",
    ]
    if category in {"fire_plume", "fire_magic_vortex"}:
        common.extend(
            [
                "regular geometric spikes instead of torn fluid flame edges",
                "large decorative floor symbol when the reference only needs ground contact",
                "white/yellow overexposure that erases flame structure",
                "2D card stack visible from side view",
            ]
        )
    if category == "fire_magic_vortex":
        common.extend(["straight vertical tower instead of spiral flow", "solid cone or goblet silhouette instead of a hollow vortex"])
    return common


def review_focus_for(category: str, structure: dict[str, Any]) -> list[str]:
    return [
        f"Does the thumbnail read as {structure.get('primary_form')}?",
        f"Does the silhouette match: {structure.get('silhouette')}?",
        "Does the preview stay readable when viewed from the side?",
        "Are secondary particles clearly secondary?",
        "Are data passes present enough to avoid a flat 2D look?",
    ]


def dominant_read_for(category: str, structure: dict[str, Any]) -> str:
    return f"{category}: {structure.get('primary_form')} / {structure.get('silhouette')}"


def confidence_for(category: str, visual_profile: dict[str, Any], media_files: list[Path]) -> str:
    if not media_files:
        return "low"
    if category != "stylized_energy" and visual_profile.get("palette"):
        return "medium"
    return "low"


def provider_recommendation_for(category: str) -> list[str]:
    if category in {"fire_plume", "fire_magic_vortex"}:
        return ["EmberGen or FluidNinja for simulation flipbooks", "OpenAI/ComfyUI for reference-guided pass cleanup", "Unreal material graph for erosion/depth/distortion"]
    return ["OpenAI/ComfyUI for reference-guided passes", "Unreal Niagara for runtime layering"]


def vision_model_prompt_for(package_name: str, category: str, structure: dict[str, Any]) -> str:
    required_layers = ", ".join(structure.get("required_layers") or [])
    return (
        f"Analyze the reference media for VFX package '{package_name}'. "
        f"Identify the effect category, dominant silhouette, motion path, layer stack, renderer needs, "
        f"and what should be avoided. Current local hypothesis: {category}, "
        f"primary form {structure.get('primary_form')}, required layers: {required_layers}. "
        "Return JSON with effect_category, primary_form, silhouette, motion_model, layers, material_passes, "
        "unreal_renderer_stack, negative_requirements, and similarity_review_focus."
    )
