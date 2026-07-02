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
            "preview": safe_preview_summary(spec, destination_path, [], {"created": False}),
            "message": "Run this script inside Unreal Editor Python to create assets.",
        }

    emitters = planned_emitters(spec)
    if len(emitters) > 1:
        return build_niagara_bundle_from_spec(unreal, spec, destination_path, emitters)

    single_result = build_single_niagara_system(unreal, spec, destination_path)
    cleanup_unsafe_preview_level(unreal, spec, destination_path)
    single_result["preview"] = create_preview_blueprint_from_bundle(unreal, spec, destination_path, [single_result], {"created": False})
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
    cleanup_unsafe_preview_level(unreal_module, spec, destination_path)
    preview = create_preview_blueprint_from_bundle(unreal_module, spec, destination_path, systems, reference_card)
    unreal_module.EditorAssetLibrary.save_directory(destination_path, only_if_is_dirty=False, recursive=True)
    return {
        "mode": "unreal-editor",
        "status": "created_bundle" if primary_system and primary_system.get("status") != "partial" else "partial_bundle",
        "asset_path": preview.get("asset_path") if preview.get("created") else (primary_system.get("asset_path") if primary_system else f"{destination_path}/NS_{spec['name']}"),
        "bundle": {
            "enabled": True,
            "primary_emitter": primary_emitter,
            "system_count": len(systems),
            "reference_card": reference_card,
            "systems": systems,
            "preview": preview,
        },
        "spec_summary": summarize_spec(spec),
        "message": "Created a VFX bundle and a stable Blueprint preview actor. Open the BP preview first; individual Niagara systems remain available for layer debugging.",
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


def safe_preview_summary(spec: dict, destination_path: str, systems: list[dict], reference_card: dict) -> dict:
    result = {
        "asset_path": None,
        "created": False,
        "strategy": "content_browser_bundle_sync",
        "unsafe_level_preview_path": preview_level_path(spec, destination_path),
        "reference_card": reference_card,
        "layers": [
            {
                "label": f"VFX Layer {index}: {(system.get('emitter_plan') or {}).get('name') or system.get('asset_path', 'layer').rsplit('/', 1)[-1]}",
                "system": system.get("asset_path"),
                "material": (system.get("materials") or {}).get("material_instance_path"),
            }
            for index, system in enumerate(systems, start=1)
        ],
        "errors": [],
    }
    return result


def create_preview_blueprint_from_bundle(unreal_module, spec: dict, destination_path: str, systems: list[dict], reference_card: dict) -> dict:
    blueprint_path = preview_blueprint_path(spec, destination_path)
    result = safe_preview_summary(spec, destination_path, systems, reference_card)
    result.update(
        {
            "asset_path": blueprint_path,
            "created": False,
            "strategy": "blueprint_actor_composite",
            "components": [],
        }
    )

    if not all(hasattr(unreal_module, name) for name in ("BlueprintEditorLibrary", "SubobjectDataSubsystem", "SubobjectDataBlueprintFunctionLibrary")):
        result["errors"].append("Blueprint component authoring API is not available in this Unreal Python environment.")
        return result

    try:
        if unreal_module.EditorAssetLibrary.does_asset_exist(blueprint_path):
            unreal_module.EditorAssetLibrary.delete_asset(blueprint_path)

        blueprint = unreal_module.BlueprintEditorLibrary.create_blueprint_asset_with_parent(blueprint_path, unreal_module.Actor)
        if not blueprint:
            result["errors"].append(f"Could not create preview Blueprint: {blueprint_path}")
            return result

        root_handle = blueprint_root_handle(unreal_module, blueprint)
        plane_mesh = unreal_module.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Plane")
        if not plane_mesh:
            result["errors"].append("Could not load /Engine/BasicShapes/Plane for Blueprint preview cards.")

        if plane_mesh and reference_card and reference_card.get("material_instance_path"):
            component = add_static_mesh_component_to_blueprint(
                unreal_module,
                blueprint,
                root_handle,
                "ReferenceCard",
                plane_mesh,
                reference_card.get("material_instance_path"),
                location=(0.0, 0.0, 155.0),
                rotation=(90.0, 0.0, 0.0),
                scale=(2.6, 2.6, 2.6),
            )
            result["components"].append(component)

        for index, system in enumerate(systems, start=1):
            emitter = system.get("emitter_plan") or {}
            material_path = (system.get("materials") or {}).get("material_instance_path")
            transform = preview_card_transform_for_emitter(emitter, index)
            if plane_mesh and material_path and transform:
                component = add_static_mesh_component_to_blueprint(
                    unreal_module,
                    blueprint,
                    root_handle,
                    f"LayerCard_{index}_{safe_asset_token(emitter.get('name', 'layer'))}",
                    plane_mesh,
                    material_path,
                    location=transform["location"],
                    rotation=transform["rotation"],
                    scale=transform["scale"],
                )
                component["timeline"] = preview_timeline_for_emitter(emitter)
                result["components"].append(component)
            niagara_transform = preview_niagara_transform_for_emitter(emitter, index)
            if niagara_transform:
                component = add_niagara_component_to_blueprint(
                    unreal_module,
                    blueprint,
                    root_handle,
                    f"NiagaraLayer_{index}_{safe_asset_token(emitter.get('name', 'layer'))}",
                    system.get("asset_path"),
                    transform=niagara_transform,
                )
                component["timeline"] = preview_timeline_for_emitter(emitter)
                result["components"].append(component)

        unreal_module.BlueprintEditorLibrary.compile_blueprint(blueprint)
        annotate_asset(unreal_module, blueprint, spec)
        unreal_module.EditorAssetLibrary.save_loaded_asset(blueprint, only_if_is_dirty=False)
        result["created"] = unreal_module.EditorAssetLibrary.does_asset_exist(blueprint_path)
        return result
    except Exception as exc:
        result["errors"].append(str(exc))
        return result


def preview_blueprint_path(spec: dict, destination_path: str) -> str:
    return f"{destination_path}/BP_{spec['name']}_VFXPreview"


def blueprint_root_handle(unreal_module, blueprint):
    subsystem = unreal_module.get_engine_subsystem(unreal_module.SubobjectDataSubsystem)
    library = unreal_module.SubobjectDataBlueprintFunctionLibrary
    handles = subsystem.k2_gather_subobject_data_for_blueprint(blueprint)
    root_handle = handles[0] if handles else None
    for handle in handles:
        data = library.get_data(handle)
        if library.is_root_component(data):
            return handle
    return root_handle


def add_static_mesh_component_to_blueprint(
    unreal_module,
    blueprint,
    parent_handle,
    name: str,
    mesh,
    material_path: str | None,
    location: tuple[float, float, float],
    rotation: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> dict:
    component, fail_reason = add_component_to_blueprint(unreal_module, blueprint, parent_handle, name, unreal_module.StaticMeshComponent)
    result = {"name": name, "type": "StaticMeshComponent", "material": material_path, "transform": {"location": location, "rotation": rotation, "scale": scale}, "created": bool(component), "errors": []}
    if str(fail_reason):
        result["errors"].append(str(fail_reason))
    if not component:
        return result
    try:
        component.set_static_mesh(mesh)
        if material_path:
            material = unreal_module.EditorAssetLibrary.load_asset(material_path)
            if material:
                component.set_material(0, material)
            else:
                result["errors"].append(f"Material does not exist: {material_path}")
        set_component_transform(unreal_module, component, location, rotation, scale)
    except Exception as exc:
        result["errors"].append(str(exc))
    return result


def add_niagara_component_to_blueprint(
    unreal_module,
    blueprint,
    parent_handle,
    name: str,
    system_path: str | None,
    transform: dict,
) -> dict:
    component, fail_reason = add_component_to_blueprint(unreal_module, blueprint, parent_handle, name, unreal_module.NiagaraComponent)
    result = {"name": name, "type": "NiagaraComponent", "system": system_path, "transform": transform, "created": bool(component), "errors": []}
    if str(fail_reason):
        result["errors"].append(str(fail_reason))
    if not component:
        return result
    try:
        if system_path:
            system = unreal_module.EditorAssetLibrary.load_asset(system_path)
            if system:
                if hasattr(component, "set_asset"):
                    component.set_asset(system)
                else:
                    component.set_editor_property("asset", system)
            else:
                result["errors"].append(f"Niagara system does not exist: {system_path}")
        set_component_transform(unreal_module, component, transform["location"], transform["rotation"], transform["scale"])
    except Exception as exc:
        result["errors"].append(str(exc))
    return result


def add_component_to_blueprint(unreal_module, blueprint, parent_handle, name: str, component_class):
    subsystem = unreal_module.get_engine_subsystem(unreal_module.SubobjectDataSubsystem)
    library = unreal_module.SubobjectDataBlueprintFunctionLibrary
    params = unreal_module.AddNewSubobjectParams()
    params.set_editor_property("blueprint_context", blueprint)
    params.set_editor_property("parent_handle", parent_handle)
    params.set_editor_property("new_class", component_class)
    params.set_editor_property("conform_transform_to_parent", False)
    handle, fail_reason = subsystem.add_new_subobject(params)
    if not handle:
        return None, fail_reason
    subsystem.rename_subobject_member_variable(blueprint, handle, name)
    data = library.get_data(handle)
    return library.get_object_for_blueprint(data, blueprint), fail_reason


def set_component_transform(unreal_module, component, location, rotation, scale) -> None:
    try_set_editor_property(component, "relative_location", unreal_module.Vector(*location))
    try_set_editor_property(component, "relative_rotation", unreal_module.Rotator(*rotation))
    try_set_editor_property(component, "relative_scale3d", unreal_module.Vector(*scale))


def preview_card_transform_for_emitter(emitter: dict, index: int) -> dict | None:
    settings = emitter.get("unreal_settings", {})
    card = settings.get("preview", {}).get("card", {}) if isinstance(settings, dict) else {}
    if card:
        if card.get("enabled") is False:
            return None
        return normalize_transform(card, default_rotation=(90.0, 0.0, 0.0))

    role = emitter.get("role")
    if role == "supporting_glow":
        return {"location": (0.0, 0.0, 4.0), "rotation": (0.0, 0.0, 0.0), "scale": (3.0, 3.0, 1.0)}
    if role == "fire_pillar":
        return {"location": (0.0, 0.0, 170.0), "rotation": (90.0, 0.0, 0.0), "scale": (1.2, 2.65, 1.2)}
    if role == "flame_slashes":
        return {"location": (0.0, 0.0, 78.0), "rotation": (90.0, 0.0, -8.0), "scale": (2.35, 1.55, 1.0)}
    if role == "ground_energy_ring":
        return {"location": (0.0, 0.0, 5.0), "rotation": (0.0, 0.0, 0.0), "scale": (3.35, 3.35, 1.0)}
    if role == "primary_bolt":
        return {"location": (0.0, 0.0, 152.0), "rotation": (90.0, 0.0, 0.0), "scale": (1.05, 2.25, 1.05)}
    if role == "secondary_bolts":
        return {"location": (0.0, 0.0, 120.0), "rotation": (90.0, 0.0, -8.0), "scale": (1.85, 1.65, 1.0)}
    if role == "impact_core":
        return {"location": (0.0, 0.0, 18.0), "rotation": (90.0, 0.0, 0.0), "scale": (1.45, 1.45, 1.45)}
    if role == "primary_body":
        return {"location": (0.0, -1.0, 135.0), "rotation": (90.0, 0.0, 0.0), "scale": (1.55, 1.55, 1.55)}
    if role == "secondary_body":
        return {"location": (5.0, 1.5, 128.0), "rotation": (90.0, 0.0, -7.0), "scale": (1.85, 1.85, 1.85)}
    if role == "atmospheric_wisp":
        return {"location": (-6.0, 2.0, 178.0), "rotation": (90.0, 0.0, 8.0), "scale": (2.0, 2.0, 2.0)}
    if role == "primary_particles":
        return {"location": (0.0, 0.0, 135.0 + index * 3.0), "rotation": (90.0, 0.0, 0.0), "scale": (1.2, 1.2, 1.2)}
    if role in {"detail_particles", "accent_particles"}:
        return None
    return {"location": (index * 3.0, 0.0, 145.0 + index * 5.0), "rotation": (90.0, 0.0, 0.0), "scale": (1.0, 1.0, 1.0)}


def preview_niagara_transform_for_emitter(emitter: dict, index: int) -> dict | None:
    settings = emitter.get("unreal_settings", {})
    niagara = settings.get("preview", {}).get("niagara", {}) if isinstance(settings, dict) else {}
    if niagara.get("enabled") is False:
        return None
    if niagara:
        return normalize_transform(
            niagara,
            default_location=(-36.0, (index - 1) * 34.0, 118.0),
            default_rotation=(0.0, 0.0, 0.0),
            default_scale=(1.0, 1.0, 1.0),
        )
    return {"location": (-36.0, (index - 1) * 34.0, 118.0), "rotation": (0.0, 0.0, 0.0), "scale": (1.0, 1.0, 1.0)}


def preview_timeline_for_emitter(emitter: dict) -> dict:
    settings = emitter.get("unreal_settings", {})
    timeline = settings.get("timeline", {}) if isinstance(settings, dict) else {}
    if isinstance(timeline, dict):
        return timeline
    return {}


def normalize_transform(
    transform: dict,
    default_location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    default_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    default_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> dict:
    return {
        "location": tuple3(transform.get("location"), default_location),
        "rotation": tuple3(transform.get("rotation"), default_rotation),
        "scale": tuple3(transform.get("scale"), default_scale),
    }


def tuple3(value, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return fallback
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return fallback


def preview_level_path(spec: dict, destination_path: str) -> str:
    return f"{destination_path}/L_{spec['name']}_VFXPreview"


def cleanup_unsafe_preview_level(unreal_module, spec: dict, destination_path: str) -> dict:
    level_path = preview_level_path(spec, destination_path)
    result = {"asset_path": level_path, "deleted": False, "errors": []}
    try:
        if unreal_module.EditorAssetLibrary.does_asset_exist(level_path):
            result["deleted"] = bool(unreal_module.EditorAssetLibrary.delete_asset(level_path))
    except Exception as exc:
        result["errors"].append(str(exc))
    return result


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
    alpha_texture_path = f"{destination_path}/{texture_name}_Alpha"
    distortion_texture_path = f"{destination_path}/{texture_name}_Distortion"

    result = {
        "material_path": material_path,
        "material_instance_path": instance_path,
        "texture_path": texture_path,
        "alpha_texture_path": None,
        "distortion_texture_path": None,
        "palette": spec["color_palette"],
        "errors": [],
        "created": False,
    }

    try:
        texture = create_or_replace_sprite_texture(unreal_module, texture_name, destination_path, spec)
        alpha_texture = create_or_replace_alpha_texture(unreal_module, f"{texture_name}_Alpha", destination_path, spec)
        distortion_texture = create_or_replace_distortion_texture(unreal_module, f"{texture_name}_Distortion", destination_path, spec)
        if alpha_texture:
            result["alpha_texture_path"] = alpha_texture_path
        if distortion_texture:
            result["distortion_texture_path"] = distortion_texture_path
        material = create_or_replace_material(unreal_module, material_name, destination_path, spec, texture, alpha_texture, distortion_texture)
        material_instance = create_or_replace_material_instance(unreal_module, instance_name, destination_path, material, spec, texture, alpha_texture, distortion_texture)
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


def create_or_replace_alpha_texture(unreal_module, texture_name: str, destination_path: str, spec: dict):
    alpha_source = primary_alpha_source_path(spec)
    if not alpha_source:
        return None
    texture = import_texture_from_source(unreal_module, texture_name, destination_path, alpha_source, spec)
    configure_alpha_texture_asset(unreal_module, texture)
    unreal_module.EditorAssetLibrary.save_loaded_asset(texture)
    return texture


def create_or_replace_distortion_texture(unreal_module, texture_name: str, destination_path: str, spec: dict):
    distortion_source = primary_distortion_source_path(spec)
    if not distortion_source:
        return None
    texture = import_texture_from_source(unreal_module, texture_name, destination_path, distortion_source, spec)
    configure_distortion_texture_asset(unreal_module, texture)
    unreal_module.EditorAssetLibrary.save_loaded_asset(texture)
    return texture


def import_texture_from_source(unreal_module, texture_name: str, destination_path: str, source_path: Path, spec: dict):
    texture_path = f"{destination_path}/{texture_name}"
    if unreal_module.EditorAssetLibrary.does_asset_exist(texture_path):
        unreal_module.EditorAssetLibrary.delete_asset(texture_path)

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
        raise RuntimeError(f"Could not import texture: {texture_path}")
    annotate_asset(unreal_module, texture, spec)
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
    elif sprite_shape == "fire_pillar":
        pixels = fire_pillar_pixels(width, height, spec)
    elif sprite_shape == "flame_slash":
        pixels = flame_slash_pixels(width, height, spec)
    elif sprite_shape == "fire_rune_ring":
        pixels = fire_rune_ring_pixels(width, height, spec)
    elif sprite_shape == "impact_flash":
        pixels = impact_flash_pixels(width, height, spec)
    elif sprite_shape == "lightning_bolt":
        pixels = lightning_bolt_pixels(width, height, spec)
    elif sprite_shape == "lightning_branch":
        pixels = lightning_branch_pixels(width, height, spec)
    elif sprite_shape == "ground_glow":
        pixels = ground_glow_pixels(width, height, spec)
    elif sprite_shape == "smoke_wisp":
        pixels = smoke_wisp_pixels(width, height, spec)
    elif sprite_shape in {"flame_tongue", "flame_wisp"} or (is_fire_spec(spec) and not sprite_shape):
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


def fire_pillar_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec.get("color_palette", [])]
    core = palette[0] if palette else (255, 244, 176, 255)
    mid = palette[1] if len(palette) > 1 else (255, 178, 46, 255)
    edge = palette[2] if len(palette) > 2 else (255, 74, 18, 255)
    smoke_dark = palette[3] if len(palette) > 3 else (42, 7, 3, 255)
    pixels = bytearray()
    for y in range(height):
        ny = y / (height - 1)
        up = 1.0 - ny
        for x in range(width):
            nx = (x / (width - 1) - 0.5) * 2.0
            taper = 0.18 + 0.48 * ((1.0 - up) ** 1.7)
            sway = 0.08 * wave(up * 1.4 + 0.15) + 0.04 * wave(up * 3.1)
            ragged = 0.055 * wave(nx * 3.6 + up * 6.5) + 0.035 * wave(nx * 8.2 - up * 4.2)
            height_mask = smoothstep(0.02, 0.16, up) * (1.0 - smoothstep(0.93, 1.0, up))
            body = gaussian(nx, sway + ragged, taper) * height_mask
            hot_core = gaussian(nx, sway * 0.35, max(0.045, taper * 0.28)) * smoothstep(0.06, 0.22, up) * (1.0 - smoothstep(0.7, 0.93, up))
            torn_left = gaussian(nx, -0.36 + 0.12 * wave(up * 2.0), 0.1 + 0.18 * (1.0 - up)) * smoothstep(0.18, 0.35, up) * (1.0 - smoothstep(0.68, 0.92, up))
            torn_right = gaussian(nx, 0.34 + 0.12 * wave(up * 2.3 + 0.4), 0.1 + 0.18 * (1.0 - up)) * smoothstep(0.12, 0.32, up) * (1.0 - smoothstep(0.64, 0.9, up))
            raw_alpha = clamp(body * 0.86 + hot_core * 0.5 + torn_left * 0.34 + torn_right * 0.32)
            alpha = smoothstep(0.08, 0.92, raw_alpha)
            color = mix_color(edge, mid, clamp(body + torn_left * 0.25 + torn_right * 0.25))
            color = mix_color(color, core, clamp(hot_core * 1.45))
            smoke_amount = smoothstep(0.68, 0.95, up) * (1.0 - hot_core) * 0.32
            color = mix_color(color, smoke_dark, smoke_amount)
            pixels.extend((color[0], color[1], color[2], int(alpha * 255)))
    return bytes(pixels)


def flame_slash_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec.get("color_palette", [])]
    hot = palette[0] if palette else (255, 178, 46, 255)
    orange = palette[1] if len(palette) > 1 else (255, 74, 18, 255)
    red = palette[2] if len(palette) > 2 else (120, 18, 6, 255)
    pixels = bytearray()
    for y in range(height):
        py = y / (height - 1)
        for x in range(width):
            px = x / (width - 1)
            nx = (px - 0.5) * 2.0
            ny = (py - 0.5) * 2.0
            arc = abs(ny - (0.38 * (nx * nx) - 0.18 + 0.08 * wave(px * 2.0)))
            left_tongue = gaussian(nx, -0.58 + 0.18 * wave(py * 1.4), 0.12) * smoothstep(0.24, 0.5, py) * (1.0 - smoothstep(0.78, 0.96, py))
            right_tongue = gaussian(nx, 0.56 + 0.12 * wave(py * 1.8), 0.13) * smoothstep(0.18, 0.42, py) * (1.0 - smoothstep(0.74, 0.94, py))
            arc_alpha = (1.0 - smoothstep(0.03, 0.14, arc)) * smoothstep(0.05, 0.22, px) * (1.0 - smoothstep(0.9, 1.0, px))
            breakup = 0.72 + 0.16 * wave(px * 8.0 + py * 4.0) + 0.12 * wave(px * 17.0 - py * 6.0)
            alpha = clamp((arc_alpha + left_tongue * 0.65 + right_tongue * 0.7) * breakup)
            color = mix_color(red, orange, clamp(alpha * 0.9 + arc_alpha * 0.3))
            color = mix_color(color, hot, clamp(arc_alpha * 0.85))
            pixels.extend((color[0], color[1], color[2], int(alpha * 255)))
    return bytes(pixels)


def fire_rune_ring_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec.get("color_palette", [])]
    orange = palette[0] if palette else (255, 178, 46, 255)
    hot = palette[1] if len(palette) > 1 else (255, 244, 176, 255)
    dark = palette[2] if len(palette) > 2 else (74, 11, 4, 255)
    pixels = bytearray()
    for y in range(height):
        ny = (y / (height - 1) - 0.5) * 2.0
        for x in range(width):
            nx = (x / (width - 1) - 0.5) * 2.0
            distance = ((nx * 0.86) ** 2 + (ny * 1.18) ** 2) ** 0.5
            angle = math.atan2(ny, nx)
            broken = 0.58 + 0.22 * wave(nx * 2.7 + ny * 1.3) + 0.16 * wave(nx * 5.1 - ny * 2.4)
            ring_outer = smoothstep(0.42, 0.54, distance) * (1.0 - smoothstep(0.57, 0.67, distance))
            ring_inner = smoothstep(0.2, 0.27, distance) * (1.0 - smoothstep(0.3, 0.38, distance))
            spiral_radius = 0.2 + 0.055 * wave(angle / math.tau + distance * 1.6)
            spiral = (1.0 - smoothstep(0.012, 0.05, abs(distance - spiral_radius))) * smoothstep(0.08, 0.18, distance) * (1.0 - smoothstep(0.38, 0.52, distance))
            glow = (1.0 - smoothstep(0.0, 0.88, distance)) * 0.22
            alpha = clamp((ring_outer * broken + ring_inner * 0.72 + spiral * 0.48 + glow) * (1.0 - smoothstep(0.9, 1.1, distance)))
            color = mix_color(dark, orange, clamp(alpha + ring_outer * 0.5))
            color = mix_color(color, hot, clamp((ring_inner + spiral) * 0.9))
            pixels.extend((color[0], color[1], color[2], int(alpha * 255)))
    return bytes(pixels)


def impact_flash_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec.get("color_palette", [])]
    hot = palette[0] if palette else (255, 244, 176, 255)
    orange = palette[1] if len(palette) > 1 else (255, 178, 46, 255)
    red = palette[2] if len(palette) > 2 else (255, 74, 18, 255)
    pixels = bytearray()
    for y in range(height):
        ny = (y / (height - 1) - 0.5) * 2.0
        for x in range(width):
            nx = (x / (width - 1) - 0.5) * 2.0
            distance = (nx * nx + (ny * 1.18) ** 2) ** 0.5
            star = max(abs(nx), abs(ny)) * 0.72 + min(abs(nx), abs(ny)) * 0.28
            core_alpha = 1.0 - smoothstep(0.05, 0.42, distance)
            star_alpha = 1.0 - smoothstep(0.04, 0.72, star)
            alpha = clamp(core_alpha * 0.9 + star_alpha * 0.34)
            color = mix_color(red, orange, clamp(star_alpha))
            color = mix_color(color, hot, clamp(core_alpha * 1.25))
            pixels.extend((color[0], color[1], color[2], int(alpha * 255)))
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


