# -*- coding: utf-8 -*-
"""-VerifyModels 的流程契約:裁判看得到報告、清理在最後、中斷有自己的退出碼。

🔴 Codex R14:清理排在裁判之前 → 成功路徑必定假陰性。
🔴 Codex R16-9/10:整段寫在 shell 裡時,Windows 真 Ctrl+C 不可靠進入 finally
   (實測掛住 15 秒、finally 沒跑、verify_* 殘留);POSIX 的 trap 又把中斷吞掉
   當成一般失敗。→ R16 起整段收進 完整驗證.py,shell 只看退出碼。
   這支改成驗那個 helper 的真實行為(stub 評審團,不跑真模型)。
"""
import os
import shutil
import subprocess
import types
from pathlib import Path
import sys

import pytest

from conftest import REPO

_STUB_OK = """import sys, json, pathlib
p = pathlib.Path(sys.argv[1])
P8 = ("人聲","和聲","結構編曲","聲學","旋律記憶","真實風格","整體","律動")
pt = {"完整評測": True, "缺柱": [], "缺柱權重合計": 0.0, "曲側合成": 70.0,
      "柱分": {k: {"score": 70.0, "items": {"x": 70.0}, "missing": []} for k in P8},
      "曲側含柱": list(P8)}
p.with_name(p.stem + "_評審團.json").write_text(
    json.dumps({"scoring_contract": "2026-07-25-v1", "pillar_totals": pt}, ensure_ascii=False),
    encoding="utf-8")
(p.parent / (p.stem + "_評分.json")).write_text("mid", encoding="utf-8")
sys.exit(0)
"""


def _stub_env(tmp_path, jury_src):
    """把 helper 需要的東西擺進 tmp:stub 評審團 + demo 音檔 + 共用模組。"""
    (tmp_path / "評審團.py").write_text(jury_src, encoding="utf-8")
    (tmp_path / "demo_mix.wav").write_bytes(b"RIFF0000")
    for mod in ("子程序.py", "驗證報告.py", "完整驗證.py"):
        shutil.copy(REPO / mod, tmp_path / mod)
    return tmp_path


def _run_helper(tmp_path, extra_env=None, timeout=90):
    env = {**os.environ, "PYTHONUTF8": "1", "SONG_JURY_VERIFY_TIMEOUT": "60"}
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "完整驗證.py"], cwd=str(tmp_path),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def test_成功路徑_裁判看得到報告且收工後全清乾淨(tmp_path):
    """🔴 R14 的順序迴歸:清理若排在裁判之前,成功路徑必定 VERIFY_BAD。"""
    _stub_env(tmp_path, _STUB_OK)
    r = _run_helper(tmp_path)
    assert r.returncode == 0, f"成功路徑應該 exit 0:\n{r.stdout}\n{r.stderr}"
    assert "VERIFY_OK" in r.stdout, r.stdout
    left = [p.name for p in tmp_path.glob("verify_*")]
    assert left == [], f"🔴 沒清乾淨(含中途的 _評分.json):{left}"


def test_缺柱要回2且不留殘檔(tmp_path):
    stub = _STUB_OK.replace('"完整評測": True, "缺柱": []',
                            '"完整評測": False, "缺柱": ["律動"]') \
                   .replace("sys.exit(0)", "sys.exit(2)")
    _stub_env(tmp_path, stub)
    r = _run_helper(tmp_path)
    assert r.returncode == 2, f"缺柱要回 2:\n{r.stdout}"
    assert [p.name for p in tmp_path.glob("verify_*")] == []


def test_jury回0但報告缺契約要被裁判擋下(tmp_path):
    """🔴 Codex R16-5:安裝證據要求版本證據 —— 舊格式相容不可以套在本輪新產物上,
    否則產出端一旦迴歸成不寫契約,VerifyModels 照樣印 VERIFY_OK。"""
    stub = _STUB_OK.replace('{"scoring_contract": "2026-07-25-v1", "pillar_totals": pt}',
                            '{"pillar_totals": pt}')
    _stub_env(tmp_path, stub)
    r = _run_helper(tmp_path)
    assert r.returncode == 1, f"缺契約要被擋:\n{r.stdout}"
    assert "VERIFY_BAD" in r.stdout and "scoring_contract" in r.stdout


