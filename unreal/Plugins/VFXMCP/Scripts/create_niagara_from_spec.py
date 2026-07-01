from __future__ import annotations

import json
import math
import sys
import struct
import zlib
from pathlib import Path


REQUIRED_TOP_LEVEL_KEYS = {
    "name",
    "source",
    "effect_type",
    "motion",
    "color_palette",
    "render_mode",
    "timing",
    "particles",
}


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]


def parse_args(argv: list[str]) -> tuple[str | None, str | None]:
    if len(argv) == 3:
        return argv[1], argv[2]

    spec_arg = None
    destination_arg = None
    for arg in argv[1:]:
        if arg.startswith("-VFXSpec="):
            spec_arg = arg.split("=", 1)[1].strip('"')
        if arg.startswith("-VFXDestination="):
            destination_arg = arg.split("=", 1)[1].strip('"')
    return spec_arg, destination_arg


def main(argv: list[str]) -> int:
    spec_arg, destination_arg = parse_args(argv)
    if not spec_arg or not destination_arg:
        print("Usage: create_niagara_from_spec.py <spec.json> <destination_path>")
        print("Or run through Unreal with -VFXSpec=<spec.json> -VFXDestination=<destination_path>")
        return 2

    spec_path = Path(spec_arg)
    destination_path = destination_arg
    spec = load_spec(spec_path)
    validate_spec(spec)
    ensure_unreal_folder(destination_path)
    result = build_niagara_from_spec(spec, destination_path)
    print(json.dumps(result, indent=2))
    return 0


def load_spec(spec_path: Path) -> dict:
    if not spec_path.exists():
        raise FileNotFoundError(f"Spec file does not exist: {spec_path}")
    return json.loads(spec_path.read_text(encoding="utf-8"))


def validate_spec(spec: dict) -> None:
    missing = sorted(REQUIRED_TOP_LEVEL_KEYS.difference(spec))
    if missing:
        raise ValueError(f"Spec is missing required keys: {', '.join(missing)}")


def ensure_unreal_folder(destination_path: str) -> None:
    try:
        import unreal
    except ImportError:
        print(f"[dry-run] Would create Unreal folder: {destination_path}")
        return

    if not unreal.EditorAssetLibrary.does_directory_exist(destination_path):
        unreal.EditorAssetLibrary.make_directory(destination_path)


def build_niagara_from_spec(spec: dict, destination_path: str) -> dict:
    try:
        import unreal
    except ImportError:
        return {
            "mode": "dry-run",
            "asset_path": f"{destination_path}/NS_{spec['name']}",
            "preview_asset_path": f"{destination_path}/L_{spec['name']}_VFXPreview",
            "message": "Run this script inside Unreal Editor Python to create assets.",
        }

    emitters = planned_emitters(spec)
    if len(emitters) > 1:
        return build_niagara_bundle_from_spec(unreal, spec, destination_path, emitters)

    single_result = build_single_niagara_system(unreal, spec, destination_path)
    single_result["preview"] = create_preview_level_from_bundle(
        unreal,
        spec,
        destination_path,
        [single_result],
        {"created": False},
    )
    single_result["preview_asset_path"] = single_result["preview"].get("asset_path")
    if single_result["preview"].get("created"):
        single_result["asset_path"] = single_result["preview"]["asset_path"]
    unreal.EditorAssetLibrary.save_directory(destination_path, only_if_is_dirty=False, recursive=True)
    return single_result


def build_niagara_bundle_from_spec(unreal_module, spec: dict, destination_path: str, emitters: list[dict]) -> dict:
    systems = []
    primary_emitter = primary_emitter_name(spec)
    reference_card = create_reference_card_assets(unreal_module, spec, destination_path)
    for emitter in emitters:
        emitter_spec = spec_for_emitter(spec, emitter, emitter.get("name") == primary_emitter)
        system_result = build_single_niagara_system(unreal_module, emitter_spec, destination_path)
        system_result["emitter_plan"] = compact_emitter_plan(emitter)
        system_result["is_primary"] = emitter.get("name") == primary_emitter
        systems.append(system_result)

    primary_system = next((system for system in systems if system.get("is_primary")), systems[0] if systems else None)
    preview = create_preview_level_from_bundle(unreal_module, spec, destination_path, systems, reference_card)
    unreal_module.EditorAssetLibrary.save_directory(destination_path, only_if_is_dirty=False, recursive=True)
    return {
        "mode": "unreal-editor",
        "status": "created_bundle" if primary_system and primary_system.get("status") != "partial" else "partial_bundle",
        "asset_path": preview.get("asset_path") if preview.get("created") else (primary_system.get("asset_path") if primary_system else f"{destination_path}/NS_{spec['name']}"),
        "preview_asset_path": preview.get("asset_path"),
        "bundle": {
            "enabled": True,
            "primary_emitter": primary_emitter,
            "system_count": len(systems),
            "reference_card": reference_card,
            "systems": systems,
            "preview": preview,
        },
        "spec_summary": summarize_spec(spec),
        "message": "Created a composited VFX preview level plus a bundle from vfx_plan emitters. Open the preview level first; individual Niagara systems remain available for layer debugging.",
    }