def ground_glow_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec.get("color_palette", [])]
    core = palette[0] if palette else (255, 240, 180, 255)
    edge = palette[1] if len(palette) > 1 else (255, 105, 32, 255)
    pixels = bytearray()
    for y in range(height):
        ny = (y / (height - 1) - 0.5) * 2.0
        for x in range(width):
            nx = (x / (width - 1) - 0.5) * 2.0
            distance = ((nx * 0.82) ** 2 + (ny * 1.22) ** 2) ** 0.5
            alpha = (1.0 - smoothstep(0.1, 0.92, distance)) * 0.82
            ring = smoothstep(0.32, 0.58, distance) * (1.0 - smoothstep(0.62, 0.98, distance))
            color = mix_color(core, edge, clamp(distance + ring * 0.25))
            pixels.extend((color[0], color[1], color[2], int(clamp(alpha) * 255)))
    return bytes(pixels)


def lightning_bolt_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec.get("color_palette", [])]
    core = palette[0] if palette else (245, 255, 255, 255)
    glow = palette[1] if len(palette) > 1 else (72, 220, 255, 255)
    purple = palette[3] if len(palette) > 3 else (128, 69, 255, 255)
    main_points = [
        (0.52, 0.02),
        (0.47, 0.18),
        (0.55, 0.33),
        (0.43, 0.49),
        (0.50, 0.66),
        (0.46, 0.82),
        (0.50, 0.98),
    ]
    branches = [
        [(0.55, 0.34), (0.70, 0.42), (0.76, 0.55)],
        [(0.43, 0.50), (0.29, 0.58), (0.22, 0.70)],
        [(0.50, 0.66), (0.63, 0.74), (0.72, 0.86)],
    ]
    return lightning_pixels(width, height, main_points, branches, core, glow, purple, core_width=0.014, glow_width=0.058)


