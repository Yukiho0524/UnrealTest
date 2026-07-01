# UnrealTest

這是一個用來研究 Unreal MCP 架構的原型專案，目標是把設計師提供的視覺參考素材，轉換成 Unreal Engine 內可生成、可迭代的 VFX 資產。

## 目標

1. 從專案內的參考素材資料夾讀取圖片或 GIF，推測特效應該有的動態，並產生對應的 Unreal VFX 規格。
2. 未來支援輸入網址，從網頁或圖片中擷取視覺風格，產生同樣格式的 VFX 規格。
3. 透過 Unreal 端 bridge，把 VFX 規格轉換成 Niagara System、材質與預覽 Actor。

## 目前架構

```text
設計師整理的特效素材包或網址
  -> MCP intake tool
  -> 圖片 / 網頁分析
  -> VFXSpec JSON
  -> Unreal bridge
  -> Niagara / 材質 / 預覽 Actor
```

## 資料夾結構

```text
mcp-server/
  server.py                  MCP MVP 工具的 CLI 入口
  schemas.py                 VFXSpec 的 Python dataclass
  tools/
    analyze_images.py        圖片資料夾分析 stub
    analyze_packages.py      特效素材包分析流程
    unreal_bridge.py         匯出 spec 與 Unreal bridge 命令輔助

specs/
  vfx_spec.schema.json       可攜式 VFX 意圖規格

samples/
  references/                設計師整理的特效素材包，例如 fire/

unreal/
  UnrealTest.uproject        綁定 UE 5.7 的 Unreal 專案描述檔
  engine.version.json        本機 UE 5.7.4 安裝資訊
  Plugins/VFXMCP/            Unreal plugin 原型
```

## Unreal 版本

此 workspace 目前指定使用本機 Unreal Engine 5.7.4：

```text
D:\Program Files\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe
```

可以用以下指令開啟 Unreal 專案：

```powershell
.\unreal\Scripts\Open-UnrealTest.ps1
```

## 設計師素材包格式

每個想生成的特效建議整理成一個資料夾，例如 `fire`：

```text
samples/references/fire/
  images/
    fire_column.png
    ember_loop.gif
  prompt.md
  config.json
```

- `images/`：放入圖片、GIF 或未來可支援的動態參考素材。
- `prompt.md`：描述設計意圖，例如用途、節奏、風格、持續時間。
- `config.json`：可選的明確設定，例如特效類型、顏色、粒子生命週期等。

## 本機 UI 測試

先把圖片或 GIF 放進：

```text
samples/references/fire/images/
```

接著啟動本機 UI：

```powershell
.\mcp-server\Start-VFXMCPUI.ps1
```

打開：

```text
http://127.0.0.1:8765
```

UI 目前可以選擇：

- Unreal project：`UnrealTest`
- Effect package：`fire`
- Destination path：`/Game/VFX/Generated/fire`

UI 目前有三個主要動作：

- `Analyze Package`：只分析素材包，預覽工具理解出來的 `VFXSpec`。
- `Generate Spec`：輸出 `generated/specs/fire.vfxspec.json`，但不啟動 Unreal。
- `Generate Unreal Assets`：啟動本機 UE 5.7.4，讀取 `fire.vfxspec.json`，嘗試在 `/Game/VFX/Generated/fire` 建立 `NS_fire`。
- `Open In Unreal`：開啟 `UnrealTest.uproject`，並嘗試在 Content Browser 內選取、開啟 `/Game/VFX/Generated/fire/NS_fire`。

目前 `Generate Unreal Assets` 已經會呼叫 Unreal Python bridge。Niagara asset 建立會依 UE Python API 是否有暴露對應 factory 而定；如果目前版本無法直接建立，UI 會回傳 `partial` 狀態，代表 spec 已驗證、目的資料夾已處理，但 Niagara 生成細節還需要下一步補齊。

## CLI 測試

也可以直接用 CLI 分析特效素材包：

```powershell
py mcp-server/server.py analyze-package samples/references/fire --out generated/specs
```

這會產生：

```text
generated/specs/fire.vfxspec.json
```

目前分析器會結合素材包名稱、檔名、`prompt.md` 與 `config.json` 來產生第一版規格。之後會把檔名 heuristic 替換成真正的圖片 / GIF / 網頁視覺分析。

## 下一步

- 將檔名 heuristic 替換成圖片與 GIF 分析。
- 新增 `analyze_reference_url(url)`。
- 在 `unreal/Plugins/VFXMCP/Scripts/create_niagara_from_spec.py` 實作真正的 Niagara asset 建立流程。
- 加入 Unreal 內 preview 與自然語言迭代調整工具。
