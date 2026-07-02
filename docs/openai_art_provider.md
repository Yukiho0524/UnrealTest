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
