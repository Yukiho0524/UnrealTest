# Unreal VFX Research Notes - 2026-07-06

This note summarizes the Unreal VFX production methods that should guide the next rebuild of the project pipeline.

## Sources Checked

- Epic Niagara overview: https://dev.epicgames.com/documentation/unreal-engine/overview-of-niagara-effects-for-unreal-engine
- Epic Niagara tutorials index: https://dev.epicgames.com/documentation/unreal-engine/tutorials-for-niagara-effects-in-unreal-engine
- Epic Niagara renderer reference: https://dev.epicgames.com/documentation/unreal-engine/render-module-reference-for-niagara-effects-in-unreal-engine
- Epic sprite particle tutorial: https://dev.epicgames.com/documentation/unreal-engine/create-a-sprite-particle-effect-in-niagara
- Epic smoke sprite tutorial: https://dev.epicgames.com/documentation/unreal-engine/how-to-create-a-smoke-effect-using-sprite-particles-in-niagara-for-unreal-engine
- Epic ribbon tutorial: https://dev.epicgames.com/documentation/unreal-engine/how-to-create-a-ribbon-effect-in-niagara-for-unreal-engine
- Epic beam tutorial: https://dev.epicgames.com/documentation/unreal-engine/how-to-create-a-beam-effect-in-niagara-for-unreal-engine
- Epic Niagara Flipbook Baker: https://dev.epicgames.com/documentation/unreal-engine/niagara-flipbook-baker-quick-start-guide-in-unreal-engine
- Epic Niagara Fluids: https://dev.epicgames.com/documentation/unreal-engine/niagara-fluids-in-unreal-engine
- Epic Niagara Fluids Quick Start: https://dev.epicgames.com/documentation/unreal-engine/niagara-fluids-quick-start-guide-for-unreal-engine
- Epic Niagara scalability and best practices: https://dev.epicgames.com/documentation/unreal-engine/scalability-and-best-practices-for-niagara

## Production Model We Should Follow

Unreal VFX should be authored as a Niagara system stack, not as a Blueprint that displays a few large cards.

Recommended fire stack:

1. `core_flame_subuv`
   - Sprite renderer.
   - Uses a clean SubUV flipbook or a Niagara Fluids baked flipbook.
   - Drives emissive and opacity through Particle Color, normalized age, and dynamic material parameters.
2. `outer_flame_tongues`
   - Sprite renderer for short-lived flame tongues.
   - Uses random frame, random rotation, non-uniform scale, and alpha erosion.
   - Should be placed in crossed or camera-facing groups, but not as one large plane.
3. `ribbon_lift_or_vortex`
   - Ribbon renderer for trails, flame licks, wind/tornado motion, lightning arcs, and spiral bands.
   - Required for firestorm/tornado references; sprite cards alone will stay too flat.
4. `smoke_heat_wisp`
   - Sprite renderer using smoke flipbook.
   - Low emissive, translucent blend, larger lifetime, slower rising motion.
5. `heat_distortion`
   - Sprite or mesh shell with distortion flow.
   - Should be visually subtle. It is a material carrier, not the main visible layer.
6. `embers`
   - Small single-sprite particles or proper SubUV random frame selection.
   - Never render the whole atlas as one sprite.
7. `impact_flash`
   - Short pulse layer at the base.
   - Should be timed first, then decay quickly.

## Material Requirements

Materials should expose these parameters:

- `SpriteTexture`
- `SubImageSize` or equivalent flipbook metadata
- `AlphaTexture` only when it matches the same UV layout as the sprite
- `EmissiveRamp`
- `OpacityErosion`
- `DistortionTexture`
- `DistortionStrength`
- `SoftParticleDepthFade`
- `DynamicMaterialParameter0..3`
- `ParticleColor`
- `NormalizedAge`

Important correction for this project:

- Global alpha masks must not be blindly multiplied into layer-specific sprites. If their UVs do not match, they create holes and strange patch textures.
- Depth, layer mask, and normal-like support maps should initially modulate emissive/edge breakup conservatively. They should not directly erase the main flame opacity unless generated from the same exact frame layout.

## Flipbook And SubUV Notes

Epic's Flipbook Baker workflow is important for this project because it bridges high-cost simulation and efficient runtime VFX:

1. Create a higher-quality Niagara Fluids or simulation source.
2. Bake it to a tiled flipbook.
3. Use the flipbook in a Sprite Renderer.
4. Configure the SubUV grid on the renderer and material.
5. Animate frames through Niagara, not by displaying the full atlas as one texture.

Project implication:

- `beauty_flipbook`, `core_flame_flipbook`, `smoke_heat_flipbook`, and `flame_slash_flipbook` must carry reliable atlas metadata.
- Preview cards may use extracted single frames.
- Niagara runtime emitters must use SubUV playback or single sprites, never raw atlas display.

## Ribbon Notes

Ribbon Renderer is the missing piece for references that contain trails, spirals, tornado bands, arcs, or flowing flame strips.

Project implication:

- Add a `renderer_kind` or `preferred_renderer` field to emitter specs.
- Firestorm should use at least one ribbon emitter for spiral bands.
- Lightning should use ribbon/beam-style emitters instead of only sprite cards.
- Fire can optionally use short ribbon trails for licking flame tips.

## Niagara Fluids Notes

Niagara Fluids provides templates for fire, smoke, gas, and other fluid effects. It is the closest in-engine path toward the tutorial-quality look the user expects.

Project implication:

- Add an optional `simulation_source` stage:
  - `niagara_fluids_template`
  - `embergen`
  - `fluidninja`
  - `ai_generated_flipbook`
  - `procedural_bootstrap`
- Mark `procedural_bootstrap` as blockout quality.
- Prefer fluid/baked flipbooks for fire and smoke when the goal is visual quality.

## Performance And Review Gates

Niagara quality must be checked with performance in mind:

- Avoid unnecessary emitters.
- Use fixed bounds.
- Keep texture sizes and card footprints within budget.
- Separate hero, gameplay, and background quality tiers.
- Add review gates for:
  - SubUV metadata present.
  - No raw atlas rendered as a sprite.
  - Ribbon required for tornado/lightning/trail references.
  - Heat distortion is present but subtle.
  - Sprite alpha source matches the sprite UV layout.
  - Bootstrap source cannot be marked final quality.

## Next Implementation Tasks

1. Add `renderer_kind` to emitter plans: `sprite`, `subuv_sprite`, `ribbon`, `mesh`, `fluid_template`, `light`.
2. Add `simulation_source` to asset pass manifests.
3. Add a real SubUV contract:
   - columns
   - rows
   - frame_count
   - fps
   - start_frame
   - random_frame_mode
4. Extend Unreal generation to configure sprite renderer SubUV settings when possible.
5. Add ribbon emitters to firestorm and lightning.
6. Add a Niagara Fluids/Flipbook Baker path for fire and smoke.
7. Update review gates so tutorial-quality fire requires:
   - core SubUV flame
   - outer flame tongues
   - smoke flipbook
   - heat distortion
   - embers as single sprites or SubUV random frames
   - no visible raw atlas cards

