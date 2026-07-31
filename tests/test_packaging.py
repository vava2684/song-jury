# -*- coding: utf-8 -*-
"""打包自足性 —— 這一支是「別人 clone 下來到底跑不跑得起來」的守門員。

🔴 真實事故(2026-07-31):白名單制 .gitignore 漏放行 分軌快取.py 與 伴奏混音.py,
   而 編曲層次.py 與 和聲分析.py 是**頂層 import** 它。每個新使用者一定 ModuleNotFoundError,
   和聲柱整根消失、41.4% 權重失去量測基礎 —— 而安裝自我檢查照樣印綠色「完整」。
   當時只用 `git status` 看「該進的都進了」就宣告完成,所以什麼都沒發現。

   ⛔ 白名單制的代價就是「忘了寫就少檔」,必須有機器把關,不能靠人記。
"""
import ast
import re
import subprocess
import pytest
from conftest import REPO

HEAVY = {"torch", "torchaudio", "librosa", "numpy", "demucs", "gradio",
         "matplotlib", "soundfile", "parselmouth", "scipy", "reportlab", "gradio_client"}


def _tracked():
    r = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"],
                       cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        pytest.skip("不在 git repo 裡,跳過打包檢查")
    return {p.strip() for p in r.stdout.splitlines() if p.strip()}


def _tracked_py():
    """只掃「產品程式」—— tests/ 底下的檔案裡有拿 BASE / "X.py" 當範例的字串,
    不排除的話這些檢查會抓到自己身上。"""
    return sorted(p for p in _tracked()
                  if p.endswith(".py") and not p.startswith("tests/"))


def test_每個被引用的本地模組都在repo裡():
    """掃所有追蹤到的 .py,凡是 import 了同目錄的本地模組,那個檔必須也被追蹤。"""
    tracked = _tracked()
    local = {p.stem for p in REPO.glob("*.py")}
    missing = []
    for f in _tracked_py():
        tree = ast.parse((REPO / f).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in local and f"{n}.py" != f and f"{n}.py" not in tracked:
                    missing.append(f"{f} import 了 {n}.py,但它沒進 repo")
    assert not missing, "\n".join(missing)


def test_每個被subprocess呼叫的腳本都在repo裡():
    """評審團.py 會 subprocess 跑 BASE / "X.py";那些檔一樣不能漏。"""
    tracked = _tracked()
    missing = []
    for f in _tracked_py():
        src = (REPO / f).read_text(encoding="utf-8")
        for m in re.finditer(r'BASE\s*/\s*"([^"]+\.py)"', src):
            if m.group(1) not in tracked:
                missing.append(f"{f} 會執行 {m.group(1)},但它沒進 repo")
    assert not missing, "\n".join(missing)


def test_規則與尺都隨包():
    """系統的靈魂:四把語言尺與評分規則缺一份,詞柱就評不出來。"""
    tracked = _tracked()
    need = ["評詞標準.md", "得獎精神綱要.md", "親聽檢查清單.md",
            "rubrics/ZH_lyric_rubric_v5.md", "rubrics/EN_lyric_rubric_v2.md",
            "rubrics/JA_lyric_rubric_v3.md", "rubrics/KO_lyric_rubric_v4.md"]
    assert [n for n in need if n not in tracked] == []


def test_不可外洩版權音檔與金鑰():
    """⛔ 公開 repo 絕不可以出現得獎歌 mp3、分軌、測試歌、金鑰、個人校準層。"""
    bad = [p for p in _tracked()
           if re.search(r"(^|/)_stems/|(^|/)_C層|(^|/)下載/|\.mp3$|\.flac$|(^|/)\.env$|個人整理", p)]
    assert bad == [], f"這些不該進 repo:{bad}"


def test_評審團py頂層只用標準庫():
    """演唱聽感.py / 真實距離.py 跑在 .venv-audition,卻要 import 評審團 的 iter_windows。
    評審團.py 頂層一旦多一個重相依,那兩支就會在別的 venv 裡爆掉。"""
    tree = ast.parse((REPO / "評審團.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module.split(".")[0])
    assert not (top & HEAVY), f"評審團.py 頂層出現重相依:{sorted(top & HEAVY)}"


def test_安裝腳本會建立全部四個環境():
    """少建一個環境 = 對應的柱永久缺項。這條擋的是「新增環境卻忘了寫進安裝腳本」。"""
    for script in ("install.ps1", "install.sh"):
        src = (REPO / script).read_text(encoding="utf-8")
        for venv in (".venv", ".venv-ml", ".venv-demucs", ".venv-audition"):
            assert venv in src, f"{script} 沒有處理 {venv}"


def test_每個requirements檔都被安裝腳本用到():
    tracked = _tracked()
    reqs = [p for p in tracked if re.fullmatch(r"requirements(-\w+)?\.txt", p)]
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    # 網頁版由 run_web 腳本裝;dev 是給改程式的人裝測試相依,兩者都不該進一般安裝流程
    可選 = {"requirements-web.txt", "requirements-dev.txt"}
    for r in reqs:
        if r in 可選:
            continue
        assert r in ps1 and r in sh, f"{r} 沒有被兩個安裝腳本用到"
