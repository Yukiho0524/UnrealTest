# Unreal VFX Authoring Notes

本文件整理 Unreal Niagara 特效製作面原則，作為本專案 `vfx_plan` 與 Unreal bridge 擴充依據。

## 核心觀念

- Niagara System 是完整特效的容器，通常由多個 emitter 組合而成。
- Emitter 不應全部做同一件事；常見分層包含主體、細節粒子、柔光、拖尾、煙霧、貼花、衝擊波與後段殘留。
- Sprite renderer 本質上是面向相機的 2D plane，外觀主要依賴 texture alpha、material、sprite size、rotation 與 per-particle material parameters。
- 參考圖轉特效時，先保留主視覺剪影，再補動態粒子；不要只提高 spawn rate。
- Niagara 的可調參數應該暴露在 emitter/system 層，讓後續可以迭代大小、生命週期、顏色、透明度、旋轉、速度與噪聲。
- 完成視覺後才做 Effect Type / scalability，避免性能設定太早限制探索。

## 本專案對應設計

- `reference_card_source`：由參考圖萃取主視覺剪影，避免結果只剩噴粒子。
- `composition_layers`：列出主視覺 card、主要 emitter、細節 emitter、柔光等層級。
- `production_notes`：把製作面的注意事項寫入 spec，提醒 Unreal bridge 下一步如何合成。
- `vfx_plan.emitters`：每個 emitter 對應一個可獨立生成與檢查的 Niagara/material/texture layer。
- `composition_layers[].module_stack`：描述該層應該使用的 Niagara 模組，例如 Spawn、Initialize Particle、Curl Noise、Color/Alpha Over Life、Scale Sprite Size。
- `composition_layers[].tuning`：記錄 spawn rate、life、size、opacity、rotation variation 等可調方向，讓 UI 與 Unreal bridge 後續能做更細的調整。

## 下一步

- 改用不切換 World 的安全預覽方式，避免 Unreal Python 開啟 map asset 時觸發 `World Memory Leaks` crash。
- 將 bundle systems 進一步合成成單一 Niagara System 內的多 emitter。
- 將 `production_notes` 轉成實際 Niagara module 設定，例如 size over life、color over life、rotation rate、curl noise、sub UV/flipbook。

## 已落地的安全預覽流程

- `Generate Unreal Assets` 會建立多個 layer asset：reference card、主 `NS_`、細節 `NS_`、各層 texture/material/material instance。
- 先前的 `L_<name>_VFXPreview` map preview 已停用，生成時會嘗試清掉舊的 unsafe preview level。
- `Open In Unreal` 只開主 `NS_`，並同步相關 bundle assets 到 Content Browser，不再由 Python 開啟 World/Map asset。

## 後續真正要再強化的地方

- 把目前分開的 Niagara layer 合成為同一個 Niagara System 內的多個 emitter。
- 依 `module_stack` 實際設定 velocity、curl noise、color/alpha over life、sprite size over life、rotation rate。
- 將 GIF/序列幀轉成 flipbook texture，讓主體不只是靜態 reference card。
- 若要做場景預覽，改用 Editor Utility 或使用者手動開啟關卡，不從 `-ExecCmds=py` 直接呼叫 `open_editor_for_assets` 開 World。
