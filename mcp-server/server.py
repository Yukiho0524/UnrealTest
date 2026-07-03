from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tools.analyze_images import analyze_reference_folder
from tools.analyze_packages import analyze_effect_package, find_package_media, list_effect_packages
from tools.art_providers import generate_art_pass
from tools.asset_passes import build_asset_pass_manifest
from tools.image_features import analyze_media_files
from tools.reference_understanding import build_reference_understanding
from tools.review_gates import review_effect_package
from tools.url_ingest import ingest_reference_url
from tools.unreal_bridge import write_package_spec, write_spec_for_unreal


def main() -> int:
    parser = argparse.ArgumentParser(description="Unreal VFX MCP server MVP utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_folder = subparsers.add_parser("analyze-folder", help="Analyze reference images into VFX specs.")
    analyze_folder.add_argument("folder", type=Path)
    analyze_folder.add_argument("--out", type=Path, default=Path("generated/specs"))

    list_packages = subparsers.add_parser("list-packages", help="List effect packages under a reference root.")
    list_packages.add_argument("--root", type=Path, default=Path("samples/references"))

    ingest_url = subparsers.add_parser("ingest-url", help="Download URL reference media into a package folder.")
    ingest_url.add_argument("url")
    ingest_url.add_argument("--name", default=None)
    ingest_url.add_argument("--root", type=Path, default=Path("samples/references"))

    analyze_package = subparsers.add_parser("analyze-package", help="Analyze one effect package into a VFX spec.")
    analyze_package.add_argument("package", type=Path)
    analyze_package.add_argument("--out", type=Path, default=Path("generated/specs"))
    analyze_package.add_argument("--vision-provider", default=None, choices=["local", "openai"])

    understand = subparsers.add_parser("understand", help="Analyze reference media into a structured VFX understanding report.")
    understand.add_argument("package", type=Path)
    understand.add_argument("--vision-provider", default=None, choices=["local", "openai"])

    art_generate = subparsers.add_parser("generate-art", help="Run an external AI art provider pass for one effect package.")
    art_generate.add_argument("package", type=Path)
    art_generate.add_argument("--provider", default="comfyui")
    art_generate.add_argument("--prompt", default=None)
    art_generate.add_argument("--out", type=Path, default=Path("generated/ai-art"))
    art_generate.add_argument("--base-url", default="http://127.0.0.1:8188")
    art_generate.add_argument("--workflow", default=None)
    art_generate.add_argument("--model", default=None)
    art_generate.add_argument("--size", default="1024x1024")
    art_generate.add_argument("--quality", default="high")
    art_generate.add_argument("--background", default="auto")
    art_generate.add_argument("--output-format", default="png")
    art_generate.add_argument("--passes", default="required")
    art_generate.add_argument("--include-optional", action="store_true")
    art_generate.add_argument("--max-passes", type=int, default=None)

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

    if args.command == "ingest-url":
        result = ingest_reference_url(args.url, package_name=args.name, references_root=args.root)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "analyze-package":
        set_vision_provider(args.vision_provider)
        spec = analyze_effect_package(args.package)
        written = write_package_spec(spec, args.out)
        print(json.dumps({"spec": spec.to_dict(), "file": str(written)}, indent=2))
        return 0

    if args.command == "understand":
        set_vision_provider(args.vision_provider)
        media_files = find_package_media(args.package)
        visual_profile = analyze_media_files(media_files)
        prompt_path = args.package / "prompt.md"
        prompt = prompt_path.read_text(encoding="utf-8").strip() if prompt_path.exists() else ""
        result = build_reference_understanding(args.package, media_files, visual_profile, prompt)
        print(json.dumps(result, indent=2))
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
                "model": args.model,
                "size": args.size,
                "quality": args.quality,
                "background": args.background,
                "output_format": args.output_format,
                "passes": args.passes,
                "include_optional": args.include_optional,
                "max_passes": args.max_passes,
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


def set_vision_provider(provider: str | None) -> None:
    if provider:
        os.environ["VFXMCP_VISION_PROVIDER"] = provider


if __name__ == "__main__":
    raise SystemExit(main())
