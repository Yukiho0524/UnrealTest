from __future__ import annotations

import json
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
