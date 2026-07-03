# Unreal VFX Material And Texture Process

This project should treat generated art as Unreal-ready VFX inputs, not final screenshots.

## Unreal Assembly Model

1. Reference understanding decides the effect structure: primary form, silhouette, motion, layers, renderer stack, and negative requirements.
2. Asset pass generation creates named texture/data passes.
3. Unreal imports each pass as a texture and binds it to a material or material instance parameter.
4. Niagara systems provide timing, particle/ribbon/mesh emission, and renderer assignment.
5. The Blueprint preview assembles authored layers so the effect can be inspected before deeper Niagara authoring.

## Renderer Roles

- Sprite renderer: good for flipbook cards, flame tongues, smoke wisps, impact flashes, and embers.
- Ribbon renderer: needed for trails, vortex bands, lightning paths, and tornado spiral motion.
- Mesh renderer: useful for volume helpers, cones, spheres, cylinders, rings, and pseudo-volumetric silhouettes.
- Blueprint preview card: acceptable for inspection and blockout, not final production quality.

## Material Inputs

- `SpriteTexture`: beauty/emissive flipbook or layer texture.
- `AlphaTexture`: opacity mask that prevents rectangular cards.
- `DistortionTexture`: heat haze or flow map support.
- `DepthThicknessTexture`: pseudo-volume opacity/depth modulation.
- `NormalLightingTexture`: lighting or normal-like modulation for volume response.
- `LayerMaskTexture`: packed masks for core, edge, smoke, ground, and spark influence.

## Current Implementation

The Unreal material builder now imports and exposes alpha, distortion, depth/thickness, normal/lighting, and layer mask textures. Depth and layer masks modulate opacity; normal/lighting and layer masks modulate emissive intensity.

This is still not a complete AAA fire shader. The next production step is replacing bootstrap data passes with AI/simulation-authored pass bundles and adding true Niagara ribbons, volume materials, and material functions for erosion, depth fade, refraction, and vector-field-driven motion.
