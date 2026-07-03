from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any
import urllib.request


def build_reference_understanding(package_path: Path, media_files: list[Path], visual_profile: dict[str, Any], prompt: str = "") -> dict[str, Any]:
    provider = os.environ.get("VFXMCP_VISION_PROVIDER", "local").strip().lower()
    if provider == "openai" and os.environ.get("OPENAI_API_KEY"):
        vision = build_openai_reference_understanding(package_path, media_files, visual_profile, prompt)
        if vision.get("status") == "ready":
            return vision
    return build_local_reference_understanding(package_path, media_files, visual_profile, prompt)


def build_openai_reference_understanding(package_path: Path, media_files: list[Path], visual_profile: dict[str, Any], prompt: str = "") -> dict[str, Any]:
    local = build_local_reference_understanding(package_path, media_files, visual_profile, prompt)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("VFXMCP_OPENAI_VISION_MODEL", "gpt-5")
    images = [image_content_part(path) for path in media_files[:4] if path.exists()]
    if not images:
        return {**local, "source": "openai_vision_unavailable", "status": "fallback_no_images"}
    instruction = (
        "You are a senior real-time game VFX art director. Analyze the reference media and return strict JSON. "
        "Focus on effect structure, silhouette, motion, material layers, Unreal renderer stack, required texture/data passes, "
        "and negative requirements. Do not describe unrelated scene content."
    )
    schema_prompt = {
        "local_hypothesis": local,
        "designer_prompt": prompt,
        "required_json_keys": [
            "effect_category",
            "confidence",
            "dominant_read",
            "vfx_structure",
            "generation_strategy",
            "unreal_strategy",
            "asset_pass_priorities",
            "negative_requirements",
            "review_focus",
        ],
    }
    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instruction + "\n\n" + json.dumps(schema_prompt, ensure_ascii=True)},
                    *images,
                ],
            }
        ],
        "text": {"format": {"type": "json_object"}},
    }
    try:
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        parsed = parse_openai_json_response(response_payload)
        return normalize_vision_understanding(parsed, local, model)
    except Exception as exc:
        fallback = dict(local)
        fallback["source"] = "local_reference_understanding_v1_openai_failed"
        fallback["vision_error"] = str(exc)
        return fallback


def build_local_reference_understanding(package_path: Path, media_files: list[Path], visual_profile: dict[str, Any], prompt: str = "") -> dict[str, Any]:
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


def image_content_part(path: Path) -> dict[str, str]:
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else ("image/gif" if suffix == ".gif" else "image/jpeg")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"}


def parse_openai_json_response(response_payload: dict[str, Any]) -> dict[str, Any]:
    texts: list[str] = []
    for item in response_payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                texts.append(str(content.get("text")))
    if not texts and response_payload.get("output_text"):
        texts.append(str(response_payload.get("output_text")))
    if not texts:
        return {}
    text = "\n".join(texts).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    return {}


def normalize_vision_understanding(parsed: dict[str, Any], local: dict[str, Any], model: str) -> dict[str, Any]:
    if not parsed:
        fallback = dict(local)
        fallback["source"] = "local_reference_understanding_v1_openai_empty"
        return fallback
    structure = parsed.get("vfx_structure") or {}
    if not structure:
        structure = {
            "primary_form": parsed.get("primary_form") or (local.get("vfx_structure") or {}).get("primary_form"),
            "silhouette": parsed.get("silhouette") or (local.get("vfx_structure") or {}).get("silhouette"),
            "motion_model": parsed.get("motion_model") or (local.get("vfx_structure") or {}).get("motion_model"),
            "required_layers": parsed.get("layers") or (local.get("vfx_structure") or {}).get("required_layers"),
            "renderer_bias": parsed.get("unreal_renderer_stack") or (local.get("vfx_structure") or {}).get("renderer_bias"),
            "ground_role": parsed.get("ground_role") or (local.get("vfx_structure") or {}).get("ground_role"),
            "needs_motion_target": (local.get("vfx_structure") or {}).get("needs_motion_target", False),
        }
    normalized = dict(local)
    normalized.update(
        {
            "source": "openai_vision",
            "vision_model": model,
            "status": "ready",
            "effect_category": parsed.get("effect_category") or local.get("effect_category"),
            "confidence": parsed.get("confidence") or "medium",
            "dominant_read": parsed.get("dominant_read") or dominant_read_for(parsed.get("effect_category") or local.get("effect_category"), structure),
            "vfx_structure": merge_dict((local.get("vfx_structure") or {}), structure),
            "generation_strategy": merge_dict((local.get("generation_strategy") or {}), parsed.get("generation_strategy") or {}),
            "unreal_strategy": merge_dict((local.get("unreal_strategy") or {}), parsed.get("unreal_strategy") or {}),
            "asset_pass_priorities": parsed.get("asset_pass_priorities") or local.get("asset_pass_priorities") or [],
            "negative_requirements": parsed.get("negative_requirements") or local.get("negative_requirements") or [],
            "review_focus": parsed.get("review_focus") or parsed.get("similarity_review_focus") or local.get("review_focus") or [],
            "raw_vision_understanding": parsed,
        }
    )
    return normalized


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in (override or {}).items():
        if value not in (None, "", []):
            result[key] = value
    return result


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
