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
    """安裝器的自檢也要問同一個問題,否則 repo 修好了、安裝器還在說謊。

    ⭐ 2026-08-02 起兩支安裝器都改成呼叫 `分軌線檢查.py`(唯一實作):
       兩邊各自抄一份探測邏輯 = 兩套真理,遲早只修到其中一邊。"""
    from conftest import REPO
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    for name, src in (("install.ps1", ps1), ("install.sh", sh)):
        assert "分軌線檢查.py" in src, f"🔴 {name} 沒有呼叫共用的分軌線體檢"
        assert "import demucs, librosa, numpy, soundfile" not in src, \
            f"🔴 {name} 又自己抄了一份探測 —— 邏輯只能有一份"
    # helper 自己不可以另抄一份模組清單(要跟評審團.py 同源)
    helper = (REPO / "分軌線檢查.py").read_text(encoding="utf-8")
    assert "DEMUCS_LINE_MODS" in helper and "from 評審團 import" in helper, \
        "🔴 分軌線檢查.py 要跟評審團.py 共用模組清單,不可以另抄"
    # ⛔ 不可以整份 grep "librosa" —— 註解裡也寫著這個字,把宣告刪掉照樣命中(裝飾品)。
    #    要**解析成套件名**再看,那才是「有沒有真的宣告」。
    req = (REPO / "requirements-demucs.txt").read_text(encoding="utf-8")
    pkgs = set()
    for ln in req.splitlines():
        ln = ln.split("#")[0].strip()
        if ln and not ln.startswith("-"):
            pkgs.add(re.split(r"[<>=!\[;]", ln)[0].strip().lower())
    assert "librosa" in pkgs, f"🔴 requirements-demucs.txt 沒宣告 librosa(宣告到的:{sorted(pkgs)})"


# ── 分軌線體檢(分軌線檢查.py)的行為 ────────────────────────────────
# 🔴 2026-08-02 實跑事故:自我檢查印「和聲 13.6% 缺項 → 評不出有效分數」exit 1,
#    同一次執行的 -VerifyModels 卻用**同一條線**跑完九柱、拿到 VERIFY_OK。
# 🔴 Codex R17-1 又抓到第一版四個病:救回來抹掉證據、三次 600s 疊成 30 分沒輸出、
#    用「單獨 import demucs 成功」反推缺套件、attempts=0 回沒有原因的失敗。
D = load("分軌線檢查")
QUIET = lambda _s: None          # noqa: E731  測試不要噴進度


class _R:
    def __init__(self, rc, err=""):
        self.returncode, self.stdout, self.stderr = rc, "", err


def test_暫時性失敗要再給一次機會(monkeypatch):
    """剛裝完那一刻最容易假失敗(幾 GB 剛寫下去、防毒正在掃、第一次 import 建快取)。
    一次失敗就定生死 = 拿最不穩的那一秒當永久結論。"""
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return _R(0) if len(calls) > 1 else _R(1, "OSError: [WinError 5] 存取被拒")

    monkeypatch.setattr(D.subprocess, "run", fake)
    res = D.probe("py", pause=0, log=QUIET)
    assert res.ok is True, f"🔴 第二次就成功了卻判死:{res.why}"
    assert len(calls) == 2, "要真的重試,不是只把旗標打開"


def test_救回來的要留下證據不可以當沒事發生(monkeypatch):
    """🔴 Codex R17-1:舊版 fail→success 直接回 (True, '') —— 第一次的錯誤消失,
    安裝器只看到綠燈。間歇性不穩正是「這次沒事、下次評分掉柱」的來源,
    必須留痕,讓安裝器印成警告。"""
    seq = iter([_R(1, "ImportError: DLL load failed while importing _C"), _R(0)])
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: next(seq))
    res = D.probe("py", pause=0, log=QUIET)
    assert res.ok and res.recovered, "🔴 這是『重試才成功』,不可以跟一次就過的畫上等號"
    assert "DLL load failed" in res.first_error, f"🔴 第一次的錯誤被抹掉了:{res!r}"
    assert res.tries == 2


