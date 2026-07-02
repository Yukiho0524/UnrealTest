# Unreal VFX 生成規則筆記

這份筆記記錄目前 VFX MCP 生成器要遵守的 Unreal / Niagara 製作方向。目標不是把參考圖直接貼到場景裡，而是把參考拆成可控的遊戲特效層。

## 核心原則

- Niagara 的 Sprite Renderer 適合小到中型的 alpha sprite、flipbook、glow、flash，不適合把整張參考圖放很大當主效果。
- 需要方向感、弧線、分支或較立體的讀形時，優先拆成 ribbon、mesh、bolt card、ground card 或多個小型 sprite layer。
- 主讀形先成立，再加火星、煙、glow、distortion。不要用增加粒子數量掩蓋主形狀不對。
- 大圖大卡會讓材質邊界、壓縮、mip、UV、解析度問題全部被放大，看起來會像破圖或平面貼片。
- 參考圖可以作為 preview-only 對位層，但不能成為最終主視覺。

## 貼圖與卡片預算

- `reference_matched_composite`：最多 512px，preview scale 不超過 1.6，只能當小型相似度錨點。
- `core_flame_flipbook`、`flame_slash_flipbook`：最多 1024px atlas，靠 alpha 與 flipbook 動態讀形，不靠放大整張圖。
- `ground_ring_mask`：最多 768px，允許較大的地面 scale，但要是環/符文形狀，不可是一整張方形卡。
- `impact_flash_mask`：最多 512px，短暫爆亮，尺寸要小。
- `ember_sprite_set`：最多 512px，小粒子用，不可當主效果。
- `distortion_flow`、`normal_or_lighting`：最多 512px，資料貼圖不做主色彩。

生成器現在會把超過預算的輸入圖縮到 runtime 版本，再匯入 Unreal。review 也會檢查 `texture_card_budget`，避免貼圖太大又被放成大卡。

## Fire 分層

- `central_fire_pillar`：主要垂直火柱，承擔主 silhouette。
- `side_flame_slashes`：左右破碎火舌，打破單根噴泉感。
- `ground_rune_ring`：地面火環或符文，提供落點與比例。
- `impact_flash`：起始瞬間的短暫高亮。
- `smoke_dust_crown`：低亮度煙/熱擾動，只做支撐，不遮住火。
- `ember_sparks`：稀疏火星，不能變成主畫面。
- `reference_matched_composite`：小型 preview 錨點，只用來對比相似度。

## Unreal 匯入規則

- 彩色 VFX sprite 使用 Effects texture group，並允許 mipmaps，避免大卡縮放時鋸齒和閃爍。
- Alpha mask 與 distortion flow 保持資料貼圖邏輯，尺寸較小，避免額外模糊。
- Preview Blueprint 使用多個 layer card 與 Niagara layer 組合，不再產生不穩定的 map preview。
- `Open In Unreal` 應優先開 `BP_<name>_VFXPreview`，用 Content Browser 同步相關材質與 Niagara 系統。

## 參考來源

- Epic Niagara Renderers: https://dev.epicgames.com/documentation/en-us/unreal-engine/render-module-reference-for-niagara-effects-in-unreal-engine
- Epic Niagara Overview: https://dev.epicgames.com/documentation/en-us/unreal-engine/overview-of-niagara-effects-for-unreal-engine
- Epic Texture Import Guide: https://dev.epicgames.com/documentation/en-us/unreal-engine/importing-images-and-textures-in-unreal-engine
