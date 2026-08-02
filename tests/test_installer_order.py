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
import time
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
    json.dumps({"scoring_contract": "2026-07-25-v1", "pillar_totals": pt,
                "evaluation_id": "a" * 32, "source_file_sha256": "b" * 64,
                "source_audio_pcm_sha256": "c" * 64}, ensure_ascii=False),
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
    stub = _STUB_OK.replace('"scoring_contract": "2026-07-25-v1", ', "")
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


def _ps_engines():
    """這台上**所有**能跑 install.ps1 的 PowerShell。

    🔴 Codex R18-6:舊寫法是 `which("pwsh") or which("powershell")` —— GitHub 的
       Windows runner 兩個都有,於是永遠只跑到 pwsh 7。但這個專案在
       **Windows PowerShell 5.1** 上出過真的相容性 bug(IsFinite 只有 .NET Core 有、
       Out-File 寫 BOM…),那個環境反而從來沒被 CI 守著。兩個都要跑。"""
    out = [shutil.which(n) for n in ("pwsh", "powershell")]
    out = [e for e in out if e]
    return out or [None]


def _git_bash():
    """找一支**能跑 install.sh** 的 bash。

    ⚠️ Windows 上 `which bash` 常常先找到 C:\\Windows\\System32\\bash.exe(WSL),
       它吃不了 Windows 路徑;但這台其實裝了 Git Bash —— 直接跳過等於把
       5 條 sh 契約測試靜靜關掉(Codex R18-6 實測)。順序:
       環境變數 SONG_JURY_TEST_BASH → Git 標準安裝位置 → PATH 上的非 WSL bash。"""
    import os as _os
    env = _os.environ.get("SONG_JURY_TEST_BASH")
    if env and Path(env).exists():
        return env
    if sys.platform == "win32":
        for c in (r"C:\Program Files\Git\bin\bash.exe",
                  r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if Path(c).exists():
                return c
    exe = shutil.which("bash")
    if exe and sys.platform == "win32" and "system32" in exe.lower():
        return None
    return exe


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


# ⭐ 全健康 fixture(Codex R18-6):上面那組故意殘缺,所以 helper=0 也只期待 1 ——
#    那驗不到「一切正常時安裝器必須回 0」。偷偷把 VerifyOk 設成 false 也照樣全綠。
#    這組把每個外部依賴都做成 stub(venv/金鑰/分軌/冒煙/ffmpeg),讓成功路徑真的成立。
_HEALTHY = [(0, 0), (2, 1), (124, 124), (130, 130)]


def _healthy_repo(tmp_path, code):
    d = _stub_repo(tmp_path, code)
    # 金鑰驗證:回 0 = 這台有可用金鑰
    (d / "金鑰驗證.py").write_text("import sys\nprint('KEYPROBE stub')\nsys.exit(0)\n",
                                  encoding="utf-8")
    # 分軌線體檢:回 0 = 分軌線可用
    (d / "分軌線檢查.py").write_text("import sys\nprint('DEMUCS_LINE_OK stub')\nsys.exit(0)\n",
                                    encoding="utf-8")
    # 冒煙測試:寫出合格的 JSON
    (d / "song_scorer.py").write_text(
        "import json, sys\n"
        "out = sys.argv[sys.argv.index('--json') + 1]\n"
        "json.dump({'scores': {'total': 80.0}}, open(out, 'w', encoding='utf-8'))\n"
        "print('smoke stub ok')\n", encoding="utf-8")
    # 其他兩個模型環境 + SongEval
    venv = d / ".venv"
    for name, mods in ((".venv-ml", ("torch", "muq", "audiobox_aesthetics")),
                       (".venv-audition", ("torch", "s3prl", "muq"))):
        tgt = d / name
        shutil.copytree(venv, tgt)
        sp = next(iter(tgt.glob("Lib/site-packages")), None) or \
            next(iter(tgt.glob("lib/python*/site-packages")))
        for m in mods:
            (sp / f"{m}.py").write_text("", encoding="utf-8")
    (d / "SongEval").mkdir(exist_ok=True)
    (d / "SongEval" / "eval.py").write_text("", encoding="utf-8")
    # ffmpeg:放一支假的到 PATH 最前面(安裝器只問「有沒有」)
    fake = d / "fakebin"
    fake.mkdir(exist_ok=True)
    (fake / "ffmpeg.cmd").write_text("@echo ffmpeg stub\r\n", encoding="utf-8", newline="")
    posix = fake / "ffmpeg"
    posix.write_text("#!/bin/sh\necho ffmpeg stub\n", encoding="utf-8", newline="\n")
    posix.chmod(0o755)
    return d, fake


def _healthy_env(fake):
    import os as _os
    return {**_os.environ, "PATH": str(fake) + _os.pathsep + _os.environ.get("PATH", "")}


@pytest.mark.parametrize("code,expect", _HEALTHY)
def test_ps1在全部健康時要把0傳成0(tmp_path, code, expect):
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器")
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        pytest.skip("這台沒有 PowerShell")
    d, fake = _healthy_repo(tmp_path, code)
    r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(d / "install.ps1"),
                        "-CheckOnly", "-NoAutoTools", "-VerifyModels"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d), env=_healthy_env(fake))
    assert r.returncode == expect, \
        f"🔴 全健康 + helper={code} 應該回 {expect},實際 {r.returncode}:\n{r.stdout[-900:]}"


