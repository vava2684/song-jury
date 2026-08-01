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


# 父程序:生一個**自行脫離程序群組**的孫程序(Codex R13 對抗探針)
_PARENT_DETACHED = r"""
import subprocess, sys, time
if sys.platform == "win32":
    flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    kw = {"creationflags": flags}
else:
    kw = {"start_new_session": True}         # setsid:跳出父親的 process group
subprocess.Popen([sys.executable, "-c",
    "import time,sys; time.sleep(6); open(sys.argv[1],'w').write('SURVIVED')",
    sys.argv[1]], **kw)
time.sleep(60)
"""


# 孤兒探針:中間程序生完孫孫**立刻退出** → 孫孫的 parent PID 指向已死的程序。
# taskkill /T 是靠 parent-child 快照遍歷的,從根本找不到孫孫;
# Job Object 的歸屬是核心層繼承的,孫孫照樣在 job 裡 → 這才真的區分得出兩者。
_PARENT_ORPHAN = r"""
import subprocess, sys, time
MID = (
    "import subprocess, sys;"
    "subprocess.Popen([sys.executable, '-c',"
    " \"import time,sys; time.sleep(6); open(sys.argv[1],'w').write('SURVIVED')\","
    " sys.argv[1]], **({'creationflags': 0x00000008 | 0x00000200}"
    " if sys.platform=='win32' else {'start_new_session': True}))"
)
kw = ({"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32"
      else {"start_new_session": True})
subprocess.Popen([sys.executable, "-c", MID, sys.argv[1]], **kw)
time.sleep(60)
"""


@pytest.mark.skipif(sys.platform != "win32",
                    reason="POSIX 的 setsid 後代不在保證範圍(見 run_tree docstring)")
def test_Windows下脫離又被孤兒化的後代也要被殺掉(tmp_path):
    """🔴 Codex R13 的 detached 探針,加強成「孤兒化」版本 ——
    中間程序生完孫孫就退出,孫孫的 parent 已死,taskkill /T 的樹遍歷找不到它。
    Job Object + KILL_ON_JOB_CLOSE 是核心層歸屬,這種也逃不掉。

    ⚠️ 誠實註記:單純的 DETACHED_PROCESS 孫程序,taskkill /T 在本機實測其實
    殺得掉(parent PID 記錄還在);真正會漏的是這裡的孤兒情境。"""
    marker = tmp_path / "survived.txt"
    t0 = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        P.run_tree([sys.executable, "-c", _PARENT_ORPHAN, str(marker)], timeout=3)
    took = time.time() - t0
    time.sleep(max(0.0, 10 - took))
    assert not marker.exists(), \
        "🔴 被孤兒化的後代活過了逾時 —— Job Object 沒生效或沒把它關進去"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 專屬:誠實邊界的文件契約")
def test_POSIX對setsid後代的邊界要寫在契約裡():
    """POSIX 沒有免權限的硬保證(cgroup v2 要額外權限)。
    ⛔ 那就必須**在契約裡講清楚**,不可以讓人以為 killpg 涵蓋一切。"""
    doc = P.run_tree.__doc__ or ""
    assert "setsid" in doc and "不在保證範圍" in doc, \
        "run_tree 的 docstring 要誠實寫出 POSIX 的邊界"


def test_正常結束照樣拿到輸出與結束碼():
    r = P.run_tree([sys.executable, "-c", "print('hi'); import sys; sys.exit(7)"],
                   timeout=30)
    assert r.returncode == 7
    assert "hi" in r.stdout


# ── Codex R14:非 timeout 的失敗路徑也要殺樹;Job handle 不可洩漏 ──────

def test_非逾時例外也要殺樹(tmp_path, monkeypatch):
    """🔴 Codex R14 注入探針:communicate 拋 OSError(管線 I/O 失敗)時,
    舊碼只特判 TimeoutExpired → 呼叫端已拿到失敗,子程序卻還活著繼續寫檔。
    評審團._optional_stage 會吞掉一般 Exception 繼續跑,所以那個子模型
    會在主流程都結束後還在占 GPU。"""
    marker = tmp_path / "leak.txt"
    real = subprocess.Popen.communicate

    def boom(self, *a, **k):
        if getattr(self, "_boomed", False):     # 第二次(回收)讓它正常跑
            return real(self, *a, **k)
        self._boomed = True
        raise OSError("injected pipe read failure")

    monkeypatch.setattr(subprocess.Popen, "communicate", boom)
    with pytest.raises(OSError):
        P.run_tree([sys.executable, "-c", _PARENT, str(marker)], timeout=30)
    time.sleep(9)
    assert not marker.exists(), \
        "🔴 非逾時失敗時沒殺樹 —— 子程序活過了呼叫端的失敗"


def test_KeyboardInterrupt也要殺樹(tmp_path, monkeypatch):
    """Ctrl+C 走的是 BaseException,不在 Exception 的涵蓋範圍 —— 一樣要殺乾淨。"""
    marker = tmp_path / "leak2.txt"
    real = subprocess.Popen.communicate

    def boom(self, *a, **k):
        if getattr(self, "_boomed", False):
            return real(self, *a, **k)
        self._boomed = True
        raise KeyboardInterrupt()

    monkeypatch.setattr(subprocess.Popen, "communicate", boom)
    with pytest.raises(KeyboardInterrupt):
        P.run_tree([sys.executable, "-c", _PARENT, str(marker)], timeout=30)
    time.sleep(9)
    assert not marker.exists(), "🔴 Ctrl+C 之後子程序還活著"


@pytest.mark.skipif(sys.platform != "win32", reason="Job handle 是 Windows 概念")
def test_Popen失敗不可洩漏Job_handle():
    """🔴 Codex R14:執行檔不存在時 Popen 拋錯,舊碼沒有 finally 關 Job handle
    → 連跑 25 次 process handle 147→173。長跑的網頁/批次服務會一直漏。"""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # ⛔ 探針自己也要宣告 prototype:預設 restype=c_int 會把 64 位 HANDLE 截斷,
    #    GetCurrentProcess 傳進去就變垃圾值 → 永遠量到 0,測試變裝飾品
    #    (我第一版就是這樣;Codex 說他的探針也踩過同一個坑)。
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    cnt = ctypes.c_uint32(0)
    def handles():
        ok = k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(cnt))
        assert ok, "GetProcessHandleCount 失敗 —— 探針壞了,不是被測程式沒問題"
        assert cnt.value > 0, "handle 數量量到 0 —— 探針的 ctypes prototype 有問題"
        return cnt.value
    for _ in range(3):                       # 暖機(第一次會建 ctypes 快取)
        with pytest.raises(Exception):
            P.run_tree(["絕對不存在的執行檔_xyz.exe"], timeout=5)
    before = handles()
    for _ in range(25):
        with pytest.raises(Exception):
            P.run_tree(["絕對不存在的執行檔_xyz.exe"], timeout=5)
    delta = handles() - before
    assert delta <= 4, f"🔴 25 次啟動失敗漏了 {delta} 個 handle(Job handle 沒關)"
