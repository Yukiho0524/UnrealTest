from __future__ import annotations

from typing import Any


def production_notes_for_plan(effect_type: str, visual_profile: dict[str, Any], emitters: list[dict[str, Any]]) -> list[str]:
    notes = [
        "Build the effect as a composition, not a single particle spray.",
        "Keep the primary silhouette readable first, then layer secondary particles and accents.",
        "Use texture alpha and emissive material response to carry shape; Niagara spawn rate should not be the main source of visual complexity.",
    ]

    roles = {emitter.get("role") for emitter in emitters}
    if "primary_body" in roles or effect_type == "fire_or_flame":
        notes.extend(
            [
                "Primary body: use a reference card or alpha-shaped flame sprite for the main read.",
                "Secondary body: add offset outer tongues/wisps so the flame has breakup instead of one solid billboard.",
                "Base glow: ground the effect with a broad low-frequency glow/decal layer before adding sparks.",
                "Atmosphere: add low-opacity smoke or heat wisps above the core to soften the silhouette.",
                "Detail layer: sparks/embers should be smaller, shorter-lived, and visually separated from the body.",
                "Timing: body should lead the effect; embers and glow should lag and fade after the main shape.",
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
    if role == "primary_body":
        return "Main visual read and silhouette."
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
    if role in {"primary_particles", "detail_particles", "accent_particles", "supporting_glow", "primary_body", "secondary_body", "atmospheric_wisp"}:
        return "Sprite Renderer"
    if shape == "ribbon":
        return "Ribbon Renderer"
    if shape == "mesh":
        return "Mesh Renderer"
    return "Sprite Renderer"


def material_goal_for_emitter(emitter: dict[str, Any]) -> str:
    role = emitter.get("role")
    style = emitter.get("material_style", "additive")
    if role == "primary_body":
        return f"{style}: alpha-shaped unlit emissive material for the main silhouette."
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
    if role in {"primary_body", "secondary_body"}:
        stack.extend(["Color Over Life: core-to-edge value shift", "Alpha Over Life: preserve silhouette then fade"])
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
    elif role == "supporting_glow":
        tuning.update({"spawn_density": "low", "opacity": "0.18-0.45", "sort": "behind primary particles"})
    elif role == "secondary_body":
        tuning.update({"opacity": "0.35-0.65", "offset": "slightly wider than core", "motion": "slower curl than core"})
    elif role == "atmospheric_wisp":
        tuning.update({"opacity": "0.08-0.28", "emissive": "very low", "motion": "slow curl/noise"})
    elif role == "accent_particles":
        tuning.update({"spawn_density": "sparse", "lifetime": "very short", "emissive": "highest layer value"})
    elif role == "detail_particles":
        tuning.update({"spawn_density": "medium-low", "velocity": "scatter/rise", "scale": "small relative to body"})
    elif role == "primary_body":
        tuning.update({"silhouette": "reference-first", "alpha": "avoid rectangular billboard", "emissive": "core hotter than edge"})
    return tuning
