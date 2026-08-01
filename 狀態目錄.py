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


def _ensure_private_dir(d: Path, what: str) -> Path:
    """確保 d 是「私人的普通資料夾」:拒 symlink/junction、建立當下就 0700、驗擁有者。

    ⛔ symlink 一律拒絕(Codex R12 探針三連):
       · `_locks` 被換成指向外部的 symlink → 鎖被導出互斥域;
       · SONG_JURY_STATE_DIR 指向 symlink → chmod 會打到別人的目錄;
       · mkdir→chmod 之間的 0777 窗口可被塞 symlink → 用 mkdir(mode=0o700)
         讓目錄**從建立那一刻**就是 0700,窗口不存在。
    ⚠️ 只驗最後一層,不驗祖先(macOS 的 /tmp 本身就是 symlink,驗祖先會誤殺)。"""
    if d.is_symlink():
        raise StateDirError(f"{what} 是符號連結,拒絕使用:{d} —— 鎖/狀態可被導向外部目錄")
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)   # mode 只作用在最後一層
    except FileExistsError:
        raise StateDirError(f"{what} 的位置被一個普通檔案佔住:{d} —— 請移走那個檔案")
    except OSError as e:
        raise StateDirError(f"{what} 建不起來:{d}({type(e).__name__}: {e})")
    if d.is_symlink() or not d.is_dir():
        raise StateDirError(f"{what} 不是普通資料夾:{d}")
    if os.name != "nt":
        st = os.lstat(d)
        if st.st_uid != os.getuid():
            raise StateDirError(f"{what} 不是目前使用者擁有:{d} —— 別把它指向共享目錄")
        if stat.S_IMODE(st.st_mode) != 0o700:
            try:
                os.chmod(d, 0o700)     # 既有目錄補鎖(新建的已經是 0700,不會走到這)
            except OSError as e:
                raise StateDirError(f"{what} 權限鎖不下來:{d}({e})")
    else:
        attrs = getattr(os.lstat(str(d)), "st_file_attributes", 0)
        if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise StateDirError(f"{what} 是 reparse point(junction/symlink),拒絕使用:{d}")
    return d


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
    return _ensure_private_dir(d, "狀態目錄")


def locks_dir() -> Path:
    """工作鎖目錄:狀態目錄底下的 _locks,同樣「私人普通資料夾」標準(Codex R12:
    `_locks` 本身被換成 symlink 時,鎖檔會被導出互斥域 —— 目錄也要驗,不只鎖檔)。"""
    return _ensure_private_dir(state_root() / "_locks", "鎖目錄 _locks")


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
        # ⛔ O_NOFOLLOW 擋不住 hardlink:把 job_<hash>.lock 硬連結到 victim.txt,
        #    開起來 fstat 看到的仍是「普通檔案」,寫 pid 就把 victim 蓋了
        #    (Codex R12 實測 st_nlink=2、victim 變成 pid=…)。鎖檔永不刪、
        #    也絕不該有第二個名字 → link count 必須是 1。
        if st.st_nlink != 1:
            raise OSError(f"鎖檔有 {st.st_nlink} 個硬連結,拒絕使用:{path}"
                          f"(寫入會蓋到別的檔案)")
        if os.name != "nt":
            if st.st_uid != os.getuid():
                raise OSError(f"鎖檔不是目前使用者擁有,拒絕使用:{path}")
        else:
            attrs = getattr(os.lstat(str(path)), "st_file_attributes", 0)
            if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise OSError(f"鎖檔是 reparse point(符號連結/junction),拒絕使用:{path}")
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "r+", encoding="utf-8", errors="replace")
