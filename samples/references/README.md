# 參考特效資料夾

每一個想生成的特效建立一個資料夾，例如 `fire/`、`lightning/`。

建議資料夾結構：

```text
fire/
  images/
    fire_column.png
    ember_loop.gif
  passes/
    core_flame_flipbook.png
    flame_slash_flipbook.png
    ground_ring_mask.png
    impact_flash_mask.png
    smoke_heat_flipbook.png
    ember_sprite_set.png
    distortion_flow.png
  prompt.md
  config.json
```

`images/` 放設計師參考圖、GIF、動圖。工具會分析它們，用來判斷特效類型、色彩、動態方向與主要輪廓。

`passes/` 放高品質美術或 AI/simulation 產出的特效 pass。這個資料夾的檔案會優先於 bootstrap 暫代圖使用，是往 3A 品質邁進的主要入口。常用命名：

- `beauty_flipbook.png`: 主色彩/發光動畫 atlas
- `alpha_mask.png`: 去背遮罩
- `core_flame_flipbook.png`: 火柱核心
- `flame_slash_flipbook.png`: 側邊火舌
- `ground_ring_mask.png`: 地面魔法陣/燃燒環
- `impact_flash_mask.png`: 起爆瞬間閃光
- `smoke_heat_flipbook.png`: 煙、熱浪、低亮度殘留
- `ember_sprite_set.png`: 火星/碎片 sprite atlas
- `distortion_flow.png`: heat haze / distortion flow map

如果 `passes/` 沒有提供對應檔案，工具會產生 bootstrap 暫代 pass，但 review 會標記為尚未達到最終 3A 品質。
