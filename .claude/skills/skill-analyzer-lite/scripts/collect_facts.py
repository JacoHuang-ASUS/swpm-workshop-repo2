#!/usr/bin/env python3
"""技能健檢資料夾：事實收集腳本。

讀取「目標」資料夾裡某個 Claude Code 技能，輸出 JSON 事實到 stdout，
交給 Agent 判讀並撰寫報告。

用法：
    collect_facts.py targets [--targets-file PATH]
    collect_facts.py --target <id> --skill <name> [--targets-file PATH]

設計原則：
- 只用內建模組；只寫 stdout，不寫任何檔案。
- 對目標唯讀：只讀檔，外加兩個 git 指令；不執行目標的任何腳本。
- 輸出（含錯誤訊息）不得出現絕對路徑。
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

# 健檢資料夾根目錄：scripts → skill-analyzer-lite → skills → .claude → 根目錄
WORKSTATION_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_TARGETS_FILE = WORKSTATION_ROOT / "targets.local.json"
DEFAULT_SKILLS_DIR = ".claude/skills"

# 遞迴走訪時一律略過的資料夾名稱
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}

# import 名稱與套件名稱不同時的對照表
IMPORT_TO_PACKAGE = {
    "sklearn": "scikit-learn",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
}

# T-2：description 中代表「不適用情境」的字詞（比對前先轉小寫）
NEGATIVE_SCOPE_WORDS = [
    "不適用", "不要用", "不用於", "不處理",
    "not for", "do not use", "don't use",
]

# T-3：標題中代表「錯誤處理」的字詞（比對前先轉小寫）
ERROR_HEADING_WORDS = [
    "錯誤", "例外", "失敗", "error", "exception", "troubleshoot",
]

# T-8：疑似明文秘密的正規式
SECRET_PATTERNS = [
    re.compile(
        r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*['\"][^'\"]{6,}['\"]"
    ),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
]

CHECK_ASPECT = {
    "T-1": "文件與觸發", "T-2": "文件與觸發", "T-3": "文件與觸發", "T-4": "文件與觸發",
    "T-5": "相依與驗證", "T-6": "相依與驗證", "T-7": "相依與驗證", "T-8": "相依與驗證",
}
CHECK_SEVERITY = {
    "T-1": "中", "T-2": "低", "T-3": "中", "T-4": "低",
    "T-5": "中", "T-6": "中", "T-7": "低", "T-8": "高",
}


class FactError(Exception):
    """帶有固定錯誤代碼的可預期錯誤。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 登錄檔讀取與驗證
# ---------------------------------------------------------------------------

