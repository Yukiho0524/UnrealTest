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


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Usage: create_niagara_from_spec.py <spec.json> <destination_path>")
        return 2

    spec_path = Path(argv[1])
    destination_path = argv[2]
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
        import unreal  # noqa: F401
    except ImportError:
        return {
            "mode": "dry-run",
            "asset_path": f"{destination_path}/{spec['name']}",
            "message": "Run this script inside Unreal Editor Python to create assets.",
        }

    # TODO: Create Niagara System, emitter, material instance, and preview actor.
    # Keeping this behind one function makes it straightforward to swap in
    # Unreal-version-specific Niagara authoring code.
    return {
        "mode": "unreal-editor",
        "asset_path": f"{destination_path}/{spec['name']}",
        "message": "Spec validated. Niagara asset creation is the next implementation pass.",
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
