# -*- coding: utf-8 -*-
"""分軌那條線的直譯器解析 —— 「和聲柱假陽性」的根。

🔴 Codex R13 兩條(互相遮蔽,合起來讓安裝器印出假的九柱齊全):
· Windows 用 py.parent 當 venv 根 → 去找 `Scripts\\Lib\\site-packages`
  (實際在 `<venv>\\Lib\\site-packages`)→ 專案 .venv-demucs 永遠被跳過,
  改用全域 anaconda;
· 而 requirements-demucs.txt 漏了 librosa,只驗 `import demucs` 的話,
  缺 librosa 的環境照樣被判「有」→ 和聲柱(13.6%)整根降級,安裝器卻綠燈。
"""
import re
import sys

import pytest

from conftest import load

J = load("評審團")


def _fake_venv(root, win_layout, pkgs=("demucs",)):
    """造一個假 venv:python 執行檔 + site-packages 套件目錄(不需要真的能跑)。"""
    if win_layout:
        py = root / "Scripts" / "python.exe"
        sp = root / "Lib" / "site-packages"
    else:
        py = root / "bin" / "python"
        sp = root / "lib" / "python3.11" / "site-packages"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    for pkg in pkgs:
        (sp / pkg).mkdir(parents=True, exist_ok=True)
    return py


def test_專案venv要贏過全域conda(tmp_path, monkeypatch):
    """🔴 核心迴歸,而且要**跑真的 _find_demucs_py**(不可以在測試裡複製一份邏輯 ——
    那樣改壞產品碼測試照樣綠,就是裝飾品)。

    佈景:專案 .venv-demucs 與家目錄 anaconda3 都「裝了 demucs」,
    兩支直譯器都能 import 整條線。正確答案永遠是專案 venv ——
    舊碼在 Windows 用 py.parent 當 venv 根,於是專案 venv 的 site-packages
    永遠找不到,靜靜改用全域 anaconda(還剛好遮住 venv 缺 librosa)。"""
    win = sys.platform == "win32"
    monkeypatch.setattr(J, "__file__", str(tmp_path / "評審團.py"))
    monkeypatch.delenv("SONG_JURY_DEMUCS_PY", raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(J.Path, "home", staticmethod(lambda: home))
    venv_py = _fake_venv(tmp_path / ".venv-demucs", win)
    conda_py = home / "anaconda3" / ("python.exe" if win else "bin/python")
    conda_py.parent.mkdir(parents=True, exist_ok=True)
    conda_py.write_text("", encoding="utf-8")
    (home / "anaconda3" / ("Lib/site-packages/demucs" if win else "lib/python3.11/site-packages/demucs")
     ).mkdir(parents=True, exist_ok=True)
    # 假直譯器不能真的執行 → 讓「整條線 import」對兩者都成立,答案只由**順序與預篩**決定
    monkeypatch.setattr(J, "_probe_import", lambda py, mods: True)
    got = J._find_demucs_py()
    assert got == str(venv_py), \
        f"🔴 選到 {got} —— 專案 .venv-demucs 應該優先於全域 conda(Windows venv 根算錯的老 bug)"


def test_整條線的模組清單要含librosa():
    """🔴 只驗 demucs 不夠:和聲分析.py 在同一個環境跑,它 import librosa。
    缺 librosa 時分軌成功、和聲柱整根降級,而安裝器印「九柱齊全」。"""
    assert set(J.DEMUCS_LINE_MODS) >= {"demucs", "librosa", "numpy", "soundfile"}, \
        f"🔴 分軌線驗證清單少了東西:{J.DEMUCS_LINE_MODS}"


def test_只有demucs沒有librosa的環境不可被當成完整(tmp_path, monkeypatch):
    """行為驗證:用真的 python 造兩個環境 —— 一個能 import 整條線、一個只有 demucs。
    _probe_import 必須分得出來(這正是安裝器自檢要問的問題)。"""
    # 真直譯器一定 import 得動 stdlib;拿 json/os 當「有裝」、拿不存在的模組當「沒裝」
    assert J._probe_import(sys.executable, ("json", "os")) is True
    assert J._probe_import(sys.executable, ("json", "絕對不存在的模組_xyz")) is False


def test_環境變數指定的直譯器最優先(tmp_path, monkeypatch):
    monkeypatch.setenv("SONG_JURY_DEMUCS_PY", r"X:\my\python.exe")
    assert J._find_demucs_py() == r"X:\my\python.exe"


def test_安裝腳本自檢要驗整條線而不是只驗demucs():
    """安裝器的自檢也要問同一個問題,否則 repo 修好了、安裝器還在說謊。"""
    from conftest import REPO
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    for name, src in (("install.ps1", ps1), ("install.sh", sh)):
        assert "import demucs, librosa, numpy, soundfile" in src, \
            f"🔴 {name} 只驗 import demucs —— 缺 librosa 的環境會被判成和聲柱完整"
    # ⛔ 不可以整份 grep "librosa" —— 註解裡也寫著這個字,把宣告刪掉照樣命中(裝飾品)。
    #    要**解析成套件名**再看,那才是「有沒有真的宣告」。
    req = (REPO / "requirements-demucs.txt").read_text(encoding="utf-8")
    pkgs = set()
    for ln in req.splitlines():
        ln = ln.split("#")[0].strip()
        if ln and not ln.startswith("-"):
            pkgs.add(re.split(r"[<>=!\[;]", ln)[0].strip().lower())
    assert "librosa" in pkgs, f"🔴 requirements-demucs.txt 沒宣告 librosa(宣告到的:{sorted(pkgs)})"