def lightning_branch_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec.get("color_palette", [])]
    core = palette[0] if palette else (245, 255, 255, 255)
    glow = palette[1] if len(palette) > 1 else (72, 220, 255, 255)
    purple = palette[2] if len(palette) > 2 else (70, 110, 255, 255)
    main_points = [(0.18, 0.62), (0.35, 0.51), (0.48, 0.55), (0.64, 0.42), (0.82, 0.35)]
    branches = [
        [(0.35, 0.51), (0.28, 0.36), (0.18, 0.25)],
        [(0.48, 0.55), (0.53, 0.72), (0.61, 0.83)],
        [(0.64, 0.42), (0.76, 0.55), (0.88, 0.60)],
    ]
    return lightning_pixels(width, height, main_points, branches, core, glow, purple, core_width=0.012, glow_width=0.045)


def lightning_pixels(
    width: int,
    height: int,
    main_points: list[tuple[float, float]],
    branches: list[list[tuple[float, float]]],
    core: tuple[int, int, int, int],
    glow: tuple[int, int, int, int],
    purple: tuple[int, int, int, int],
    core_width: float,
    glow_width: float,
) -> bytes:
    segments = [(main_points[index], main_points[index + 1], 1.0) for index in range(len(main_points) - 1)]
    for branch in branches:
        segments.extend((branch[index], branch[index + 1], 0.72) for index in range(len(branch) - 1))

    pixels = bytearray()
    for y in range(height):
        py = y / (height - 1)
        for x in range(width):
            px = x / (width - 1)
            nearest = min((distance_to_segment(px, py, start, end), weight) for start, end, weight in segments)
            distance, weight = nearest
            core_alpha = (1.0 - smoothstep(core_width * weight, core_width * 2.1, distance)) * weight
            glow_alpha = (1.0 - smoothstep(core_width, glow_width, distance)) * 0.68 * weight
            outer_alpha = (1.0 - smoothstep(glow_width * 0.45, glow_width * 1.8, distance)) * 0.25 * weight
            alpha = clamp(core_alpha + glow_alpha + outer_alpha)
            color = mix_color(purple, glow, clamp(glow_alpha + core_alpha))
            color = mix_color(color, core, clamp(core_alpha * 1.4))
            pixels.extend((color[0], color[1], color[2], int(alpha * 255)))
    return bytes(pixels)