def test_失敗一定要講出真正的原因(monkeypatch):
    """沒有原因的「你缺一根柱子」是最難修的訊息 —— 使用者無從查起。"""
    monkeypatch.setattr(D.subprocess, "run",
                        lambda cmd, **kw: _R(1, "ModuleNotFoundError: No module named 'librosa'"))
    res = D.probe("py", attempts=1, pause=0, log=QUIET)
    assert res.ok is False
    assert "librosa" in res.why, f"🔴 原因被吞掉了:{res.why!r}"


def test_缺套件是確定性的不可以重試(monkeypatch):
    """🔴 Codex R17-1:缺模組重試一百次還是缺 —— 白等一次 pause + 一次冷啟動。
    只有『可能是暫時的』那兩類(啟動失敗 / import 炸掉)才值得再試。"""
    calls = []
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: (
        calls.append(1),
        _R(1, "ModuleNotFoundError: No module named 'librosa'"))[1])
    res = D.probe("py", attempts=3, pause=0, log=QUIET)
    assert len(calls) == 1, f"🔴 缺套件還重試了 {len(calls)} 次"
    assert res.kind == D.MISSING and res.module == "librosa"


def test_逾時不可以乘成好幾份預算(monkeypatch):
    """🔴 Codex R17-1 實測:三次各 600s + pause = 1805 秒(30 分)完全沒有輸出,
    使用者只會覺得安裝器當掉。預算是**整段**的,不是每次各給一份。

    ⚠️ 要**模擬時間真的過去**才測得出來:不然「剩餘預算」與「整份預算」在
    第一次嘗試時剛好一樣大,把 timeout=left 改成 timeout=budget 也照樣綠
    (變異驗證抓到我這個裝飾品)。"""
    seen = []
    clock = iter(range(0, 10_000, 3))       # 每次讀秒都前進 3 秒

    def fake_run(cmd, **kw):
        seen.append(kw.get("timeout"))
        if len(seen) == 1:
            return _R(1, "ImportError: 剛裝完還沒穩")     # 可重試 → 會有第二次
        raise D.subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(D.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(D.subprocess, "run", fake_run)
    res = D.probe("py", attempts=3, budget=10, pause=0, log=QUIET)
    assert res.kind == D.TIMEOUT
    assert len(seen) == 2, f"逾時之後不該再試:{seen}"
    assert seen[-1] < 10, \
        f"🔴 第二次又拿到整份預算({seen[-1]}s)—— 這正是 600+600+600 的來源"
    # ⚠️ 不可以拿「各次上限相加」當判準:上限是還能等多久,不是真的等了多久
    #    (第一次很快就失敗,只用掉 3 秒)。要驗的是**每次都不超過剩餘預算**。
    assert all(x <= 10 for x in seen), f"🔴 有一次的上限超過總預算:{seen}"


def test_每次嘗試都要先報進度(monkeypatch):
    """⛔ 這支最壞會等好幾分鐘 —— 沒有輸出的等待跟當機在使用者眼裡一模一樣。"""
    lines = []
    seq = iter([_R(1, "ImportError: boom"), _R(0)])
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: next(seq))
    D.probe("py", pause=0, log=lines.append)
    assert len([x for x in lines if "分軌線體檢" in x]) == 2, f"🔴 進度沒印全:{lines}"


def test_不驗就不該叫這支():
    """attempts=0 舊版回 (False, "") —— 一個沒有原因的失敗,違反這支自己的規矩。"""
    import pytest as _pt
    with _pt.raises(ValueError):
        D.probe("py", attempts=0, log=QUIET)


def test_非缺套件的錯不可以被說成缺套件(monkeypatch, tmp_path, capsys):
    """🔴 Codex R17-1:舊版用「單獨 import demucs 成功」反推「缺 librosa/numpy/soundfile」。
    DLL/ABI 壞掉、權限、快取損壞全被導向「請重裝 requirements」—— 修不好還怪自己。
    分類要由**錯誤本身**決定。"""
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("", encoding="utf-8")
    monkeypatch.setattr(D.subprocess, "run",
                        lambda *a, **k: _R(1, "ImportError: DLL load failed while importing _C"))
    assert D.main([str(fake_py)]) == 2, "🔴 DLL 壞掉被歸類成缺套件(exit 1)"
    out = capsys.readouterr().out
    assert "import_error" in out and "DLL" in out