def load_registry(registry_path):
    """讀取並完整驗證登錄檔，回傳已解析路徑的目標清單。"""
    if not registry_path.is_file():
        raise FactError(
            "targets_file_not_found",
            f"找不到目標登錄檔（{registry_path.name}）。請先依 targets.example.json 建立。",
        )
    try:
        raw = registry_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise FactError(
            "targets_invalid",
            f"目標登錄檔（{registry_path.name}）無法讀取或不是 UTF-8 編碼（{type(exc).__name__}）。",
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FactError(
            "targets_invalid",
            f"目標登錄檔不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 欄，{exc.msg}。",
        )
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        raise FactError("targets_invalid", "目標登錄檔缺少 targets 清單。")

    # 第一階段：結構驗證
    seen_ids = set()
    entries = []
    for index, item in enumerate(data["targets"], start=1):
        if not isinstance(item, dict):
            raise FactError("targets_invalid", f"targets 第 {index} 筆不是物件。")
        target_id = item.get("id")
        path_value = item.get("path")
        if not isinstance(target_id, str) or not target_id:
            raise FactError("targets_invalid", f"targets 第 {index} 筆缺少字串型的 id。")
        if not isinstance(path_value, str) or not path_value:
            raise FactError("targets_invalid", f"目標 {target_id} 缺少字串型的 path。")
        if target_id in seen_ids:
            raise FactError("targets_invalid", f"目標 id 重複：{target_id}。")
        seen_ids.add(target_id)
        skills_dir = item.get("skills_dir", DEFAULT_SKILLS_DIR)
        if not isinstance(skills_dir, str):
            raise FactError("targets_invalid", f"目標 {target_id} 的 skills_dir 必須是字串。")
        normalized = skills_dir.replace("\\", "/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ".." in PurePosixPath(normalized).parts
        ):
            raise FactError(
                "targets_invalid",
                f"目標 {target_id} 的 skills_dir 必須是相對路徑且不得含 ..。",
            )
        entries.append(
            {
                "id": target_id,
                "raw_path": path_value,
                "skills_dir": PurePosixPath(normalized).as_posix(),
            }
        )

    # 第二階段：路徑驗證（相對路徑以登錄檔所在資料夾為基準）
    base = registry_path.resolve().parent
    for entry in entries:
        candidate = Path(entry["raw_path"])
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve()
        if not resolved.is_dir():
            raise FactError(
                "target_path_not_found",
                f"目標 {entry['id']} 的路徑不存在或不是資料夾。",
            )
        if (
            resolved == WORKSTATION_ROOT
            or WORKSTATION_ROOT in resolved.parents
            or resolved in WORKSTATION_ROOT.parents
        ):
            raise FactError(
                "target_overlaps_workstation",
                f"目標 {entry['id']} 與健檢資料夾互相包含，請改用健檢資料夾以外且不包含健檢資料夾的資料夾。",
            )
        entry["root"] = resolved
    return entries


# ---------------------------------------------------------------------------
# 檔案走訪工具
# ---------------------------------------------------------------------------

def walk_files(top):
    """遞迴列出 top 底下所有檔案（略過 SKIP_DIRS，不跟隨符號連結），回傳排序後的 Path。"""
    result = []
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in filenames:
            result.append(Path(dirpath) / name)
    return sorted(result)


def rel_posix(path, root):
    """相對於目標根目錄的正斜線路徑。"""
    return path.relative_to(root).as_posix()


# ---------------------------------------------------------------------------
# SKILL.md 解析
# ---------------------------------------------------------------------------

KEY_RE = re.compile(r"^([A-Za-z0-9_][\w.\-]*)\s*:\s*(.*?)\s*$")
BLOCK_SCALAR_RE = re.compile(r"^[>|](?:[+-]\d?|\d[+-]?)?$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)\s*$")


def find_frontmatter_end(lines):
    """回傳結束 --- 的索引（0 起算）；沒有 frontmatter 回傳 None。"""
    if not lines or lines[0].rstrip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return index
    return None


def parse_frontmatter(fm_lines, first_lineno):
    """逐行解析頂層 key: value，回傳 {key: (value, 行號)}。"""
    result = {}
    count = len(fm_lines)
    i = 0
    while i < count:
        match = KEY_RE.match(fm_lines[i])
        if not match:
            i += 1
            continue
        key, raw = match.group(1), match.group(2)
        key_lineno = first_lineno + i
        i += 1
        block = []
        while i < count and (not fm_lines[i].strip() or fm_lines[i][0] in " \t"):
            block.append(fm_lines[i])
            i += 1

        if BLOCK_SCALAR_RE.match(raw):
            nonblank = [ln for ln in block if ln.strip()]
            if nonblank:
                indent = min(len(ln) - len(ln.lstrip(" \t")) for ln in nonblank)
            else:
                indent = 0
            dedented = [ln[indent:].rstrip() if ln.strip() else "" for ln in block]
            while dedented and not dedented[-1]:
                dedented.pop()
            if raw.startswith(">"):
                value = " ".join(s.strip() for s in dedented if s.strip())
            else:
                value = "\n".join(dedented).rstrip()
        else:
            parts = [raw] + [ln.strip() for ln in block if ln.strip()]
            value = " ".join(p for p in parts if p)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
        result.setdefault(key, (value, key_lineno))
    return result


def extract_headings(lines, start_index):
    """取出 ATX 標題（忽略程式碼區塊），回傳 [(行號, 文字)]。"""
    headings = []
    fence = None
    for index in range(start_index, len(lines)):
        line = lines[index]
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        match = HEADING_RE.match(line)
        if not match:
            continue
        text = re.sub(r"\s+#+$", "", match.group(2)).strip()
        if text:
            headings.append((index + 1, text))
    return headings


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def run_git(root, args):
    """執行唯讀 git 指令，成功回傳 stdout 字串，失敗回傳 None。"""
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def collect_git(root):
    """只有目標根目錄下有 .git 才執行 git，避免誤抓到上層 repo。"""
    if not (root / ".git").exists():
        return {"commit": None, "dirty": None}
    commit_out = run_git(root, ["rev-parse", "--short", "HEAD"])
    status_out = run_git(root, ["status", "--porcelain"])
    commit = commit_out.strip() if commit_out and commit_out.strip() else None
    dirty = bool(status_out.strip()) if status_out is not None else None
    return {"commit": commit, "dirty": dirty}


# ---------------------------------------------------------------------------
# 8 項檢查
# ---------------------------------------------------------------------------

def make_check(check_id, result, evidence):
    return {
        "id": check_id,
        "aspect": CHECK_ASPECT[check_id],
        "severity": CHECK_SEVERITY[check_id],
        "result": result,
        "evidence": evidence,
    }


def ev(file, line, note):
    return {"file": file, "line": line, "note": note}


def normalize_req_name(name):
    return name.strip().lower().replace("_", "-")


def parse_requirements(text):
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[\s]", line, maxsplit=1)[0]
        if name:
            names.add(normalize_req_name(name))
    return names


def check_t5(ctx):
    """T-5：第三方 import 是否都列在 requirements.txt。"""
    root = ctx["root"]
    skill_dir = ctx["skill_dir"]
    py_files = ctx["py_files"]
    skill_md_rel = ctx["skill_md_rel"]
    notes = []

    local_names = set()
    for py in py_files:
        local_names.add(py.stem)
        if py.parent != skill_dir:
            local_names.add(py.parent.name)
    # 含 .py 的子資料夾（任何一層）名稱都視為本地模組
    for py in py_files:
        parent = py.parent
        while parent != skill_dir and skill_dir in parent.parents:
            local_names.add(parent.name)
            parent = parent.parent

    # package -> (file_rel, line, import 名稱)，記錄最早出現處
    third_party = {}
    for py in py_files:
        rel = rel_posix(py, root)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=py.name)
        except (OSError, UnicodeDecodeError) as exc:
            notes.append(ev(rel, None, f"無法讀取或解碼，已略過 import 分析（{type(exc).__name__}）"))
            continue
        except (SyntaxError, ValueError, RecursionError) as exc:
            lineno = getattr(exc, "lineno", None)
            notes.append(ev(rel, lineno, f"語法錯誤，已略過 import 分析（{type(exc).__name__}）"))
            continue
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0 or not node.module:
                    continue
                modules = [node.module]
            for module in modules:
                top = module.split(".")[0]
                if top in sys.stdlib_module_names or top in local_names:
                    continue
                package = IMPORT_TO_PACKAGE.get(top, top)
                candidate = (rel, node.lineno, top)
                current = third_party.get(package)
                if current is None or (candidate[0], candidate[1]) < (current[0], current[1]):
                    third_party[package] = candidate

    # 找 requirements.txt：先技能資料夾，再目標根目錄
    req_path = None
    for candidate in (skill_dir / "requirements.txt", root / "requirements.txt"):
        if candidate.is_file():
            req_path = candidate
            break
    req_rel = rel_posix(req_path, root) if req_path else None
    req_names = set()
    if req_path:
        try:
            req_names = parse_requirements(req_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as exc:
            notes.append(ev(req_rel, None, f"requirements.txt 無法讀取或解碼（{type(exc).__name__}）"))

    if not third_party and req_path is None:
        evidence = [ev(skill_md_rel, None, "沒有第三方 import，且找不到 requirements.txt")] + notes
        return make_check("T-5", "na", evidence)

    missing = [
        (package, info)
        for package, info in sorted(third_party.items())
        if normalize_req_name(package) not in req_names
    ]
    if missing:
        evidence = []
        for package, (rel, lineno, _top) in missing:
            if req_path is None:
                note = f"找不到 requirements.txt，未宣告套件：{package}"
            else:
                note = f"{req_rel} 缺少套件：{package}"
            evidence.append(ev(rel, lineno, note))
        return make_check("T-5", "fail", evidence + notes)

    if third_party:
        note = f"第三方 import（{len(third_party)} 個套件）皆已列於 {req_rel}"
    else:
        note = f"沒有第三方 import；使用的 requirements.txt：{req_rel}"
    return make_check("T-5", "pass", [ev(req_rel, None, note)] + notes)


def check_t6(ctx):
    """T-6：SKILL.md 提到的腳本是否有對應測試。"""
    root = ctx["root"]
    skill_md_rel = ctx["skill_md_rel"]
    lines = ctx["lines"]

    scripts = {}  # basename -> 首次出現行號（dict 保留插入順序）
    pattern = re.compile(r"[\w./\\\-]+\.py\b")
    for index, line in enumerate(lines, start=1):
        for match in pattern.finditer(line):
            base = re.split(r"[/\\]", match.group(0))[-1]
            if len(base) > 3:
                scripts.setdefault(base, index)
    if not scripts:
        return make_check("T-6", "na", [ev(skill_md_rel, None, "SKILL.md 沒有提到任何 .py 腳本")])

    test_files = []
    for path in walk_files(root):
        name = path.name.lower()
        if name.startswith("test_") and name.endswith(".py"):
            test_files.append(path)

    evidence = []
    all_found = True
    for base, lineno in scripts.items():
        stem = base[:-3].lower()
        prefix = f"test_{stem}"
        matches = [p for p in test_files if p.name.lower().startswith(prefix)]
        if matches:
            rels = [rel_posix(p, root) for p in matches]
            evidence.append(ev(rels[0], None, f"腳本 {base} 的測試：{'、'.join(rels)}"))
        else:
            all_found = False
            evidence.append(ev(skill_md_rel, lineno, f"腳本 {base}：找不到測試"))
    return make_check("T-6", "pass" if all_found else "fail", evidence)


def check_t8(ctx):
    """T-8：掃描技能資料夾內的疑似明文秘密（命中的值一律不輸出）。"""
    root = ctx["root"]
    skill_md_rel = ctx["skill_md_rel"]
    hits = []
    notes = []
    scanned = 0
    skipped_binary = 0
    for path in walk_files(ctx["skill_dir"]):
        rel = rel_posix(path, root)
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            skipped_binary += 1
            continue
        except OSError as exc:
            notes.append(ev(rel, None, f"無法讀取，未掃描（{type(exc).__name__}）"))
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(p.search(line) for p in SECRET_PATTERNS):
                hits.append(ev(rel, lineno, "疑似明文秘密（值不顯示）"))
    if hits:
        return make_check("T-8", "fail", hits + notes)
    summary = f"掃描 {scanned} 個文字檔，未發現疑似明文秘密"
    if skipped_binary:
        summary += f"（略過 {skipped_binary} 個非 UTF-8 檔案）"
    return make_check("T-8", "pass", [ev(skill_md_rel, None, summary)] + notes)


def build_checks(ctx):
    description = ctx["description"]
    desc_line = ctx["desc_line"]
    skill_md_rel = ctx["skill_md_rel"]
    headings = ctx["headings"]
    py_files = ctx["py_files"]
    skill_md_lines = ctx["skill_md_lines"]
    read_note = ctx["read_note"]
    checks = []

    # T-1
    t1_note = "依 checklist 判讀 description 是否含做什麼＋何時用＋觸發關鍵字；原文見 skill.description"
    if desc_line is None:
        t1_note = "frontmatter 沒有 description；" + t1_note
    if read_note:
        t1_note += f"；{read_note}"
    checks.append(make_check("T-1", "needs_judgement", [ev(skill_md_rel, desc_line, t1_note)]))

    # T-2
    desc_lower = description.lower().replace("’", "'")
    found = [w for w in NEGATIVE_SCOPE_WORDS if w in desc_lower]
    if found:
        checks.append(make_check("T-2", "pass", [ev(skill_md_rel, desc_line, f"命中字詞：{'、'.join(found)}")]))
    else:
        checks.append(make_check("T-2", "fail", [ev(skill_md_rel, desc_line, "未找到不適用情境的字詞")]))

    # T-3
    if not py_files:
        checks.append(make_check("T-3", "na", [ev(skill_md_rel, None, "技能資料夾內沒有 .py 檔")]))
    else:
        hit_evidence = []
        for lineno, text in headings:
            lowered = text.lower()
            matched = [w for w in ERROR_HEADING_WORDS if w in lowered]
            if matched:
                hit_evidence.append(ev(skill_md_rel, lineno, f"標題「{text}」命中：{'、'.join(matched)}"))
        if hit_evidence:
            checks.append(make_check("T-3", "pass", hit_evidence))
        else:
            checks.append(make_check("T-3", "fail", [ev(skill_md_rel, None, "技能含 .py，但標題中沒有錯誤處理相關字詞")]))

    # T-4
    result4 = "fail" if skill_md_lines >= 500 else "pass"
    checks.append(make_check("T-4", result4, [ev(skill_md_rel, None, f"SKILL.md 共 {skill_md_lines} 行（門檻 500）")]))

    # T-5 ～ T-8
    checks.append(check_t5(ctx))
    checks.append(check_t6(ctx))
    checks.append(make_check("T-7", "needs_judgement", [
        ev(skill_md_rel, None, "依 checklist 判讀是否寫了完成條件／驗證方式；標題見 skill.headings")
    ]))
    checks.append(check_t8(ctx))
    return checks


# ---------------------------------------------------------------------------
# 兩種模式
# ---------------------------------------------------------------------------

def list_skills(entry):
    skills_root = entry["root"] / entry["skills_dir"]
    if not skills_root.is_dir():
        return []
    names = []
    try:
        children = list(skills_root.iterdir())
    except OSError:
        return []
    for child in children:
        if child.is_dir() and (child / "SKILL.md").is_file():
            names.append(child.name)
    return sorted(names)


def mode_targets(entries):
    return {
        "targets": [
            {"id": e["id"], "skills_dir": e["skills_dir"], "skills": list_skills(e)}
            for e in entries
        ]
    }


def mode_facts(entries, target_id, skill_name):
    entry = next((e for e in entries if e["id"] == target_id), None)
    if entry is None:
        raise FactError("target_not_found", f"登錄檔中找不到目標 {target_id}。")
    root = entry["root"]

    if (
        not skill_name
        or "/" in skill_name
        or "\\" in skill_name
        or ".." in skill_name
    ):
        raise FactError("skill_not_found", f"目標 {target_id} 找不到技能 {skill_name}。")
    skill_dir = root / entry["skills_dir"] / skill_name
    skill_md = skill_dir / "SKILL.md"
    skill_md_rel_hint = f"{entry['skills_dir']}/{skill_name}/SKILL.md"
    if not skill_md.is_file():
        raise FactError(
            "skill_not_found",
            f"目標 {target_id} 找不到技能 {skill_name}（預期 {skill_md_rel_hint}）。",
        )
    skill_md_rel = rel_posix(skill_md, root)

    read_note = None
    try:
        raw_bytes = skill_md.read_bytes()
    except OSError as exc:
        raise FactError(
            "skill_not_found",
            f"目標 {target_id} 的技能 {skill_name} 其 SKILL.md 無法讀取（{type(exc).__name__}）。",
        )
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8-sig", errors="replace")
        read_note = "SKILL.md 含無法以 UTF-8 解碼的內容，已以取代字元讀取"

    lines = text.splitlines()
    end_index = find_frontmatter_end(lines)
    if end_index is None:
        raise FactError(
            "no_frontmatter",
            f"{skill_md_rel_hint} 的第一行不是 ---，或找不到對應的結束 ---。",
        )
    frontmatter = parse_frontmatter(lines[1:end_index], first_lineno=2)
    description, desc_line = "", None
    if "description" in frontmatter:
        description, desc_line = frontmatter["description"]

    headings = extract_headings(lines, end_index + 1)
    py_files = [p for p in walk_files(skill_dir) if p.suffix == ".py"]
    scripts = sorted(p.relative_to(skill_dir).as_posix() for p in py_files)

    ctx = {
        "root": root,
        "skill_dir": skill_dir,
        "skill_md_rel": skill_md_rel,
        "lines": lines,
        "description": description,
        "desc_line": desc_line,
        "headings": headings,
        "py_files": py_files,
        "skill_md_lines": len(lines),
        "read_note": read_note,
    }
    return {
        "target": {"id": entry["id"], "skills_dir": entry["skills_dir"]},
        "git": collect_git(root),
        "skill": {
            "name": skill_name,
            "description": description,
            "skill_md_lines": len(lines),
            "headings": [text for _lineno, text in headings],
            "scripts": scripts,
        },
        "checks": build_checks(ctx),
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description="收集單一 Claude Code 技能的事實（JSON 輸出到 stdout）。"
    )
    parser.add_argument("command", nargs="?", choices=["targets"], help="targets：列出登錄檔中的目標與技能")
    parser.add_argument("--target", help="目標代號（登錄檔中的 id）")
    parser.add_argument("--skill", help="技能資料夾名稱")
    parser.add_argument(
        "--targets-file",
        help="目標登錄檔路徑（預設：健檢資料夾根目錄的 targets.local.json）",
    )
    return parser


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()
    if args.command != "targets" and not (args.target and args.skill):
        parser.error("請使用 targets，或同時提供 --target 與 --skill")

    registry_path = Path(args.targets_file) if args.targets_file else DEFAULT_TARGETS_FILE
    try:
        entries = load_registry(registry_path)
        if args.command == "targets":
            output = mode_targets(entries)
        else:
            output = mode_facts(entries, args.target, args.skill)
    except FactError as exc:
        print(json.dumps({"error": exc.code, "message": exc.message}, ensure_ascii=False, indent=2))
        sys.exit(1)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
