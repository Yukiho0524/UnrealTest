# Fire/Ice Tornado Reference Learning

This project should treat reference VFX as implementation guidance, not just visual inspiration. The current firestorm target is closer to a stylized fire/ice magic tornado than a pure fire column.

## Reference Videos

- Gabriel Aguiar, Unity VFX Graph Fire Tornado: `https://www.youtube.com/watch?v=gLWe_Wzc8Xc`
- Unreal Engine 5 Twister / Tornado Niagara: `https://www.youtube.com/watch?v=GIfGq9pB_xE`
- Unreal Engine 5 Niagara Wind Swirl: `https://www.youtube.com/watch?v=trO_VREoEEk`
- Unity Shader Graph Tornado Shader: `https://www.youtube.com/watch?v=Qyh9RPxeKcA`
- Original user reference, Fire and Ice Tornado Tutorial: `https://www.youtube.com/watch?v=xvV90kdBCPQ`

## Shared Structure

- The tornado silhouette is built from continuous spiral bands, not many random flame cards.
- The lower half reads as a cool cyan/white energy or ice vortex.
- The upper half reads as an orange fire crown with wider, brighter spiral rings.
- The base is a glowing energy pool or ring; it supports the tornado but should not become a pedestal.
- Mesh or shader-guided funnel shapes are used to keep the volume coherent from oblique camera angles.
- Noise/erosion controls edge breakup, while the main silhouette remains clean and readable.

## Current Generator Requirements

- Keep a narrow lower contact point and widen toward the upper fire crown.
- Maintain a cyan lower band, white transition, and orange upper fire band.
- Add top and bottom rings as deliberate shapes, not accidental card intersections.
- Use review gates to prevent regression back to pure orange fire, grey checker materials, or cup-like silhouettes.

## Known Gaps

- The current procedural atlas is still a bootstrap substitute for simulation or AI-generated flipbooks.
- The Unreal material does not yet use true motion vectors, depth/thickness, or volumetric raymarching.
- The current 3D helpers are preview geometry, not a production fire/ice tornado shader.