def distance_to_segment(px: float, py: float, start: tuple[float, float], end: tuple[float, float]) -> float:
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    length_squared = dx * dx + dy * dy
    if length_squared <= 0.000001:
        return ((px - sx) ** 2 + (py - sy) ** 2) ** 0.5
    amount = clamp(((px - sx) * dx + (py - sy) * dy) / length_squared)
    closest_x = sx + dx * amount
    closest_y = sy + dy * amount
    return ((px - closest_x) ** 2 + (py - closest_y) ** 2) ** 0.5


def smoke_wisp_pixels(width: int, height: int, spec: dict) -> bytes:
    palette = [hex_to_rgba_tuple(color) for color in spec.get("color_palette", [])]
    smoke = palette[0] if palette else (95, 82, 74, 255)
    warm_edge = palette[2] if len(palette) > 2 else (190, 90, 48, 255)
    pixels = bytearray()
    for y in range(height):
        ny01 = y / (height - 1)
        up = 1.0 - ny01
        for x in range(width):
            nx = (x / (width - 1) - 0.5) * 2.0
            center = 0.16 * wave(up * 1.7 + 0.2) + 0.08 * wave(up * 4.1)
            width_at_y = 0.18 + 0.34 * (1.0 - up)
            body = gaussian(nx, center, width_at_y)
            breakup = 0.58 + 0.22 * wave(nx * 2.3 + up * 3.7) + 0.2 * wave(nx * 4.8 - up * 2.1)
            height_mask = smoothstep(0.02, 0.18, up) * (1.0 - smoothstep(0.88, 1.0, up))
            alpha = clamp(body * height_mask * breakup * 0.55)
            color = mix_color(smoke, warm_edge, clamp(body * 0.32))
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


