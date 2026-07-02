from __future__ import annotations

import json
import subprocess
from pathlib import Path

from schemas import VFXSpec


def write_spec_for_unreal(spec: VFXSpec, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{spec.name}.json"
    output_path.write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")
    return output_path


def write_package_spec(spec: VFXSpec, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{spec.name}.vfxspec.json"
    output_path.write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")
    return output_path


def write_spec_dict(spec: dict, output_dir: Path, name: str | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    spec_name = name or str(spec.get("name") or "effect")
    output_path = output_dir / f"{spec_name}.vfxspec.json"
    output_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return output_path


def create_niagara_from_spec_command(spec_path: Path, destination_path: str) -> list[str]:
    return [
        "unreal-python",
        "unreal/Plugins/VFXMCP/Scripts/create_niagara_from_spec.py",
        str(spec_path),
        destination_path,
    ]


def run_unreal_generation(
    editor_path: Path,
    project_path: Path,
    script_path: Path,
    spec_path: Path,
    destination_path: str,
    timeout_seconds: int = 420,
) -> dict:
    if not editor_path.exists():
        raise FileNotFoundError(f"UnrealEditor-Cmd.exe was not found: {editor_path}")
    if not project_path.exists():
        raise FileNotFoundError(f"Unreal project was not found: {project_path}")
    if not script_path.exists():
        raise FileNotFoundError(f"Unreal Python script was not found: {script_path}")
    if not spec_path.exists():
        raise FileNotFoundError(f"VFXSpec file was not found: {spec_path}")

    result_path = Path("generated/unreal-results").resolve() / f"{spec_path.stem}.result.json"
    runner_path = write_unreal_runner(script_path, spec_path, destination_path, result_path)
    command = [
        str(editor_path),
        str(project_path),
        f"-ExecutePythonScript={runner_path}",
        "-unattended",
        "-nop4",
        "-nosplash",
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "runner": str(runner_path),
            "resultFile": str(result_path),
            "result": read_unreal_result(result_path),
            "returnCode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "succeeded": False,
            "status": "timeout",
            "message": f"Unreal generation exceeded {timeout_seconds} seconds.",
        }

    return {
        "command": command,
        "runner": str(runner_path),
        "resultFile": str(result_path),
        "result": read_unreal_result(result_path),
        "returnCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "succeeded": completed.returncode == 0,
    }


def write_unreal_runner(script_path: Path, spec_path: Path, destination_path: str, result_path: Path) -> Path:
    runner_dir = Path("generated/unreal-runners").resolve()
    runner_dir.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    runner_path = runner_dir / f"run_{spec_path.stem}.py"
    runner_path.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import json",
                "import importlib.util",
                "import sys",
                "",
                f"script_path = Path({str(script_path)!r})",
                f"spec_path = Path({str(spec_path)!r})",
                f"destination_path = {destination_path!r}",
                f"result_path = Path({str(result_path)!r})",
                "spec = importlib.util.spec_from_file_location('vfxmcp_create_niagara', script_path)",
                "module = importlib.util.module_from_spec(spec)",
                "assert spec.loader is not None",
                "spec.loader.exec_module(module)",
                "try:",
                "    vfx_spec = module.load_spec(spec_path)",
                "    module.validate_spec(vfx_spec)",
                "    module.ensure_unreal_folder(destination_path)",
                "    result = module.build_niagara_from_spec(vfx_spec, destination_path)",
                "    result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')",
                "    print(json.dumps(result, indent=2))",
                "except Exception as exc:",
                "    error = {'status': 'error', 'message': str(exc)}",
                "    result_path.write_text(json.dumps(error, indent=2), encoding='utf-8')",
                "    raise",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return runner_path


def read_unreal_result(result_path: Path) -> dict | None:
    if not result_path.exists():
        return None
    return json.loads(result_path.read_text(encoding="utf-8"))


def open_unreal_asset(
    editor_path: Path,
    project_path: Path,
    asset_path: str,
    fallback_asset_path: str | None = None,
) -> dict:
    if not editor_path.exists():
        raise FileNotFoundError(f"UnrealEditor.exe was not found: {editor_path}")
    if not project_path.exists():
        raise FileNotFoundError(f"Unreal project was not found: {project_path}")

    runner_path = write_open_asset_runner(asset_path, fallback_asset_path)
    runner_arg = str(runner_path).replace("\\", "/")
    command = [
        str(editor_path),
        str(project_path),
        f"-ExecCmds=py {runner_arg}",
        "-nop4",
    ]
    process = subprocess.Popen(command)
    return {
        "command": command,
        "runner": str(runner_path),
        "assetPath": asset_path,
        "fallbackAssetPath": fallback_asset_path,
        "processId": process.pid,
        "message": "Opening Unreal Editor and focusing the generated VFX asset.",
    }


def write_open_asset_runner(asset_path: str, fallback_asset_path: str | None = None) -> Path:
    runner_dir = Path("generated/unreal-runners").resolve()
    runner_dir.mkdir(parents=True, exist_ok=True)
    safe_name = asset_path.strip("/").replace("/", "_").replace(".", "_") or "asset"
    runner_path = runner_dir / f"open_{safe_name}.py"
    runner_path.write_text(
        "\n".join(
            [
                "import unreal",
                "",
                f"asset_path = {asset_path!r}",
                f"fallback_asset_path = {fallback_asset_path!r}",
                "requested_asset_path = asset_path",
                "requested_name = requested_asset_path.rsplit('/', 1)[-1]",
                "if requested_name.startswith('L_') and requested_name.endswith('_VFXPreview'):",
                "    effect_from_level = requested_name[2:-11]",
                "    asset_path = f\"{requested_asset_path.rsplit('/', 1)[0]}/NS_{effect_from_level}\"",
                "    unreal.log_warning(f'VFX MCP map preview opening is disabled for stability; opening primary Niagara instead: {asset_path}')",
                "if not unreal.EditorAssetLibrary.does_asset_exist(asset_path) and fallback_asset_path:",
                "    unreal.log_warning(f'VFX MCP preview asset was not found, falling back: {asset_path} -> {fallback_asset_path}')",
                "    asset_path = fallback_asset_path",
                "asset_name = asset_path.rsplit('/', 1)[-1]",
                "asset_dir = asset_path.rsplit('/', 1)[0]",
                "effect_name = asset_name[3:] if asset_name.startswith('NS_') else asset_name",
                "effect_name = effect_name[3:-11] if effect_name.startswith('BP_') and effect_name.endswith('_VFXPreview') else effect_name",
                "related_paths = [",
                "    asset_path,",
                "    f'{asset_dir}/BP_{effect_name}_VFXPreview',",
                "    f'{asset_dir}/NS_{effect_name}',",
                "    f'{asset_dir}/T_{effect_name}_VFX_Sprite',",
                "    f'{asset_dir}/M_{effect_name}_VFX',",
                "    f'{asset_dir}/MI_{effect_name}_VFX',",
                "]",
                "bundle_prefixes = [",
                "    f'{asset_dir}/NS_{effect_name}',",
                "    f'{asset_dir}/T_{effect_name}',",
                "    f'{asset_dir}/M_{effect_name}',",
                "    f'{asset_dir}/MI_{effect_name}',",
                "    f'{asset_dir}/T_{effect_name}_reference_card',",
                "    f'{asset_dir}/M_{effect_name}_reference_card',",
                "    f'{asset_dir}/MI_{effect_name}_reference_card',",
                "]",
                "bundle_paths = []",
                "try:",
                "    for listed_path in unreal.EditorAssetLibrary.list_assets(asset_dir, recursive=False, include_folder=False):",
                "        package_path = listed_path.split('.', 1)[0]",
                "        if any(package_path.startswith(prefix) for prefix in bundle_prefixes):",
                "            bundle_paths.append(package_path)",
                "except Exception as exc:",
                "    unreal.log_warning(f'VFX MCP could not list bundle assets: {exc}')",
                "existing_paths = []",
                "for path in [*related_paths, *bundle_paths]:",
                "    if path not in existing_paths and unreal.EditorAssetLibrary.does_asset_exist(path):",
                "        existing_paths.append(path)",
                "asset = unreal.EditorAssetLibrary.load_asset(asset_path)",
                "if asset is None:",
                "    unreal.log_error(f'VFX MCP could not find asset: {asset_path}')",
                "else:",
                "    unreal.EditorAssetLibrary.sync_browser_to_objects(existing_paths)",
                "    asset_editor = unreal.get_editor_subsystem(unreal.AssetEditorSubsystem)",
                "    asset_editor.open_editor_for_assets([asset])",
                "    unreal.log(f'VFX MCP opened generated asset: {asset_path}; related assets: {existing_paths}')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return runner_path
