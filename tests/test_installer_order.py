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
                "source_audio_pcm_sha256": "c" * 64,
                "source_audio_pcm_contract":
                    "pcm-v5/native-rate/canonical-speakers/native-sample-fmt"},
               ensure_ascii=False),
    encoding="utf-8")
(p.parent / (p.stem + "_評分.json")).write_text("mid", encoding="utf-8")
sys.exit(0)
"""


def _stub_env(tmp_path, jury_src):
    """把 helper 需要的東西擺進 tmp:stub 評審團 + demo 音檔 + 共用模組。"""
    (tmp_path / "評審團.py").write_text(jury_src, encoding="utf-8")
    (tmp_path / "demo_mix.wav").write_bytes(b"RIFF0000")
    # ⚠️ helper 的相依會長(R19-5 加了 設定讀取)—— 少複製一個就會變成
    #    ModuleNotFoundError,而那看起來像「helper 壞了」
    for mod in ("子程序.py", "驗證報告.py", "完整驗證.py", "設定讀取.py"):
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
    for name in ("install.ps1", "install.sh", "狀態驗證.py"):
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


@pytest.mark.parametrize("exe", _ps_engines(), ids=lambda e: Path(e).stem if e else "none")
@pytest.mark.parametrize("code,expect", _HEALTHY)
def test_ps1在全部健康時要把0傳成0(tmp_path, code, expect, exe):
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器")
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


# ── 非 ASCII 訊息不可以在路上壞掉(Codex R19-3)──────────────────────
# 🔴 PYTHONUTF8=1 只管 python 端;PowerShell 仍照 console code page 解碼
#    (繁中 Windows 常見 cp950)→ 中文/日文/韓文/emoji 會被解成亂碼,
#    而且是**捕捉到的字串本身**就壞了(不是畫面字型問題)。
#    ⛔ 舊測試的 marker 是純 ASCII,所以中文全爛也照樣綠。
_NONASCII_STUB = """import sys, time
print("LIVE_中文_日本語_한국어_🙂", flush=True)
time.sleep(4)
print("DEMUCS_LINE_OK stub")
sys.exit(0)
"""


@pytest.mark.parametrize("exe", _ps_engines(), ids=lambda e: Path(e).stem if e else "none")
def test_ps1的非ASCII進度不可以變亂碼(tmp_path, exe):
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器")
    if not exe:
        pytest.skip("這台沒有 PowerShell")
    d = _stub_repo(tmp_path, 0)
    (d / "分軌線檢查.py").write_text(_NONASCII_STUB, encoding="utf-8")
    log = tmp_path / "installer.log"
    # ⚠️ 先把 console code page 切成 950(繁中 Windows 的預設),重現真實環境;
    #    切不過去就跳過 —— 那台機器沒有這個 code page,不是產品的問題。
    script = ("chcp 950 > $null; "
              f"& '{exe}' -NoProfile -ExecutionPolicy Bypass -File '{d / 'install.ps1'}' "
              "-CheckOnly -NoAutoTools")
    with log.open("wb") as fh:
        proc = subprocess.Popen([exe, "-NoProfile", "-Command", script],
                                cwd=str(d), stdout=fh, stderr=subprocess.STDOUT)
        try:
            live = _wait_for_marker(log, proc, "LIVE_中文_日本語_한국어_🙂", limit=25.0)
            proc.wait(timeout=600)
        finally:
            if proc.poll() is None:
                proc.kill()
    after = log.read_text(encoding="utf-8", errors="replace")
    assert "LIVE_中文_日本語_한국어_🙂" in after, \
        f"🔴 非 ASCII 訊息在路上壞掉了(console 編碼沒切/沒還原):\n{after[-600:]}"
    assert live, "🔴 非 ASCII 也要即時看得到,不是最後才吐"


def test_安裝器不可以再去解析人類訊息():
    """🔴 Codex R19-3 的根治:PS 5.1 在 cp950 下把子程序的 UTF-8 解成 big5,
    捕捉到的字串本身就壞掉(韓文/emoji 變 ?);2>&1 還會把 stderr 包成 ErrorRecord,
    Out-String 再灌進整段 PowerShell 診斷。

    ⛔ 所以契約改成:**子程序的輸出直接寫終端**(即時、不經手、不會壞),
    判斷改讀 helper 寫的 UTF-8 JSON。安裝器裡不該再有管線捕捉與中文 grep。"""
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    for name, src in (("install.ps1", ps1), ("install.sh", sh)):
        assert "--status-json" in src, f"🔴 {name} 沒有用機器可讀的狀態檔"
        assert "DEMUCS_LINE_RECOVERED" not in src, \
            f"🔴 {name} 還在 grep 人類訊息找 RECOVERED —— 換個 code page 就會壞"
    # ⚠️ 只看**分軌線那一段**:冒煙測試那邊的 Out-String 是拿失敗尾段給人看,
    #    不是判斷契約(範圍寫太寬會擋住無關的正常寫法 —— 自己踩到)。
    # ⚠️ 還要把註解拿掉再看:那段註解本身就在解釋「為什麼不用 Out-String」,
    #    直接 grep 會命中自己的說明(這個坑踩過不只一次)。
    head = ps1.index("# ── 分軌線(結構編曲柱")
    seg = "\n".join(ln for ln in ps1[head:ps1.index("$hasMl", head)].splitlines()
                    if not ln.lstrip().startswith("#"))
    assert "Tee-Object" not in seg and "Out-String" not in seg, \
        "🔴 install.ps1 又把分軌體檢的輸出接進管線了(那正是編碼壞掉的來源)"
    assert "LINE_OUT=$(" not in sh, \
        "🔴 install.sh 又用 command substitution 把輸出收住了"


def _kind_stub(kind, rc):
    """一支只寫狀態檔的假 helper:退出碼與 kind 可以各自控制。"""
    return ("import json, sys\n"
            "a = sys.argv[1:]\n"
            "p = a[a.index('--status-json') + 1] if '--status-json' in a else None\n"
            "if p:\n"
            f"    json.dump({{'ok': False, 'kind': {kind!r}, 'rc': {rc}, 'why': 'stub'}},\n"
            "              open(p, 'w', encoding='utf-8'))\n"
            f"print('stub kind={kind}')\n"
            f"sys.exit({rc})\n")


@pytest.mark.parametrize("exe", _ps_engines(), ids=lambda e: Path(e).stem if e else "none")
# ⚠️ 期望字串要用**標題**,不能用會出現在別條說明裡的詞:
#    「分軌線不可用」那條的說明就寫著「⛔ 不是缺套件 ——」,拿「缺套件」當關鍵字
#    會在退化成通用訊息時照樣命中(變異驗證抓到我這個裝飾品)。
@pytest.mark.parametrize("kind,rc,expect", [
    ("missing_module", 1, "分軌環境缺套件"),
    ("import_error", 1, "分軌線不可用"),
    ("config_error", 3, "設定值有問題"),
    ("internal_error", 4, "自己出錯了"),
])
def test_ps1要照狀態檔的種類給建議(tmp_path, exe, kind, rc, expect):
    """🔴 Codex R19-3/R19-4:判斷若靠 grep 人類訊息,換個 code page 就判錯;
    而 config 與 internal 共用一個碼時,使用者會被導去改沒問題的環境變數。
    ⛔ 所以「rc + 狀態檔的 kind」兩者都要用上,而且訊息要各自不同。"""
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器")
    if not exe:
        pytest.skip("這台沒有 PowerShell")
    d = _stub_repo(tmp_path, 0)
    (d / "分軌線檢查.py").write_text(_kind_stub(kind, rc), encoding="utf-8")
    # ⚠️ 這條要讀**安裝器自己印的中文**,所以得叫 PowerShell 用 UTF-8 寫 stdout ——
    #    否則在繁中 Windows 上它會寫 cp950,我們這端解成 UTF-8 就變亂碼,
    #    測試會因為「解碼」而紅,跟產品行為無關(自己踩到)。
    script = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
              f"& '{d / 'install.ps1'}' -CheckOnly -NoAutoTools")
    r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d))
    assert expect in r.stdout, f"🔴 kind={kind} 應該給「{expect}」的建議:\n{r.stdout[-900:]}"


def _tamper_stub(json_kind, json_rc, real_rc):
    """狀態檔說一套、實際退出碼另一套 —— 模擬殘留檔或被改過的檔。"""
    return ("import json, sys\n"
            "a = sys.argv[1:]\n"
            "p = a[a.index('--status-json') + 1] if '--status-json' in a else None\n"
            "if p:\n"
            f"    json.dump({{'ok': {json_rc == 0}, 'kind': {json_kind!r}, 'rc': {json_rc},\n"
            "               'why': 'stub', 'recovered': True},\n"
            "              open(p, 'w', encoding='utf-8'))\n"
            f"print('tamper stub rc={real_rc}')\n"
            f"sys.exit({real_rc})\n")


@pytest.mark.parametrize("exe", _ps_engines(), ids=lambda e: Path(e).stem if e else "none")
def test_ps1不可以採信與實際結果矛盾的狀態檔(tmp_path, exe):
    """🔴 Codex R20-P2-1:狀態檔寫 kind=config_error 但實際 exit 4 時,
    舊版照樣顯示「設定值有問題」—— 使用者被導去改一個根本沒問題的環境變數。
    殘留檔、被改過的檔、半份 JSON 都會造成這種矛盾。
    ⛔ 規則:狀態檔只是**診斷補充**,與實際 rc 不一致就整份忽略;
       成功與否**永遠只看實際 rc**。"""
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器")
    if not exe:
        pytest.skip("這台沒有 PowerShell")
    d = _stub_repo(tmp_path, 0)
    (d / "分軌線檢查.py").write_text(_tamper_stub("config_error", 3, 4), encoding="utf-8")
    script = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
              f"& '{d / 'install.ps1'}' -CheckOnly -NoAutoTools")
    r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d))
    assert "不可採信" in r.stdout, f"🔴 沒有察覺狀態檔與實際結果矛盾:\n{r.stdout[-900:]}"
    assert "設定值有問題" not in r.stdout, "🔴 採信了矛盾狀態檔的 kind"
    assert "自己出錯了" in r.stdout, "應該照實際 rc=4 給 internal 的建議"


def test_sh不可以採信與實際結果矛盾的狀態檔(tmp_path):
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash")
    d = _stub_repo(tmp_path, 0)
    (d / "分軌線檢查.py").write_text(_tamper_stub("config_error", 3, 4), encoding="utf-8")
    r = subprocess.run([bash, str(d / "install.sh"), "--check-only", "--no-auto-tools"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d))
    assert "不可採信" in r.stdout, f"🔴 沒有察覺矛盾:\n{r.stdout[-900:]}"
    assert "自己出錯了" in r.stdout


def test_狀態檔要原子寫入且用完就清(tmp_path):
    """半份 JSON(中斷/競速)不可以被讀成有效狀態;檔案也不該留在磁碟上。"""
    helper = (REPO / "分軌線檢查.py").read_text(encoding="utf-8")
    assert "os.replace(tmp, p)" in helper, "🔴 狀態檔不是原子寫入"
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "GetRandomFileName" in ps1, "🔴 PS 用可預測的固定檔名(殘留會被誤用)"
    # ⛔ 這裡**故意不**用 grep 驗 PowerShell(Codex R22-P2-3):
    #    「Remove-Item 與 finally 都出現在同一個檔案裡」證明不了它們在同一塊 ——
    #    產品當時正是反例(清理在 try 之外 20 行)。結構由下面兩條真的檢查:
    #    test_ps1的狀態檔清理必須在finally區塊裡(AST)、
    #    test_ps1中途被中斷時狀態檔不可以留在磁碟上(真的 Stop 一個執行中的 pipeline)。
    assert "Remove-Item -LiteralPath $statusFile" in ps1, "🔴 PS 沒有清狀態檔"
    # ⛔ sh 這邊同樣不用 grep 驗行為(R24-P2-1 起清理統一由 cleanup_all 負責):
    #    正常 / INT / TERM 三條路都有真跑的測試掃「TMPDIR 必須是空的」——
    #    這裡只確認那個統一入口還在,行為由那三條守。
    assert "cleanup_all()" in sh and "trap 'cleanup_all' EXIT" in sh,         "🔴 sh 沒有全域清理入口(cleanup_all)"


def test_helper的未預期例外一律收斂成4(tmp_path):
    """🔴 Codex R20-P2-2:probe() 回 None 之後讀 res.ok 會 AttributeError → rc=1,
    而 1 在安裝器眼裡是「缺套件」。任何未預期例外都要變成 internal_error/4。"""
    probe = tmp_path / "boom.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, r'{REPO}')\n"
        "import 分軌線檢查 as D\n"
        "D.probe = lambda *a, **k: None\n"
        # ⚠️ 這條打的是 **main 自己那層**的收斂;bootstrap 那層由
        #    test_import階段就爆掉… 負責。兩層分開測才不會互相掩護(變異驗證抓到)。
        "sys.exit(D.main([sys.executable]))\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300,
                       env={**os.environ, "PYTHONUTF8": "1"})
    assert r.returncode == 4, f"要收斂成 4(拿到 {r.returncode}):{r.stdout}{r.stderr}"
    assert "internal_error" in r.stdout


def test_import階段就爆掉也要收斂成4並寫得出狀態檔(tmp_path):
    """⛔ 連 main 都進不去的情況:舊版是裸 traceback rc=1(=「缺套件」)。"""
    for mod in ("分軌線檢查.py", "設定讀取.py", "狀態驗證.py"):
        shutil.copy(REPO / mod, tmp_path / mod)
    (tmp_path / "評審團.py").write_text("raise RuntimeError('import 就炸')\n",
                                        encoding="utf-8")
    st = tmp_path / "st.json"
    r = subprocess.run([sys.executable, "分軌線檢查.py", "--status-json", str(st),
                        sys.executable],
                       cwd=str(tmp_path), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300,
                       env={**os.environ, "PYTHONUTF8": "1"})
    assert r.returncode == 4, f"要收斂成 4(拿到 {r.returncode})"
    assert st.exists(), "🔴 連狀態檔都沒寫 —— 安裝器只能瞎猜"
    import json as _json
    assert _json.loads(st.read_text(encoding="utf-8"))["kind"] == "internal_error"


@pytest.mark.parametrize("bad", ["nan", "inf", "0", "-5", "abc"])
def test_完整驗證的CLI_timeout也要驗(bad):
    """🔴 Codex R20-P2-3:`--timeout nan` 以前一路傳到 subprocess,訊息還印「逾時 nans」。"""
    r = subprocess.run([sys.executable, str(REPO / "完整驗證.py"), "--timeout", bad,
                        "--audio", str(REPO / "demo_mix.wav")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300, cwd=str(REPO),
                       env={**os.environ, "PYTHONUTF8": "1"})
    assert r.returncode != 0, f"{bad!r} 應該被擋下"
    assert "實跑 評審團" not in r.stdout, f"🔴 {bad!r} 竟然開始跑評測了"


def test_網頁版的timeout不可以被截成0():
    """🔴 Codex R20-P2-3:0.5 是合法的正數,int() 會截成 0 → 又變回非正逾時。"""
    app = (REPO / "app.py").read_text(encoding="utf-8")
    assert "int(positive_finite" not in app, "🔴 又直接 int() 了"
    assert "max(1, round(positive_finite" in app


def test_安裝器要分開設定錯誤與工具自己出錯():
    """🔴 Codex R19-4:config_error 與 internal_error 以前共用 rc=3,
    兩支安裝器一律說「設定值有問題」→ 把使用者導去改一個根本沒問題的環境變數。"""
    for name in ("install.ps1", "install.sh"):
        src = (REPO / name).read_text(encoding="utf-8")
        assert "internal_error" in src or "-eq 4" in src or '= 4 ' in src, \
            f"🔴 {name} 沒有分出 internal_error"
        assert "自己出錯了" in src, f"🔴 {name} 沒有給 internal 專屬訊息"


def test_helper自己出錯要用專屬退出碼(tmp_path):
    """rc=3 只留給設定錯誤;工具自己爆掉是 rc=4 —— 兩者的建議完全不同。"""
    probe = tmp_path / "boom.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, r'{REPO}')\n"
        "import 分軌線檢查 as D\n"
        "D.probe = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))\n"
        "sys.exit(D.main([sys.executable]))\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300,
                       env={**os.environ, "PYTHONUTF8": "1"})
    assert r.returncode == 4, f"internal 要回 4(拿到 {r.returncode}):{r.stdout}"
    assert "internal_error" in r.stdout and "config_error" not in r.stdout


def test_完整驗證的timeout也要走共用設定解析(tmp_path):
    """🔴 Codex R19-5:設定讀取自稱「唯一入口」,但 完整驗證 仍直接 float(env) ——
    abc 會變成裸 traceback rc=1,而 1 在安裝器眼裡是別的意思。"""
    r = subprocess.run([sys.executable, str(REPO / "完整驗證.py"),
                        "--audio", str(REPO / "demo_mix.wav")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300, cwd=str(REPO),
                       env={**os.environ, "PYTHONUTF8": "1",
                            "SONG_JURY_VERIFY_TIMEOUT": "abc"})
    assert r.returncode == 3, f"設定錯誤要回 3(拿到 {r.returncode}):{r.stdout}{r.stderr}"
    assert "設定值有問題" in r.stdout
    assert "Traceback" not in r.stderr, "不可以只丟裸 traceback"


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


def test_單檔驗證不可以把舊報告說成本輪新產物(tmp_path):
    """🔴 Codex R21-P2-4:pcm-v2 的舊報告、甚至完全沒有身分的舊格式,
    單檔 CLI 一律印「VERIFY_OK 九柱完整、格式合格、本輪新產物」——
    相容可讀 ≠ 本輪新產物的證據。只有三個嚴格條件都要求過才可以那樣講。"""
    import json as _json
    P8 = ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")
    pt = {"完整評測": True, "缺柱": [], "缺柱權重合計": 0.0, "曲側合成": 70.0,
          "柱分": {k: {"score": 70.0, "items": {"x": 70.0}, "missing": []} for k in P8},
          "曲側含柱": list(P8)}
    old = tmp_path / "舊_評審團.json"
    old.write_text(_json.dumps({"scoring_contract": "2026-07-25-v1", "pillar_totals": pt,
                                "evaluation_id": "a" * 32, "source_file_sha256": "b" * 64},
                               ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(REPO / "驗證報告.py"), str(old)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300,
                       env={**os.environ, "PYTHONUTF8": "1"})
    assert r.returncode == 0, r.stdout
    # ⚠️ 只看**標籤**,不要 grep 「本輪新產物」四個字 —— 提示句裡也有它(自己踩到)
    assert not r.stdout.startswith("VERIFY_OK 九柱"),         f"🔴 舊報告被說成本輪新產物:{r.stdout!r}"
    assert "VERIFY_OK_LEGACY" in r.stdout and "身分證據" in r.stdout


def test_每一個本地模組的import爆掉都要收斂成4(tmp_path):
    """🔴 Codex R21-P2-3:上一版只把 評審團 的 import 包進保護傘,
    設定讀取 仍在傘外 —— 它爆掉照樣裸 traceback rc=1(=安裝器眼中的「缺套件」)。
    ⛔ 這條逐一驗每個本地相依,不是只驗其中一個。"""
    import json as _json
    for victim in ("評審團.py", "設定讀取.py", "狀態驗證.py"):
        d = tmp_path / victim.replace(".py", "")
        d.mkdir()
        for mod in ("分軌線檢查.py", "評審團.py", "設定讀取.py", "狀態驗證.py"):
            shutil.copy(REPO / mod, d / mod)
        (d / victim).write_text(f"raise RuntimeError('{victim} import 就炸')\n",
                                encoding="utf-8")
        st = d / "st.json"
        # ⚠️ **不帶 python 參數**跑 —— 那才是安裝器真正的呼叫形式。
        #    帶參數時,壞掉的 import 會晚一點才在別處炸出來(照樣 4),
        #    於是「main 開頭那道 raise」拿掉也測不出來(變異驗證抓到)。
        r = subprocess.run([sys.executable, "分軌線檢查.py", "--status-json", str(st)],
                           cwd=str(d), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300,
                           env={**os.environ, "PYTHONUTF8": "1"})
        assert r.returncode == 4, f"🔴 {victim} 爆掉時回 {r.returncode}(要 4):{r.stderr[-300:]}"
        assert st.exists(), f"🔴 {victim} 爆掉時連狀態檔都沒寫"
        st_data = _json.loads(st.read_text(encoding="utf-8"))
        assert st_data["kind"] == "internal_error"
        # ⛔ 回報的原因要是**真正的 import 錯誤**,不是下游的符號錯誤 ——
        #    使用者拿到 NameError 只會更難查(變異驗證抓到:少了 raise 也「剛好」回 4)
        assert "import 就炸" in st_data.get("why", ""),             f"🔴 {victim} 的真正原因沒被回報:{st_data.get('why')!r}"


def test_bootstrap是main之外的最後一道保護傘(tmp_path):
    """⛔ main 自己也有 except,所以「import 爆掉」那條其實被內圈接走了。
    這條直接讓 **main 本身**爆,驗最外圈那道:任何漏網例外仍要 rc=4 + 狀態檔。
    (兩層分開測才不會互相掩護 —— 變異驗證抓到過。)"""
    import json as _json
    st = tmp_path / "st.json"
    probe = tmp_path / "boom.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, r'{REPO}')\n"
        "import 分軌線檢查 as D\n"
        "def boom(*a, **k):\n"
        "    raise RuntimeError('main 自己爆了')\n"
        "D.main = boom\n"
        f"sys.exit(D.bootstrap(['--status-json', r'{st}', sys.executable]))\n",
        encoding="utf-8")
    r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300,
                       env={**os.environ, "PYTHONUTF8": "1"})
    assert r.returncode == 4, f"最外圈要收斂成 4(拿到 {r.returncode}):{r.stderr[-300:]}"
    assert st.exists(), "🔴 最外圈也要盡量寫得出狀態檔"
    assert _json.loads(st.read_text(encoding="utf-8"))["kind"] == "internal_error"


# ── 狀態檔的生命週期(Codex R22-P2-3 / P2-4)────────────────────────
_AST_PROBE = """param([string]$Ps1)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Ps1, [ref]$null, [ref]$null)
$tries = $ast.FindAll({ param($n)
    $n -is [System.Management.Automation.Language.TryStatementAst] }, $true)