def configure_alpha_texture_asset(unreal_module, texture) -> None:
    try_set_editor_property(texture, "srgb", False)
    if hasattr(unreal_module, "TextureCompressionSettings"):
        try_set_editor_property(texture, "compression_settings", unreal_module.TextureCompressionSettings.TC_MASKS)
    if hasattr(unreal_module, "TextureMipGenSettings"):
        try_set_editor_property(texture, "mip_gen_settings", unreal_module.TextureMipGenSettings.TMGS_NO_MIPMAPS)


def configure_distortion_texture_asset(unreal_module, texture) -> None:
    try_set_editor_property(texture, "srgb", False)
    if hasattr(unreal_module, "TextureCompressionSettings"):
        compression = getattr(unreal_module.TextureCompressionSettings, "TC_VECTOR_DISPLACEMENTMAP", None)
        if compression is None:
            compression = getattr(unreal_module.TextureCompressionSettings, "TC_DEFAULT", None)
        if compression is not None:
            try_set_editor_property(texture, "compression_settings", compression)
    if hasattr(unreal_module, "TextureMipGenSettings"):
        try_set_editor_property(texture, "mip_gen_settings", unreal_module.TextureMipGenSettings.TMGS_NO_MIPMAPS)


def create_or_replace_material(unreal_module, material_name: str, destination_path: str, spec: dict, sprite_texture=None, alpha_texture=None, distortion_texture=None):
    material_path = f"{destination_path}/{material_name}"
    if unreal_module.EditorAssetLibrary.does_asset_exist(material_path):
        unreal_module.EditorAssetLibrary.delete_asset(material_path)

    asset_tools = unreal_module.AssetToolsHelpers.get_asset_tools()
    factory = unreal_module.MaterialFactoryNew()
    material = asset_tools.create_asset(material_name, destination_path, unreal_module.Material, factory)
    if not material:
        raise RuntimeError(f"Could not create material: {material_path}")

    configure_material_properties(unreal_module, material, spec)
    build_sprite_material_graph(unreal_module, material, spec, sprite_texture, alpha_texture, distortion_texture)
    annotate_asset(unreal_module, material, spec)
    unreal_module.EditorAssetLibrary.save_loaded_asset(material)
    return material


