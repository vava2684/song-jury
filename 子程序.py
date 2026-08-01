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
_SJ_NEVER = ()          # 變異驗證用的空例外組(產品路徑永不匹配)
_SJ_NEVER2 = ()         # 同上(變異需要「兩道防線一起拔」時用)
# Windows:殺樹的 taskkill 自己也不要彈黑框
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _WIN else {}


class _WinJob:
    """Windows Job Object:把子程序關進 job,關掉 handle 就**核心保證**整個 job 全死。

    ⛔ 為什麼需要它(Codex R13 探針):taskkill /T 走的是「父子關係快照」,
       孫程序若以 DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP 自行脫離,
       taskkill 找不到它 —— run_tree 已回報逾時,孫程序 5 秒後照樣寫出檔案。
       Job Object 是核心層的歸屬關係,脫離程序群組也逃不掉
       (除非明確帶 CREATE_BREAKAWAY_FROM_JOB,而我們不允許 breakaway)。
    只用 ctypes,不加相依。建不起來就回 None,呼叫端退回 taskkill(誠實降級)。"""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # ⛔ 一定要宣告 prototype:ctypes 預設 restype 是 c_int(32 位),
        #    64 位的 HANDLE 會被**截斷**。實測時多半還是能用(Windows 的 handle
        #    值通常很小),但那是運氣不是保證 —— handle 一旦超過 2^31 就會拿到
        #    垃圾值,Job 靜靜失效、殺樹保證跟著蒸發。(Codex R14 的探針自己也踩過
        #    同一個坑,他修好 prototype 後才量得到數字。)
        _k = self._k32
        _k.CreateJobObjectW.restype = ctypes.c_void_p
        _k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        _k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
        _k.OpenProcess.restype = ctypes.c_void_p
        _k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        _k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _k.TerminateJobObject.argtypes = [ctypes.c_void_p, wintypes.UINT]
        _k.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = _k.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObject 失敗")

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IOCTR(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                        ("WriteOperationCount", ctypes.c_uint64),
                        ("OtherOperationCount", ctypes.c_uint64),
                        ("ReadTransferCount", ctypes.c_uint64),
                        ("WriteTransferCount", ctypes.c_uint64),
                        ("OtherTransferCount", ctypes.c_uint64)]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC),
                        ("IoInfo", _IOCTR),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000   # KILL_ON_JOB_CLOSE
        if not self._k32.SetInformationJobObject(
                self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):   # 9=ExtendedLimit
            self.close()
            raise OSError("SetInformationJobObject 失敗")

    def assign(self, pid: int):
        import ctypes
        h = self._k32.OpenProcess(0x1F0FFF, False, pid)      # PROCESS_ALL_ACCESS
        if not h:
            raise OSError("OpenProcess 失敗")
        try:
            if not self._k32.AssignProcessToJobObject(self.handle, h):
                raise OSError("AssignProcessToJobObject 失敗")
        finally:
            self._k32.CloseHandle(h)

    def terminate(self):
        if not getattr(self, "handle", None):
            return
        try:
            self._k32.TerminateJobObject(self.handle, 1)
        except Exception:
            pass

    def close(self):
        """關 handle(KILL_ON_JOB_CLOSE:關掉即全滅)。⚠️ 必須可重複呼叫 ——
        finally 與早期錯誤路徑都會叫它,雙關 handle 是未定義行為。"""
        h, self.handle = getattr(self, "handle", None), None
        if h:
            try:
                self._k32.CloseHandle(h)
            except Exception:
                pass


def kill_tree(p: subprocess.Popen, job=None):
    """殺掉 p 的整棵程序樹。有 job 就用 job(硬保證),否則退回 taskkill/killpg。"""
    if job is not None:
        job.terminate()
        return
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
       killpg 會把批次/網頁服務甚至啟動它的 shell 一起殺掉(app.py 踩過)。

    ⚠️ 保證範圍(誠實劃界,Codex R13 探針逼出來的):
       · Windows:用 Job Object + KILL_ON_JOB_CLOSE → **連自行 DETACHED_PROCESS /
         新程序群組的後代也殺得掉**(除非它自己帶 CREATE_BREAKAWAY_FROM_JOB,
         而我們沒有開放 breakaway)。Job 建不起來時退回 taskkill /T(較弱)。
       · POSIX:殺的是 process group。**自己 setsid 脫離的後代不在保證範圍** ——
         要硬保證得用 cgroup v2/systemd scope,那需要額外權限,本工具不做。
         實務上 Demucs/torch/ffmpeg 都不會自我 daemonize,所以夠用;
         真的遇到會自己脫離的工具,請自行用 cgroup 包起來跑。"""
    job = None
    if _WIN:
        iso = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
               | 0x00000004 | extra_creationflags}          # CREATE_SUSPENDED:先凍住再入 job
        try:
            job = _WinJob()
        except Exception:
            job = None
            iso = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | extra_creationflags}
    else:
        iso = {"start_new_session": True}
    # ⛔ 從 Job 建立起就要有最外層 finally(Codex R14):
    #    · Popen 失敗(執行檔不存在等)時舊碼直接把例外往外拋 → Job handle 洩漏
    #      (實測連跑 25 次:process handle 147 → 173);
    #    · communicate 拋的若不是 TimeoutExpired(管線 OSError、KeyboardInterrupt)
    #      舊碼完全不殺樹 → 呼叫端已拿到失敗,子程序卻還活著繼續吃 GPU、寫中間檔,
    #      這跟 run_tree 的核心契約直接衝突。
    p = None
    try:
        p = subprocess.Popen(cmd, cwd=(str(cwd) if cwd else None), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace", **iso)
        if job is not None:
            # ⚠️ 先 CREATE_SUSPENDED 再指派再喚醒:程序一啟動就可能生孫程序,
            #    指派前生的那些不會進 job(競態窗口)。凍住入 job 才沒有窗口。
            try:
                job.assign(p.pid)
            except Exception:
                job.close()
                job = None
            _resume_process(p.pid)
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p, job)
            try:
                out, err = p.communicate(timeout=10)   # 回收,不留殭屍
            except Exception:
                out, err = "", ""
            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
        except BaseException:
            # ⛔ 任何其他失敗(含 KeyboardInterrupt)也要殺樹再往外拋 ——
            #    「呼叫端失敗了但子程序還在跑」是這個模組存在的理由要防的事。
            kill_tree(p, job)
            try:
                p.communicate(timeout=10)
            except Exception:
                pass
            raise
        return subprocess.CompletedProcess(cmd, p.returncode, stdout=out, stderr=err)
    finally:
        if job is not None:
            job.close()      # KILL_ON_JOB_CLOSE:即使前面漏殺,關 handle 也會收乾淨


def _resume_process(pid: int):
    """喚醒以 CREATE_SUSPENDED 啟動的程序(Windows 專用)。"""
    if not _WIN:
        return
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPTHREAD = 0x00000004

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD)]

    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p     # 同上:HANDLE 不可截斷
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.OpenThread.restype = ctypes.c_void_p
    k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.ResumeThread.argtypes = [ctypes.c_void_p]
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return
    try:
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(te)
        ok = k32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                h = k32.OpenThread(0x0002, False, te.th32ThreadID)   # THREAD_SUSPEND_RESUME
                if h:
                    k32.ResumeThread(h)
                    k32.CloseHandle(h)
            ok = k32.Thread32Next(snap, ctypes.byref(te))
    finally:
        k32.CloseHandle(snap)
