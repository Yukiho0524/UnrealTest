# UnrealTest

Prototype workspace for researching an Unreal MCP architecture that turns visual references into Unreal Engine VFX assets.

## Goals

1. Read reference images from a project folder, infer the intended motion, and generate a matching VFX plan for Unreal.
2. Read a URL, capture or inspect the visual style, and generate the same kind of VFX plan.
3. Use an Unreal-side bridge to convert the plan into Niagara systems, materials, and preview actors.

## Current Architecture

```text
Designer effect package or URL
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
  references/                Designer effect packages, such as fire/

unreal/
  UnrealTest.uproject        Unreal project descriptor pinned to UE 5.7
  engine.version.json        Exact local UE 5.7.4 installation metadata
  Plugins/VFXMCP/            Unreal plugin prototype
```

## Unreal Version

This workspace is pinned to the local Unreal Engine 5.7.4 install:

```text
D:\Program Files\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe
```

Open the Unreal project with:

```powershell
.\unreal\Scripts\Open-UnrealTest.ps1
```

## First Local Test

Place images or GIFs into an effect package, for example:

```text
samples/references/fire/images/
```

Then run the local UI:

```powershell
.\mcp-server\Start-VFXMCPUI.ps1
```

Open:

```text
http://127.0.0.1:8765
```

The UI lets you choose:

- Unreal project: `UnrealTest`
- Effect package: `fire`
- Destination path: `/Game/VFX/Generated/fire`

You can also run the package analyzer directly:


```powershell
py mcp-server/server.py analyze-package samples/references/fire --out generated/specs
```

The command writes `generated/specs/fire.vfxspec.json`. The current analyzer uses package metadata and filename heuristics so that the data contract can be tested before wiring in real vision analysis.

## Next Implementation Pass

- Replace filename heuristics with image analysis.
- Add `analyze_reference_url(url)`.
- Implement Niagara asset creation inside `unreal/Plugins/VFXMCP/Scripts/create_niagara_from_spec.py`.
- Add preview and iteration tools.