def create_or_replace_material_instance(unreal_module, instance_name: str, destination_path: str, material, spec: dict, sprite_texture=None, alpha_texture=None, distortion_texture=None):
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
    unreal_module.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(material_instance, "Opacity", inferred_opacity(spec))
    unreal_module.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(material_instance, "DistortionStrength", inferred_distortion_strength(spec))
    if sprite_texture:
        unreal_module.MaterialEditingLibrary.set_material_instance_texture_parameter_value(material_instance, "SpriteTexture", sprite_texture)
    if alpha_texture:
        unreal_module.MaterialEditingLibrary.set_material_instance_texture_parameter_value(material_instance, "AlphaTexture", alpha_texture)
    if distortion_texture:
        unreal_module.MaterialEditingLibrary.set_material_instance_texture_parameter_value(material_instance, "DistortionTexture", distortion_texture)
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


def configure_material_properties(unreal_module, material, spec: dict) -> None:
    material_style = primary_material_style(spec)
    blend_mode_name = material_setting(spec, "blend_mode")
    blend_mode = unreal_blend_mode(unreal_module, blend_mode_name, material_style)
    material.set_editor_property("blend_mode", blend_mode)
    material.set_editor_property("shading_model", unreal_module.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", True)
    material.set_editor_property("use_material_attributes", False)


def build_sprite_material_graph(unreal_module, material, spec: dict, sprite_texture=None, alpha_texture=None, distortion_texture=None) -> None:
    library = unreal_module.MaterialEditingLibrary
    palette = palette_as_linear_colors(spec["color_palette"])

    texture_sample = None
    if sprite_texture and hasattr(unreal_module, "MaterialExpressionTextureSampleParameter2D"):
        texture_sample = library.create_material_expression(material, unreal_module.MaterialExpressionTextureSampleParameter2D, -980, -160)
        texture_sample.set_editor_property("parameter_name", "SpriteTexture")
        texture_sample.set_editor_property("texture", sprite_texture)
        connect_flipbook_uv_if_needed(unreal_module, material, texture_sample, spec)

    alpha_sample = None
    if alpha_texture and hasattr(unreal_module, "MaterialExpressionTextureSampleParameter2D"):
        alpha_sample = library.create_material_expression(material, unreal_module.MaterialExpressionTextureSampleParameter2D, -980, 410)
        alpha_sample.set_editor_property("parameter_name", "AlphaTexture")
        alpha_sample.set_editor_property("texture", alpha_texture)
        connect_flipbook_uv_if_needed(unreal_module, material, alpha_sample, spec)

    if distortion_texture and hasattr(unreal_module, "MaterialExpressionTextureSampleParameter2D"):
        distortion_sample = library.create_material_expression(material, unreal_module.MaterialExpressionTextureSampleParameter2D, -1180, 610)
        distortion_sample.set_editor_property("parameter_name", "DistortionTexture")
        distortion_sample.set_editor_property("texture", distortion_texture)
        strength_node = library.create_material_expression(material, unreal_module.MaterialExpressionScalarParameter, -940, 610)
        strength_node.set_editor_property("parameter_name", "DistortionStrength")
        strength_node.set_editor_property("default_value", inferred_distortion_strength(spec))

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
    opacity.set_editor_property("default_value", inferred_opacity(spec))
    opacity_multiply = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -60, 300)
    alpha_mask_multiply = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -210, 410) if alpha_sample else None

    library.connect_material_expressions(particle_color, "RGB", particle_tint_multiply, "A")
    library.connect_material_expressions(core_color, "", particle_tint_multiply, "B")
    if texture_sample:
        library.connect_material_expressions(texture_sample, "RGB", texture_color_multiply, "A")
        library.connect_material_expressions(particle_tint_multiply, "", texture_color_multiply, "B")
        if alpha_sample and alpha_mask_multiply:
            library.connect_material_expressions(texture_sample, "A", alpha_mask_multiply, "A")
            library.connect_material_expressions(alpha_sample, "A", alpha_mask_multiply, "B")
            library.connect_material_expressions(alpha_mask_multiply, "", opacity_multiply, "A")
        else:
            library.connect_material_expressions(texture_sample, "A", opacity_multiply, "A")
        library.connect_material_expressions(opacity, "", opacity_multiply, "B")
        opacity_output = opacity_multiply
    elif alpha_sample:
        library.connect_material_expressions(particle_tint_multiply, "", texture_color_multiply, "A")
        library.connect_material_expressions(alpha_sample, "A", opacity_multiply, "A")
        library.connect_material_expressions(opacity, "", opacity_multiply, "B")
        opacity_output = opacity_multiply
    else:
        library.connect_material_expressions(particle_tint_multiply, "", texture_color_multiply, "A")
        opacity_output = opacity
    library.connect_material_expressions(texture_color_multiply, "", emissive_multiply, "A")
    library.connect_material_expressions(strength, "", emissive_multiply, "B")
    try:
        library.connect_material_property(texture_color_multiply, "", unreal_module.MaterialProperty.MP_BASE_COLOR)
    except Exception:
        pass
    library.connect_material_property(emissive_multiply, "", unreal_module.MaterialProperty.MP_EMISSIVE_COLOR)
    library.connect_material_property(opacity_output, "", unreal_module.MaterialProperty.MP_OPACITY)
    library.layout_material_expressions(material)