def build_single_niagara_system(unreal, spec: dict, destination_path: str) -> dict:
    asset_name = f"NS_{spec['name']}"
    asset_path = f"{destination_path}/{asset_name}"
    template_result = create_niagara_system_from_template(unreal, spec, asset_path)
    material_result = create_vfx_material_assets(unreal, spec, destination_path)
    renderer_result = assign_material_to_niagara_renderers(
        unreal,
        asset_path,
        material_result.get("material_instance_path"),
    )
    if template_result["created"]:
        return {
            "mode": "unreal-editor",
            "status": template_result["status"],
            "asset_path": asset_path,
            "template": template_result["template"],
            "materials": material_result,
            "renderer_material_assignment": renderer_result,
            "spec_summary": summarize_spec(spec),
            "message": "Created a non-empty Niagara System, generated VFX material assets, and assigned the material instance to its sprite renderer.",
        }

    factory_result = create_niagara_system_asset(unreal, asset_name, destination_path)
    if factory_result["created"]:
        return {
            "mode": "unreal-editor",
            "status": "created",
            "asset_path": asset_path,
            "spec_summary": summarize_spec(spec),
            "template_errors": template_result["errors"],
            "materials": material_result,
            "renderer_material_assignment": renderer_result,
            "message": "Created initial Niagara System asset and VFX material assets, but template duplication was unavailable.",
        }

    return {
        "mode": "unreal-editor",
        "status": "partial",
        "asset_path": asset_path,
        "spec_summary": summarize_spec(spec),
        "template_errors": template_result["errors"],
        "factory_errors": factory_result["errors"],
        "materials": material_result,
        "renderer_material_assignment": renderer_result,
        "message": "Created destination folder and validated spec, but Niagara factory creation did not succeed in this UE Python API.",
    }


def create_reference_card_assets(unreal_module, spec: dict, destination_path: str) -> dict:
    plan = spec.get("vfx_plan") or {}
    source = plan.get("reference_card_source")
    result = {
        "source": source,
        "texture_path": None,
        "material_path": None,
        "material_instance_path": None,
        "created": False,
        "errors": [],
    }
    if not source:
        return result
    source_path = Path(source)
    if not source_path.exists():
        result["errors"].append(f"Reference card source does not exist: {source}")
        return result

    card_spec = json.loads(json.dumps(spec))
    card_spec["name"] = f"{spec['name']}_reference_card"
    card_spec["vfx_plan"] = {
        "visual_intent": plan.get("visual_intent", ""),
        "primary_emitter": "reference_card",
        "emitters": [
            {
                "name": "reference_card",
                "role": "reference_composite",
                "sprite_shape": "reference_card",
                "material_style": "reference_card_emissive",
                "motion": "static_preview_card",
                "spawn_rate": 1.0,
                "lifetime_seconds": max(float(spec.get("timing", {}).get("duration_seconds", 1.0)), 1.0),
                "start_size": max(float(spec.get("particles", {}).get("end_size", 96.0)), 128.0),
                "end_size": max(float(spec.get("particles", {}).get("end_size", 96.0)), 128.0),
                "color_palette": spec.get("color_palette", ["#FFFFFF"]),
                "sprite_source": str(source_path),
            }
        ],
    }
    material_result = create_vfx_material_assets(unreal_module, card_spec, destination_path)
    result.update(
        {
            "texture_path": material_result.get("texture_path"),
            "material_path": material_result.get("material_path"),
            "material_instance_path": material_result.get("material_instance_path"),
            "created": bool(material_result.get("created")),
            "errors": material_result.get("errors", []),
        }
    )
    return result


def create_preview_level_from_bundle(unreal_module, spec: dict, destination_path: str, systems: list[dict], reference_card: dict) -> dict:
    level_path = preview_level_path(spec, destination_path)
    result = {
        "asset_path": level_path,
        "created": False,
        "actors": [],
        "errors": [],
    }
    if not hasattr(unreal_module, "EditorLevelLibrary"):
        result["errors"].append("EditorLevelLibrary is not available; preview level could not be created.")
        return result

    try:
        if unreal_module.EditorAssetLibrary.does_asset_exist(level_path):
            unreal_module.EditorAssetLibrary.delete_asset(level_path)
        if not unreal_module.EditorLevelLibrary.new_level(level_path):
            result["errors"].append(f"Could not create preview level: {level_path}")
            return result

        spawn_preview_environment(unreal_module, result)
        plane_mesh = unreal_module.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Plane")
        if not plane_mesh:
            result["errors"].append("Could not load /Engine/BasicShapes/Plane for preview cards.")

        reference_material = reference_card.get("material_instance_path") if reference_card else None
        if plane_mesh and reference_material:
            spawn_material_preview_plane(
                unreal_module,
                result,
                plane_mesh,
                reference_material,
                "VFX Reference Silhouette",
                layer_index=0,
                scale=2.1,
                opacity_offset=0,
            )

        for index, system in enumerate(systems, start=1):
            material_path = (system.get("materials") or {}).get("material_instance_path")
            emitter = system.get("emitter_plan") or {}
            label = f"VFX Layer {index}: {emitter.get('name') or system.get('asset_path', 'layer').rsplit('/', 1)[-1]}"
            if plane_mesh and material_path:
                spawn_material_preview_plane(
                    unreal_module,
                    result,
                    plane_mesh,
                    material_path,
                    label,
                    layer_index=index,
                    scale=preview_scale_for_emitter(emitter, index),
                    opacity_offset=index,
                )
            spawn_niagara_preview_actor(unreal_module, result, system, emitter, index)

        try:
            unreal_module.EditorLevelLibrary.save_current_level()
        except Exception as exc:
            result["errors"].append(f"Could not save preview level: {exc}")

        level_asset = unreal_module.EditorAssetLibrary.load_asset(level_path)
        if level_asset:
            annotate_asset(unreal_module, level_asset, spec)
            unreal_module.EditorAssetLibrary.save_loaded_asset(level_asset)

        result["created"] = unreal_module.EditorAssetLibrary.does_asset_exist(level_path)
        return result
    except Exception as exc:
        result["errors"].append(str(exc))
        return result


def preview_level_path(spec: dict, destination_path: str) -> str:
    return f"{destination_path}/L_{spec['name']}_VFXPreview"


