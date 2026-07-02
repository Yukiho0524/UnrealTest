from __future__ import annotations

from typing import Any


def production_notes_for_plan(effect_type: str, visual_profile: dict[str, Any], emitters: list[dict[str, Any]]) -> list[str]:
    notes = [
        "Build the effect as a composition, not a single particle spray.",
        "Keep the primary silhouette readable first, then layer secondary particles and accents.",
        "Use texture alpha and emissive material response to carry shape; Niagara spawn rate should not be the main source of visual complexity.",
        "Keep source textures and preview cards within a clear size budget; oversized billboard sheets quickly reveal texture artifacts.",
        "AI/simulation generation must output a pass bundle, not only a beauty image: beauty, alpha, motion, depth/thickness, lighting, masks, and flow data each have separate Unreal uses.",
    ]

    roles = {emitter.get("role") for emitter in emitters}
    if "primary_body" in roles or effect_type == "fire_or_flame":
        notes.extend(
            [
                "High-similarity mode: use sampled reference flipbooks as motion targets, then rebuild editable layers around that timing.",
                "Fire impact: separate the vertical pillar, side tongues, ground ring, impact flash, smoke crown, and embers.",
                "Primary fire pillar must carry the read; do not let embers or template particles become the main shape.",
                "Ground ring/rune should anchor the effect before the pillar reaches full brightness.",
                "Side flame slashes should be broad, shaped, and asymmetric rather than many identical sprites.",
                "Smoke/dust crown belongs low around the blast base and should stay darker than the fire.",
                "Timing: flash and ring lead, pillar peaks next, slashes and embers trail, smoke lingers last.",
            ]
        )
    if "primary_particles" in roles or effect_type == "glowing_particles":
        notes.extend(
            [
                "Shard/square particles: vary size, rotation, lifetime, and opacity so the result reads as fragments instead of uniform spray.",
                "Add a soft glow/core layer behind hard particles to reproduce overexposed reference images.",
                "Use a few bright glints instead of raising all particle brightness equally.",
            ]
        )
    if "primary_bolt" in roles or effect_type == "electric_arc":
        notes.extend(
            [
                "Lightning: the main bolt silhouette must dominate; ion sparks are only accents.",
                "Use branching bolt cards/ribbons for structure, then add a small impact core and ground energy ring.",
                "Keep spark density sparse so the result does not become a white particle fountain.",
            ]
        )

    if visual_profile.get("motion_hint") == "vertical_column_rise":
        notes.append("Motion: bias velocity upward, but add turbulence/noise so the column does not look like a fountain preset.")
    if visual_profile.get("sparks_hint"):
        notes.append("Reference contains spark-like accents; include a separate short-life accent layer.")

    notes.extend(
        [
            "Renderer authoring: expose sprite size, rotation, sub-image, and material parameters so they can be driven per-particle later.",
            "Performance: group generated systems under an Effect Type / scalability profile once the look is accepted.",
        ]
    )
    return notes


def quality_target_for_plan(effect_type: str, visual_profile: dict[str, Any], emitters: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tier": "aaa_reference_match",
        "goal": "Generate an editable Unreal Niagara effect that can approach shipped-game quality from a small reference set.",
        "minimum_similarity": {
            "silhouette": "match the dominant reference read before adding detail",
            "timing": "match reference anticipation, peak, trail, and fade",
            "palette": "preserve core/edge/haze value hierarchy",
            "motion": "avoid template fountain/spray behavior unless present in the reference",
        },
        "production_constraints": {
            "not_allowed": [
                "single static billboard as final effect",
                "uniform particle spray as main shape",
                "visible flipbook atlas/grid",
                "opaque rectangular cards",
                "oversized reference images used as full effect cards",
            ],
            "required": [
                "layered emitters with clear roles",
                "alpha-shaped sprites or flipbooks",
                "small-to-medium texture cards that are supported by particles, ribbons, or mesh layers",
                "material-driven emissive, opacity, and distortion controls",
                "AI/simulation asset passes with explicit filenames and atlas metadata",
                "preview asset that plays in Unreal with Realtime enabled",
            ],
        },
        "source_asset_contract": {
            "minimum": [
                "beauty_flipbook",
                "alpha_mask",
                "layer_mask_pack",
                "renderer_layout_metadata",
            ],
            "production_quality": [
                "motion_vectors",
                "distortion_flow",
                "depth_or_thickness",
                "normal_or_six_point_lighting",
                "sdf_or_vector_field",
            ],
            "notes": [
                "Beauty-only generation is considered blockout quality.",
                "Each output must identify columns, rows, frame_count, fps, color_space, and intended Unreal renderer.",
            ],
        },
        "effect_type": effect_type,
        "reference_profile": {
            "animated_count": visual_profile.get("animated_count", 0),
            "shape_hint": visual_profile.get("shape_hint"),
            "motion_hint": visual_profile.get("motion_hint"),
            "sparks_hint": visual_profile.get("sparks_hint", False),
        },
        "emitter_roles": [emitter.get("role") for emitter in emitters],
    }