def connect_flipbook_uv_if_needed(unreal_module, material, texture_sample, spec: dict) -> None:
    settings = material_setting(spec, "flipbook")
    if not isinstance(settings, dict):
        return

    required = [
        "MaterialExpressionTextureCoordinate",
        "MaterialExpressionTime",
        "MaterialExpressionConstant",
        "MaterialExpressionMultiply",
        "MaterialExpressionDivide",
        "MaterialExpressionFloor",
        "MaterialExpressionFmod",
        "MaterialExpressionAppendVector",
        "MaterialExpressionAdd",
    ]
    missing = [name for name in required if not hasattr(unreal_module, name)]
    if missing:
        try:
            unreal_module.log_warning(f"VFX MCP flipbook material fallback: missing nodes {missing}")
        except Exception:
            pass
        return

    library = unreal_module.MaterialEditingLibrary
    columns = max(1.0, float(settings.get("columns", 1)))
    rows = max(1.0, float(settings.get("rows", 1)))
    frame_count = max(1.0, float(settings.get("frame_count", columns * rows)))
    fps = max(0.01, float(settings.get("fps", 12.0)))

    try:
        texcoord = library.create_material_expression(material, unreal_module.MaterialExpressionTextureCoordinate, -1510, -480)
        try_set_editor_property(texcoord, "u_tiling", 1.0 / columns)
        try_set_editor_property(texcoord, "v_tiling", 1.0 / rows)
        time = library.create_material_expression(material, unreal_module.MaterialExpressionTime, -1510, -300)
        fps_const = create_material_constant(unreal_module, material, fps, -1510, -190)
        columns_const = create_material_constant(unreal_module, material, columns, -1290, -80)
        rows_const = create_material_constant(unreal_module, material, rows, -1290, 40)
        frame_count_const = create_material_constant(unreal_module, material, frame_count, -1290, -210)

        time_scaled = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -1280, -290)
        frame_floor = library.create_material_expression(material, unreal_module.MaterialExpressionFloor, -1080, -290)
        frame_loop = library.create_material_expression(material, unreal_module.MaterialExpressionFmod, -890, -290)
        column_mod = library.create_material_expression(material, unreal_module.MaterialExpressionFmod, -690, -250)
        row_divide = library.create_material_expression(material, unreal_module.MaterialExpressionDivide, -690, -120)
        row_floor = library.create_material_expression(material, unreal_module.MaterialExpressionFloor, -500, -120)
        column_offset = library.create_material_expression(material, unreal_module.MaterialExpressionDivide, -500, -250)
        row_offset = library.create_material_expression(material, unreal_module.MaterialExpressionDivide, -310, -120)
        offset = library.create_material_expression(material, unreal_module.MaterialExpressionAppendVector, -110, -200)
        atlas_uv = library.create_material_expression(material, unreal_module.MaterialExpressionAdd, 90, -350)

        connect_material_expression_first(unreal_module, time, "", time_scaled, ["A"])
        connect_material_expression_first(unreal_module, fps_const, "", time_scaled, ["B"])
        connect_material_expression_first(unreal_module, time_scaled, "", frame_floor, ["", "Input", "X"])
        connect_material_expression_first(unreal_module, frame_floor, "", frame_loop, ["A"])
        connect_material_expression_first(unreal_module, frame_count_const, "", frame_loop, ["B"])
        connect_material_expression_first(unreal_module, frame_loop, "", column_mod, ["A"])
        connect_material_expression_first(unreal_module, columns_const, "", column_mod, ["B"])
        connect_material_expression_first(unreal_module, frame_loop, "", row_divide, ["A"])
        connect_material_expression_first(unreal_module, columns_const, "", row_divide, ["B"])
        connect_material_expression_first(unreal_module, row_divide, "", row_floor, ["", "Input", "X"])
        connect_material_expression_first(unreal_module, column_mod, "", column_offset, ["A"])
        connect_material_expression_first(unreal_module, columns_const, "", column_offset, ["B"])
        connect_material_expression_first(unreal_module, row_floor, "", row_offset, ["A"])
        connect_material_expression_first(unreal_module, rows_const, "", row_offset, ["B"])
        connect_material_expression_first(unreal_module, column_offset, "", offset, ["A"])
        connect_material_expression_first(unreal_module, row_offset, "", offset, ["B"])
        connect_material_expression_first(unreal_module, texcoord, "", atlas_uv, ["A"])
        connect_material_expression_first(unreal_module, offset, "", atlas_uv, ["B"])
        if not connect_material_expression_first(unreal_module, atlas_uv, "", texture_sample, ["Coordinates", "UVs", "Coordinate", "UV"]):
            unreal_module.log_warning("VFX MCP could not connect flipbook UVs to texture sample; the atlas will render as a grid.")
    except Exception as exc:
        try:
            unreal_module.log_warning(f"VFX MCP could not build flipbook UV graph; using static atlas: {exc}")
        except Exception:
            pass


def connect_material_expression_first(unreal_module, source, source_output: str, target, target_inputs: list[str]) -> bool:
    library = unreal_module.MaterialEditingLibrary
    valid_inputs = material_expression_input_names(unreal_module, target)
    if valid_inputs:
        target_inputs = [name for name in target_inputs if normalized_material_input_name(name) in valid_inputs]
        if not target_inputs:
            try:
                target_name = target.__class__.__name__
                unreal_module.log_warning(f"VFX MCP material connection skipped for {target_name}; valid inputs are {sorted(valid_inputs)}")
            except Exception:
                pass
            return False

    last_error = None
    for target_input in target_inputs:
        try:
            library.connect_material_expressions(source, source_output, target, target_input)
            return True
        except Exception as exc:
            last_error = exc
    try:
        target_name = target.__class__.__name__
        unreal_module.log_warning(f"VFX MCP material connection failed for {target_name} inputs {target_inputs}: {last_error}")
    except Exception:
        pass
    return False