def spawn_preview_environment(unreal_module, result: dict) -> None:
    try:
        camera = unreal_module.EditorLevelLibrary.spawn_actor_from_class(
            unreal_module.CameraActor,
            unreal_module.Vector(-520.0, 0.0, 180.0),
            unreal_module.Rotator(-8.0, 0.0, 0.0),
        )
        if camera:
            camera.set_actor_label("VFX Preview Camera")
            result["actors"].append({"label": "VFX Preview Camera", "type": "CameraActor"})
    except Exception as exc:
        result["errors"].append(f"Could not spawn preview camera: {exc}")

    light_class = getattr(unreal_module, "DirectionalLight", None)
    if light_class:
        try:
            light = unreal_module.EditorLevelLibrary.spawn_actor_from_class(
                light_class,
                unreal_module.Vector(-180.0, -220.0, 380.0),
                unreal_module.Rotator(-42.0, 24.0, 0.0),
            )
            if light:
                light.set_actor_label("VFX Preview Key Light")
                result["actors"].append({"label": "VFX Preview Key Light", "type": "DirectionalLight"})
        except Exception as exc:
            result["errors"].append(f"Could not spawn preview light: {exc}")


def spawn_material_preview_plane(
    unreal_module,
    result: dict,
    plane_mesh,
    material_path: str,
    label: str,
    layer_index: int,
    scale: float,
    opacity_offset: int,
) -> None:
    material = unreal_module.EditorAssetLibrary.load_asset(material_path)
    if not material:
        result["errors"].append(f"Preview material does not exist: {material_path}")
        return
    try:
        actor = unreal_module.EditorLevelLibrary.spawn_actor_from_class(
            unreal_module.StaticMeshActor,
            unreal_module.Vector(opacity_offset * 4.0, 0.0, 150.0 + layer_index * 8.0),
            unreal_module.Rotator(90.0, 0.0, 0.0),
        )
        if not actor:
            result["errors"].append(f"Could not spawn preview plane for {material_path}")
            return
        actor.set_actor_label(label)
        actor.set_actor_scale3d(unreal_module.Vector(scale, scale, scale))
        component = static_mesh_component_for_actor(unreal_module, actor)
        if component:
            component.set_static_mesh(plane_mesh)
            component.set_material(0, material)
        result["actors"].append({"label": label, "type": "StaticMeshActor", "material": material_path})
    except Exception as exc:
        result["errors"].append(f"Could not spawn preview plane {label}: {exc}")


def spawn_niagara_preview_actor(unreal_module, result: dict, system: dict, emitter: dict, index: int) -> None:
    niagara_actor_class = getattr(unreal_module, "NiagaraActor", None)
    niagara_component_class = getattr(unreal_module, "NiagaraComponent", None)
    if not niagara_actor_class or not niagara_component_class:
        result["errors"].append("NiagaraActor/NiagaraComponent is not available for preview placement.")
        return

    system_path = system.get("asset_path")
    system_asset = unreal_module.EditorAssetLibrary.load_asset(system_path) if system_path else None
    if not system_asset:
        result["errors"].append(f"Preview Niagara system does not exist: {system_path}")
        return

    try:
        actor = unreal_module.EditorLevelLibrary.spawn_actor_from_class(
            niagara_actor_class,
            unreal_module.Vector(-38.0, (index - 1) * 34.0, 118.0),
            unreal_module.Rotator(0.0, 0.0, 0.0),
        )
        if not actor:
            result["errors"].append(f"Could not spawn Niagara preview actor: {system_path}")
            return
        label = f"VFX Niagara Layer {index}: {emitter.get('name') or system_path.rsplit('/', 1)[-1]}"
        actor.set_actor_label(label)
        component = actor.get_component_by_class(niagara_component_class)
        if component:
            if hasattr(component, "set_asset"):
                component.set_asset(system_asset)
            else:
                component.set_editor_property("asset", system_asset)
        result["actors"].append({"label": label, "type": "NiagaraActor", "system": system_path})
    except Exception as exc:
        result["errors"].append(f"Could not spawn Niagara preview actor for {system_path}: {exc}")


def static_mesh_component_for_actor(unreal_module, actor):
    try:
        return actor.get_component_by_class(unreal_module.StaticMeshComponent)
    except Exception:
        pass
    try:
        return actor.static_mesh_component
    except Exception:
        return None


def preview_scale_for_emitter(emitter: dict, index: int) -> float:
    role = emitter.get("role")
    if role == "supporting_glow":
        return 1.7
    if role == "accent_particles":
        return 0.65
    if role == "detail_particles":
        return 0.85
    if role == "primary_body":
        return 1.45
    if role == "primary_particles":
        return 1.15
    return max(0.8, 1.2 - index * 0.08)


def planned_emitters(spec: dict) -> list[dict]:
    plan = spec.get("vfx_plan") or {}
    emitters = plan.get("emitters") or []
    return [emitter for emitter in emitters if emitter.get("name")]


def primary_emitter_name(spec: dict) -> str | None:
    plan = spec.get("vfx_plan") or {}
    return plan.get("primary_emitter")


def spec_for_emitter(base_spec: dict, emitter: dict, is_primary: bool) -> dict:
    emitter_name = safe_asset_token(emitter.get("name", "emitter"))
    spec = json.loads(json.dumps(base_spec))
    spec["name"] = base_spec["name"] if is_primary else f"{base_spec['name']}_{emitter_name}"
    spec["motion"] = emitter.get("motion") or base_spec.get("motion", "unknown")
    spec["color_palette"] = emitter.get("color_palette") or base_spec.get("color_palette", ["#FFFFFF"])
    spec["particles"] = {
        "spawn_rate": float(emitter.get("spawn_rate", base_spec["particles"]["spawn_rate"])),
        "lifetime_seconds": float(emitter.get("lifetime_seconds", base_spec["particles"]["lifetime_seconds"])),
        "start_size": float(emitter.get("start_size", base_spec["particles"]["start_size"])),
        "end_size": float(emitter.get("end_size", base_spec["particles"]["end_size"])),
    }
    spec["vfx_plan"] = {
        "visual_intent": (base_spec.get("vfx_plan") or {}).get("visual_intent", ""),
        "primary_emitter": emitter.get("name"),
        "emitters": [emitter],
    }
    spec.setdefault("notes", [])
    spec["notes"] = [*spec["notes"], f"Generated as VFX bundle emitter: {emitter.get('name')}"]
    return spec


