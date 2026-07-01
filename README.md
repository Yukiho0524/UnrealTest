# UnrealTest

這個專案用來研究 Unreal MCP 架構，目標是讓設計人員把特效參考圖、GIF 或網址整理成資料包後，由工具分析參考內容並在 Unreal Engine 5.7.4 中生成可檢視的 Niagara 特效雛形。

## 目前目標

1. 設計人員把參考圖或 GIF 放進資料夾，例如 `samples/references/fire/images/`。
2. UI 讀取資料包，分析圖片特徵、設計文字與 `config.json`。
3. 工具產生 `VFXSpec`，其中包含 `visual_profile` 與 `vfx_plan`。
4. Unreal bridge 讀取 spec，在專案內生成 Niagara System、材質、材質實例與 sprite texture。
5. UI 可開啟 Unreal Editor 並聚焦到生成的特效資產。

## 架構

```text
Reference package
  -> image / GIF analysis
  -> visual_profile
  -> vfx_plan
  -> VFXSpec JSON
  -> Unreal Python bridge
  -> Niagara System + Texture + Material + Material Instance
```

## 重要資料夾

```text
mcp-server/
  server.py                  CLI 入口
  ui_server.py               本機 UI server
  schemas.py                 VFXSpec / VFXPlan dataclass
  tools/
    analyze_packages.py      資料包分析與 vfx_plan 產生
    image_features.py        圖片亮度、色彩、形狀特徵分析
    unreal_bridge.py         呼叫 Unreal 生成與開啟資產

specs/
  vfx_spec.schema.json       VFXSpec JSON schema

samples/
  references/                設計參考資料包

unreal/
  UnrealTest.uproject
  engine.version.json        指定 UE 5.7.4
  Plugins/VFXMCP/
    Scripts/create_niagara_from_spec.py
```

## Unreal 版本

目前指定使用 Unreal Engine 5.7.4：

```text
D:\Program Files\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe
```

## 參考資料包格式

以 `fire` 為例：

```text
samples/references/fire/
  images/
    fire_column.png
    ember_loop.gif
  prompt.md
  config.json
```

- `images/`：放參考圖、GIF、截圖。
- `prompt.md`：補充設計意圖，例如火焰、方形發光粒子、煙霧、衝擊波。
- `config.json`：可指定或覆寫 effect type、motion、顏色與粒子參數。

## vfx_plan 是什麼

早期版本只產生單一 `effect_type`，例如 `fire_or_flame`。這會讓生成結果太粗，常常只像「同類型特效」，不像原圖。

現在 spec 會多一層 `vfx_plan`，把參考圖拆成多個 emitter 意圖。例如白色發光方片會變成：

```json
{
  "visual_intent": "White emissive square particles drifting upward in a loose vertical column with soft bloom.",
  "primary_emitter": "glowing_squares",
  "emitters": [
    {
      "name": "glowing_squares",
      "role": "primary_particles",
      "sprite_shape": "square",
      "material_style": "white_emissive",
      "motion": "rise_with_turbulence"
    },
    {
      "name": "soft_bloom_core",
      "role": "supporting_glow",
      "sprite_shape": "soft_disc",
      "material_style": "warm_white_glow",
      "motion": "slow_vertical_drift"
    }
  ]
}
```

Unreal 端會先用 primary emitter 的 `sprite_shape` 產生對應貼圖。火焰會產生火舌 alpha texture，白色方片會產生方形 emissive texture，避免所有效果都變成滿版矩形 sprite。

如果參考圖可被萃取，primary emitter 也會帶 `sprite_source`，指向 `generated/reference-sprites/<package>/` 內的 PNG。Unreal 端會優先匯入這張由原圖亮部/暖色前景裁切出的 sprite，只有沒有 `sprite_source` 時才使用程序貼圖。

對於大量小型亮片或三角碎光，工具會優先使用分析出的 palette 與乾淨的 shard sprite。這比直接把低解析度截圖中的單顆亮片放大更穩定，也能避免背景窗格或白牆被誤裁進貼圖。

`vfx_plan.composition_layers` 會把特效拆成 reference card、主體、細節粒子、柔光與 glint 等製作層，並記錄每層預期的 Renderer、材質目的、Niagara module stack 與可調參數。`vfx_plan.production_notes` 則是整體製作提醒，例如先做剪影、再補粒子細節，避免只靠提高 spawn rate 讓畫面變「比較多粒子」。

## UI 使用

啟動 UI：

```powershell
.\mcp-server\Start-VFXMCPUI.ps1
```

開啟：

```text
http://127.0.0.1:8765
```

UI 按鈕：

- `Analyze Package`：只分析資料包，顯示 `VFXSpec`、`visual_profile`、`vfx_plan`。
- `Generate Spec`：寫出 `generated/specs/<name>.vfxspec.json`。
- `Generate Unreal Assets`：呼叫 UE 5.7.4，生成 Niagara 與相關材質貼圖。
- `Open In Unreal`：開啟 Unreal Editor，並同步到 `NS_`、`T_`、`M_`、`MI_` 相關資產。

當 `vfx_plan.emitters` 有多個 emitter 時，Unreal 端會生成一組 bundle。主 emitter 會使用 `NS_<name>`，其他 emitter 會使用 `NS_<name>_<emitter>`，並各自產生對應的 `T_`、`M_`、`MI_` 資產。這讓火焰主體、火星、碎光、柔光可以先分開生成與檢查，而不是全部被壓成單一噴粒子。

bundle 也會產生 reference card assets，例如 `T_<name>_reference_card_VFX_Sprite`、`M_<name>_reference_card_VFX`、`MI_<name>_reference_card_VFX`。這張 card 來自原始參考圖的整體亮部剪影，用來保留主視覺形狀；粒子層則用來補火星、碎片與柔光。

生成 Unreal Assets 時，工具現在會再建立 `L_<name>_VFXPreview` 預覽關卡。這個關卡會把 reference card、各層材質平面與 Niagara layer 放在同一個場景，並擺好 camera/light，作為主要檢視入口。`Open In Unreal` 會優先開這個 preview level；`NS_` 資產則保留給 layer debug。

如果 Unreal Editor 已經開著同一個專案，`Generate Unreal Assets` 可能因資產鎖定而失敗或 partial。建議先關掉 Unreal Editor，再執行生成，生成完成後再用 `Open In Unreal` 檢視。

## CLI

```powershell
py mcp-server/server.py analyze-package samples/references/fire --out generated/specs
```

輸出：

```text
generated/specs/fire.vfxspec.json
```

## 下一步

- 讓 UE 端依 `vfx_plan.emitters` 建立多個實際 Niagara emitter，而不只使用 primary emitter。
- 從 GIF 生成 flipbook 或序列幀 texture。
- 將網址擷取出的圖片走同一套資料包分析流程。
- 加入 vision model，提升形狀、材質與動態拆解精度。