@pytest.mark.parametrize("code,expect", _HEALTHY)
def test_sh在全部健康時要把0傳成0(tmp_path, code, expect):
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash")
    d, fake = _healthy_repo(tmp_path, code)
    r = subprocess.run([bash, str(d / "install.sh"),
                        "--check-only", "--no-auto-tools", "--verify-models"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d), env=_healthy_env(fake))
    assert r.returncode == expect, \
        f"🔴 全健康 + helper={code} 應該回 {expect},實際 {r.returncode}:\n{r.stdout[-900:]}"


@pytest.mark.parametrize("exe", _ps_engines(), ids=lambda e: Path(e).stem if e else "none")
@pytest.mark.parametrize("code,expect", _MATRIX)
def test_ps1把helper的退出碼照契約傳出(tmp_path, code, expect, exe):
    # ⚠️ install.ps1 是**Windows 專用**安裝器:它找的是 .venv\Scripts\python.exe。
    #    CI 的 ubuntu/macOS 也有 pwsh,但那裡的 venv 是 bin/python → 自我檢查
    #    永遠判 base 環境不可用,根本走不到驗證段(第一次推上 CI 就踩到)。
    #    ⛔ 這條在 POSIX 上跳過**不是**放水:那邊的對應契約由 install.sh 那組驗。
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器(venv layout 不同);POSIX 看 install.sh 那組")
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
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash(WSL 的 bash 吃不了 Windows 路徑)")
    d = _stub_repo(tmp_path, code)
    r = subprocess.run([bash, str(d / "install.sh"),
                        "--check-only", "--no-auto-tools", "--verify-models"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d))
    assert "STUB VERIFY" in r.stdout, f"沒走到驗證段:\n{r.stdout[-900:]}"
    assert r.returncode == expect, \
        f"🔴 helper 回 {code},install.sh 應該回 {expect},實際 {r.returncode}"


# ── 進度要**邊跑邊看得到**(Codex R18-1)────────────────────────────
# 🔴 helper 有 flush,但安裝器用 `(… | Out-String)` / `LINE_OUT=$(…)` 把整段收進變數,
#    使用者在執行中什麼都看不到 —— 最壞 15 分鐘像當機。R17-1 那句「不再像當機」
#    在**真安裝器**裡其實沒有成立。
#    ⛔ 只驗「helper 有呼叫 logger」抓不到這件事:log 呼叫了,訊息卻卡在管線裡。
_PROBE_STUB = """import sys, time
print("LIVE_PROGRESS_MARK", flush=True)
time.sleep(6)
print("DEMUCS_LINE_OK stub")
sys.exit(0)
"""


