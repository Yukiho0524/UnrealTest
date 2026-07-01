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
    if template_result["created"]:
        unreal.EditorAssetLibrary.save_directory(destination_path, only_if_is_dirty=False, recursive=True)
        return {
            "mode": "unreal-editor",
            "status": template_result["status"],
            "asset_path": asset_path,
            "template": template_result["template"],
            "spec_summary": summarize_spec(spec),
            "message": "Created a non-empty Niagara System from an Unreal template.",
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
            "message": "Created initial Niagara System asset from VFXSpec, but template duplication was unavailable.",
        }

    return {
        "mode": "unreal-editor",
        "status": "partial",
        "asset_path": asset_path,
        "spec_summary": summarize_spec(spec),
        "template_errors": template_result["errors"],
        "factory_errors": factory_result["errors"],
        "message": "Created destination folder and validated spec, but Niagara factory creation did not succeed in this UE Python API.",
    }


def create_niagara_system_from_template(unreal_module, spec: dict, asset_path: str) -> dict:
    errors: list[str] = []
    template_paths = template_paths_for_effect(spec["effect_type"])

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


def annotate_asset(unreal_module, asset, spec: dict) -> None:
    try:
        unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_EffectType", spec["effect_type"])
        unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_Motion", spec["motion"])
        unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_ColorPalette", ",".join(spec["color_palette"]))
        unreal_module.EditorAssetLibrary.set_metadata_tag(asset, "VFXMCP_Source", spec["source"]["uri"])
    except Exception as exc:
        unreal_module.log_warning(f"VFX MCP could not write metadata: {exc}")


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
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
