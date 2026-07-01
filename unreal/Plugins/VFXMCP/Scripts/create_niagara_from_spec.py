from __future__ import annotations

import json
import sys
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
            "message": "Run this script inside Unreal Editor Python to create assets.",
        }

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
        unreal.EditorAssetLibrary.save_directory(destination_path, only_if_is_dirty=False, recursive=True)
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
        unreal.EditorAssetLibrary.save_directory(destination_path, only_if_is_dirty=False, recursive=True)
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
    except Exception as exc:
        unreal_module.log_warning(f"VFX MCP could not write metadata: {exc}")


def create_vfx_material_assets(unreal_module, spec: dict, destination_path: str) -> dict:
    material_name = f"M_{spec['name']}_VFX"
    instance_name = f"MI_{spec['name']}_VFX"
    material_path = f"{destination_path}/{material_name}"
    instance_path = f"{destination_path}/{instance_name}"

    result = {
        "material_path": material_path,
        "material_instance_path": instance_path,
        "palette": spec["color_palette"],
        "errors": [],
        "created": False,
    }

    try:
        material = create_or_replace_material(unreal_module, material_name, destination_path, spec)
        material_instance = create_or_replace_material_instance(unreal_module, instance_name, destination_path, material, spec)
        result["created"] = bool(material and material_instance)
        return result
    except Exception as exc:
        result["errors"].append(str(exc))
        return result


def create_or_replace_material(unreal_module, material_name: str, destination_path: str, spec: dict):
    material_path = f"{destination_path}/{material_name}"
    if unreal_module.EditorAssetLibrary.does_asset_exist(material_path):
        unreal_module.EditorAssetLibrary.delete_asset(material_path)

    asset_tools = unreal_module.AssetToolsHelpers.get_asset_tools()
    factory = unreal_module.MaterialFactoryNew()
    material = asset_tools.create_asset(material_name, destination_path, unreal_module.Material, factory)
    if not material:
        raise RuntimeError(f"Could not create material: {material_path}")

    configure_material_properties(unreal_module, material)
    build_fire_material_graph(unreal_module, material, spec)
    annotate_asset(unreal_module, material, spec)
    unreal_module.EditorAssetLibrary.save_loaded_asset(material)
    return material


def create_or_replace_material_instance(unreal_module, instance_name: str, destination_path: str, material, spec: dict):
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


def build_fire_material_graph(unreal_module, material, spec: dict) -> None:
    library = unreal_module.MaterialEditingLibrary
    palette = palette_as_linear_colors(spec["color_palette"])

    particle_color = library.create_material_expression(material, unreal_module.MaterialExpressionParticleColor, -800, -120)
    core_color = library.create_material_expression(material, unreal_module.MaterialExpressionVectorParameter, -800, 80)
    core_color.set_editor_property("parameter_name", "CoreColor")
    core_color.set_editor_property("default_value", palette[0])

    strength = library.create_material_expression(material, unreal_module.MaterialExpressionScalarParameter, -580, 180)
    strength.set_editor_property("parameter_name", "EmissiveStrength")
    strength.set_editor_property("default_value", inferred_emissive_strength(spec))

    color_multiply = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -380, 0)
    emissive_multiply = library.create_material_expression(material, unreal_module.MaterialExpressionMultiply, -160, 40)
    opacity = library.create_material_expression(material, unreal_module.MaterialExpressionScalarParameter, -180, 220)
    opacity.set_editor_property("parameter_name", "Opacity")
    opacity.set_editor_property("default_value", 0.92)

    library.connect_material_expressions(particle_color, "RGB", color_multiply, "A")
    library.connect_material_expressions(core_color, "", color_multiply, "B")
    library.connect_material_expressions(color_multiply, "", emissive_multiply, "A")
    library.connect_material_expressions(strength, "", emissive_multiply, "B")
    library.connect_material_property(emissive_multiply, "", unreal_module.MaterialProperty.MP_EMISSIVE_COLOR)
    library.connect_material_property(opacity, "", unreal_module.MaterialProperty.MP_OPACITY)
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


def inferred_emissive_strength(spec: dict) -> float:
    visual_profile = spec.get("visual_profile", {})
    bright = float(visual_profile.get("bright_pixel_ratio", 0.08) or 0.08)
    vertical = float(visual_profile.get("vertical_energy", 0.3) or 0.3)
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
        "vertical_energy": visual_profile.get("vertical_energy"),
        "base_energy": visual_profile.get("base_energy"),
        "dark_smoke_ratio": visual_profile.get("dark_smoke_ratio"),
        "sparks_hint": visual_profile.get("sparks_hint"),
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