def asset_passes_for_plan(effect_type: str, visual_profile: dict[str, Any], emitters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passes = [
        {
            "name": "beauty_flipbook",
            "source": "ai_or_simulation",
            "format": "png_sequence_or_atlas",
            "purpose": "Main color/emissive animation reference with transparent or black background.",
            "unreal_usage": "SubUV/flipbook sprite material",
            "required": True,
        },
        {
            "name": "alpha_mask",
            "source": "ai_segmentation_or_luminance_extract",
            "format": "single_channel_png_sequence_or_atlas",
            "purpose": "Remove rectangular cards and preserve torn/soft silhouettes.",
            "unreal_usage": "Opacity input and overdraw control",
            "required": True,
        },
        {
            "name": "motion_vectors",
            "source": "simulation_or_video_optical_flow",
            "format": "rg_vector_atlas",
            "purpose": "Interpolate flipbook frames and add directional smear without extra frames.",
            "unreal_usage": "Material flipbook interpolation or Niagara dynamic material parameter",
            "required": False,
        },
        {
            "name": "distortion_flow",
            "source": "simulation_or_noise_synthesis",
            "format": "rg_flow_texture",
            "purpose": "Heat haze, smoke curl, flame edge breakup, or electric shimmer.",
            "unreal_usage": "Translucent distortion/refraction material layer",
            "required": False,
        },
        {
            "name": "normal_or_lighting",
            "source": "simulation_bake_or_ai_normal_estimate",
            "format": "normal_map_or_6_point_lighting",
            "purpose": "Add volume response for smoke, fire lobes, and magic clouds.",
            "unreal_usage": "Lit/unlit hybrid material parameters",
            "required": False,
        },
        {
            "name": "depth_or_thickness",
            "source": "simulation_bake_or_ai_depth_estimate",
            "format": "single_channel_depth_or_thickness_atlas",
            "purpose": "Describe volumetric depth/thickness so smoke, flame, and magic plumes can be shaded instead of flat-carded.",
            "unreal_usage": "Material depth fade, opacity modulation, soft particle contact, and pseudo-volume shading",
            "required": False,
        },
        {
            "name": "layer_mask_pack",
            "source": "ai_segmentation_or_manual_authoring",
            "format": "packed_rgba_masks",
            "purpose": "Separate core, edge, smoke, sparks, and ground influence masks so Unreal materials can tune layers independently.",
            "unreal_usage": "Dynamic material parameters, opacity erosion, emissive isolation, and layer balance review",
            "required": False,
        },
        {
            "name": "sdf_or_vector_field",
            "source": "simulation_or_procedural_field_generation",
            "format": "signed_distance_or_rg_vector_field",
            "purpose": "Drive edge erosion, curl, ribbon deformation, or particle steering without relying on random spray.",
            "unreal_usage": "Niagara vector field, material erosion, ribbon width/facing, or dynamic material flow",
            "required": False,
        },
        {
            "name": "renderer_layout_metadata",
            "source": "generation_pipeline_metadata",
            "format": "json",
            "purpose": "Declare atlas columns/rows/fps, frame order, per-pass color space, pivot, bounds, and intended renderer.",
            "unreal_usage": "Import validation and Niagara/SubUV/material setup",
            "required": False,
        },
        {
            "name": "reference_matched_composite",
            "source": "local_layer_composite_or_ai_video",
            "format": "transparent_single_preview_png",
            "purpose": "Small high-similarity viewport fidelity anchor generated from the layered passes.",
            "unreal_usage": "Preview-only small composite card behind editable production layers",
            "required": False,
        },
    ]

    if effect_type == "fire_or_flame":
        passes.extend(
            [
                {
                    "name": "core_flame_flipbook",
                    "source": "embergen_fluidninja_or_ai_video",
                    "format": "premultiplied_emissive_atlas",
                    "purpose": "White/yellow vertical core with orange torn edges.",
                    "unreal_usage": "Primary fire pillar emitter",
                    "required": True,
                },
                {
                    "name": "smoke_heat_flipbook",
                    "source": "simulation_or_ai_video",
                    "format": "low_emissive_translucent_atlas",
                    "purpose": "Dark crown, heat wisp, and linger after fire peak.",
                    "unreal_usage": "Atmospheric wisp emitter plus distortion layer",
                    "required": True,
                },
                {
                    "name": "ground_ring_mask",
                    "source": "procedural_or_ai_stroke",
                    "format": "radial_mask_texture",
                    "purpose": "Molten ring/rune anchor that sells impact scale.",
                    "unreal_usage": "Ground card or mesh ring emitter",
                    "required": True,
                },
                {
                    "name": "flame_slash_flipbook",
                    "source": "simulation_or_ai_video",
                    "format": "premultiplied_emissive_atlas",
                    "purpose": "Broad asymmetric side tongues that break the silhouette away from a single vertical column.",
                    "unreal_usage": "Side flame slash emitter",
                    "required": True,
                },
                {
                    "name": "impact_flash_mask",
                    "source": "procedural_or_ai",
                    "format": "radial_alpha_texture_or_flipbook_atlas",
                    "purpose": "Short overexposed ignition pulse that leads the fire animation.",
                    "unreal_usage": "Impact flash emitter",
                    "required": True,
                },
                {
                    "name": "ember_sprite_set",
                    "source": "procedural_or_ai_sprite_set",
                    "format": "small_alpha_sprite_atlas",
                    "purpose": "Sparse, varied ember shapes used as accents only.",
                    "unreal_usage": "Detail particle emitter",
                    "required": True,
                },
            ]
        )
    elif effect_type == "electric_arc":
        passes.extend(
            [
                {
                    "name": "bolt_branch_set",
                    "source": "procedural_ai_or_vector_authoring",
                    "format": "alpha_sprites_or_ribbon_masks",
                    "purpose": "Readable main bolt plus branch silhouettes.",
                    "unreal_usage": "Sprite or ribbon renderers with flicker",
                    "required": True,
                },
                {
                    "name": "impact_flash_mask",
                    "source": "procedural_or_ai",
                    "format": "radial_alpha_texture",
                    "purpose": "Short contact flash and ground energy pulse.",
                    "unreal_usage": "Impact core emitter",
                    "required": True,
                },
            ]
        )

    if any(emitter.get("role") == "reference_motion" for emitter in emitters):
        passes.append(
            {
                "name": "reference_motion_overlay",
                "source": "sampled_reference_gif_or_image_sequence",
                "format": "flipbook_atlas",
                "purpose": "Temporary visual target used to compare generated editable layers against the reference.",
                "unreal_usage": "Preview-only or low-opacity final overlay",
                "required": True,
            }
        )

    return passes


def review_gates_for_plan(effect_type: str, visual_profile: dict[str, Any], emitters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gates = [
        {
            "name": "reference_read",
            "pass_condition": "At thumbnail size, the effect reads as the same category and main silhouette as the reference.",
            "failure_action": "Adjust primary body/card/flipbook before adding particles.",
        },
        {
            "name": "motion_match",
            "pass_condition": "Peak timing, direction, and fade order match the reference or designer prompt.",
            "failure_action": "Retune layer delays, lifetime, SubUV fps, velocity, and alpha-over-life.",
        },
        {
            "name": "material_quality",
            "pass_condition": "No visible atlas grid, oversized billboard, or rectangular card; emissive core, edge alpha, and haze/distortion are separated.",
            "failure_action": "Regenerate alpha/motion/distortion passes, downsize card textures, and verify material graph inputs.",
        },
        {
            "name": "texture_card_budget",
            "pass_condition": "Runtime VFX textures and preview card scales stay within the role budget.",
            "failure_action": "Downsample the texture, split it into smaller layers, or reduce the preview card scale.",
        },
        {
            "name": "source_asset_contract",
            "pass_condition": "AI/simulation output includes more than beauty/alpha: masks, motion, depth/thickness, lighting, and metadata are present or intentionally waived.",
            "failure_action": "Generate a pass bundle with named files before trying to polish Unreal placement.",
        },
        {
            "name": "layer_balance",
            "pass_condition": "Primary layer dominates; accents, sparks, smoke, and glow support instead of becoming noise.",
            "failure_action": "Lower spawn density and opacity on secondary emitters.",
        },
        {
            "name": "engine_readiness",
            "pass_condition": "Blueprint/Niagara preview plays in Unreal viewport with Realtime enabled and does not crash.",
            "failure_action": "Remove unstable preview worlds and validate generated assets through Editor Python.",
        },
    ]
    if effect_type == "fire_or_flame":
        gates.append(
            {
                "name": "fire_specific_read",
                "pass_condition": "Impact flash leads, ground ring anchors, flame core rises, embers trail, smoke lingers.",
                "failure_action": "Split or reorder fire emitters; do not compensate by increasing spark count.",
            }
        )
    if effect_type == "electric_arc":
        gates.append(
            {
                "name": "lightning_specific_read",
                "pass_condition": "Main bolt and branches are readable before particles; spark spray is sparse.",
                "failure_action": "Use bolt/ribbon masks and reduce ion spark density.",
            }
        )
    return gates


def composition_layers_for_plan(effect_type: str, emitters: list[dict[str, Any]], has_reference_card: bool) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    if has_reference_card:
        layers.append(
            {
                "name": "reference_card",
                "purpose": "Preserve the reference image's main silhouette and value grouping.",
                "implementation": "Texture/material card asset generated from reference foreground.",
                "renderer": "Sprite Renderer",
                "material": "Unlit additive emissive card using extracted alpha.",
                "module_stack": [
                    "Spawn Burst Instantaneous: 1",
                    "Initialize Particle: long lifetime, large screen-readable size",
                    "Sprite Facing Mode: camera facing",
                    "Color/Alpha Over Life: hold then fade",
                ],
                "tuning": {
                    "size_source": "reference bounds",
                    "opacity": "medium-high; use as visual guide, not final opaque billboard",
                    "priority": "blockout silhouette before detail emitters",
                },
                "priority": 0,
            }
        )

    for index, emitter in enumerate(emitters, start=1):
        layers.append(
            {
                "name": emitter.get("name"),
                "purpose": purpose_for_role(emitter.get("role"), effect_type),
                "implementation": f"Niagara system layer using {emitter.get('sprite_shape', 'sprite')} sprite/material.",
                "renderer": renderer_for_emitter(emitter),
                "material": material_goal_for_emitter(emitter),
                "module_stack": module_stack_for_emitter(effect_type, emitter),
                "tuning": tuning_for_emitter(emitter),
                "priority": index,
            }
        )

    return layers


def purpose_for_role(role: str | None, effect_type: str) -> str:
    if role == "reference_motion":
        return "Sampled reference motion layer used to preserve timing and silhouette similarity."
    if role == "fire_pillar":
        return "Dominant vertical fire impact column."
    if role == "flame_slashes":
        return "Large side flame tongues and broken slash silhouettes."
    if role == "ground_energy_ring":
        return "Molten ground ring or rune that anchors the blast."
    if role == "primary_body":
        return "Main visual read and silhouette."
    if role == "primary_bolt":
        return "Dominant lightning strike silhouette."
    if role == "secondary_bolts":
        return "Branching side arcs around the main bolt."
    if role == "impact_core":
        return "Bright contact point at the strike target."
    if role == "secondary_body":
        return "Outer silhouette breakup and secondary flame tongues."
    if role == "primary_particles":
        return "Main readable particle fragments."
    if role == "detail_particles":
        return "Small secondary motion and breakup detail."
    if role == "supporting_glow":
        return "Soft bloom/value support behind hard sprites."
    if role == "atmospheric_wisp":
        return "Smoke, heat, or haze layer that softens the main body."
    if role == "accent_particles":
        return "Brief hot flashes and high-value accents."
    if effect_type == "fire_or_flame":
        return "Fire layer."
    return "Supporting VFX layer."


def renderer_for_emitter(emitter: dict[str, Any]) -> str:
    shape = emitter.get("sprite_shape")
    role = emitter.get("role")
    if role in {"primary_particles", "detail_particles", "accent_particles", "supporting_glow", "primary_body", "secondary_body", "atmospheric_wisp", "primary_bolt", "secondary_bolts", "impact_core", "fire_pillar", "flame_slashes", "ground_energy_ring", "reference_motion"}:
        return "Sprite Renderer"
    if shape == "ribbon":
        return "Ribbon Renderer"
    if shape == "mesh":
        return "Mesh Renderer"
    return "Sprite Renderer"


def material_goal_for_emitter(emitter: dict[str, Any]) -> str:
    role = emitter.get("role")
    style = emitter.get("material_style", "additive")
    if role == "reference_motion":
        return f"{style}: sampled animated flipbook from the reference, used as a similarity guide and optional final overlay."
    if role == "fire_pillar":
        return f"{style}: overexposed white/yellow core with orange torn edges and additive bloom."
    if role == "flame_slashes":
        return f"{style}: broad orange-red flame tongues with sharp torn alpha."
    if role == "ground_energy_ring":
        return f"{style}: molten ring/rune strokes with broken hot arcs."
    if role == "primary_body":
        return f"{style}: alpha-shaped unlit emissive material for the main silhouette."
    if role == "primary_bolt":
        return f"{style}: thin white-blue emissive core with cyan/purple outer glow."
    if role == "secondary_bolts":
        return f"{style}: branching fork sprites with lower opacity than the main bolt."
    if role == "impact_core":
        if "fire" in style:
            return f"{style}: overexposed warm ignition flash at the blast center."
        return f"{style}: concentrated electric contact flash."
    if role == "secondary_body":
        return f"{style}: offset flame tongues with lower opacity than the core."
    if role == "supporting_glow":
        return f"{style}: soft additive bloom layer with low detail and broad alpha."
    if role == "atmospheric_wisp":
        return f"{style}: low-opacity translucent haze/smoke used as breakup, not brightness."
    if role == "accent_particles":
        return f"{style}: very bright short-life glints with tight alpha."
    if role == "detail_particles":
        return f"{style}: small emissive particles separated from the main body."
    if role == "primary_particles":
        return f"{style}: crisp alpha sprite with per-particle color/rotation."
    return f"{style}: unlit particle material."


def module_stack_for_emitter(effect_type: str, emitter: dict[str, Any]) -> list[str]:
    role = emitter.get("role")
    motion = emitter.get("motion", "")
    stack = [
        "Emitter State: loop or finite according to timing",
        "Spawn Rate / Spawn Burst: driven by planned density",
        "Initialize Particle: lifetime, sprite size, color palette",
        "Sprite Renderer: material, alignment, facing mode",
    ]

    if "rise" in motion:
        stack.extend(["Add Velocity: upward bias", "Drag: stabilize vertical column"])
    if "turbulence" in motion or effect_type in {"fire_or_flame", "glowing_particles"}:
        stack.append("Curl Noise Force: break uniform fountain motion")
    if role in {"primary_particles", "accent_particles"}:
        stack.extend(["Sprite Rotation Rate: random angular velocity", "Scale Sprite Size: nonuniform size over life"])
    if role == "supporting_glow":
        stack.extend(["Color/Alpha Over Life: slow fade", "Scale Sprite Size: grow then dissolve"])
    if role == "reference_motion":
        stack.extend(["SubUV/Flipbook playback: sampled reference frames", "Material Time: frame index driven by FPS", "Opacity: tune as guide/final overlay"])
    if role == "fire_pillar":
        stack.extend(["Spawn Burst: one shaped pillar", "Scale Sprite Size: fast vertical stretch then collapse", "Color Over Life: white core to orange edge"])
    if role == "flame_slashes":
        stack.extend(["Spawn Burst: 2-4 broad tongues", "Sprite Rotation: asymmetric offsets", "Alpha Over Life: torn edges fade after pillar"])
    if role == "ground_energy_ring":
        stack.extend(["Spawn Burst: one ring", "Scale Sprite Size: radial expansion", "Alpha Over Life: hold briefly then burn out"])
    if role in {"primary_body", "secondary_body"}:
        stack.extend(["Color Over Life: core-to-edge value shift", "Alpha Over Life: preserve silhouette then fade"])
    if role in {"primary_bolt", "secondary_bolts"}:
        stack.extend(["Spawn Burst: low count", "Alpha Over Life: hard flicker", "Color Over Life: white core to blue/purple edge"])
    if role == "impact_core":
        stack.extend(["Spawn Burst: contact flash", "Scale Sprite Size: expand then collapse", "Alpha Over Life: fast decay"])
    if role == "atmospheric_wisp":
        stack.extend(["Curl Noise Force: slow rolling motion", "Alpha Over Life: delayed fade-in then dissolve"])
    if role == "detail_particles":
        stack.extend(["Random Velocity Cone: scatter away from body", "Alpha Over Life: quick decay"])

    return stack


def tuning_for_emitter(emitter: dict[str, Any]) -> dict[str, Any]:
    role = emitter.get("role")
    tuning: dict[str, Any] = {
        "spawn_rate": emitter.get("spawn_rate"),
        "lifetime_seconds": emitter.get("lifetime_seconds"),
        "start_size": emitter.get("start_size"),
        "end_size": emitter.get("end_size"),
        "palette": emitter.get("color_palette", []),
    }
    if role == "primary_particles":
        tuning.update({"size_variation": "0.45x-1.8x", "rotation_variation": "0-360 degrees", "opacity_variation": "0.55-1.0"})
    elif role == "reference_motion":
        tuning.update({"similarity": "highest priority", "opacity": "0.45-0.75", "usage": "motion target plus optional final overlay"})
    elif role == "fire_pillar":
        tuning.update({"spawn_density": "single burst", "opacity": "0.75-0.95", "shape": "vertical torn column with hot white center"})
    elif role == "flame_slashes":
        tuning.update({"spawn_density": "2-4 large cards", "opacity": "0.45-0.75", "shape": "wide broken side arcs"})
    elif role == "ground_energy_ring":
        tuning.update({"spawn_density": "single ring", "opacity": "0.45-0.8", "shape": "broken molten circle/rune"})
    elif role == "supporting_glow":
        tuning.update({"spawn_density": "low", "opacity": "0.18-0.45", "sort": "behind primary particles"})
    elif role == "secondary_body":
        tuning.update({"opacity": "0.35-0.65", "offset": "slightly wider than core", "motion": "slower curl than core"})
    elif role == "primary_bolt":
        tuning.update({"spawn_density": "single/low burst", "opacity": "0.85-1.0", "width": "thin core, strong glow"})
    elif role == "secondary_bolts":
        tuning.update({"spawn_density": "low", "opacity": "0.45-0.75", "branch_count": "2-5 visible forks"})
    elif role == "impact_core":
        tuning.update({"spawn_density": "single pulse", "opacity": "0.45-0.7", "scale": "small bright contact point"})
    elif role == "atmospheric_wisp":
        tuning.update({"opacity": "0.08-0.28", "emissive": "very low", "motion": "slow curl/noise"})
    elif role == "accent_particles":
        tuning.update({"spawn_density": "sparse", "lifetime": "very short", "emissive": "highest layer value"})
    elif role == "detail_particles":
        tuning.update({"spawn_density": "medium-low", "velocity": "scatter/rise", "scale": "small relative to body"})
    elif role == "primary_body":
        tuning.update({"silhouette": "reference-first", "alpha": "avoid rectangular billboard", "emissive": "core hotter than edge"})
    return tuning