def safe_asset_token(value: str) -> str:
    token = "".join(character if character.isalnum() else "_" for character in value)
    return token.strip("_") or "emitter"


def create_niagara_system_from_template(unreal_module, spec: dict, asset_path: str) -> dict:
    errors: list[str] = []
    template_paths = template_paths_for_spec(spec)

    if unreal_module.EditorAssetLibrary.does_asset_exist(asset_path):
        unreal_module.EditorAssetLibrary.delete_asset(asset_path)

    for template_path in template_paths:
        if not unreal_module.EditorAssetLibrary.does_asset_exist(template_path):
            errors.append(f"Template does not exist: {template_path}")
            continue
        try:
            duplicated_asset = unreal_module.EditorAssetLibrary.duplicate_asset(template_path, asset_path)
            if duplicated_asset:
                annotate_asset(unreal_module, duplicated_asset, spec)
                return {
                    "created": True,
                    "status": "created_from_template",
                    "template": template_path,
                    "errors": errors,
                }
            errors.append(f"Duplicate returned no asset: {template_path}")
        except Exception as exc:
            errors.append(f"Duplicate failed for {template_path}: {exc}")

    return {"created": False, "status": "template_failed", "template": None, "errors": errors}


def template_paths_for_effect(effect_type: str) -> list[str]:
    if effect_type in {"fire_or_flame", "impact_burst", "magic_energy"}:
        return [
            "/Niagara/DefaultAssets/Templates/Systems/SimpleExplosion",
            "/Niagara/DefaultAssets/DefaultSystem",
        ]
    if effect_type in {"smoke_or_mist"}:
        return [
            "/Niagara/DefaultAssets/Templates/Systems/FountainLightweight",
            "/Niagara/DefaultAssets/DefaultSystem",
        ]
    if effect_type in {"electric_arc"}:
        return [
            "/Niagara/DefaultAssets/Templates/Systems/SimpleExplosion",
            "/Niagara/DefaultAssets/DefaultSystem",
        ]
    if effect_type in {"glowing_particles"}:
        return [
            "/Niagara/DefaultAssets/Templates/Systems/FountainLightweight",
            "/Niagara/DefaultAssets/DefaultSystem",
        ]
    return ["/Niagara/DefaultAssets/DefaultSystem"]


def template_paths_for_spec(spec: dict) -> list[str]:
    effect_type = spec["effect_type"]
    motion = spec["motion"]

    if effect_type == "fire_or_flame" and motion == "rise_and_fade":
        return [
            "/Niagara/DefaultAssets/Templates/Systems/FountainLightweight",
            "/Niagara/DefaultAssets/Templates/Systems/SimpleExplosion",
            "/Niagara/DefaultAssets/DefaultSystem",
        ]
    if motion == "radial_expand_then_fade":
        return [
            "/Niagara/DefaultAssets/Templates/Systems/SimpleExplosion",
            "/Niagara/DefaultAssets/DefaultSystem",
        ]
    return template_paths_for_effect(effect_type)


def annotate_asset(unreal_module, asset, spec: dict) -> None:
    try:
        unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_EffectType", spec["effect_type"])
        unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_Motion", spec["motion"])
        unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_ColorPalette", ",".join(spec["color_palette"]))
        unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_Source", spec["source"]["uri"])
        if spec.get("visual_profile"):
            unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_VisualProfile", json.dumps(compact_visual_profile(spec["visual_profile"])))
        if spec.get("vfx_plan"):
            unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_Plan", json.dumps(compact_vfx_plan(spec["vfx_plan"])))
    except Exception as exc:
        unreal_module.log_warning(f"VFX MCP could not write metadata: {exc}")


def create_vfx_material_assets(unreal_module, spec: dict, destination_path: str) -> dict:
    material_name = f"M_{spec['name']}_VFX"
    instance_name = f"MI_{spec['name']}_VFX"
    texture_name = f"T_{spec['name']}_VFX_Sprite"
    material_path = f"{destination_path}/{material_name}"
    instance_path = f"{destination_path}/{instance_name}"
    texture_path = f"{destination_path}/{texture_name}"

    result = {
        "material_path": material_path,
        "material_instance_path": instance_path,
        "texture_path": texture_path,
        "palette": spec["color_palette"],
        "errors": [],
        "created": False,
    }

    try:
        texture = create_or_replace_sprite_texture(unreal_module, texture_name, destination_path, spec)
        material = create_or_replace_material(unreal_module, material_name, destination_path, spec, texture)
        material_instance = create_or_replace_material_instance(unreal_module, instance_name, destination_path, material, spec, texture)
        result["created"] = bool(material and material_instance)
        return result
    except Exception as exc:
        result["errors"].append(str(exc))
        return result


def create_or_replace_sprite_texture(unreal_module, texture_name: str, destination_path: str, spec: dict):
    texture_path = f"{destination_path}/{texture_name}"
    if unreal_module.EditorAssetLibrary.does_asset_exist(texture_path):
        unreal_module.EditorAssetLibrary.delete_asset(texture_path)

    source_path = primary_sprite_source_path(spec)
    if not source_path:
        source_path = generated_texture_source_path(spec["name"], texture_name)
        write_sprite_png(source_path, spec)

    task = unreal_module.AssetImportTask()
    task.set_editor_property("filename", str(source_path))
    task.set_editor_property("destination_path", destination_path)
    task.set_editor_property("destination_name", texture_name)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", True)
    unreal_module.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    texture = unreal_module.EditorAssetLibrary.load_asset(texture_path)
    if not texture:
        raise RuntimeError(f"Could not import sprite texture: {texture_path}")
    configure_texture_asset(unreal_module, texture)
    annotate_asset(unreal_module, texture, spec)
    unreal_module.EditorAssetLibrary.save_loaded_asset(texture)
    return texture