def test_逾時要回124且殺乾淨(tmp_path):
    _stub_env(tmp_path, "import time\ntime.sleep(120)\n")
    r = _run_helper(tmp_path, {"SONG_JURY_VERIFY_TIMEOUT": "3"})
    assert r.returncode == 124, f"逾時要回 124:\n{r.stdout}\n{r.stderr}"
    assert [p.name for p in tmp_path.glob("verify_*")] == []


def test_中斷要回130且清乾淨(tmp_path):
    """🔴 Codex R16-9/10:中斷必須跟「失敗」分開(130),而且清理一定要跑。
    ⚠️ 真 console 事件在 CI 上不可移植 —— 這裡注入 KeyboardInterrupt 驗同一條
       語意契約(退出碼 + 清理),那正是把流程搬進 python 才拿得到的保證。"""
    _stub_env(tmp_path, _STUB_OK)
    (tmp_path / "probe.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "import 完整驗證 as H\n"
        "def boom(*a, **k):\n"
        "    raise KeyboardInterrupt()\n"
        "H.run_tree = boom\n"
        "sys.exit(H.main([]))\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "probe.py"], cwd=str(tmp_path),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60,
                       env={**os.environ, "PYTHONUTF8": "1"})
    assert r.returncode == 130, f"中斷要回 130(拿到 {r.returncode}):\n{r.stdout}\n{r.stderr}"
    assert [p.name for p in tmp_path.glob("verify_*")] == [], "中斷也要清乾淨"


def test_子環境要清掉跳關變數且不動呼叫者(tmp_path):
    """⛔ 呼叫 shell 若殘留 SONG_JURY_SKIP_GEMINI,驗證就不是真的全模型跑。
    helper 用 subprocess env 拿掉它們,而且不改自己的 os.environ。"""
    stub = ("import os, sys, pathlib\n"
            "pathlib.Path('SEEN.txt').write_text(\n"
            "    repr(sorted(k for k in os.environ if k.startswith('SONG_JURY_'))),\n"
            "    encoding='utf-8')\n"
            "sys.exit(1)\n")
    _stub_env(tmp_path, stub)
    r = _run_helper(tmp_path, {"SONG_JURY_SKIP_GEMINI": "1",
                               "SONG_JURY_TRUST_LEGACY_STEMS": "1"})
    seen = (tmp_path / "SEEN.txt").read_text(encoding="utf-8")
    assert "SKIP_GEMINI" not in seen and "TRUST_LEGACY" not in seen, \
        f"🔴 跳關變數被帶進評測子程序:{seen}"
    assert r.returncode == 1


# ── 安裝器 ↔ helper 的退出碼契約(Codex R17-2)────────────────────────
# 🔴 實測抓到:helper 回 130 時 install.sh 傳 130、install.ps1 卻被一般失敗洗成 1;
#    124 兩邊都折成 1。於是 Windows 的自動化分不出「逾時 / 使用者取消 / 真的裝壞」,
#    Linux 分得出來 —— 同一個上層工具要為兩個平台寫兩套邏輯。
#    ⛔ 只 grep「檔案裡有沒有 130」抓不到這件事:字串在,行為不在。
#
# ⚠️ 這裡的環境是**故意不完整**的(只有安裝器 + stub helper),所以一般碼都會落到 1。
#    這正是要測的重點:124/130 必須**贏過**一般失敗傳到最外層;0 能不能回 0 由
#    真機的 -CheckOnly 驗(那需要九柱真的裝好,不是這支的工作)。
_MATRIX = [(0, 1), (1, 1), (2, 1), (124, 124), (130, 130)]


