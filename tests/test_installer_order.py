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