def test_缺套件回1並指名模組(monkeypatch, tmp_path, capsys):
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("", encoding="utf-8")
    monkeypatch.setattr(D.subprocess, "run",
                        lambda *a, **k: _R(1, "ModuleNotFoundError: No module named 'soundfile'"))
    assert D.main([str(fake_py)]) == 1
    assert "soundfile" in capsys.readouterr().out


def test_找不到直譯器不可以靜靜當成沒事(capsys):
    """⛔ 安裝器最怕的就是「沒有結論也沒有訊息」—— 那正是這次事故的樣子。"""
    assert D.main(["Z:/沒有這支/python.exe"]) == 2
    assert "DEMUCS_LINE_BAD" in capsys.readouterr().out


def test_成功時要印出用的是哪支python_救回來的要標RECOVERED(monkeypatch, tmp_path, capsys):
    fake_py = tmp_path / "python.exe"
    fake_py.write_text("", encoding="utf-8")
    seq = iter([_R(1, "ImportError: 剛裝完還沒穩"), _R(0)])
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: next(seq))
    monkeypatch.setattr(D, "RETRY_PAUSE", 0)
    assert D.main([str(fake_py)]) == 0
    out = capsys.readouterr().out
    assert "DEMUCS_LINE_OK" in out and str(fake_py) in out, \
        "要講清楚是哪一支 —— 跟實際跑分用的必須是同一支"
    assert "DEMUCS_LINE_RECOVERED" in out, "🔴 重試才成功卻給了一個乾淨的綠燈"


def test_安裝器要把RECOVERED當警告而不是靜靜放行():
    """⛔ 救回來≠沒事:這台機器的分軌線是不穩的,正式評分可能掉 26.2% 的權重。"""
    from conftest import REPO
    for name in ("install.ps1", "install.sh"):
        src = (REPO / name).read_text(encoding="utf-8")
        assert "DEMUCS_LINE_RECOVERED" in src, f"🔴 {name} 沒有處理『重試才成功』"


def test_分軌探針要排在base環境檢查之後():
    """🔴 Codex R17-5:PowerShell 只要看到 python.exe 就先跑最壞好幾分鐘的探針,
    但 base venv 都不成立時結論早就註定了 —— sh 是先驗 HAS_ENV 才跑。
    兩邊順序要一致,否則同一種壞環境在兩個平台的耗時與訊息都不同。"""
    from conftest import REPO
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    assert ps1.index('$hasEnv = Test-Import ".venv"') < ps1.index("分軌線檢查.py"), \
        "🔴 install.ps1 在還沒確認 base venv 之前就跑分軌探針"
    assert sh.index('HAS_ENV=') < sh.index("分軌線檢查.py")
    # 呼叫者的環境不可以被改掉(sh 用 `VAR=值 命令` 只作用於 child;PS 要自己存回)
    assert "$oldUtf8Line" in ps1, "🔴 install.ps1 沒有存回呼叫者的 PYTHONUTF8"


def test_等待也要吃預算(monkeypatch):
    """🔴 Codex R18-7 實測:budget=0.05 / pause=0.2 時實際跑了 0.203s ——
    封頂的必須是**牆上時間**,不是「幾次 import」。重試前的等待也要扣預算。"""
    import time as _t
    monkeypatch.setattr(D.subprocess, "run",
                        lambda *a, **k: _R(1, "PermissionError: 被鎖住"))
    slept = []
    monkeypatch.setattr(D.time, "sleep", lambda s: slept.append(s))
    D.probe("py", attempts=3, budget=0.05, pause=5.0, log=QUIET)
    assert all(s <= 0.05 + 1e-6 for s in slept),         f"🔴 等待超過總預算:{slept}(預算只有 0.05s)"
