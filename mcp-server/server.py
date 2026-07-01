from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.analyze_images import analyze_reference_folder
from tools.analyze_packages import analyze_effect_package, list_effect_packages
from tools.unreal_bridge import write_package_spec, write_spec_for_unreal


def main() -> int:
    parser = argparse.ArgumentParser(description="Unreal VFX MCP server MVP utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_folder = subparsers.add_parser("analyze-folder", help="Analyze reference images into VFX specs.")
    analyze_folder.add_argument("folder", type=Path)
    analyze_folder.add_argument("--out", type=Path, default=Path("generated/specs"))

    list_packages = subparsers.add_parser("list-packages", help="List effect packages under a reference root.")
    list_packages.add_argument("--root", type=Path, default=Path("samples/references"))

    analyze_package = subparsers.add_parser("analyze-package", help="Analyze one effect package into a VFX spec.")
    analyze_package.add_argument("package", type=Path)
    analyze_package.add_argument("--out", type=Path, default=Path("generated/specs"))

    ui = subparsers.add_parser("ui", help="Start the local VFX MCP web UI.")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--references", type=Path, default=Path("samples/references"))
    ui.add_argument("--out", type=Path, default=Path("generated/specs"))

    args = parser.parse_args()

    if args.command == "analyze-folder":
        specs = analyze_reference_folder(args.folder)
        written = [write_spec_for_unreal(spec, args.out) for spec in specs]
        print(json.dumps({"count": len(written), "files": [str(path) for path in written]}, indent=2))
        return 0

    if args.command == "list-packages":
        print(json.dumps({"packages": list_effect_packages(args.root)}, indent=2))
        return 0

    if args.command == "analyze-package":
        spec = analyze_effect_package(args.package)
        written = write_package_spec(spec, args.out)
        print(json.dumps({"spec": spec.to_dict(), "file": str(written)}, indent=2))
        return 0

    if args.command == "ui":
        from ui_server import run_ui

        run_ui(args.host, args.port, args.references, args.out)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
