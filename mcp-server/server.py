from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.analyze_images import analyze_reference_folder
from tools.analyze_packages import analyze_effect_package, list_effect_packages
from tools.art_providers import generate_art_pass
from tools.asset_passes import build_asset_pass_manifest
from tools.review_gates import review_effect_package
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

    art_generate = subparsers.add_parser("generate-art", help="Run an external AI art provider pass for one effect package.")
    art_generate.add_argument("package", type=Path)
    art_generate.add_argument("--provider", default="comfyui")
    art_generate.add_argument("--prompt", default=None)
    art_generate.add_argument("--out", type=Path, default=Path("generated/ai-art"))
    art_generate.add_argument("--base-url", default="http://127.0.0.1:8188")
    art_generate.add_argument("--workflow", default=None)

    prepare_assets = subparsers.add_parser("prepare-assets", help="Build an asset pass manifest for one effect package.")
    prepare_assets.add_argument("package", type=Path)
    prepare_assets.add_argument("--out", type=Path, default=Path("generated/asset-passes"))
    prepare_assets.add_argument("--ai-art-root", type=Path, default=Path("generated/ai-art"))

    review = subparsers.add_parser("review", help="Evaluate generated VFX against production review gates.")
    review.add_argument("package", type=Path)
    review.add_argument("--destination", default=None)

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

    if args.command == "generate-art":
        result = generate_art_pass(
            args.package,
            args.provider,
            prompt=args.prompt,
            output_root=args.out,
            options={
                "base_url": args.base_url,
                "workflow_path": args.workflow,
            },
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "prepare-assets":
        result = build_asset_pass_manifest(args.package, output_root=args.out, ai_art_root=args.ai_art_root)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "review":
        result = review_effect_package(args.package, destination_path=args.destination)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "ui":
        from ui_server import run_ui

        run_ui(args.host, args.port, args.references, args.out)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
