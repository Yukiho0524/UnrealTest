# OpenAI Art Provider

The `openai` art provider calls the OpenAI Image API and writes outputs into the same AI art manifest shape used by ComfyUI:

```text
generated/ai-art/<effect-name>/openai/<timestamp>/manifest.json
```

## Setup

```powershell
$env:OPENAI_API_KEY="sk-..."
```

## Commands

Generate required production passes:

```powershell
py mcp-server/server.py generate-art samples/references/firestorm --provider openai --passes required
```

Generate every pass declared by the analyzed VFX plan:

```powershell
py mcp-server/server.py generate-art samples/references/firestorm --provider openai --passes all
```

Generate a focused subset:

```powershell
py mcp-server/server.py generate-art samples/references/firestorm --provider openai --passes core_flame_flipbook,alpha_mask,distortion_flow
```

Useful options:

- `--model gpt-image-2`
- `--size 1024x1024`
- `--quality high`
- `--background auto`
- `--max-passes 2`

When reference images exist, the provider uses the largest static image as an image-edit reference. Without reference images it falls back to text-to-image generation. The manifest marks each output with `candidate_passes`, so `prepare-assets` can select OpenAI outputs before bootstrap procedural passes.

## AI-Derived Companion Passes

If an AI provider returns only a beauty/core image, `prepare-assets` can derive companion passes from that AI output:

- `alpha_mask`
- `distortion_flow`
- `normal_or_lighting`
- `depth_or_thickness`
- `layer_mask_pack`
- `sdf_or_vector_field`
- `renderer_layout_metadata`

These are marked as `source: ai_output_derivative`. They are better than falling all the way back to firestorm-only procedural bootstrap maps because their masks and volume cues follow the generated artwork, but review gates still keep them as warnings. Final-quality VFX should replace them with provider-native outputs or simulation-baked passes.
