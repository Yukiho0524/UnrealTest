# AAA VFX 生成管線研究筆記

目標不是把參考圖直接貼進 Unreal，而是把少量參考圖拆解成可控的遊戲特效資料，再由 Niagara 組裝成可調、可播放、可優化的效果。

## 核心結論

1. 參考圖只能當視覺目標，不能直接當最終特效。
2. AAA 特效通常是多層組合：主形體、邊緣破碎、衝擊閃光、地面錨點、煙霧/熱扭曲、火星/碎屑、光照與後處理。
3. 需要產出多張 pass，而不是一張 beauty 圖：color/emissive、alpha、motion vectors、flow/distortion、normal/lighting、mask。
4. Unreal 端必須用 SubUV/flipbook、材質參數、Niagara 多 emitter、排序與 lifetime/timing 控制來重建動態。
5. AI 應該負責生成高品質視覺素材與 motion target；Niagara 負責遊戲內可控表現。

## 推薦資產管線

```text
Designer references
  -> visual analysis
  -> AI/simulation asset generation
  -> asset pass manifest
  -> VFXSpec quality target
  -> Niagara layered assembly
  -> Unreal viewport review gates
  -> iterate until pass
```

## 需要的 asset passes

| Pass | 來源 | Unreal 用途 |
| --- | --- | --- |
| beauty_flipbook | ComfyUI / video model / simulation | 主視覺 SubUV flipbook |
| alpha_mask | segmentation / luminance extract | 移除矩形卡片，控制 opacity |
| motion_vectors | EmberGen / optical flow | frame interpolation、方向性 smear |
| distortion_flow | simulation / noise synthesis | heat haze、smoke curl、electric shimmer |
| normal_or_lighting | simulation bake / AI estimate | 增加煙霧/火焰體積感 |
| core_flame_flipbook | EmberGen / FluidNinja / AI video | 火焰主體 |
| smoke_heat_flipbook | simulation / AI video | 煙、熱氣與尾韻 |
| ground_ring_mask | procedural / AI stroke | 地面衝擊錨點 |

## Unreal/Niagara 實作原則

- 用 Niagara System/Emitter 分層，不要把所有東西放在單一粒子噴射。
- Sprite Renderer 要使用 alpha-shaped texture 或 flipbook；不可露出 atlas grid。
- Flipbook 材質要接到 TextureSample 的真實 `UVs` pin，並用 columns/rows/fps 控制播放。
- 火焰類效果至少拆成：impact flash、ground ring、core pillar、side tongues、smoke/heat、embers。
- 閃電類效果至少拆成：main bolt、branch arcs、impact core、ground pulse、ion sparks。
- 粒子密度不是品質。品質主要來自形狀、材質、timing、layer balance、光暈與扭曲。
- Preview 必須在 Blueprint 或 Niagara viewport 開 Realtime 後能播放，不能只是靜態圖。

## 外部生成式/模擬工具定位

- ComfyUI：適合接 Stable Diffusion/ControlNet/AnimateDiff/Video workflow，生成 style target、sprite sheet、mask 或 image-to-image 變體。
- EmberGen：適合輸出 realtime VFX flipbook，包含 motion vectors、normal、depth、lighting 等 pass。
- FluidNinja：適合 Unreal 內生成/處理 fluid flipbook、flow map、volume 或 stylized effects。
- Houdini Niagara：適合把程序點資料、mesh/ribbon/particle caches 帶進 Niagara，補足 AI 無法保證的結構控制。

## Review gates

1. Reference read：縮圖看起來就要像同類效果，主 silhouette 不能跑掉。
2. Motion match：anticipation、peak、trail、fade 順序要接近參考。
3. Material quality：不能看到格子 atlas、矩形卡、髒邊；emissive/alpha/distortion 要分開。
4. Layer balance：主體先成立，sparks/smoke/glow 只能支援，不能變成雜訊。
5. Engine readiness：Unreal viewport 能播放、不 crash、有可調材質參數與 emitter layer。

## 下一步實作順序

