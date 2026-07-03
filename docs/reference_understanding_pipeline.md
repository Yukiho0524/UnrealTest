# Reference Understanding Pipeline

The VFX pipeline must understand the reference before generating textures or Unreal preview assets.

## Order

1. Analyze reference media into a structured VFX understanding report.
2. Decide effect category, dominant silhouette, motion model, required layers, renderer stack, and negative requirements.
3. Use that structure in the AI art prompt and asset-pass manifest.
4. Generate a pass bundle: beauty, alpha, masks, motion vectors, depth/thickness, distortion, lighting, and layout metadata.
5. Assemble the Unreal preview from the understood structure.
6. Review the result against the reference understanding before tuning particle counts or card sizes.

## Why

Previous iterations reached Unreal too quickly. The result looked like flat cards, decorative floor graphics, or a vertical glowing tower because the system did not first decide what the reference effect actually was.

The new `reference_understanding` block is the handoff contract between visual analysis, AI generation, and Unreal assembly. It is intentionally provider-agnostic: local heuristics can produce it now, and an OpenAI Vision, ComfyUI captioner, CLIP, EmberGen, or FluidNinja adapter can replace or enrich it later.

## CLI

```powershell
py mcp-server/server.py ingest-url https://example.com/reference.png --name fire_from_url
py mcp-server/server.py understand samples/references/fire
py mcp-server/server.py understand samples/references/fire --vision-provider openai
py mcp-server/server.py analyze-package samples/references/fire
py mcp-server/server.py prepare-assets samples/references/fire
py mcp-server/server.py review samples/references/fire
```

To use OpenAI vision:

```powershell
$env:OPENAI_API_KEY="sk-..."
$env:VFXMCP_VISION_PROVIDER="openai"
py mcp-server/server.py understand samples/references/fire --vision-provider openai
```

## Review Rule

The first review gate now checks whether a structured reference understanding exists. If the generated effect looks wrong, fix the understanding layer before tuning Unreal placement.
