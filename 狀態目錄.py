# -*- coding: utf-8 -*-
"""使用者全域狀態目錄 —— 工作鎖與 Gemini 冷卻狀態的唯一位置。

⛔ 不放在工具資料夾(BASE)底下:電腦上同時存在新舊兩份 ZIP、App 與 CLI
   來自不同副本時,各副本的 BASE 不同 → 各鎖各的,互斥保證只在「單一副本」
   內成立(Codex R10 實測)。放在使用者層級的固定目錄,**同一位 OS 使用者**
   的所有副本才共用同一組鎖與冷卻。
⚠️ 誠實的邊界(Codex R11 驗證後改口):互斥保證的範圍是「同一台機器上的
   **同一位 OS 使用者**」——兩個帳號(例如登入使用者與排程服務帳號)各有
   自己的家目錄,同時評同一個共享音檔仍會互踩中間產物;跨機器(NFS/SMB)
   同理。不要那樣用。
環境變數 SONG_JURY_STATE_DIR 可覆寫(測試/沙箱用);**必須是絕對路徑** ——
相對路徑會隨工作目錄漂移,兩個不同 cwd 啟動的工作各鎖各的,互斥域無聲分裂。
"""
import os
import stat
import sys
from pathlib import Path


class StateDirError(RuntimeError):
    """狀態目錄不可用。訊息必含真正的路徑與原因,呼叫端要 fail-closed。"""


def state_root() -> Path:
    """回使用者全域狀態目錄(會建立、會驗證)。每次呼叫都重讀環境變數,測試才能覆寫。

    ⛔ 所有失敗都拋 StateDirError(帶路徑),不讓 FileExistsError 之類的原始
       traceback 直接噴到使用者臉上(Codex R11:覆寫指到普通檔案時炸在保護層外)。
    """
    env = os.environ.get("SONG_JURY_STATE_DIR")
    if env:
        d = Path(env)
        if not d.is_absolute():
            raise StateDirError(
                f"SONG_JURY_STATE_DIR 必須是絕對路徑(拿到:{env!r})——"
                f"相對路徑會隨工作目錄漂移,兩個 cwd 各鎖各的,互斥域無聲分裂")
    elif sys.platform == "win32":
        d = Path(os.environ.get("LOCALAPPDATA")
                 or (Path.home() / "AppData" / "Local")) / "song-jury"
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        d = (Path(xdg) if xdg else Path.home() / ".local" / "state") / "song-jury"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        raise StateDirError(f"狀態目錄的位置被一個普通檔案佔住:{d} —— 請移走那個檔案")
    except OSError as e:
        raise StateDirError(f"狀態目錄建不起來:{d}({type(e).__name__}: {e})")
    if not d.is_dir():
        raise StateDirError(f"狀態目錄不是資料夾:{d}")
    if os.name != "nt":
        # ⛔ POSIX 一律鎖 0700 並驗擁有者:共享目錄(0777)裡別人可以預植
        #    symlink 鎖檔,把工作鎖的 pid 寫進任意檔案(Codex R11 探針)。
        st = os.stat(d)
        if st.st_uid != os.getuid():
            raise StateDirError(f"狀態目錄不是目前使用者擁有:{d} —— 別把它指向共享目錄")
        try:
            os.chmod(d, 0o700)
        except OSError as e:
            raise StateDirError(f"狀態目錄權限鎖不下來:{d}({e})")
    return d


def safe_open_lock(path: Path):
    """安全地開鎖檔:不跟隨符號連結、必須是普通檔案、POSIX 0600。

    ⛔ 直接 open(path, "a+") 會跟著 symlink 走:攻擊者在鎖目錄預植
       `job_<hash>.lock -> victim.txt`,持鎖時寫 pid 就把 victim 覆寫了
       (Codex R11 POSIX 探針實證)。O_NOFOLLOW 讓 open 對 symlink 直接
       ELOOP;開起來之後再 fstat 驗它真的是普通檔案。
    ⚠️ Windows 沒有 O_NOFOLLOW:開檔後用 lstat 驗 reparse point(有極小的
       事後檢查競態,但 Windows 建 symlink 需要系統管理員/開發者模式,
       且預設狀態目錄的 ACL 只有本使用者可寫,風險可接受並在此記錄)。
    失敗一律拋 OSError,呼叫端 fail-closed。
    """
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"鎖檔不是普通檔案,拒絕使用:{path}")
        if os.name == "nt":
            attrs = getattr(os.lstat(str(path)), "st_file_attributes", 0)
            if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise OSError(f"鎖檔是 reparse point(符號連結/junction),拒絕使用:{path}")
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "r+", encoding="utf-8", errors="replace")
