# Fire/Ice Tornado Reference Learning

This project should treat reference VFX as implementation guidance, not just visual inspiration. The current firestorm target is closer to a stylized fire/ice magic tornado than a pure fire column.

## Reference Videos

- Gabriel Aguiar, Unity VFX Graph Fire Tornado: `https://www.youtube.com/watch?v=gLWe_Wzc8Xc`
- Unreal Engine 5 Twister / Tornado Niagara: `https://www.youtube.com/watch?v=GIfGq9pB_xE`
- Unreal Engine 5 Niagara Wind Swirl: `https://www.youtube.com/watch?v=trO_VREoEEk`
- CGHOW, UE5 Dynamic Trails Niagara: `https://www.youtube.com/watch?v=Ey5sJDF_q1Q`
- CGHOW, UE5 Ribbon Trail in Niagara: `https://www.youtube.com/watch?v=Y5EUpKx8k_g`
- Unreal Engine VFX Tutorials collection: `https://www.bilibili.com/video/BV1hb411E7oH/`
- Unreal Engine 4/5 Advanced VFX Tutorial playlist: `https://www.youtube.com/playlist?list=PLnfzvYOawOqAdnEOOW00BHX0_c4NgFcCK`
- Unity Shader Graph Tornado Shader: `https://www.youtube.com/watch?v=Qyh9RPxeKcA`
- Original user reference, Fire and Ice Tornado Tutorial: `https://www.youtube.com/watch?v=xvV90kdBCPQ`

## Shared Structure

- The tornado silhouette is built from continuous spiral bands, not many random flame cards.
- The lower half reads as a cool cyan/white energy or ice vortex.
- The upper half reads as an orange fire crown with wider, brighter spiral rings.
- The base is a glowing energy pool or ring; it supports the tornado but should not become a pedestal.
- Mesh or shader-guided funnel shapes are used to keep the volume coherent from oblique camera angles.
- Noise/erosion controls edge breakup, while the main silhouette remains clean and readable.
- Trail effects are built from motion-following ribbons with tunable length, speed, color, and fade, not from static streak textures.
- Ribbon trails use a dark/transparent tail, bright inner core, and color-gradient edge; this is the right visual model for tornado spiral bands.
- Beam and aura effects separate core, Fresnel edge, Voronoi/noise breakup, stretched particles, and impact rings.
- Advanced Unreal VFX examples frequently use material functions and post-process/force-field layers to make the effect react to space instead of staying as a flat sprite stack.

## Current Generator Requirements

- Keep a narrow lower contact point and widen toward the upper fire crown.
- Maintain a cyan lower band, white transition, and orange upper fire band.
- Add top and bottom rings as deliberate shapes, not accidental card intersections.
- Use review gates to prevent regression back to pure orange fire, grey checker materials, or cup-like silhouettes.
- Add a real ribbon/trail layer for the tornado path before adding more sprite cards.
- Tornado spiral bands should use ribbon-style UV flow, tapered width, and opacity falloff rather than short disconnected arc sprites.
- Add material-driven erosion and edge lighting before increasing emissive strength.
- Keep secondary particles as detail only; the main read should come from the vortex path, rings, and material motion.

## Known Gaps

- The current procedural atlas is still a bootstrap substitute for simulation or AI-generated flipbooks.
- The Unreal material does not yet use true motion vectors, depth/thickness, or volumetric raymarching.
- The current 3D helpers are preview geometry, not a production fire/ice tornado shader.
- The current preview does not yet author Niagara ribbons or spline-guided trails, which are essential for matching the wind swirl and dynamic trail references.
- The current material builder does not yet create Fresnel, Voronoi, or vertical dissolve layers like the vertical beam and advanced VFX references.
