# Fire VFX Rebuild Research

This note records the next rebuild direction after reviewing Unreal/Niagara production documentation and the current fire preview failures.

## Current Failure

The current pipeline is still too close to this invalid path:

```text
reference image -> extracted/similar-looking sprite -> Niagara particles
```

That creates copied mini fire columns, pasted flame icons, or abstract blobs. It can look statistically similar to a reference frame while being wrong as a real-time VFX asset.

The pipeline must split these ideas:

- Reference similarity is an analysis and scoring target.
- Runtime sprites are small animation cells, not whole reference screenshots.
- The main flame body should come from authored flame tongues, ribbons, volume/mesh helpers, or baked fluid flipbooks.
- AI-generated images should become named VFX passes, not final viewport screenshots.

## Unreal/Niagara Lessons

Epic's Niagara Flipbook Baker workflow shows the intended direction for high-quality sprite flipbooks: create or simulate a Niagara system, often from Niagara Fluids, then bake that result to a flipbook for material playback. This is the opposite of cutting a static reference into particles.

For flame motion that reads as continuous streaks, Unreal's ribbon workflow is the better mental model than a sprite burst. Epic's ribbon tutorial explicitly replaces a sprite renderer with a Ribbon Renderer when the effect needs connected trails. Fire tongues, vortex bands, lightning, and streaking flame edges should use this style of renderer or a mesh/ribbon proxy rather than repeated cards.

Niagara sprite/SubUV documentation is still useful, but only for the right layer types: smoke wisps, embers, small lick cells, impact flashes, and baked fluid/flipbook cells. A full flame column inside each SubUV frame is the wrong asset.

Relevant references:

- Unreal Niagara Flipbook Baker: https://dev.epicgames.com/documentation/unreal-engine/niagara-flipbook-baker-quick-start-guide-in-unreal-engine
- Unreal Niagara ribbon effect: https://dev.epicgames.com/documentation/unreal-engine/how-to-create-a-ribbon-effect-in-niagara-for-unreal-engine
- Unreal sprite/SubUV particle setup: https://dev.epicgames.com/documentation/unreal-engine/create-a-sprite-particle-effect-in-niagara
- Unreal Niagara renderers reference: https://dev.epicgames.com/documentation/unreal-engine/render-module-reference-for-niagara-effects-in-unreal-engine
- Niagara Fluids overview: https://dev.epicgames.com/documentation/unreal-engine/niagara-fluids-in-unreal-engine

## AI Decomposition Lessons

OpenAI image and vision APIs can analyze images and use images as inputs for generation. For this project, vision should produce a strict VFX structure before any image generation:

```json
{
  "effect_category": "fire_plume",
  "primary_form": "sustained_flame_plume",
  "motion_model": "continuous upward flame lift with turbulent licking edges",
  "layers": [
    {"name": "core_heat_body", "renderer": "mesh_volume_or_fluid_flipbook"},
    {"name": "flame_tongue_ribbons", "renderer": "ribbon_or_mesh_streak"},
    {"name": "edge_lick_cells", "renderer": "subuv_sprite_cells"},
    {"name": "smoke_heat", "renderer": "subuv_translucent_smoke"},
    {"name": "embers", "renderer": "small_sprites"}
  ],
  "forbidden_runtime_assets": [
    "complete_reference_cutout_as_particle",
    "whole_fire_column_sprite",
    "large_airborne_billboard_card"
  ]
}
```

Structured Outputs are important here because the model result must be machine-checkable. The project should validate the AI decomposition before generating assets.

Image generation should then create a pass bundle:

- `beauty_flipbook`
- `alpha_mask`
- `emissive`
- `flame_tongue_mask`
- `motion_vectors`
- `distortion_flow`
- `depth_or_thickness`
- `normal_or_lighting`
- `renderer_layout_metadata`

The renderer layout metadata should explicitly say which pass is allowed to be used by which renderer. For example, `edge_lick_cells` may be used by SubUV sprites, but `reference_matched_preview` must never be used by Niagara particles.

Relevant references:

- OpenAI images and vision guide: https://platform.openai.com/docs/guides/images
- OpenAI image input requirements: https://platform.openai.com/docs/guides/images#image-input-requirements
- OpenAI structured outputs: https://platform.openai.com/docs/guides/structured-outputs

## Proposed Rebuild

### 1. Split Analysis From Runtime Assets

Keep `reference_matched_preview` only as a debug/scoring layer. Add review gates that fail if any reference-matched or reference-extracted complete flame source is assigned to a runtime particle renderer.

### 2. Add Renderer Layout Schema

Extend `renderer_layout_metadata` with:

```json
{
  "runtime_layers": [
    {
      "name": "flame_tongue_ribbons",
      "renderer": "ribbon",
      "source_passes": ["flame_tongue_mask", "emissive", "distortion_flow"],
      "forbidden_sources": ["reference_matched_preview", "core_flame_from_reference_layer"]
    }
  ]
}
```

### 3. Build A Fire Authoring Stack

For sustained fire:

- Core body: soft mesh/volume or baked fluid flipbook.
- Main flame motion: ribbon or mesh streaks, not sprite icons.
- Edge detail: tiny SubUV lick cells only.
- Smoke/heat: translucent low-opacity SubUV plus distortion.
- Embers: small point sprites.
- Ground: small support contact glow, not a magic floor graphic unless explicitly requested.

### 4. Add AI Provider Tasks

Create two separate provider calls:

1. `understand-reference`: image input -> strict JSON VFX decomposition.
2. `generate-pass-bundle`: decomposition + reference -> separate transparent passes.

The second call must ask for transparent, isolated passes, not a composed screenshot.

### 5. Unreal Implementation Priority

Next code work should focus on:

- A renderer-layout validator.
- A ribbon/streak preview path for flame tongues.
- Review gates that block whole-flame sprites in runtime emitters.
- Optional Niagara Fluids or external EmberGen/FluidNinja hook to bake a real flipbook.

Until one of those exists, further procedural sprite drawing will keep producing toy-looking fire.
