# -*- coding: utf-8 -*-
"""變異驗證這支工具自己的行為 —— ⛔ 它壞了的話,所有「測試不是裝飾品」的證據都不算數。

🔴 Codex R17-4:打包類變異用 `git rm --cached` 模擬「這個檔沒進 repo」,
   舊版只要 git 回非零就標成「ZIP 沒有 .git」跳過。但 index.lock 競態、index 唯讀、
   repo 損壞也都回非零(實測 rc=128)—— 於是**最需要打包保護的 clone**,
   會在 git 故障時把整組打包檢查靜靜關掉,報表還寫著「只是 ZIP 限制」,整支照樣 exit 0。
"""
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tests"))
import 變異驗證 as M  # noqa: E402


def test_在git工作樹裡就不可以自稱是ZIP版():
    """這份 repo 自己就是 worktree —— in_worktree() 說不是的話,分流從根就錯了。"""
    if not (REPO / ".git").exists():
        pytest.skip("這是 ZIP/非 git 目錄")
    assert M.in_worktree() is True


def test_git失敗要炸不可以吞掉(monkeypatch):
    """index.lock 的實測樣子:rc=128 + 明確 stderr。"""
    monkeypatch.setattr(M.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 128, "", "fatal: Unable to create '.git/index.lock': File exists."))
    with pytest.raises(M.GitFailure) as e:
        M.git_must(["rm", "--cached", "-q", "--", "完整驗證.py"])
    assert "128" in str(e.value) and "index.lock" in str(e.value), \
        "🔴 錯誤原因要帶出來,不然沒人知道是 git 壞了還是真的沒進 repo"


def test_還原用的git也要驗成功(monkeypatch):
    """⛔ 還原失敗卻宣稱乾淨,比不還原更糟:下一次跑的是被汙染的樹。"""
    monkeypatch.setattr(M.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, "", "error: unable to add"))
    with pytest.raises(M.GitFailure):
        M.git_must(["add", "--", "完整驗證.py"])


def test_index_lock故障時整支不可以還是綠的(tmp_path):
    """端對端:在真的 clone 裡塞一個 index.lock,變異驗證必須以非零收場,
    而且**不可以**把它說成 ZIP skip。"""
    git = shutil.which("git")
    if not git or not (REPO / ".git").exists():
        pytest.skip("需要 git 與 worktree")
    clone = tmp_path / "clone"
    r = subprocess.run([git, "-c", "safe.directory=*", "clone", "-q", str(REPO), str(clone)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        pytest.skip(f"clone 失敗(環境限制):{r.stderr[-200:]}")
    # ⛔ clone 拿到的是**已提交**的版本 —— 要驗的是工作樹裡這一份(可能被變異注入過),
    #    所以把它覆蓋進去,否則這條端對端測試對變異驗證自己的迴歸是瞎的(自己踩到)。
    shutil.copy(REPO / "tests" / "變異驗證.py", clone / "tests" / "變異驗證.py")
    (clone / ".git" / "index.lock").write_text("", encoding="utf-8")
    probe = clone / "probe.py"
    probe.write_text(
        "import sys\n"
        "sys.path.insert(0, 'tests')\n"
        "import 變異驗證 as M\n"
        "M.MUTATIONS = []\n"
        "M.GIT_MUTATIONS = M.GIT_MUTATIONS[:1]\n"
        # ⚠️ 健康檢查(target='tests')必須「沒失敗」,否則 main() 一開始就
        #    印『乾淨狀態下測試就沒過』回 1 —— 那樣不管有沒有缺陷都是非零,
        #    這條端對端測試就變成裝飾品(變異驗證抓到我這個錯)。
        # ⚠️ 回傳值要跟 run_pytest 的簽章一致(第四個是子 pytest 的輸出)——
        #    少一個會 ValueError,那樣**不管有沒有缺陷都是非零**,這條端對端
        #    測試就以錯誤的理由通過(R25 實測:三條變異因此驗不到)。
        "M.run_pytest = lambda t: (t != 'tests', True, 1, '')\n"
        "sys.exit(M.main())\n", encoding="utf-8")
    out = subprocess.run([sys.executable, "probe.py"], cwd=str(clone),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=600,
                         env={**os.environ, "PYTHONUTF8": "1"})
    assert out.returncode != 0, f"🔴 git 壞掉還是綠的:\n{out.stdout[-600:]}"
    assert "ZIP" not in out.stdout.split("=" * 60)[-1], \
        f"🔴 把 git 故障說成 ZIP 限制:\n{out.stdout[-600:]}"


import os      # noqa: E402  (端對端那條要用)
import shutil  # noqa: E402
