"""collect_facts.py 的測試：全部使用 tmp_path 合成的假目標與假技能。"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".claude/skills/skill-analyzer-lite/scripts/collect_facts.py"
)
GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def make_skill(target, name="demo", description="示範技能。不適用於其他情境。",
               body="# 說明\n", extra=None, raw_text=None):
    """在目標內建立假技能；extra 為 {相對技能資料夾的路徑: 內容}。"""
    skill_dir = target / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = raw_text if raw_text is not None else (
        f"---\nname: {name}\ndescription: {description}\n---\n{body}"
    )
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    for rel, content in (extra or {}).items():
        path = skill_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return skill_dir


def write_registry(tmp_path, entries):
    """entries: [(id, 路徑)]；登錄檔放在目標之外。"""
    registry = tmp_path / "registry.json"
    data = {"targets": [
        {"id": i, "path": str(p), "skills_dir": ".claude/skills"} for i, p in entries
    ]}
    registry.write_text(json.dumps(data), encoding="utf-8")
    return registry


def run(*args):
    """執行腳本，回傳 (結束碼, 解析後 JSON, 原始 stdout)。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, encoding="utf-8",
    )
    return proc.returncode, json.loads(proc.stdout), proc.stdout


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "target"
    path.mkdir()
    return path


def facts(tmp_path, target, skill="demo"):
    registry = write_registry(tmp_path, [("t1", target)])
    code, data, raw = run("--target", "t1", "--skill", skill,
                          "--targets-file", str(registry))
    return code, data, raw


def check(data, check_id):
    return next(c for c in data["checks"] if c["id"] == check_id)


# ---------------------------------------------------------------------------
# targets 模式
# ---------------------------------------------------------------------------

def test_targets_正常載入(tmp_path, target):
    make_skill(target, "demo")
    registry = write_registry(tmp_path, [("t1", target)])
    code, data, _ = run("targets", "--targets-file", str(registry))
    assert code == 0
    assert data["targets"][0]["id"] == "t1"
    assert data["targets"][0]["skills"] == ["demo"]


def test_targets_id重複(tmp_path, target):
    registry = write_registry(tmp_path, [("dup", target), ("dup", target)])
    code, data, _ = run("targets", "--targets-file", str(registry))
    assert code == 1
    assert data["error"] == "targets_invalid"


def test_targets_路徑不存在(tmp_path):
    registry = write_registry(tmp_path, [("t1", tmp_path / "missing")])
    code, data, _ = run("targets", "--targets-file", str(registry))
    assert code == 1
    assert data["error"] == "target_path_not_found"


# ---------------------------------------------------------------------------
# 檢查項
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("description, expected", [
    ("做某事。不適用於其他情境。", "pass"),
    ("做某事，何時都能用。", "fail"),
])
def test_t2_不適用情境(tmp_path, target, description, expected):
    make_skill(target, description=description)
    _, data, _ = facts(tmp_path, target)
    assert check(data, "T-2")["result"] == expected


def test_t3_錯誤處理標題(tmp_path, target):
    make_skill(target, extra={"scripts/run.py": "import json\n"})
    _, data, _ = facts(tmp_path, target)
    assert check(data, "T-3")["result"] == "fail"


def test_t3_沒有py為na(tmp_path, target):
    make_skill(target)
    _, data, _ = facts(tmp_path, target)
    assert check(data, "T-3")["result"] == "na"


@pytest.mark.parametrize("total, expected", [(499, "pass"), (500, "fail")])
def test_t4_行數門檻(tmp_path, target, total, expected):
    # frontmatter 佔 4 行，其餘補內文至指定總行數
    body = "\n".join(["內文"] * (total - 4)) + "\n"
    make_skill(target, body=body)
    _, data, _ = facts(tmp_path, target)
    assert data["skill"]["skill_md_lines"] == total
    assert check(data, "T-4")["result"] == expected


@pytest.mark.parametrize("script, reqs, expected", [
    ("import requests\n", "click\n", "fail"),
    ("from PIL import Image\n", "Pillow\n", "pass"),
    ("import json\nimport os\n", None, "na"),
])
def test_t5_第三方相依(tmp_path, target, script, reqs, expected):
    extra = {"scripts/run.py": script}
    if reqs is not None:
        extra["requirements.txt"] = reqs
    make_skill(target, extra=extra)
    _, data, _ = facts(tmp_path, target)
    assert check(data, "T-5")["result"] == expected


@pytest.mark.parametrize("with_test, expected", [(True, "pass"), (False, "fail")])
def test_t6_腳本有對應測試(tmp_path, target, with_test, expected):
    make_skill(target, body="執行 scripts/foo.py\n",
               extra={"scripts/foo.py": "import json\n"})
    if with_test:
        (target / "tests").mkdir()
        (target / "tests" / "test_foo.py").write_text("", encoding="utf-8")
    _, data, _ = facts(tmp_path, target)
    assert check(data, "T-6")["result"] == expected


def test_t8_秘密不外洩(tmp_path, target):
    fake = "Zq9" * 4  # 12 個字元的假值，於測試內組出
    make_skill(target, extra={"scripts/cfg.py": f'x = 1\napi_key = "{fake}"\n'})
    _, data, raw = facts(tmp_path, target)
    t8 = check(data, "T-8")
    assert t8["result"] == "fail"
    assert fake not in raw
    hit = t8["evidence"][0]
    assert hit["file"].endswith("scripts/cfg.py")
    assert hit["line"] == 2


# ---------------------------------------------------------------------------
# git 與唯讀
# ---------------------------------------------------------------------------

def snapshot(root):
    """目標內所有檔案（含 .git）的 {相對路徑: SHA-256}。"""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_唯讀且git資訊正確(tmp_path, target):
    make_skill(target, extra={"scripts/run.py": "import json\n"})
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "add", "."], cwd=target, check=True)
    subprocess.run(["git", *GIT_ID, "commit", "-q", "-m", "init"],
                   cwd=target, check=True)
    before = snapshot(target)
    _, data, _ = facts(tmp_path, target)
    after = snapshot(target)
    assert before == after
    assert isinstance(data["git"]["commit"], str) and data["git"]["commit"]
    assert data["git"]["dirty"] is False


def test_非git目標(tmp_path, target):
    make_skill(target)
    _, data, _ = facts(tmp_path, target)
    assert data["git"] == {"commit": None, "dirty": None}


# ---------------------------------------------------------------------------
# 錯誤情境
# ---------------------------------------------------------------------------

def test_找不到技能(tmp_path, target):
    make_skill(target)
    code, data, _ = facts(tmp_path, target, skill="nope")
    assert code != 0
    assert data["error"] == "skill_not_found"


def test_沒有frontmatter(tmp_path, target):
    make_skill(target, raw_text="# 只有標題\n")
    code, data, _ = facts(tmp_path, target)
    assert code != 0
    assert data["error"] == "no_frontmatter"