def generated_texture_source_path(effect_name: str, texture_name: str) -> Path:
    safe_effect_name = "".join(character if character.isalnum() else "_" for character in effect_name).strip("_") or "effect"
    output_dir = WORKSPACE_ROOT / "generated" / "unreal-imports" / safe_effect_name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{texture_name}.png"


def write_sprite_png(path: Path, spec: dict) -> None:
    width = 256
    height = 256
    sprite_shape = primary_sprite_shape(spec)
    if sprite_shape == "square":
        pixels = square_sprite_pixels(width, height, spec)
    elif sprite_shape == "shard":
        pixels = shard_sprite_pixels(width, height, spec)
    elif is_fire_spec(spec) or sprite_shape == "flame_tongue":
        pixels = fire_sprite_pixels(width, height, spec)
    else:
        pixels = soft_disc_pixels(width, height, spec)
    write_rgba_png(path, width, height, pixels)


def fire_sprite_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec["color_palette"]]
    core = palette[0] if palette else (255, 248, 200, 255)
    mid = palette[1] if len(palette) > 1 else (255, 155, 45, 255)
    edge = palette[2] if len(palette) > 2 else (240, 80, 32, 255)
    pixels = bytearray()
    for y in range(height):
        ny = y / (height - 1)
        up = 1.0 - ny
        for x in range(width):
            nx = (x / (width - 1) - 0.5) * 2.0
            height_fade = smoothstep(0.0, 0.1, up) * (1.0 - smoothstep(0.96, 1.0, up))
            main_center = 0.035 * wave(up * 1.2 + 0.1)
            main_width = 0.06 + 0.56 * ((1.0 - up) ** 1.18)
            main = gaussian(nx, main_center, main_width) * height_fade

            left_center = -0.24 + 0.08 * wave(up * 1.8 + 0.35)
            right_center = 0.24 + 0.08 * wave(up * 1.7 + 0.8)
            side_width = 0.045 + 0.22 * ((1.0 - up) ** 1.4)
            side_window = smoothstep(0.08, 0.22, up) * (1.0 - smoothstep(0.62, 0.88, up))
            left = gaussian(nx, left_center, side_width) * side_window * 0.7
            right = gaussian(nx, right_center, side_width) * side_window * 0.62

            tip_center = 0.06 * wave(up * 2.4 + 0.55)
            tip_width = 0.035 + 0.12 * (1.0 - up)
            tip_window = smoothstep(0.52, 0.74, up) * (1.0 - smoothstep(0.92, 1.0, up))
            tip = gaussian(nx, tip_center, tip_width) * tip_window

            alpha = clamp(main + left + right + tip)

            core_width = max(0.035, main_width * 0.34)
            core_alpha = gaussian(nx, main_center * 0.45, core_width)
            core_alpha *= smoothstep(0.06, 0.2, up) * (1.0 - smoothstep(0.7, 0.92, up))

            body_amount = clamp(alpha * 0.85 + main * 0.35)
            color = mix_color(edge, mid, body_amount)
            color = mix_color(color, core, min(1.0, core_alpha * 1.35))
            pixels.extend((color[0], color[1], color[2], int(clamp(alpha + core_alpha * 0.35) * 255)))
    return bytes(pixels)


def soft_disc_pixels(width: int, height: int, spec: dict) -> bytes:
    color = hex_to_rgba_tuple(spec["color_palette"][0] if spec.get("color_palette") else "#FFFFFF")
    pixels = bytearray()
    for y in range(height):
        ny = (y / (height - 1) - 0.5) * 2.0
        for x in range(width):
            nx = (x / (width - 1) - 0.5) * 2.0
            distance = (nx * nx + ny * ny) ** 0.5
            alpha = 1.0 - smoothstep(0.18, 0.92, distance)
            pixels.extend((color[0], color[1], color[2], int(alpha * 255)))
    return bytes(pixels)


def square_sprite_pixels(width: int, height: int, spec: dict) -> bytes:
    color = hex_to_rgba_tuple(spec["color_palette"][0] if spec.get("color_palette") else "#FFFFFF")
    pixels = bytearray()
    for y in range(height):
        ny = abs((y / (height - 1) - 0.5) * 2.0)
        for x in range(width):
            nx = abs((x / (width - 1) - 0.5) * 2.0)
            body = 1.0 - smoothstep(0.72, 0.98, max(nx, ny))
            inner = 1.0 - smoothstep(0.0, 0.82, max(nx, ny))
            alpha = clamp(body)
            brightness = 0.82 + inner * 0.18
            pixels.extend((int(color[0] * brightness), int(color[1] * brightness), int(color[2] * brightness), int(alpha * 255)))
    return bytes(pixels)


def shard_sprite_pixels(width: int, height: int, spec: dict) -> bytes:
    color = hex_to_rgba_tuple(spec["color_palette"][0] if spec.get("color_palette") else "#FFFFFF")
    warm = hex_to_rgba_tuple(spec["color_palette"][1] if len(spec.get("color_palette", [])) > 1 else "#FFF0C8")
    pixels = bytearray()
    points = [(-0.82, -0.46), (0.72, -0.08), (-0.18, 0.74)]
    for y in range(height):
        ny = (y / (height - 1) - 0.5) * 2.0
        for x in range(width):
            nx = (x / (width - 1) - 0.5) * 2.0
            inside = triangle_coverage(nx, ny, points)
            ridge = 1.0 - min(1.0, abs(nx + ny * 0.42) * 1.55)
            alpha = inside * (0.78 + ridge * 0.22)
            mixed = mix_color(warm, color, ridge)
            pixels.extend((mixed[0], mixed[1], mixed[2], int(clamp(alpha) * 255)))
    return bytes(pixels)