def _wait_for_marker(log_path, proc, mark, limit=20.0):
    """在**子程序還活著**的時候就要看得到 marker;逾時回 False。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if proc.poll() is not None:
            break                      # 已經結束 → 再看一次就知道是不是只有事後才有
        try:
            if mark in log_path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def _stream_case(tmp_path, cmd, exe_ok):
    d = _stub_repo(tmp_path, 0)
    (d / "分軌線檢查.py").write_text(_PROBE_STUB, encoding="utf-8")
    log = tmp_path / "installer.log"
    with log.open("wb") as fh:
        proc = subprocess.Popen(cmd(d), cwd=str(d), stdout=fh,
                                stderr=subprocess.STDOUT)
        try:
            live = _wait_for_marker(log, proc, "LIVE_PROGRESS_MARK")
            proc.wait(timeout=600)
        finally:
            if proc.poll() is None:
                proc.kill()
    after = log.read_text(encoding="utf-8", errors="replace")
    assert "LIVE_PROGRESS_MARK" in after, f"連事後都沒有 helper 的輸出:\n{after[-600:]}"
    assert live, ("🔴 執行中看不到進度,結束後才一次吐出來 —— "
                  "使用者眼中就是當機(安裝器把輸出收進變數了)")


@pytest.mark.parametrize("exe", _ps_engines(), ids=lambda e: Path(e).stem if e else "none")
def test_ps1要即時顯示分軌體檢的進度(tmp_path, exe):
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器")
    if not exe:
        pytest.skip("這台沒有 PowerShell")
    _stream_case(tmp_path,
                 lambda d: [exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(d / "install.ps1"), "-CheckOnly", "-NoAutoTools"],
                 exe)


def test_sh要即時顯示分軌體檢的進度(tmp_path):
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash")
    _stream_case(tmp_path,
                 lambda d: [bash, str(d / "install.sh"), "--check-only", "--no-auto-tools"],
                 bash)


def test_設定打錯不可以被說成缺套件(tmp_path):
    """🔴 Codex R18-3:SONG_JURY_DEMUCS_PROBE_TIMEOUT 填 abc/nan/inf/0/-1 時,
    舊版是未捕捉例外(rc=1),而安裝器把 1 讀成「缺套件」→ 叫人重裝幾 GB。
    設定錯誤有自己的碼(3),訊息也要指向設定。"""
    import os as _os
    for bad in ("abc", "nan", "inf", "0", "-1"):
        r = subprocess.run([sys.executable, str(REPO / "分軌線檢查.py"), sys.executable],
                           cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300,
                           env={**_os.environ, "PYTHONUTF8": "1",
                                "SONG_JURY_DEMUCS_PROBE_TIMEOUT": bad})
        assert r.returncode == 3, f"{bad!r} 應該是設定錯誤(3),拿到 {r.returncode}:{r.stdout}"
        assert "config_error" in r.stdout, f"{bad!r} 沒有標成設定問題:{r.stdout}"
        assert "missing_module" not in r.stdout


def test_安裝器對設定錯誤要給改設定的建議而不是重裝():
    """兩支安裝器都要把 rc=3 跟「缺套件」分開,而且缺套件要看結構化標記,
    不能只憑 rc=1(任何崩潰都是 1)。"""
    for name in ("install.ps1", "install.sh"):
        src = (REPO / name).read_text(encoding="utf-8")
        assert "missing_module" in src, f"🔴 {name} 只憑 rc=1 就說缺套件"
        assert "設定值有問題" in src, f"🔴 {name} 沒有處理設定錯誤(rc=3)"


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


def test_本輪新產物沒有來源身分要被擋下(tmp_path):
    """🔴 Codex R18-2:安裝證據(本輪剛跑出來的報告)必須帶得出來源身分。

    ⚠️ 舊報告可以沒有(相容路徑,比較器會標較弱等級);但**本輪新產物**沒有,
    代表產出端迴歸了 —— 那時九柱照樣 VERIFY_OK,下游卻連「這是哪首歌的評測」
    都證明不了。這條與「缺 scoring_contract 要擋」是同一種要求。"""
    stub = _STUB_OK.replace('"evaluation_id": "a" * 32,', '')
    assert stub != _STUB_OK, "fixture 沒改到 —— 這條會變成假綠"
    _stub_env(tmp_path, stub)
    r = _run_helper(tmp_path)
    assert r.returncode == 1, f"缺身分要被擋:\n{r.stdout}"
    assert "VERIFY_BAD" in r.stdout and "來源身分" in r.stdout


def test_清理沒過時不可以出現成功標記(tmp_path, monkeypatch):
    """🔴 Codex R18-5:舊版在裁判過關當下就印 VERIFY_OK,清理失敗時同一份輸出
    同時有 OK 與 BAD —— 退出碼雖然對,但任何 grep 成功字串的日誌工具都會假綠,
    人讀起來也自相矛盾。成功標記只能在**確認零殘留之後**發布一次。"""
    V = _load_helper()
    monkeypatch.setattr(V, "BASE", tmp_path)
    audio = tmp_path / "demo_mix.wav"
    audio.write_bytes(b"RIFF0000")
    monkeypatch.setattr(V, "run_tree",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(V, "validate", lambda *a, **k: "")
    monkeypatch.setattr(V, "_cleanup", lambda vid, *a, **k: [str(tmp_path / f"{vid}.wav")])
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = V.run(audio, timeout=5)
    out = buf.getvalue()
    assert rc == 1
    assert "VERIFY_OK" not in out, f"🔴 失敗路徑還是印了成功標記:\n{out}"
    assert "VERIFY_BAD" in out


def test_成功時的成功標記只出現一次(tmp_path, monkeypatch):
    V = _load_helper()
    monkeypatch.setattr(V, "BASE", tmp_path)
    audio = tmp_path / "demo_mix.wav"
    audio.write_bytes(b"RIFF0000")
    monkeypatch.setattr(V, "run_tree",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(V, "validate", lambda *a, **k: "")
    monkeypatch.setattr(V, "_cleanup", lambda vid, *a, **k: [])
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = V.run(audio, timeout=5)
    out = buf.getvalue()
    assert rc == 0 and out.count("VERIFY_OK") == 1, f"rc={rc}\n{out}"
    assert "零殘留" in out, "成功標記要包含『零殘留』這個保證"
