# -*- coding: utf-8 -*-
"""子程序.run_tree:逾時必須殺**整棵程序樹**,不是只殺直屬子程序。

🔴 Codex R12 探針:subprocess.run(timeout=) 逾時後,子程序開的孫程序
(Demucs/torch)存活並在稍後寫出 marker(grandchild_survived_and_wrote_later=true)
—— UI 顯示逾時,GPU 卻還在背景燒。這裡用真的三代程序驗行為,不檢查字樣。
"""
import subprocess
import sys
import time

import pytest

from conftest import load

P = load("子程序")

# 父程序:生一個「6 秒後寫 marker」的孫程序,自己睡 60 秒等著被逾時殺掉
_PARENT = r"""
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c",
    "import time,sys; time.sleep(6); open(sys.argv[1],'w').write('LEAK')",
    sys.argv[1]])
time.sleep(60)
"""


def test_逾時要殺整棵樹_孫程序不可存活寫檔(tmp_path):
    marker = tmp_path / "leak.txt"
    t0 = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        P.run_tree([sys.executable, "-c", _PARENT, str(marker)], timeout=3)
    took = time.time() - t0
    assert took < 30, f"逾時處理拖了 {took:.0f}s —— 殺樹後沒回收好?"
    # 孫程序原定 t+6s 寫檔;等到 t+9s 再驗,活著就一定寫得出來
    time.sleep(max(0.0, 9 - took))
    assert not marker.exists(), \
        "🔴 孫程序在逾時之後還活著把 marker 寫出來了 —— 程序樹沒殺乾淨"


def test_正常結束照樣拿到輸出與結束碼():
    r = P.run_tree([sys.executable, "-c", "print('hi'); import sys; sys.exit(7)"],
                   timeout=30)
    assert r.returncode == 7
    assert "hi" in r.stdout
