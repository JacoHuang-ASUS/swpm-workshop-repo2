# swpm-workshop-repo2 作業規則（技能健檢資料夾）

本檔是本資料夾的專案層規則；使用者層的 CLAUDE.md 同樣適用，兩者衝突時採較嚴格者。

## 1. 這個資料夾是什麼
- 技能健檢資料夾：在這裡啟動 Claude Code，用 skill-analyzer-lite（skill-analyzer 的教學版）分析其他資料夾（「目標」）裡的技能。
- 預設分支：main。
- 目標登錄在 targets.local.json（格式見 targets.example.json）。
- 分析報告一律寫在本資料夾的 .analysis/<目標代號>/。

## 2. 目標一律只讀
- 登錄的目標資料夾只讀：不修改、不刪除、不搬移、不新增任何檔案。
- 對目標只用唯讀的 git 操作（status、log、diff、show），不做任何會改變目標的 git 操作（含 add、commit、push、pull）。

## 3. 不刪除檔案
- 不刪除本資料夾的任何檔案。需要刪除時，先列出清單，等使用者確認。

## 4. 不進存檔的內容
- 以下內容不進存檔，存檔前確認都已被忽略：.venv/、.analysis/、targets.local.json、.plans/。
- 進存檔的檔案與存檔說明，不得含本機絕對路徑、使用者名稱、電腦名稱、個人電子郵件。

## 5. 存檔規則
- 只把這次改到的檔案加入存檔（git add <指定檔案>），不一次加入整個資料夾。
- 不改寫已存的存檔（不 amend、不 force push）；要修正就再存一筆新的。
- 要撤銷已推送的存檔，用新增一筆撤銷存檔（git revert）的方式。

## 6. 環境
- Python 虛擬環境放在本資料夾的 .venv/，依 requirements.txt 建立。
- 執行分析：`.venv\Scripts\python .claude\skills\skill-analyzer-lite\scripts\collect_facts.py --target <目標代號> --skill <技能名稱>`
- 列出目標：`.venv\Scripts\python .claude\skills\skill-analyzer-lite\scripts\collect_facts.py targets`
- 測試：`.venv\Scripts\python -m pytest -q`（在本資料夾執行）

## 7. 計畫
- 計畫檔放在 .plans/，不進存檔。