def material_expression_input_names(unreal_module, expression) -> set[str]:
    try:
        names = unreal_module.MaterialEditingLibrary.get_material_expression_input_names(expression)
    except Exception:
        return set()
    return {normalized_material_input_name(str(name)) for name in names}


def normalized_material_input_name(name: str) -> str:
    return "" if name in {"", "None"} else name


def create_material_constant(unreal_module, material, value: float, x: int, y: int):
    node = unreal_module.MaterialEditingLibrary.create_material_expression(material, unreal_module.MaterialExpressionConstant, x, y)
    try_set_editor_property(node, "r", float(value))
    return node


def create_material_constant2(unreal_module, material, x_value: float, y_value: float, x: int, y: int):
    node = unreal_module.MaterialEditingLibrary.create_material_expression(material, unreal_module.MaterialExpressionConstant2Vector, x, y)
    try_set_editor_property(node, "r", float(x_value))
    try_set_editor_property(node, "g", float(y_value))
    if hasattr(unreal_module, "LinearColor"):
        try_set_editor_property(node, "constant", unreal_module.LinearColor(float(x_value), float(y_value), 0.0, 0.0))
    return node


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


def primary_material_style(spec: dict) -> str:
    plan = spec.get("vfx_plan") or {}
    primary_name = plan.get("primary_emitter")
    for emitter in plan.get("emitters", []):
        if emitter.get("name") == primary_name:
            return emitter.get("material_style", "")
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


def primary_alpha_source_path(spec: dict) -> Path | None:
    source = material_setting(spec, "alpha_source")
    if not source:
        return None
    source_path = Path(str(source))
    if source_path.exists():
        return source_path
    return None


def primary_distortion_source_path(spec: dict) -> Path | None:
    source = material_setting(spec, "distortion_source")
    if not source:
        return None
    source_path = Path(str(source))
    if source_path.exists():
        return source_path
    return None


def try_set_editor_property(asset, property_name: str, value) -> None:
    try:
        asset.set_editor_property(property_name, value)
    except Exception:
        pass


def inferred_emissive_strength(spec: dict) -> float:
    override = material_setting(spec, "emissive_strength", "emissive")
    if override is not None:
        return float(override)
    visual_profile = spec.get("visual_profile", {})
    bright = float(visual_profile.get("bright_pixel_ratio", 0.08) or 0.08)
    vertical = float(visual_profile.get("vertical_energy", 0.3) or 0.3)
    style = primary_material_style(spec)
    if "fire_pillar_core" in style:
        return 18.0
    if "fire_side_slashes" in style:
        return 11.0
    if "fire_ground_rune" in style:
        return 9.0
    if "fire_impact_flash" in style:
        return 20.0
    if "translucent_smoke_dust" in style:
        return 0.35
    if "electric_core_bolt" in style:
        return 22.0
    if "electric_branch" in style:
        return 15.0
    if "electric_impact" in style:
        return 14.0
    if "electric_ground" in style:
        return 7.0
    if "blue_white_sparks" in style:
        return 13.0
    if "smoke" in style:
        return 0.65
    if "base_glow" in style:
        return 5.25
    if "reference_card" in style:
        return 2.4
    if "outer_flame" in style:
        return round(5.5 + bright * 12.0 + vertical * 3.0, 2)
    if visual_profile.get("style_hint") == "white_gold_glowing_shards":
        return round(18.0 + bright * 24.0, 2)
    if is_fire_spec(spec):
        return round(8.0 + bright * 28.0 + vertical * 8.0, 2)
    return round(12.0 + bright * 38.0 + vertical * 10.0, 2)


def inferred_opacity(spec: dict) -> float:
    override = material_setting(spec, "opacity")
    if override is not None:
        return float(override)
    style = primary_material_style(spec)
    if "fire_pillar_core" in style:
        return 0.82
    if "fire_side_slashes" in style:
        return 0.66
    if "fire_ground_rune" in style:
        return 0.68
    if "fire_impact_flash" in style:
        return 0.78
    if "translucent_smoke_dust" in style:
        return 0.24
    if "electric_core_bolt" in style:
        return 0.9
    if "electric_branch" in style:
        return 0.72
    if "electric_impact" in style:
        return 0.56
    if "electric_ground" in style:
        return 0.34
    if "blue_white_sparks" in style:
        return 0.7
    if "reference_card" in style:
        return 0.38
    if "base_glow" in style:
        return 0.36
    if "smoke" in style:
        return 0.22
    if "outer_flame" in style:
        return 0.55
    if "glow" in style:
        return 0.42
    return 0.82


def inferred_distortion_strength(spec: dict) -> float:
    override = material_setting(spec, "distortion_strength")
    if override is not None:
        return float(override)
    style = primary_material_style(spec)
    if "smoke" in style or "translucent_smoke" in style:
        return 0.11
    if "fire_pillar" in style or "fire_side" in style:
        return 0.075
    if "electric" in style:
        return 0.045
    return 0.0


def primary_unreal_settings(spec: dict) -> dict:
    plan = spec.get("vfx_plan") or {}
    primary_name = plan.get("primary_emitter")
    for emitter in plan.get("emitters", []):
        if emitter.get("name") == primary_name:
            settings = emitter.get("unreal_settings") or {}
            return settings if isinstance(settings, dict) else {}
    return {}


def material_setting(spec: dict, *keys: str):
    material = primary_unreal_settings(spec).get("material", {})
    if not isinstance(material, dict):
        return None
    for key in keys:
        if key in material:
            return material[key]
    return None


def unreal_blend_mode(unreal_module, blend_mode_name, material_style: str):
    normalized = str(blend_mode_name or "").lower()
    if normalized in {"translucent", "blend_translucent"}:
        return unreal_module.BlendMode.BLEND_TRANSLUCENT
    if normalized in {"masked", "blend_masked"}:
        return unreal_module.BlendMode.BLEND_MASKED
    if normalized in {"opaque", "blend_opaque"}:
        return unreal_module.BlendMode.BLEND_OPAQUE
    return unreal_module.BlendMode.BLEND_TRANSLUCENT if "smoke" in material_style else unreal_module.BlendMode.BLEND_ADDITIVE


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
                "unreal_settings": emitter.get("unreal_settings", {}),
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
        "unreal_settings": emitter.get("unreal_settings", {}),
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
