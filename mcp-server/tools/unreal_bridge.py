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
    timeout_seconds: int = 180,
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

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )

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