def triangle_coverage(x: float, y: float, points: list[tuple[float, float]]) -> float:
    def sign(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
        return (a[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (a[1] - c[1])

    point = (x, y)
    d1 = sign(point, points[0], points[1])
    d2 = sign(point, points[1], points[2])
    d3 = sign(point, points[2], points[0])
    inside = not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))
    if inside:
        edge_distance = min(abs(d1), abs(d2), abs(d3))
        return smoothstep(0.0, 0.08, edge_distance)
    return 0.0


def write_rgba_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    raw_rows = bytearray()
    stride = width * 4
    for y in range(height):
        raw_rows.append(0)
        raw_rows.extend(pixels[y * stride : (y + 1) * stride])

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + chunk_type
            + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw_rows), 9))
        + chunk(b"IEND", b"")
    )


def configure_texture_asset(unreal_module, texture) -> None:
    try_set_editor_property(texture, "srgb", True)
    if hasattr(unreal_module, "TextureCompressionSettings"):
        try_set_editor_property(texture, "compression_settings", unreal_module.TextureCompressionSettings.TC_DEFAULT)
    if hasattr(unreal_module, "TextureMipGenSettings"):
        try_set_editor_property(texture, "mip_gen_settings", unreal_module.TextureMipGenSettings.TMGS_NO_MIPMAPS)


def create_or_replace_material(unreal_module, material_name: str, destination_path: str, spec: dict, sprite_texture=None):
    material_path = f"{destination_path}/{material_name}"
    if unreal_module.EditorAssetLibrary.does_asset_exist(material_path):
        unreal_module.EditorAssetLibrary.delete_asset(material_path)

    asset_tools = unreal_module.AssetToolsHelpers.get_asset_tools()
    factory = unreal_module.MaterialFactoryNew()
    material = asset_tools.create_asset(material_name, destination_path, unreal_module.Material, factory)
    if not material:
        raise RuntimeError(f"Could not create material: {material_path}")

    configure_material_properties(unreal_module, material)
    build_sprite_material_graph(unreal_module, material, spec, sprite_texture)
    annotate_asset(unreal_module, material, spec)
    unreal_module.EditorAssetLibrary.save_loaded_asset(material)
    return material


def create_or_replace_material_instance(unreal_module, instance_name: str, destination_path: str, material, spec: dict, sprite_texture=None):
    instance_path = f"{destination_path}/{instance_name}"
    if unreal_module.EditorAssetLibrary.does_asset_exist(instance_path):
        unreal_module.EditorAssetLibrary.delete_asset(instance_path)

    asset_tools = unreal_module.AssetToolsHelpers.get_asset_tools()
    factory = unreal_module.MaterialInstanceConstantFactoryNew()
    material_instance = asset_tools.create_asset(instance_name, destination_path, unreal_module.MaterialInstanceConstant, factory)
    if not material_instance:
        raise RuntimeError(f"Could not create material instance: {instance_path}")

    material_instance.set_editor_property("parent", material)
    palette = palette_as_linear_colors(spec["color_palette"])
    unreal_module.MaterialEditingLibrary.set_material_instance_vector_parameter_value(material_instance, "CoreColor", palette[0])
    unreal_module.MaterialEditingLibrary.set_material_instance_vector_parameter_value(material_instance, "OuterColor", palette[2 if len(palette) > 2 else 0])
    unreal_module.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(material_instance, "EmissiveStrength", inferred_emissive_strength(spec))
    unreal_module.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(material_instance, "Opacity", 0.92)
    if sprite_texture:
        unreal_module.MaterialEditingLibrary.set_material_instance_texture_parameter_value(material_instance, "SpriteTexture", sprite_texture)
    annotate_asset(unreal_module, material_instance, spec)
    unreal_module.EditorAssetLibrary.save_loaded_asset(material_instance)
    return material_instance


def assign_material_to_niagara_renderers(unreal_module, asset_path: str, material_instance_path: str | None) -> dict:
    result = {
        "asset_path": asset_path,
        "material_instance_path": material_instance_path,
        "assigned_count": 0,
        "matched_renderers": [],
        "errors": [],
    }
    if not material_instance_path:
        result["errors"].append("No material instance path was produced.")
        return result
    if not hasattr(unreal_module, "ObjectIterator"):
        result["errors"].append("Unreal ObjectIterator is not available in this Python API.")
        return result

    system_asset = unreal_module.EditorAssetLibrary.load_asset(asset_path)
    material_instance = unreal_module.EditorAssetLibrary.load_asset(material_instance_path)
    if not system_asset:
        result["errors"].append(f"Niagara system could not be loaded: {asset_path}")
        return result
    if not material_instance:
        result["errors"].append(f"Material instance could not be loaded: {material_instance_path}")
        return result

    asset_package = asset_path.rsplit("/", 1)[0]
    asset_name = asset_path.rsplit("/", 1)[-1]
    matched_objects = []
    for obj in unreal_module.ObjectIterator():
        class_name = safe_unreal_class_name(obj)
        if class_name != "NiagaraSpriteRendererProperties":
            continue
        try:
            outer_chain = object_outer_chain(obj)
            if not is_renderer_owned_by_asset(outer_chain, asset_package, asset_name):
                continue
            matched_objects.append(obj)
            set_renderer_material(unreal_module, obj, material_instance)
            result["assigned_count"] += 1
            result["matched_renderers"].append(
                {
                    "object": str(obj),
                    "outer_chain": outer_chain,
                    "material": str(obj.get_editor_property("material")),
                }
            )
        except Exception as exc:
            result["errors"].append(f"Renderer assignment failed for {obj}: {exc}")

    if not matched_objects:
        result["errors"].append(f"No Niagara sprite renderer was found for {asset_path}.")
    else:
        try:
            unreal_module.EditorAssetLibrary.save_loaded_asset(system_asset)
        except Exception as exc:
            result["errors"].append(f"Could not save Niagara system after renderer assignment: {exc}")
    return result


