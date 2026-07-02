# Unreal VFX 製作筆記

這份筆記整理目前工具在 Unreal/Niagara 端應該遵守的製作規則。

## Niagara 分層

高品質特效通常不是單一 emitter，而是多個角色明確的 layer：

- 主形狀：火柱、雷電主幹、衝擊核心。
- 支撐形狀：火舌、地面圈、分支電弧、煙塵。
- 細節：火星、碎片、glint。
- 材質資料：alpha、distortion、depth/thickness、normal/lighting、mask pack。

Niagara Sprite Renderer 適合 alpha-shaped sprite 與 flipbook；Ribbon Renderer 適合雷電、尾跡與能量流；Mesh/Card 適合地面圈或特定剪影；Light Renderer 只應用於少量重點閃光。

## 材質規則

- Beauty 貼圖接 emissive/color。
- Alpha mask 接 opacity，避免矩形卡片。
- Layer mask pack 用 RGBA 分開控制 core、edge、smoke、spark。
- Distortion flow 用於熱扭曲或電流 shimmer，不應直接當顏色貼圖。
- Depth/thickness 用於透明度、depth fade、pseudo-volume。
- Normal/lighting 用於增加體積感，但不能蓋掉 emissive 主讀性。

## 貼圖與卡片預算

- reference_matched_composite：512px，debug/對照用途。
- core_flame_flipbook：1024px 以內，主層。
- flame_slash_flipbook：1024px 以內，側向支撐層。
- ground_ring_mask：768px 以內，貼地。
- impact_flash_mask：512px 以內，短時間爆亮。
- ember_sprite_set：512px 以內，小粒子。
- distortion/depth/normal/vector field：通常 512px 以內。

過大的卡片會讓特效看起來像壞掉的平面貼圖。Preview Blueprint 會檢查卡片 scale 與 atlas 是否被錯誤地直接顯示。

## Preview 檢查

`Open In Unreal` 產生的 `BP_<name>_VFXPreview` 應該滿足：

- 地面圈在低 Z。
- impact flash 接近中心底部。
- 主火柱或主 bolt 在視覺中心。
- smoke/wisp 是支撐層，不是黑色大片。
- ember/spark 是少量細節，不是主形狀。
- reference overlay 不應當成 production preview 主體。

## Review Gate

`Review Gates` 目前會檢查：

- required asset passes
- 80% reference similarity target
- fire production pass coverage
- layer timing
- distortion/alpha material link
- production preview
- preview component contract
- fire spatial design
- texture card budget
- source asset contract
- final bootstrap quality

如果 `source_asset_contract` 或 `final_quality_assets` 顯示 warning/fail，代表目前仍不能稱作 AAA，只是架構或 blockout 可用。