1. 讓 `VFXSpec.vfx_plan` 輸出 `quality_target`、`asset_passes`、`review_gates`。已完成。
2. 建立 `asset_pass_manifest.json`，把 beauty/alpha/motion/distortion 等檔案交給 Unreal importer。已完成第一版。
3. 若 reference flipbook 已存在，先自動派生 bootstrap `alpha_mask`、`core_flame_flipbook`、`smoke_heat_flipbook`。已完成第一版。
4. `Generate Unreal Assets` 會套用 asset pass manifest，將 fire pillar/smoke 等 emitter 指到對應 pass。已完成第一版。
5. 讓 AI art provider 的 manifest 能標記輸出的 pass type，不只收集圖片。下一步。
6. Unreal 材質支援獨立 alpha atlas。已完成第一版，`AlphaTexture` 會乘進 opacity。
7. Review Gates 會檢查 required passes、production preview、alpha mask、reference overlay 依賴、Unreal 生成結果。已完成第一版。
8. Unreal 材質支援 motion-vector flipbook interpolation、distortion pass。下一步。
9. Niagara 生成器支援 layer delay、burst timing、curve-driven alpha/size/color。下一步。
10. 做 viewport screenshot/thumbnail 自動評估，至少能偵測 atlas grid、矩形卡、過量粒子噴射。下一步。

## UI 新流程

1. 在 `samples/references/<effect-name>/` 放入參考圖、GIF 或 prompt/config。
2. 按 `Analyze Package` 檢查 `quality_target`、`asset_passes`、`review_gates`。
3. 按 `Generate AI Art Pass` 產生外部 AI 素材；如果尚未接 ComfyUI workflow，可以先跳過。
4. 按 `Prepare AAA Passes` 產生 `generated/asset-passes/<effect-name>/asset_pass_manifest.json`。
5. 按 `Generate Unreal Assets`，系統會先套用 asset pass manifest，再匯入 Unreal。
6. 按 `Open In Unreal`，在 BP preview viewport 開啟 Realtime 檢查播放。
7. 按 `Review Gates`，檢查目前是否仍依賴 reference overlay、required passes 是否 ready、alpha mask 是否接入材質。

`asset_pass_manifest.json` 會標記每個 pass 的狀態：

- `ready`: 已有可用素材。
- `missing`: 還缺素材，通常需要 ComfyUI、EmberGen、FluidNinja 或人工指定。
- `quality_note`: 若是 `bootstrap_only_replace_with_ai_or_simulation_for_final_aaa_quality`，代表它可先推進測試，但最終 AAA 品質仍建議替換成正式生成/模擬 pass。

## Review Gates

`Review Gates` 目前會檢查：

- `required_asset_passes`: required pass 是否都有候選素材。
- `production_layer_preview`: BP preview 是否使用 production layers，而不是 reference flipbook 大卡。
- `alpha_mask_material_link`: alpha mask 是否接入材質設定。
- `reference_overlay_not_primary`: reference flipbook 是否不再是 primary preview。
- `unreal_generation_result`: 最新 Unreal generation 是否成功建立 bundle。
- `final_quality_assets`: 是否仍使用 bootstrap pass。若有 warning，代表流程可測，但最終品質仍需替換成 AI/模擬 pass。

## 參考資料

- Epic Games, Niagara overview: https://dev.epicgames.com/documentation/en-us/unreal-engine/overview-of-niagara-effects-for-unreal-engine
- Epic Games, Niagara renderers: https://dev.epicgames.com/documentation/en-us/unreal-engine/render-module-reference-for-niagara-effects-in-unreal-engine
- Epic Games, Niagara Fluids: https://dev.epicgames.com/documentation/en-us/unreal-engine/niagara-fluids-in-unreal-engine
- Epic Games, Niagara Fluids reference: https://dev.epicgames.com/documentation/en-us/unreal-engine/niagara-fluids-reference-in-unreal-engine
- JangaFX EmberGen: https://jangafx.com/software/embergen/
- SideFX Houdini Niagara: https://www.sidefx.com/docs/houdini/unreal/niagara.html
- ComfyUI server/API docs: https://docs.comfy.org/development/comfyui-server/comms_routes