def _stub_repo(tmp_path, code):
    """一個只有安裝器 + stub helper 的最小工作目錄,外加一個**真的** venv。"""
    for name in ("install.ps1", "install.sh"):
        shutil.copy(REPO / name, tmp_path / name)
    (tmp_path / "完整驗證.py").write_text(
        f"import sys\nprint('STUB VERIFY')\nsys.exit({code})\n", encoding="utf-8")
    (tmp_path / "demo_mix.wav").write_bytes(b"RIFF0000")
    # ⛔ 不能只複製一支 python.exe:沒有 pyvenv.cfg 它根本起不來(自己踩到)。
    #    用真的 venv,再塞空模組讓 base 環境的 import 檢查過關。
    venv = tmp_path / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   check=True, capture_output=True, timeout=180)
    sp = next(iter(venv.glob("Lib/site-packages")), None) or \
        next(iter(venv.glob("lib/python*/site-packages")))
    for mod in ("librosa", "numpy", "soundfile", "pyloudnorm", "reportlab"):
        (sp / f"{mod}.py").write_text("", encoding="utf-8")
    # Git Bash 走的是 .venv/bin/python —— Windows 的 venv 沒有,補一個轉手的 shell wrapper
    posix = venv / "bin" / "python"
    if not posix.exists():
        win = venv / "Scripts" / "python.exe"
        posix.parent.mkdir(parents=True, exist_ok=True)
        posix.write_text(f'#!/bin/sh\nexec "{str(win).replace(chr(92), "/")}" "$@"\n',
                         encoding="utf-8", newline="\n")
        posix.chmod(0o755)
    return tmp_path


@pytest.mark.parametrize("code,expect", _MATRIX)
def test_ps1把helper的退出碼照契約傳出(tmp_path, code, expect):
    # ⚠️ install.ps1 是**Windows 專用**安裝器:它找的是 .venv\Scripts\python.exe。
    #    CI 的 ubuntu/macOS 也有 pwsh,但那裡的 venv 是 bin/python → 自我檢查
    #    永遠判 base 環境不可用,根本走不到驗證段(第一次推上 CI 就踩到)。
    #    ⛔ 這條在 POSIX 上跳過**不是**放水:那邊的對應契約由 install.sh 那組驗。
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器(venv layout 不同);POSIX 看 install.sh 那組")
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        pytest.skip("這台沒有 PowerShell")
    d = _stub_repo(tmp_path, code)
    r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(d / "install.ps1"),
                        "-CheckOnly", "-NoAutoTools", "-VerifyModels"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d))
    assert "STUB VERIFY" in r.stdout, f"沒走到驗證段:\n{r.stdout[-900:]}"
    assert r.returncode == expect, \
        f"🔴 helper 回 {code},install.ps1 應該回 {expect},實際 {r.returncode}"


@pytest.mark.parametrize("code,expect", _MATRIX)
def test_sh把helper的退出碼照契約傳出(tmp_path, code, expect):
    bash = shutil.which("bash")
    if not bash or (sys.platform == "win32" and "system32" in bash.lower()):
        pytest.skip("這台沒有 Git Bash(WSL 的 bash 吃不了 Windows 路徑)")
    d = _stub_repo(tmp_path, code)
    r = subprocess.run([bash, str(d / "install.sh"),
                        "--check-only", "--no-auto-tools", "--verify-models"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d))
    assert "STUB VERIFY" in r.stdout, f"沒走到驗證段:\n{r.stdout[-900:]}"
    assert r.returncode == expect, \
        f"🔴 helper 回 {code},install.sh 應該回 {expect},實際 {r.returncode}"


def test_兩支安裝器對同一個helper碼要給同一個答案():
    """契約是**跨平台同一份**:上層工具不該為了作業系統寫兩套重試/告警策略。"""
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    for code in ("124", "130"):
        assert f"exit {code}" in ps1, f"🔴 install.ps1 沒有原樣傳出 {code}"
        assert f"exit {code}" in sh, f"🔴 install.sh 沒有原樣傳出 {code}"
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for code in ("124", "130"):
        assert f"| **{code}** |" in readme, f"🔴 README 的退出碼表沒有列 {code}"


