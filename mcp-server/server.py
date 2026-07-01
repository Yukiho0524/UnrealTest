from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.analyze_images import analyze_reference_folder
from tools.unreal_bridge import write_spec_for_unreal


def main() -> int:
    parser = argparse.ArgumentParser(description="Unreal VFX MCP server MVP utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_folder = subparsers.add_parser("analyze-folder", help="Analyze reference images into VFX specs.")
    analyze_folder.add_argument("folder", type=Path)
    analyze_folder.add_argument("--out", type=Path, default=Path("generated/specs"))

    args = parser.parse_args()

    if args.command == "analyze-folder":
        specs = analyze_reference_folder(args.folder)
        written = [write_spec_for_unreal(spec, args.out) for spec in specs]
        print(json.dumps({"count": len(written), "files": [str(path) for path in written]}, indent=2))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