def safe_unreal_class_name(obj) -> str | None:
    try:
        unreal_class = obj.get_class()
        if unreal_class:
            return unreal_class.get_name()
    except Exception:
        return None
    return None


def object_outer_chain(obj) -> list[str]:
    chain = [str(obj)]
    current = obj
    seen = set()
    while hasattr(current, "get_outer"):
        try:
            current = current.get_outer()
        except Exception:
            break
        if not current:
            break
        current_text = str(current)
        if current_text in seen:
            break
        seen.add(current_text)
        chain.append(current_text)
        if " Class 'Package'" in current_text:
            break
    return chain


def is_renderer_owned_by_asset(outer_chain: list[str], asset_package: str, asset_name: str) -> bool:
    package_prefix = f"{asset_package}/{asset_name}.{asset_name}:"
    asset_object = f"{asset_package}/{asset_name}.{asset_name}"
    return any(package_prefix in item or asset_object in item for item in outer_chain)


def set_renderer_material(unreal_module, renderer, material_instance) -> None:
    if hasattr(renderer, "modify"):
        renderer.modify()
    renderer.set_editor_property("material", material_instance)
    if hasattr(renderer, "post_edit_change"):
        renderer.post_edit_change()


def configure_material_properties(unreal_module, material) -> None:
    material.set_editor_property("blend_mode", unreal_module.BlendMode.BLEND_ADDITIVE)
    material.set_editor_property("shading_model", unreal_module.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", True)
    material.set_editor_property("use_material_attributes", False)


def build_sprite_material_graph(unreal_module, material, spec: dict, sprite_texture=None) -> None:
    library = unreal_module.MaterialEditingLibrary
    palette = palette_as_linear_colors(spec["color_palette"])

    texture_sample = None
    if sprite_texture and hasattr(unreal_module, "MaterialExpressionTextureSampleParameter2D"):
        texture_sample = library.create_material_expression(material, unreal_module.MaterialExpressionTextureSampleParameter2D, -980, -160)
        texture_sample.set_editor_property("parameter_name", "SpriteTexture")
        texture_sample.set_editor_property("texture", sprite_texture)

    particle_color = library.create_material_expression(material, unreal_module.MaterialExpressionParticleColor, -760, 40)
    core_color = library.create_material_expression(material, unreal_module.MaterialExpressionVectorParameter, -760, 220)
    core_color.set_editor_property("parameter_name", "CoreColor")
    core_color.set_editor_property("default_value", palette[0])

    strength = library.create_material_expression(material, unreal_module.MaterialExpressionScalarParameter, -520, 260)
    strength.set_editor_property("parameter_name", "EmissiveStrength")
    strength.set_editor_property("default_value", inferred_emissive_strength(spec))

    particle_tint_multiply = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -520, 60)
    texture_color_multiply = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -300, 20)
    emissive_multiply = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -80, 90)
    opacity = library.create_material_expression(material, unreal_module.MaterialExpressionScalarParameter, -300, 300)
    opacity.set_editor_property("parameter_name", "Opacity")
    opacity.set_editor_property("default_value", 0.92)
    opacity_multiply = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -60, 300)

    library.connect_material_expressions(particle_color, "RGB", particle_tint_multiply, "A")
    library.connect_material_expressions(core_color, "", particle_tint_multiply, "B")
    if texture_sample:
        library.connect_material_expressions(texture_sample, "RGB", texture_color_multiply, "A")
        library.connect_material_expressions(particle_tint_multiply, "", texture_color_multiply, "B")
        library.connect_material_expressions(texture_sample, "A", opacity_multiply, "A")
        library.connect_material_expressions(opacity, "", opacity_multiply, "B")
        opacity_output = opacity_multiply
    else:
        library.connect_material_expressions(particle_tint_multiply, "", texture_color_multiply, "A")
        opacity_output = opacity
    library.connect_material_expressions(texture_color_multiply, "", emissive_multiply, "A")
    library.connect_material_expressions(strength, "", emissive_multiply, "B")
    library.connect_material_property(emissive_multiply, "", unreal_module.MaterialProperty.MP_EMISSIVE_COLOR)
    library.connect_material_property(opacity_output, "", unreal_module.MaterialProperty.MP_OPACITY)
    library.layout_material_expressions(material)


def palette_as_linear_colors(palette: list[str]) -> list:
    try:
        import unreal
    except ImportError:
        return []
    colors = [hex_to_linear_color(unreal, color) for color in palette]
    return colors or [unreal.LinearColor(1.0, 0.5, 0.1, 1.0)]


def hex_to_linear_color(unreal_module, color: str):
    color = color.lstrip("#")
    red = int(color[0:2], 16) / 255.0
    green = int(color[2:4], 16) / 255.0
    blue = int(color[4:6], 16) / 255.0
    return unreal_module.LinearColor(red, green, blue, 1.0)


def hex_to_rgba_tuple(color: str) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255)


def mix_color(a: tuple[int, int, int, int], b: tuple[int, int, int, int], amount: float) -> tuple[int, int, int, int]:
    amount = clamp(amount)
    return (
        int(a[0] + (b[0] - a[0]) * amount),
        int(a[1] + (b[1] - a[1]) * amount),
        int(a[2] + (b[2] - a[2]) * amount),
        255,
    )


def wave(value: float) -> float:
    return math.sin(value * math.tau)


def gaussian(value: float, center: float, width: float) -> float:
    if width <= 0:
        return 0.0
    normalized = (value - center) / width
    return math.exp(-(normalized * normalized) * 1.45)


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 0.0
    x = clamp((value - edge0) / (edge1 - edge0))
    return x * x * (3.0 - 2.0 * x)


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def is_fire_spec(spec: dict) -> bool:
    if spec.get("effect_type") == "fire_or_flame":
        return True
    visual_profile = spec.get("visual_profile", {})
    return visual_profile.get("style_hint") in {"high_intensity_stylized_fire", "smoky_fire_impact"}


