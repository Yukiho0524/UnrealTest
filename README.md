# UnrealTest

Prototype workspace for researching an Unreal MCP architecture that turns visual references into Unreal Engine VFX assets.

## Goals

1. Read reference images from a project folder, infer the intended motion, and generate a matching VFX plan for Unreal.
2. Read a URL, capture or inspect the visual style, and generate the same kind of VFX plan.
3. Use an Unreal-side bridge to convert the plan into Niagara systems, materials, and preview actors.

## Current Architecture

```text
Reference folder or URL
  -> MCP intake tool
  -> image or page analysis
  -> VFXSpec JSON
  -> Unreal bridge
  -> Niagara / materials / preview actors
```

## Repository Layout

```text
mcp-server/
  server.py                  CLI entrypoint for the MCP MVP utilities
  schemas.py                 Python dataclasses for VFXSpec
  tools/
    analyze_images.py        Folder/image analysis stub
    unreal_bridge.py         Spec export and Unreal command helpers

specs/
  vfx_spec.schema.json       Portable VFX intent schema

samples/
  references/                Put source images here for local tests

unreal/
  Plugins/VFXMCP/            Unreal plugin prototype
```

## First Local Test

Place images into `samples/references`, preferably with descriptive names such as:

- `magic_burst.png`
- `fire_column.png`
- `electric_spark.png`
- `smoke_puff.png`

Then run:

```powershell
python mcp-server/server.py analyze-folder samples/references --out generated/specs
```

The command writes one JSON spec per image. The current analyzer uses filename heuristics so that the data contract can be tested before wiring in real vision analysis.

## Next Implementation Pass

- Replace filename heuristics with image analysis.
- Add `analyze_reference_url(url)`.
- Implement Niagara asset creation inside `unreal/Plugins/VFXMCP/Scripts/create_niagara_from_spec.py`.
- Add preview and iteration tools.
