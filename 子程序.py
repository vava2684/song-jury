# -*- coding: utf-8 -*-
"""子程序共用底層:跑一個指令,逾時就把**整棵程序樹**殺掉。

⛔ `subprocess.run(..., timeout=)` 逾時只處理**直屬子程序**:子程序另外啟動的
   Demucs/torch/ffmpeg 孫程序會存活(Codex R12 探針:parent_timed_out=true、
   grandchild_survived_and_wrote_later=true)—— UI 已顯示逾時,GPU 模型卻還在
   背景吃顯存、寫中間檔,下一個工作跟殘留程序互踩。
修法(與 app.py 實戰驗證過的同一套):
   · POSIX:`start_new_session=True` 開新 session,逾時 `killpg(SIGKILL)`
   · Windows:`CREATE_NEW_PROCESS_GROUP`,逾時 `taskkill /F /T /PID`
   · 殺完必 `communicate()` 回收,不留殭屍
⚠️ `TimeoutExpired` 沒有 .pid 欄位 —— 一定要用 Popen 自己保管 PID
   (舊寫法 getattr(e,"pid",0) 等於 taskkill /PID 0,真實事故)。
評審團(_optional_stage)、批次評測、app 三處共用這一份,不再各寫各的。
"""
import os
import subprocess
import sys

_WIN = sys.platform == "win32"
# Windows:殺樹的 taskkill 自己也不要彈黑框
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _WIN else {}


def kill_tree(p: subprocess.Popen):
    """殺掉 p 的整棵程序樹(p 需以 run_tree/新程序群組方式啟動)。"""
    if _WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True, **_NO_WINDOW)
    else:
        import signal
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            p.kill()


def run_tree(cmd, *, timeout, cwd=None, env=None, extra_creationflags=0):
    """跑指令並擷取輸出;逾時 → 殺整棵樹、回收、再拋 TimeoutExpired。

    介面對齊 subprocess.run(capture_output=True, text=True):回 CompletedProcess。
    ⛔ POSIX 一定要開新 session:不開的話子程序留在**呼叫者自己的程序群組**,
       killpg 會把批次/網頁服務甚至啟動它的 shell 一起殺掉(app.py 踩過)。"""
    iso = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | extra_creationflags}
           if _WIN else {"start_new_session": True})
    p = subprocess.Popen(cmd, cwd=(str(cwd) if cwd else None), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", **iso)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        try:
            out, err = p.communicate(timeout=10)   # 回收,不留殭屍
        except Exception:
            out, err = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(cmd, p.returncode, stdout=out, stderr=err)
