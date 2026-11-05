---
name: skill-analyzer-lite
description: 分析指定目標 repo 裡某個 Claude Code 技能的文件與相依品質，並產出一頁【高】【中】【低】問題清單報告（不打分數）。當使用者提到技能分析、技能審查、analyze skill、skill review，或想知道某個技能的 description、錯誤處理、相依套件、測試與完成條件寫得好不好時使用。若只要求執行某技能本身，或要修改目標技能的內容，則不適用；也不用於審查技能以外的一般程式碼或文件。
---

# 技能分析（skill-analyzer-lite）

分析「目標」repo 裡的單一技能。腳本負責收集事實並判定 8 項檢查中的 6 項，Agent 只判讀 T-1、T-7 兩項，再寫一頁報告。目標時間 1–3 分鐘。

指令在健檢資料夾根目錄執行。以下為 Windows 寫法；macOS／Linux 把 `.venv\Scripts\python` 換成 `.venv/bin/python`。

## 限制（務必遵守）

- 對目標只讀：不修改、不新增、不刪除、不搬移任何檔案。
- 不讀目標的 `.venv`、`.git` 內容。
- 不執行目標的任何腳本。
- 對目標只透過 `collect_facts.py` 執行兩個唯讀 git 指令；Agent 自己不對目標執行 git。
- 報告只寫在本健檢資料夾的 `.analysis/` 底下。

## 流程

### 1. 確認目標與技能

使用者沒有指定目標或技能時，先列出可選項目：

```
.venv\Scripts\python .claude\skills\skill-analyzer-lite\scripts\collect_facts.py targets
```

輸出 `{"targets": [{"id", "skills_dir", "skills": [...]}]}`。把目標代號與各目標的技能列給使用者，請使用者選一個目標與一個技能。

### 2. 取得事實

```
.venv\Scripts\python .claude\skills\skill-analyzer-lite\scripts\collect_facts.py --target <目標代號> --skill <技能名稱>
```

輸出單一技能的事實 JSON：

- `target`：`id`、`skills_dir`
- `git`：`commit`（短碼）、`dirty`（是否有未存檔修改）；非 git 目標兩者為 `null`
- `skill`：`name`、`description`、`skill_md_lines`、`headings`、`scripts`
- `checks`：8 個物件，含 `id`（T-1…T-8）、`aspect`、`severity`、`result`、`evidence`（每筆 `file`、`line`、`note`）

### 3. 只判讀 `needs_judgement` 的項目

- 只處理 `result` 為 `needs_judgement` 的項目，也就是 T-1 與 T-7，依 `references/checklist.md` 的準則判成 `pass` 或 `fail`，各附一句理由。
- T-7 若標題清單不足以判斷，可讀目標技能的 `SKILL.md`（唯讀）。
- 腳本已判定為 `pass`、`fail`、`na` 的項目照用，不要重新判定。

### 4. 寫報告

依 `references/report-template.md` 寫成一頁報告，存到本健檢資料夾：

```
.analysis/<目標代號>/<技能名稱>_<MMDD>.md
```

- `MMDD` 為分析當天的月日。
- `.analysis/<目標代號>/` 不存在就建立。
- 同名檔已存在時，改用 `<技能名稱>_<MMDD>-2.md`、`-3`……依序往後；不覆寫、不刪除任何既有報告。
- 報告不得含本機絕對路徑，一律用目標內的相對路徑。
- T-8 的證據只寫檔名與行號，不寫命中的值。

### 5. 回報

在對話中用三行以內回報：結論（有無【高】、【中】【低】各幾項）與報告位置。

## 錯誤處理

腳本失敗時輸出 `{"error": "<代碼>", "message": "<說明>"}` 且結束碼非 0。依代碼回應使用者，不要自行猜測或繞過：

| 代碼 | 意義 | Agent 的回應 |
|---|---|---|
| `targets_file_not_found` | 沒有 `targets.local.json` | 請使用者依 `targets.example.json` 建立 `targets.local.json`，建好後再重跑。 |
| `targets_invalid` | JSON 格式錯、缺 id 或 path、id 重複 | 轉述 `message`，請使用者修正 `targets.local.json`。 |
| `target_path_not_found` | 登錄的路徑不存在 | 請使用者確認該目標的 `path` 是否正確。 |
| `target_overlaps_workstation` | 目標與健檢資料夾互相包含 | 說明目標不能在健檢資料夾內，也不能包含健檢資料夾，請使用者改登錄其他路徑。 |
| `target_not_found` | 目標代號不在登錄檔 | 跑 `targets` 列出可選目標，請使用者重選。 |
| `skill_not_found` | 目標內找不到該技能 | 列出該目標的 `skills`（來自 `targets` 輸出），請使用者重選。 |
| `no_frontmatter` | SKILL.md 沒有 frontmatter | 這本身就是重大問題（技能無法被觸發），向使用者說明；不寫報告，並建議先補上含 `name` 與 `description` 的 frontmatter 後再分析。 |

其他未列出的錯誤或非預期輸出：原樣轉述 `message`，不寫報告。

## 怎樣才算完成

- 報告檔已存在於 `.analysis/<目標代號>/`。
- 8 項檢查（T-1…T-8）都有結果。
- 判讀的 T-1、T-7 都有寫一句理由。
- 目標的檔案沒有任何改動，既有報告也沒有被覆寫或刪除。
- 對話中已用三行以內回報結論與報告位置。