$out = @()
foreach ($t in $tries) {
    if (-not ($t.Body.Extent.Text -like '*分軌線檢查.py*')) { continue }
    $f = $t.Finally
    $out += [pscustomobject]@{
        CleanupInFinally = ($null -ne $f -and
            $f.Extent.Text -like '*Remove-Item -LiteralPath $statusFile*')
        ValidatorInTry   = ($t.Body.Extent.Text -like '*狀態驗證.py*')
    }
}
if ($out.Count -eq 0) { 'NO_TRY_FOUND' } else { $out | ConvertTo-Json -Compress -Depth 3 }
"""


def _write_ps(path: Path, text: str):
    """⛔ 給 PS 5.1 的 .ps1 一定要 UTF-8 **有 BOM**:沒有 BOM 它會用 cp950 解,
    腳本裡的中文字面值變亂碼 → 比對永遠不成立,測試會靜靜地驗不到東西
    (自己踩到:同一支探針 pwsh 有輸出、powershell 什麼都沒印)。"""
    path.write_text(text, encoding="utf-8-sig", newline="\r\n")


@pytest.mark.parametrize("exe", _ps_engines(), ids=lambda e: Path(e).stem if e else "none")
def test_ps1的狀態檔清理必須在finally區塊裡(tmp_path, exe):
    """🔴 Codex R22-P2-3:舊版的 finally 只還原 PYTHONUTF8(結束於第 266 行),
    狀態檔卻到第 286 行才刪 —— 中間任何中斷/終止性錯誤都會把隨機檔名的
    狀態檔留在 TEMP 裡累積。⛔ 而當時的測試只驗「兩個字都在檔案裡」,
    產品是反例卻照樣綠燈(兩道防線互相遮蔽的教科書案例)。"""
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器")
    if not exe:
        pytest.skip("這台沒有 PowerShell")
    probe = tmp_path / "ast.ps1"
    _write_ps(probe, _AST_PROBE)
    r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(probe), "-Ps1", str(REPO / "install.ps1")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    out = (r.stdout or "").strip()
    assert out and "NO_TRY_FOUND" not in out, f"🔴 找不到跑 helper 的 try 區塊:{out!r}"
    import json as _json
    got = _json.loads(out)
    got = got if isinstance(got, list) else [got]
    assert any(g["CleanupInFinally"] for g in got), \
        f"🔴 狀態檔的清理不在 finally 裡(AST 說的,不是 grep):{got}"
    assert any(g["ValidatorInTry"] for g in got), \
        f"🔴 狀態驗證排在 try 之外 —— 那段中斷一樣會留檔:{got}"


_STOP_PROBE = """param([string]$Repo, [string]$TempDir)
$env:TMP = $TempDir
$env:TEMP = $TempDir
$ps = [PowerShell]::Create()
[void]$ps.AddScript("Set-Location -LiteralPath '$Repo'; " +
                    "& '$Repo\\install.ps1' -CheckOnly -NoAutoTools")
