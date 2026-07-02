# AI Art Provider Pipeline

AI provider 的責任是產生「特效製作用 pass bundle」，不是只產生一張好看的圖。單張圖可以當參考，但 Unreal 端需要可拆層、可控材質、可播放的資料。

## Package 結構

```text
samples/references/<effect-name>/
  prompt.md
  images/
  passes/
    beauty_flipbook.png
    alpha_mask.png
    layer_mask_pack.png
    motion_vectors.png
    distortion_flow.png
    depth_or_thickness.png
    normal_or_lighting.png
    sdf_or_vector_field.png
    renderer_layout_metadata.json
```

`passes/` 不是必填，但如果設計師或外部工具已經輸出 pass，放在這裡會優先被使用。

## ComfyUI

目前 provider 支援 `comfyui`：

- 預設 URL：`http://127.0.0.1:8188`
- workflow 需要支援 API JSON。
- placeholder：
  - `{{PROMPT}}`
  - `{{NEGATIVE_PROMPT}}`
  - `{{REFERENCE_IMAGE}}`
  - `{{OUTPUT_PREFIX}}`

執行範例：

```powershell
py mcp-server/server.py generate-art samples/references/fire --provider comfyui --base-url http://127.0.0.1:8188 --workflow path/to/workflow.json
```

輸出位置：

```text
generated/ai-art/<effect-name>/<provider>/<timestamp>/manifest.json
```

## Prompt 契約

預設 prompt 會要求 AI 產生 pass bundle，並要求檔名包含 pass 名稱。這是為了讓 importer 能自動分類：

- `beauty_flipbook`
- `alpha_mask`
- `layer_mask_pack`
- `renderer_layout_metadata`
- `motion_vectors`
- `distortion_flow`
- `depth_or_thickness`
- `normal_or_lighting`
- `sdf_or_vector_field`

若 provider 只回傳 beauty 圖，manifest 會把它視為 blockout 候選，不會被視為最終 AAA 品質。

## 推薦外部工具定位

- ComfyUI：適合做 reference-guided image/video pass、segmentation mask、風格與輪廓重建。
- EmberGen：適合做火、煙、爆炸、能量等模擬 flipbook，並輸出 motion/depth/normal/lighting 類資料。
- Houdini Niagara：適合做可控的粒子、ribbon、mesh、point cache，再交給 Niagara 使用。
- Unreal Niagara Fluids：適合在 Unreal 內建立可調的流體基底，再 bake 成 runtime-friendly pass。

## 下一步

每次 `Generate AI Art Pass` 後，請執行 `Prepare AAA Passes`。工具會建立 `asset_pass_manifest.json`，檢查哪些 pass 已 ready、哪些仍是 bootstrap、哪些需要外部 AI 或 simulation 補齊。
