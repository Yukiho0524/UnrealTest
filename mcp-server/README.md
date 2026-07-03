# Unreal VFX MCP Server

This server is the first-layer bridge between reference material and Unreal Engine VFX generation.

The MVP flow is:

1. Read images from a reference folder.
2. Convert each image into a portable `VFXSpec`.
3. Save specs as JSON for inspection and iteration.
4. Pass a spec to Unreal-side tooling to create Niagara assets.

The current implementation has a structured `reference_understanding` layer. By default it uses local image heuristics. Set `VFXMCP_VISION_PROVIDER=openai` and `OPENAI_API_KEY` to use the OpenAI vision adapter before pass generation.

## Local Run

```powershell
python mcp-server/server.py analyze-folder samples/references --out generated/specs
```

## Planned MCP Tools

- `analyze_reference_folder(path) -> VFXSpec[]`
- `ingest_reference_url(url, package_name) -> ReferencePackage`
- `understand_reference_package(path, vision_provider) -> ReferenceUnderstanding`
- `create_niagara_from_spec(spec, destination_path) -> UnrealAssetResult`
- `preview_effect(asset_path) -> PreviewResult`
- `iterate_effect(asset_path, instruction) -> UnrealAssetResult`