[void]$ps.BeginInvoke()
$marker = Join-Path $TempDir 'validator-started.txt'
$t = 0
while (-not (Test-Path $marker) -and $t -lt 120) { Start-Sleep -Milliseconds 100; $t++ }
$seen = Test-Path $marker
Start-Sleep -Milliseconds 300
$ps.Stop()                      # ← 這正是 Ctrl+C 走的那條路(finally 要跑)
Start-Sleep -Milliseconds 800
$left = @(Get-ChildItem -LiteralPath $TempDir -Filter 'song-jury-demucs-*.json' `
          -EA SilentlyContinue)
[pscustomobject]@{MarkerSeen = $seen; Left = $left.Count} | ConvertTo-Json -Compress
"""


@pytest.mark.parametrize("exe", _ps_engines(), ids=lambda e: Path(e).stem if e else "none")
def test_ps1中途被中斷時狀態檔不可以留在磁碟上(tmp_path, exe):
    """🔴 Codex R22-P2-3 的動態版:在 helper 跑完、狀態還在驗的那一刻停掉整條
    pipeline(PowerShell 的 Stop 就是 Ctrl+C 的內部路徑,會跑 finally),
    然後看那個隨機檔名的狀態檔還在不在。⛔ 清理若排在 try 之外,這裡必留檔。"""
    if sys.platform != "win32":
        pytest.skip("install.ps1 是 Windows 安裝器")
    if not exe:
        pytest.skip("這台沒有 PowerShell")
    d = _stub_repo(tmp_path, 0)
    # helper:寫一份合法狀態檔就走人
    (d / "分軌線檢查.py").write_text(_kind_stub("ok", 0).replace("'ok': False", "'ok': True"),
                                    encoding="utf-8")
    # 狀態驗證:先立旗標,再賴著不走 —— 給測試一個穩定的「中斷時機」
    tempdir = tmp_path / "tmp"
    tempdir.mkdir()
    (d / "狀態驗證.py").write_text(
        "import pathlib, sys, time\n"
        f"pathlib.Path(r'{tempdir / 'validator-started.txt'}').write_text('1')\n"
        "time.sleep(30)\n"
        "print('ok\\t')\n", encoding="utf-8")
    probe = tmp_path / "stop.ps1"
    _write_ps(probe, _STOP_PROBE)
    r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe),
                        "-Repo", str(d), "-TempDir", str(tempdir)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    import json as _json
    out = (r.stdout or "").strip().splitlines()
    assert out, f"🔴 探針沒有輸出:{r.stderr[-500:]}"
    got = _json.loads(out[-1])
    assert got["MarkerSeen"], "🔴 沒等到驗證階段就停了 —— 這次沒驗到要測的時機"
    assert got["Left"] == 0, \
        f"🔴 中斷之後還留著 {got['Left']} 份狀態檔 —— 清理沒有在 finally 裡"


# ⚠️ 每個訊號跑**兩輪**(Codex R25-P1-2):那個 bug 是非決定性的 ——
#    同一份程式碼第一次通過、第二次等滿 60 秒。只跑一次的測試會週期性地假綠。
@pytest.mark.parametrize("round_", [1, 2])
@pytest.mark.parametrize("sig,rc_want", [("INT", 130), ("TERM", 143)])
def test_sh在分軌體檢被中斷時要立刻停下來(tmp_path, sig, rc_want, round_):
    """🔴 Codex R22-P2-4:`trap 'rm -f ...' EXIT INT TERM` 的 handler 只刪檔、沒有
    exit —— 實測 shell 會**繼續往下裝**,最外層還回 0。
    🔴 Codex R23-P2-2:handler 修好之後還有第二半 —— 探針跑在**前景**時,bash 會把
    trap 押到它結束才執行。只把訊號送給安裝器 PID 的自動化(systemd/CI/supervisor)
    要傻等探針跑完(實測等滿 5 秒才回 130)。⛔ 所以這條驗四件事:
    退出碼、**多久回來**、探針子程序有沒有被收掉、後面的步驟有沒有偷跑。"""
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash")
    d = _stub_repo(tmp_path, 0)
    pidfile = tmp_path / "probe.pid"
    beat = tmp_path / "probe.beat"                # 還活著的探針會一直更新它
    natural = tmp_path / "probe-finished.txt"     # 探針「自然跑完」才會有
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    # helper:寫狀態檔 + 自己的 PID → 賴很久 → 只有沒被殺掉才會寫自然完成旗標
    (d / "分軌線檢查.py").write_text(
        "import json, os, pathlib, sys, time\n"
        "a = sys.argv[1:]\n"
        "p = a[a.index('--status-json') + 1] if '--status-json' in a else None\n"
        "if p:\n"
        "    json.dump({'ok': True, 'kind': 'ok', 'rc': 0, 'why': ''},\n"
        "              open(p, 'w', encoding='utf-8'))\n"
        f"pathlib.Path(r'{pidfile}').write_text(str(os.getpid()))\n"
        # ⛔ 心跳而不是 `kill -0`:Git Bash 的 kill 認 MSYS PID,python 給的是
        #    Windows PID —— 用 kill -0 檢查「還活著嗎」在這台永遠回答「死了」,
        #    那條斷言就是裝飾品(變異驗證抓到我這個錯)。
        "for _i in range(600):\n"
        "    time.sleep(0.1)\n"
        f"    pathlib.Path(r'{beat}').write_text(str(_i))\n"
        f"pathlib.Path(r'{natural}').write_text('1')\n"
        "sys.exit(0)\n", encoding="utf-8")
    runner = tmp_path / "run.sh"
    posix = lambda q: str(q).replace(chr(92), "/")
    runner.write_text(
        "#!/usr/bin/env bash\n"
        # ⛔ set -m:非互動 shell 用 `&` 起的背景工作會**繼承 SIGINT 為忽略**,
        #    而 bash 規定「進入時被忽略的訊號不能被 trap」→ kill -INT 什麼都不會
        #    發生,這條會變成驗不到東西的裝飾品(自己踩到)。
        "set -m\n"
        f"export TMPDIR='{posix(tmpdir)}'\n"
        f"cd '{posix(d)}'\n"
        "bash install.sh --check-only --no-auto-tools > out.txt 2>&1 &\n"
        "pid=$!\n"
        # ⚠️ 要等**心跳**不是等 PID 檔:PID 一寫完就送訊號的話,可能還沒跳第一下,
        #    下面「心跳有沒有停」就成了 '' == '' 的假通過(自己踩到,TERM 那組先紅)。
        f"for i in $(seq 1 300); do [ -s '{posix(beat)}' ] && break; sleep 0.1; done\n"
        "start=$SECONDS\n"
        f"kill -{sig} \"$pid\"\n"
        "wait \"$pid\"; rc=$?\n"
        "echo \"RC=$rc\"\n"
        "echo \"ELAPSED=$((SECONDS - start))\"\n"
        "\n",
        encoding="utf-8", newline="\n")
    r = subprocess.run([bash, str(runner)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, cwd=str(tmp_path))
    got = dict(x.split("=", 1) for x in (r.stdout or "").splitlines() if "=" in x)
    out = (d / "out.txt").read_text(encoding="utf-8", errors="replace")
    assert beat.exists(), f"🔴 探針還沒開始就結束了,這次沒驗到中斷:\n{out[-600:]}"
    assert got.get("RC") == str(rc_want), \
        f"🔴 {sig} 之後應該回 {rc_want},拿到 {got.get('RC')};輸出尾巴:\n{out[-600:]}"
    # ⛔ 立刻:探針還要睡 60 秒,若我們等它自然結束就不叫「中斷」
    assert int(got.get("ELAPSED", "999")) <= 15, \
        f"🔴 等了 {got.get('ELAPSED')} 秒才回來 —— trap 被押到前景命令結束才跑"
    # ⛔ 探針要**真的被收掉**:心跳在中斷之後不可以再往前走
    beat1 = beat.read_text(encoding="utf-8") if beat.exists() else ""
    time.sleep(2.0)
    beat2 = beat.read_text(encoding="utf-8") if beat.exists() else ""
    assert beat1 and beat1 == beat2, \
        f"🔴 探針還活著(心跳從 {beat1!r} 走到 {beat2!r})—— handler 沒把訊號轉下去"
    assert not natural.exists(), "🔴 探針其實跑完了(沒有被中斷)"
    # ⛔ 兩種 prefix 都要掃(Codex R24-P2-1):只掃狀態檔的話,被局部 EXIT trap
    #    蓋掉的**全域** sj_step 清理照樣沒人管,而測試全綠。
    left = sorted(x.name for x in tmpdir.iterdir())
    assert left == [], f"🔴 中斷後 TMPDIR 還有殘留:{left}"
    for marker in ("冒煙測試", "接下來怎麼用"):
        assert marker not in out, \
            f"🔴 中斷之後還繼續往下跑(看到「{marker}」):\n{out[-600:]}"


def test_sh正常跑完不可以留下任何暫存檔(tmp_path):
    """🔴 Codex R24-P2-1:shell 的 `trap` **不是堆疊** —— 分軌探針那段自己裝了
    `trap ... EXIT`,把開頭那份 `$SJ_STEP_LOG` 的清理整個蓋掉,結束前再
    `trap - EXIT` 把它清空。結果:每跑一次就在 TEMP 留一份 sj_step.*
    (裡面可能有失敗命令的診斷與本機路徑)。⛔ 而當時的中斷測試只掃
    song-jury-demucs.*,所以完全看不到。"""
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash")
    d = _stub_repo(tmp_path, 0)
    (d / "分軌線檢查.py").write_text(
        "import json, sys\n"
        "a = sys.argv[1:]\n"
        "p = a[a.index('--status-json') + 1] if '--status-json' in a else None\n"
        "if p:\n"
        "    json.dump({'ok': True, 'kind': 'ok', 'rc': 0, 'why': ''},\n"
        "              open(p, 'w', encoding='utf-8'))\n"
        "sys.exit(0)\n", encoding="utf-8")
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    r = subprocess.run([bash, str(d / "install.sh"), "--check-only", "--no-auto-tools"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600, cwd=str(d),
                       env={**os.environ, "TMPDIR": str(tmpdir).replace(chr(92), "/")})
    left = sorted(x.name for x in tmpdir.iterdir())
    assert left == [], f"🔴 正常跑完 TMPDIR 還有殘留:{left}\n{r.stdout[-400:]}"


def test_sh在冒煙測試階段被中斷也不可以留下暫存檔(tmp_path):
    """🔴 Codex R25-P2-3:單一 cleanup_all 當初沒有涵蓋冒煙測試那份 JSON ——
    它用固定的 `$$` 檔名、又只在線性成功/失敗路徑刪,所以在 song_scorer
    跑到一半被中斷時會留在 TEMP。⛔ 而既有的中斷測試都打在分軌探針那一段,
    完全照不到這裡(同一類問題、不同階段)。"""
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash")
    d = _stub_repo(tmp_path, 0)
    # 分軌探針:秒回,讓流程往下走到冒煙測試
    (d / "分軌線檢查.py").write_text(
        "import json, sys\n"
        "a = sys.argv[1:]\n"
        "p = a[a.index('--status-json') + 1] if '--status-json' in a else None\n"
        "if p:\n"
        "    json.dump({'ok': True, 'kind': 'ok', 'rc': 0, 'why': ''},\n"
        "              open(p, 'w', encoding='utf-8'))\n"
        "sys.exit(0)\n", encoding="utf-8")
    beat = tmp_path / "smoke.beat"
    # song_scorer:先寫一點東西到 --json(製造「跑到一半」的暫存檔)再賴著
    (d / "song_scorer.py").write_text(
        "import pathlib, sys, time\n"
        "a = sys.argv[1:]\n"
        "out = a[a.index('--json') + 1] if '--json' in a else None\n"
        "if out:\n"
        "    pathlib.Path(out).write_text('{\"scores\": {\"total\": 1}}', encoding='utf-8')\n"
        "for _i in range(600):\n"
        "    time.sleep(0.1)\n"
        f"    pathlib.Path(r'{beat}').write_text(str(_i))\n", encoding="utf-8")
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    runner = tmp_path / "run.sh"
    posix = lambda q: str(q).replace(chr(92), "/")
    runner.write_text(
        "#!/usr/bin/env bash\n"
        "set -m\n"
        f"export TMPDIR='{posix(tmpdir)}'\n"
        f"cd '{posix(d)}'\n"
        "bash install.sh --check-only --no-auto-tools > out.txt 2>&1 &\n"
        "pid=$!\n"
        f"for i in $(seq 1 400); do [ -s '{posix(beat)}' ] && break; sleep 0.1; done\n"
        "kill -INT \"$pid\"\n"
        "wait \"$pid\"; echo \"RC=$?\"\n",
        encoding="utf-8", newline="\n")
    r = subprocess.run([bash, str(runner)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, cwd=str(tmp_path))
    assert beat.exists(), f"🔴 沒跑到冒煙測試就結束了:{(d / 'out.txt').read_text(encoding='utf-8', errors='replace')[-500:]}"
    left = sorted(x.name for x in tmpdir.iterdir())
    assert left == [], f"🔴 冒煙階段被中斷後 TMPDIR 還有殘留:{left}"
    assert "RC=130" in (r.stdout or ""), f"🔴 中斷沒有以 130 收場:{r.stdout!r}"


def test_sh在探針不理會TERM時要升級成KILL(tmp_path):
    """🔴 Codex R25-P1-2:`群組 kill || 單一 kill` + 無上限 `wait` 的組合,在
    「訊號沒真的生效」時會一路等到探針自然結束(實測等滿 60 秒才回 130)。
    ⚠️ 那是**非決定性**的:同一份程式碼有時候第一道就殺掉了,所以用一般的探針
       測不穩。這條把條件寫死 —— 探針**明確忽略 TERM**,只有「有上限的等待 +
       升級成 KILL」能救它。⛔ 沒有那道後備的話,這裡一定會等到天荒地老。"""
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash")
    d = _stub_repo(tmp_path, 0)
    beat = tmp_path / "probe.beat"
    posix = lambda q: str(q).replace(chr(92), "/")
    # ⭐ 只在「跑分軌線檢查」時走忽略 TERM 的路徑,其餘一律交還給真的 python ——
    #    ⛔ 整支換掉的話,安裝器每一次呼叫 python(import 檢查、狀態驗證、冒煙測試)
    #       都會掉進那個迴圈,整支卡死(自己踩到,第一版 600 秒逾時)。
    wrapper = d / ".venv" / "bin" / "python"
    # ⛔ 不可以把它當文字讀:POSIX 上那是**真的執行檔/symlink**,read_text 直接
    #    UnicodeDecodeError(CI 三個平台實測)。改成先搬到 python.real 再 exec 它 ——
    #    不管原本是 Windows 的 shell wrapper 還是 POSIX 的 binary 都成立。
    real = wrapper.with_name("python.real")
    if real.exists():
        real.unlink()
    wrapper.replace(real)
    # ⚠️ POSIX 的 .venv/bin/python 常常是**symlink**(指到系統 python)——
    #    對 symlink chmod 會跟著改到目標(系統的檔案),絕不可以。
    if not real.is_symlink():
        real.chmod(0o755)
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "  *分軌線檢查.py*)\n"
        "    trap '' TERM\n"                     # ⛔ 故意不理會 TERM
        "    st=''\n"
        "    while [ $# -gt 0 ]; do\n"
        "      if [ \"$1\" = '--status-json' ]; then st=\"$2\"; fi\n"
        "      shift\n"
        "    done\n"
        "    [ -n \"$st\" ] && printf '%s' "
        "'{\"ok\": true, \"kind\": \"ok\", \"rc\": 0, \"why\": \"\"}' > \"$st\"\n"
        "    for i in $(seq 1 600); do sleep 0.1; printf '%s' \"$i\" > "
        f"'{posix(beat)}'; done\n"
        "    exit 0 ;;\n"
        "esac\n"
        f"exec '{posix(real)}' \"$@\"\n",
        encoding="utf-8", newline="\n")
    (d / ".venv" / "bin" / "python").chmod(0o755)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    runner = tmp_path / "run.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        "set -m\n"
        f"export TMPDIR='{posix(tmpdir)}'\n"
        f"cd '{posix(d)}'\n"
        "bash install.sh --check-only --no-auto-tools > out.txt 2>&1 &\n"
        "pid=$!\n"
        f"for i in $(seq 1 400); do [ -s '{posix(beat)}' ] && break; sleep 0.1; done\n"
        "start=$SECONDS\n"
        "kill -INT \"$pid\"\n"
        "wait \"$pid\"; rc=$?\n"
        "echo \"RC=$rc\"\n"
        "echo \"ELAPSED=$((SECONDS - start))\"\n",
        encoding="utf-8", newline="\n")
    r = subprocess.run([bash, str(runner)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, cwd=str(tmp_path))
    got = dict(x.split("=", 1) for x in (r.stdout or "").splitlines() if "=" in x)
    out = (d / "out.txt").read_text(encoding="utf-8", errors="replace")
    assert beat.exists(), f"🔴 探針沒跑起來,這次沒驗到:\n{out[-500:]}"
    assert int(got.get("ELAPSED", "999")) <= 15, \
        (f"🔴 等了 {got.get('ELAPSED')} 秒 —— 探針不理會 TERM 時沒有升級成 KILL,"
         f"就這樣一路等到它自然結束")
    assert got.get("RC") == "130", f"🔴 中斷沒有以 130 收場:{got}"
    # 心跳要停(真的被 KILL 掉,不是還在跑)
    b1 = beat.read_text(encoding="utf-8")
    time.sleep(1.5)
    assert beat.read_text(encoding="utf-8") == b1, "🔴 探針還活著 —— 升級終止沒生效"
    assert sorted(x.name for x in tmpdir.iterdir()) == [], "🔴 TMPDIR 還有殘留"


def test_sh在mktemp失敗時不可以退回固定檔名(tmp_path):
    """🔴 Codex R26-P2-2:`mktemp || 固定的 $$ 檔名` —— 「隨機私密」只在 mktemp
    成功時成立,而最需要私密性的正是 TEMP 權限/工具異常那種環境。
    ⛔ 那等於把最壞情況做成最不安全的情況:直接停,不要退回可預測的名字。"""
    bash = _git_bash()
    if not bash:
        pytest.skip("這台沒有 Git Bash")
    d = _stub_repo(tmp_path, 0)
    # ⚠️ 假的 mktemp 不可以放在 tmp_path:pytest 的目錄名帶了這條測試的**中文**名字,
    #    Git Bash 解不了那個 PATH 項目 → 直接忽略 → 用到真的 mktemp,
    #    這條就變成「什麼都沒驗到卻綠燈」(自己踩到)。放到純 ASCII 的暫存目錄。
    import tempfile as _tf
    fake_bin = Path(_tf.mkdtemp(prefix="sj-fakebin-"))
    (fake_bin / "mktemp").write_text("#!/usr/bin/env bash\nexit 1\n",
                                     encoding="utf-8", newline="\n")
    (fake_bin / "mktemp").chmod(0o755)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    posix = lambda q: str(q).replace(chr(92), "/")
    # ⚠️ PATH 的項目要是**這個 shell 認得的**格式:Git Bash 看不懂 `C:/…`,
    #    整個項目會被靜靜忽略 → 用到真的 mktemp,這條又變成驗不到東西
    #    (自己踩到第二次:第一次是中文路徑,這次是路徑格式)。用 cygpath 轉。
    r = subprocess.run(
        [bash, "-c",
         f"p='{posix(fake_bin)}'; "
         "command -v cygpath >/dev/null 2>&1 && p=\"$(cygpath -u \"$p\")\"; "
         "export PATH=\"$p:$PATH\"; "
         "command -v mktemp | grep -q sj-fakebin || "
         "{ echo 'FAKE_MKTEMP_NOT_ON_PATH'; exit 99; }; "
         f"export TMPDIR='{posix(tmpdir)}'; "
         f"cd '{posix(d)}'; bash install.sh --check-only --no-auto-tools"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    assert "FAKE_MKTEMP_NOT_ON_PATH" not in (r.stdout or ""), \
        "🔴 假的 mktemp 沒有蓋過真的 —— 這次什麼都沒驗到"
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"🔴 mktemp 壞掉卻照樣往下跑:\n{out[-500:]}"
    assert "mktemp" in out and "暫存檔" in out, f"🔴 沒有講清楚為什麼停:\n{out[-500:]}"
    # ⛔ 要停在**第一個** mktemp:退回固定檔名的話會一路跑到自我檢查,才因為
    #    別處的 mktemp 失敗而停 —— 那樣「rc 非零 + 有訊息」照樣成立,變異就驗不到
    #    (變異驗證抓到我這條是裝飾品)。
    assert "自我檢查" not in out, f"🔴 沒有在第一個 mktemp 就停下來:\n{out[-500:]}"
    left = sorted(x.name for x in tmpdir.iterdir())
    assert not [x for x in left if "$$" in x or x.startswith("sj_step_")], \
        f"🔴 還是產生了可預測的固定檔名:{left}"
