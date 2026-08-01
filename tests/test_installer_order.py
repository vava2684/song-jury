# -*- coding: utf-8 -*-
"""-VerifyModels 的執行順序:裁判必須看得到報告,清理只能在最後。

🔴 Codex R14:`finally` 裡的清理排在 `驗證報告.py` **之前** —— 真實
-VerifyModels 已九柱跑完、jury exit 0,裁判卻拿到「檔案不存在」,
安裝器 exit 1。完整驗證的成功路徑變成必定假陰性。
203 條測試 + 90 條變異全綠也沒守住,因為當時只驗字樣、沒驗真實執行順序。

這支用真的 shell(PowerShell / bash)跑安裝器的那段邏輯骨架,
用 stub 取代評審團與裁判,觀察:① 裁判被呼叫時報告還在;② 收工後全清乾淨。
"""
import os
import shutil
import subprocess
import sys

import pytest

from conftest import REPO


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)


def _diag(msg, r):
    """失敗訊息一定要帶 returncode 與 stderr —— 只印 stdout 的話,
    shell 根本沒跑起來時會得到一則空訊息,完全查不下去(CI 上實際踩到)。"""
    return (f"{msg}:rc={r.returncode}\n"
            f"OUT={r.stdout[-600:]}\n"
            f"ERR={r.stderr[-600:]}")


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell 版")
@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")),
                    reason="沒有 PowerShell")
def test_ps1的驗證順序_裁判先看到報告清理在最後(tmp_path):
    """把 install.ps1 的 VerifyModels 區塊原文抽出來跑,jury/裁判都換成 stub。"""
    src = (REPO / "install.ps1").read_text(encoding="utf-8")
    # 用安裝腳本裡的標記抽取(切點寫死行為文字會隨改碼漂掉,抽到半截 if/else)
    i = src.index("# <verify-block-start>")
    j = src.index("# <verify-block-end>")
    # 直譯器換成本測試的 python:單純複製 python.exe 到假 venv 是跑不起來的
    # (缺 DLL/stdlib);這條測的是**順序**,不是路徑字面。
    block = src[i:j].replace(".venv\Scripts\python.exe", f'"{sys.executable}"')
    # stub:jury 寫出報告並 exit 0;裁判把「我看到報告了嗎」寫進見證檔
    (tmp_path / "評審團.py").write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1]).with_name(pathlib.Path(sys.argv[1]).stem + '_評審團.json')\n"
        "p.write_text('{\"ok\": 1}', encoding='utf-8')\n"
        "pathlib.Path('mid_評分.json').write_text('x', encoding='utf-8')\n"
        "sys.exit(0)\n", encoding="utf-8")
    (tmp_path / "驗證報告.py").write_text(
        "import sys, pathlib\n"
        "seen = pathlib.Path(sys.argv[1]).exists()\n"
        "pathlib.Path('WITNESS.txt').write_text('SEEN' if seen else 'GONE', encoding='utf-8')\n"
        "sys.exit(0 if seen else 1)\n", encoding="utf-8")
    (tmp_path / "demo_mix.wav").write_bytes(b"RIFF0000")

    ps = shutil.which("pwsh") or shutil.which("powershell")
    script = tmp_path / "run.ps1"
    script.write_text(
        "function Ok($m){Write-Host \"OK $m\"}\n"
        "function Bad($m,$w){Write-Host \"BAD $m\"}\n"
        "$script:VerifyOk = $true\n" + block +
        "\nWrite-Host \"VERIFYOK=$($script:VerifyOk)\"\n", encoding="utf-8")
    r = _run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], tmp_path)

    witness = (tmp_path / "WITNESS.txt")
    assert witness.exists(), _diag("裁判沒被呼叫到", r)
    assert witness.read_text(encoding="utf-8") == "SEEN", \
        "🔴 裁判被呼叫時報告已經被清掉了 —— 清理排在驗證之前(成功路徑必定假陰性)"
    assert "VERIFYOK=True" in r.stdout, _diag("成功路徑不該判失敗", r)
    # 收工後所有 $vid 前綴的產物都要清乾淨(含中途寫出的 _評分.json)
    left = [p.name for p in tmp_path.glob("verify_*")]
    assert left == [], f"🔴 沒清乾淨:{left}"


@pytest.mark.skipif(sys.platform == "win32", reason="bash 版")
def test_sh的驗證順序_裁判先看到報告清理在最後(tmp_path):
    src = (REPO / "install.sh").read_text(encoding="utf-8")
    i = src.index('VID="verify_')
    j = src.index("  else\n    bad \"--verify-models", i)
    block = src[i:j]
    (tmp_path / "評審團.py").write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1]).with_name(pathlib.Path(sys.argv[1]).stem + '_評審團.json')\n"
        "p.write_text('{\"ok\": 1}', encoding='utf-8')\n"
        "pathlib.Path('mid_評分.json').write_text('x', encoding='utf-8')\n"
        "sys.exit(0)\n", encoding="utf-8")
    (tmp_path / "驗證報告.py").write_text(
        "import sys, pathlib\n"
        "seen = pathlib.Path(sys.argv[1]).exists()\n"
        "pathlib.Path('WITNESS.txt').write_text('SEEN' if seen else 'GONE', encoding='utf-8')\n"
        "sys.exit(0 if seen else 1)\n", encoding="utf-8")
    (tmp_path / "demo_mix.wav").write_bytes(b"RIFF0000")

    script = tmp_path / "run.sh"
    script.write_text(
        "ok(){ echo \"OK $1\"; }\nbad(){ echo \"BAD $1\"; }\n"
        "VERIFY_OK=1\nC_DIM=''; C_OFF=''\n" + block +
        "\necho \"VERIFYOK=$VERIFY_OK\"\n", encoding="utf-8")
    r = _run(["bash", str(script)], tmp_path)

    witness = (tmp_path / "WITNESS.txt")
    assert witness.exists(), _diag("裁判沒被呼叫到", r)
    assert witness.read_text(encoding="utf-8") == "SEEN", \
        "🔴 裁判被呼叫時報告已經被清掉了"
    assert "VERIFYOK=1" in r.stdout, _diag("成功路徑不該判失敗", r)
    assert [p.name for p in tmp_path.glob("verify_*")] == []
