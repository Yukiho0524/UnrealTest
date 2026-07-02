# AI Art Provider Pipeline

這個專案現在支援一個外部美術生成 AI 的前置流程，用來解決「只靠 Unreal procedural layer 很難貼近參考圖」的問題。

## 目標

設計師把參考圖、GIF 或短序列放進：

```text
samples/references/<effect-name>/
```

工具先執行 AI art pass，產生乾淨的 VFX 圖像或序列幀，再交給 Unreal pipeline 轉成 flipbook、材質與 Niagara/Blueprint 預覽。

## 第一版 Provider

目前先支援 `comfyui`：

- 預設 URL：`http://127.0.0.1:8188`
- 支援上傳參考圖到 ComfyUI
- 支援載入 ComfyUI API workflow JSON
- 支援 workflow placeholder：
  - `{{PROMPT}}`
  - `{{NEGATIVE_PROMPT}}`
  - `{{REFERENCE_IMAGE}}`
  - `{{OUTPUT_PREFIX}}`
- 支援下載 ComfyUI history 裡的 image outputs
- 每次輸出一份 `manifest.json`

## UI 使用方式

1. 開啟 VFX MCP UI。
2. 選擇 effect package。
3. 在 `Art Provider` 選 `ComfyUI`。
4. 設定 `ComfyUI Base URL`。
5. 填入你匯出的 ComfyUI API workflow JSON 路徑。
6. 按 `Generate AI Art Pass`。

如果 ComfyUI 沒有啟動，工具會輸出 `provider_unavailable`，並不會假裝生成成功。

## CLI 使用方式

```powershell
py mcp-server/server.py generate-art samples/references/fire --provider comfyui --base-url http://127.0.0.1:8188 --workflow path/to/workflow.json
```

輸出會在：

```text
generated/ai-art/<effect-name>/<provider>/<timestamp>/manifest.json
```

## 下一步

這版先建立 provider 架構。後續可以把 AI art pass 的輸出接回 `analyze-package`，讓 Unreal 自動使用最新 AI 生成序列，而不是只使用原始參考 GIF。
