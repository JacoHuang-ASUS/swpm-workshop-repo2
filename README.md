# swpm-workshop-repo2：技能健檢資料夾（教學版）

Workshop 教學用的技能健檢資料夾。把你在其他資料夾做的練習 repo 登錄成「目標」，在這裡啟動 Claude Code，用 `skill-analyzer-lite` 技能分析目標裡的技能，產出一頁的問題清單報告。它和其他資料夾一樣只是一個工作資料夾，只是專門用來替其他資料夾裡的技能做健檢——只看不改。

原型是講者自己使用的技能健檢資料夾，這個版本大幅簡化：只保留「腳本收集事實＋Agent 判讀＋一頁報告」的核心流程。

本健檢資料夾對目標**只讀不寫**：不修改、不新增、不刪除目標內的任何檔案，也不執行目標的腳本。

## 結構

| 路徑 | 內容 | 進 git |
|---|---|---|
| `.claude/skills/skill-analyzer-lite/` | 分析技能（SKILL.md、檢查表、報告範本、收集事實的腳本） | 是 |
| `tests/` | 收集事實腳本的測試 | 是 |
| `targets.example.json` | 目標登錄檔的格式範例 | 是 |
| `requirements.txt` | 測試用套件（只有 pytest；分析腳本只用 Python 內建功能） | 是 |
| `CLAUDE.md` | 本資料夾的作業規則 | 是 |
| `targets.local.json` | 你自己的目標登錄（含本機路徑） | 否 |
| `.analysis/` | 分析報告 | 否 |
| `.venv/` | Python 虛擬環境 | 否 |
| `.plans/` | 計畫檔 | 否 |

## 需求

- Python 3.10 以上
- Git

## 使用方式

1. 建立虛擬環境並安裝測試套件（在本資料夾執行）：

   ```
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

2. 複製 `targets.example.json` 為 `targets.local.json`，把 `path` 改成你的目標工作資料夾的絕對路徑。目標不能放在本資料夾裡，也不能包含本資料夾。

3. 平常直接對 Claude Code 說「分析〈目標〉的〈技能〉」即可。Claude Code 會用 `skill-analyzer-lite` 技能收集事實、判讀，並把報告寫到 `.analysis/<目標代號>/<技能名稱>_<MMDD>.md`。

需要手動執行時（Windows 寫法；macOS／Linux 把 `.venv\Scripts\python` 換成 `.venv/bin/python`）：

| 用途 | 指令 |
|---|---|
| 列出目標 | `.venv\Scripts\python .claude\skills\skill-analyzer-lite\scripts\collect_facts.py targets` |
| 執行分析 | `.venv\Scripts\python .claude\skills\skill-analyzer-lite\scripts\collect_facts.py --target <目標代號> --skill <技能名稱>` |
| 測試 | `.venv\Scripts\python -m pytest -q` |

腳本只把事實 JSON 印到畫面，不寫任何檔案；報告由 Claude Code 依技能的範本撰寫。

## 授權

MIT，見 [LICENSE](LICENSE)。
