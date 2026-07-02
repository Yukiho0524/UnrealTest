# AAA 特效生成流程

目前工具的目標不是把參考圖直接貼到 Unreal 裡，而是把參考圖拆成可製作、可檢查、可迭代的 VFX pass bundle，再由 Unreal/Niagara 分層組裝。

## 核心判斷

單張 beauty 圖或一張序列幀 atlas 只能當 blockout。要接近高品質遊戲特效，至少需要：

- 明確的主形狀：火柱、雷電主幹、爆點、地面圈等。
- 多層 renderer：Sprite、SubUV flipbook、Ribbon、Mesh/Card、Light、Distortion。
- 多種資料 pass：beauty、alpha、layer mask、motion vector、distortion flow、depth/thickness、normal/lighting、SDF/vector field。
- 明確的 atlas metadata：columns、rows、frame_count、fps、color_space、pivot、bounds、intended renderer。
- Review gate：不只確認有檔案，還要確認比例、位置、材質、透明度、layer balance、是否仍是 bootstrap。

## 設計師流程

```text
samples/references/<effect-name>/
  -> 放參考圖、GIF、prompt
  -> Analyze Package
  -> Generate AI Art Pass / 匯入手動 pass
  -> Prepare AAA Passes
  -> Generate Unreal Assets
  -> Open In Unreal
  -> Review Gates
  -> 針對缺的 pass 或失敗 gate 迭代
```

## 必要 Asset Pass

| Pass | 用途 | Unreal 使用方式 |
| --- | --- | --- |
| beauty_flipbook | 主視覺顏色與 emissive 動畫 | Sprite/SubUV flipbook |
| alpha_mask | 去掉矩形卡片，保留破碎/柔邊輪廓 | Opacity、overdraw 控制 |
| layer_mask_pack | RGBA 分離 core、edge、smoke、spark 等區域 | 材質動態參數、分層亮度與透明度 |
| renderer_layout_metadata | atlas 與 renderer 設定 | 匯入驗證、SubUV、材質與 Niagara 設定 |

## Production Quality Pass

| Pass | 用途 | Unreal 使用方式 |
| --- | --- | --- |
| motion_vectors | frame interpolation、方向性 smear | 材質 flipbook interpolation、Niagara 動態參數 |
| distortion_flow | 熱扭曲、煙卷、電流 shimmer | Translucent distortion/refraction |
| depth_or_thickness | 讓火/煙/魔法雲不只是平面卡片 | depth fade、pseudo-volume opacity |
| normal_or_lighting | 提供體積受光或六向 lighting 資料 | Lit/unlit hybrid 材質 |
| sdf_or_vector_field | 邊緣腐蝕、curl、粒子導流 | Vector field、ribbon deformation、材質 erosion |

## Fire 分層範例

- impact_flash：最先爆亮，時間短，大小不能太大。
- ground_ring_mask：貼近地面，負責規模與錨點。
- core_flame_flipbook：主火柱，必須承擔縮圖可讀性。
- flame_slash_flipbook：側向火舌，破壞單一柱狀感。
- smoke_heat_flipbook：低亮度煙/熱氣，留在中低區域。
- ember_sprite_set：少量細節，不可變成主要形狀。

## 品質紅線

- 不接受單張靜態卡片當最終效果。
- 不接受只是增加粒子數量。
- 不接受可見 atlas grid 或矩形透明卡邊。
- 不接受超大貼圖卡片硬塞成整個特效。
- 不接受 preview 看起來通過，但 review gate 顯示仍是 bootstrap 的狀態被視為最終品質。

## 參考資料

- Epic Games, Niagara Overview: https://dev.epicgames.com/documentation/en-us/unreal-engine/overview-of-niagara-effects-for-unreal-engine
- Epic Games, Niagara Renderers: https://dev.epicgames.com/documentation/en-us/unreal-engine/render-module-reference-for-niagara-effects-in-unreal-engine
- Epic Games, Texture Import: https://dev.epicgames.com/documentation/en-us/unreal-engine/importing-images-and-textures-in-unreal-engine
- Epic Games, Niagara Fluids: https://dev.epicgames.com/documentation/en-us/unreal-engine/niagara-fluids-in-unreal-engine
- SideFX, Houdini Niagara: https://www.sidefx.com/docs/houdini/unreal/niagara.html
- JangaFX, EmberGen: https://jangafx.com/software/embergen/
- ComfyUI Server/API: https://docs.comfy.org/development/comfyui-server/comms_routes