def test_安裝器只呼叫helper不再自己編排順序():
    """R16 起 shell 不該再有自己的 jury→validator→cleanup 邏輯 ——
    那正是 Windows Ctrl+C 不可靠的來源。"""
    for name in ("install.ps1", "install.sh"):
        src = (REPO / name).read_text(encoding="utf-8")
        assert "完整驗證.py" in src, f"{name} 沒有呼叫共用 helper"
        assert "驗證報告.py" not in src, \
            f"🔴 {name} 又自己叫裁判了 —— 順序與中斷處理要留在 helper 裡"
        for code in ("124", "130"):
            assert code in src, f"{name} 沒有分開處理退出碼 {code}"


# ── 清理要誠實(Codex R17-6)──────────────────────────────────────────
# 🔴 舊版 `except Exception: pass` + `rmtree(ignore_errors=True)`:刪不掉時
#    沒有任何人知道,helper 照樣回 0/130 並印「已中止並清理」。防毒、索引器、
#    還沒放手的 child handle 都會讓刪除失敗,音檔與分軌快取默默留在磁碟上。
def _load_helper():
    import importlib.util
    spec = importlib.util.spec_from_file_location("完整驗證_t", REPO / "完整驗證.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_清不掉的檔案要被回報而不是靜靜留著(tmp_path, monkeypatch):
    V = _load_helper()
    monkeypatch.setattr(V, "BASE", tmp_path)
    victim = tmp_path / "verify_abc.wav"
    victim.write_bytes(b"x")
    real = Path.unlink

    def boom(self, *a, **k):
        if self.name.startswith("verify_"):
            raise PermissionError("被佔用")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom)
    left = V._cleanup("verify_abc", retries=2, pause=0)
    assert [Path(x).name for x in left] == ["verify_abc.wav"], \
        "🔴 清不掉卻回報乾淨 —— 呼叫者無從知道磁碟上還有東西"


def test_清乾淨時回空清單(tmp_path, monkeypatch):
    V = _load_helper()
    monkeypatch.setattr(V, "BASE", tmp_path)
    (tmp_path / "verify_abc.wav").write_bytes(b"x")
    (tmp_path / "verify_abc_評分.json").write_text("{}", encoding="utf-8")
    assert V._cleanup("verify_abc", retries=1, pause=0) == []
    assert list(tmp_path.glob("verify_*")) == []


def test_九柱都過但清不乾淨要降級成失敗(tmp_path, monkeypatch, capsys):
    """⛔「零殘留」是這條驗證對外宣稱的一部分 —— 宣稱做不到就不能給綠燈。"""
    V = _load_helper()
    monkeypatch.setattr(V, "BASE", tmp_path)
    audio = tmp_path / "demo_mix.wav"
    audio.write_bytes(b"RIFF0000")
    monkeypatch.setattr(V, "run_tree",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(V, "validate", lambda *a, **k: "")
    monkeypatch.setattr(V, "_cleanup", lambda vid, *a, **k: [str(tmp_path / f"{vid}.wav")])
    rc = V.run(audio, timeout=5)
    out = capsys.readouterr().out
    assert rc == 1, f"🔴 清不乾淨卻回 {rc}"
    assert "VERIFY_BAD" in out and "沒清乾淨" in out


def test_中斷時也要講出殘留而不是一律說已清理(tmp_path, monkeypatch, capsys):
    V = _load_helper()
    monkeypatch.setattr(V, "BASE", tmp_path)
    audio = tmp_path / "demo_mix.wav"
    audio.write_bytes(b"RIFF0000")

    def interrupted(*a, **k):
        raise KeyboardInterrupt()

    monkeypatch.setattr(V, "run_tree", interrupted)
    monkeypatch.setattr(V, "_cleanup", lambda vid, *a, **k: [str(tmp_path / f"{vid}.wav")])
    rc = V.run(audio, timeout=5)
    err = capsys.readouterr().err
    assert rc == 130, "中斷仍然是 130(那是使用者的決定,不是失敗)"
    assert "清理沒完全成功" in err, "🔴 還在說『已清理』"