def primary_sprite_shape(spec: dict) -> str:
    plan = spec.get("vfx_plan") or {}
    primary_name = plan.get("primary_emitter")
    for emitter in plan.get("emitters", []):
        if emitter.get("name") == primary_name:
            return emitter.get("sprite_shape", "")
    return ""


def primary_sprite_source_path(spec: dict) -> Path | None:
    plan = spec.get("vfx_plan") or {}
    primary_name = plan.get("primary_emitter")
    for emitter in plan.get("emitters", []):
        if emitter.get("name") != primary_name:
            continue
        source = emitter.get("sprite_source")
        if not source:
            return None
        source_path = Path(source)
        if source_path.exists():
            return source_path
    return None


def try_set_editor_property(asset, property_name: str, value) -> None:
    try:
        asset.set_editor_property(property_name, value)
    except Exception:
        pass


def inferred_emissive_strength(spec: dict) -> float:
    visual_profile = spec.get("visual_profile", {})
    bright = float(visual_profile.get("bright_pixel_ratio", 0.08) or 0.08)
    vertical = float(visual_profile.get("vertical_energy", 0.3) or 0.3)
    if visual_profile.get("style_hint") == "white_gold_glowing_shards":
        return round(18.0 + bright * 24.0, 2)
    if is_fire_spec(spec):
        return round(8.0 + bright * 28.0 + vertical * 8.0, 2)
    return round(12.0 + bright * 38.0 + vertical * 10.0, 2)


def create_niagara_system_asset(unreal_module, asset_name: str, destination_path: str) -> dict:
    asset_tools = unreal_module.AssetToolsHelpers.get_asset_tools()
    errors: list[str] = []
    factory_names = ["NiagaraSystemFactoryNew", "NiagaraSystemFactory"]

    for factory_name in factory_names:
        if not hasattr(unreal_module, factory_name):
            errors.append(f"{factory_name} is not exposed.")
            continue
        try:
            factory = getattr(unreal_module, factory_name)()
            asset_class = getattr(unreal_module, "NiagaraSystem", None)
            asset = asset_tools.create_asset(asset_name, destination_path, asset_class, factory)
            if asset:
                return {"created": True, "asset": str(asset), "errors": errors}
            errors.append(f"{factory_name} returned no asset.")
        except Exception as exc:  # Unreal Python exceptions are version-specific.
            errors.append(f"{factory_name} failed: {exc}")

    return {"created": False, "asset": None, "errors": errors}


def summarize_spec(spec: dict) -> dict:
    return {
        "name": spec["name"],
        "effect_type": spec["effect_type"],
        "motion": spec["motion"],
        "color_palette": spec["color_palette"],
        "duration_seconds": spec["timing"]["duration_seconds"],
        "looping": spec["timing"]["looping"],
        "visual_profile": compact_visual_profile(spec.get("visual_profile", {})),
        "vfx_plan": compact_vfx_plan(spec.get("vfx_plan")),
    }


def compact_visual_profile(visual_profile: dict) -> dict:
    if not visual_profile:
        return {}
    return {
        "shape_hint": visual_profile.get("shape_hint"),
        "motion_hint": visual_profile.get("motion_hint"),
        "style_hint": visual_profile.get("style_hint"),
        "palette": visual_profile.get("palette"),
        "bright_pixel_ratio": visual_profile.get("bright_pixel_ratio"),
        "warm_pixel_ratio": visual_profile.get("warm_pixel_ratio"),
        "bright_component_count": visual_profile.get("bright_component_count"),
        "square_component_ratio": visual_profile.get("square_component_ratio"),
        "isolated_bright_ratio": visual_profile.get("isolated_bright_ratio"),
        "vertical_energy": visual_profile.get("vertical_energy"),
        "base_energy": visual_profile.get("base_energy"),
        "dark_smoke_ratio": visual_profile.get("dark_smoke_ratio"),
        "sparks_hint": visual_profile.get("sparks_hint"),
    }


def compact_vfx_plan(plan: dict | None) -> dict:
    if not plan:
        return {}
    return {
        "visual_intent": plan.get("visual_intent"),
        "primary_emitter": plan.get("primary_emitter"),
        "reference_card_source": plan.get("reference_card_source"),
        "composition_layers": plan.get("composition_layers", []),
        "production_notes": plan.get("production_notes", []),
        "emitters": [
            {
                "name": emitter.get("name"),
                "role": emitter.get("role"),
                "sprite_shape": emitter.get("sprite_shape"),
                "material_style": emitter.get("material_style"),
                "motion": emitter.get("motion"),
                "spawn_rate": emitter.get("spawn_rate"),
                "lifetime_seconds": emitter.get("lifetime_seconds"),
                "start_size": emitter.get("start_size"),
                "end_size": emitter.get("end_size"),
                "color_palette": emitter.get("color_palette"),
                "sprite_source": emitter.get("sprite_source"),
            }
            for emitter in plan.get("emitters", [])
        ],
    }


def compact_emitter_plan(emitter: dict) -> dict:
    return {
        "name": emitter.get("name"),
        "role": emitter.get("role"),
        "sprite_shape": emitter.get("sprite_shape"),
        "material_style": emitter.get("material_style"),
        "motion": emitter.get("motion"),
        "spawn_rate": emitter.get("spawn_rate"),
        "lifetime_seconds": emitter.get("lifetime_seconds"),
        "start_size": emitter.get("start_size"),
        "end_size": emitter.get("end_size"),
        "color_palette": emitter.get("color_palette"),
        "sprite_source": emitter.get("sprite_source"),
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
