# -*- coding: utf-8 -*-
"""暫存目錄的共用契約:**確實刪掉**,以及**證明還有人在用**。

⛔ 為什麼要獨立成一支(而不是各自寫一份):
   這套系統裡有四種暫存,每一種裡面都可能是「一整份音訊/分軌/歌詞」——
   評測快照、上傳補正檔、song_scorer 的分軌、網頁請求的產物、分軌快取的 .tmp_。
   R24~R28 每一輪都在同一個地方跌倒:**刪不掉卻當作刪掉了**、或**還在用卻被刪掉**。
   規則只寫一次,大家共用,才不會有人漏掉哪一半。

兩個原語:
  · force_rmtree() —— 唯讀先 chmod、短暫重試、最後**回報還在的路徑**(不吞)。
  · lease()/try_take() —— 用 OS 鎖(Windows msvcrt / POSIX flock)證明「還有人在用」。
    ⛔ 用時間判死是錯的(Codex R28-P2-1 實測):時鐘往前跳、機器休眠、或工作本來
       就比預設放棄期長(SONG_JURY_WEB_TIMEOUT 允許到 24 小時),都會把**還活著**
       的工作誤判成孤兒。鎖拿得到 = 持有者已經不在了,那才是可靠的判準。
"""
import contextlib
import os
import shutil
import stat
import sys
import time
from pathlib import Path


def force_rmtree(d, retries: int = 3, pause: float = 0.25) -> str:
    """盡力刪掉整個目錄;回「還在的路徑」(空字串 = 真的乾淨了)。

    🔴 Codex R24-P1-1:`shutil.rmtree(d, ignore_errors=True)` 在 Windows 上兩種
       很普通的情況都會失敗,而那個旗標把失敗**整個吞掉**:
       ① 來源是唯讀檔(copy2 會把唯讀屬性一起複製過來);
       ② 檔案還被某個 handle 開著(防毒、索引、剛結束的子程序)。
    """
    d = Path(d)

    def _fix_and_retry(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass                       # 這一輪失敗沒關係,外層還會再試

    kw = ({"onexc": _fix_and_retry} if sys.version_info >= (3, 12)
          else {"onerror": _fix_and_retry})
    for _ in range(max(1, retries)):
        if not d.exists():
            return ""
        try:
            shutil.rmtree(d, **kw)
        except OSError:
            pass
        if not d.exists():
            return ""
        time.sleep(pause)
    return str(d) if d.exists() else ""


def _lock_fd(fd) -> bool:
    """對已開啟的 fd 拿**非阻塞**獨占鎖;拿到回 True。"""
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_fd(fd) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def take(path) -> object:
    """試著取得 path 這個鎖檔;拿到回一個「持有物」,拿不到回 None。

    ⚠️ 拿到 = **原本沒有人持有**(持有者已經結束或崩潰)。
    ⚠️ 拿不到 = 有人正在用 —— 那個目錄絕對不可以回收。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        f = open(path, "a+b")
    except OSError:
        return None
    try:
        f.seek(0)
        if not _lock_fd(f.fileno()):
            f.close()
            return None
    except OSError:
        f.close()
        return None
    return f


def release(holder) -> None:
    """放掉 take() 拿到的持有物。"""
    if holder is None:
        return
    try:
        _unlock_fd(holder.fileno())
    finally:
        try:
            holder.close()
        except OSError:
            pass


@contextlib.contextmanager
def lease(path):
    """context manager 版:with lease(p) as ok: ... (ok=False 表示別人正在用)"""
    h = take(path)
    try:
        yield h is not None
    finally:
        release(h)


def is_busy(path) -> bool:
    """那個鎖檔現在有沒有人持有(拿得到鎖 → 沒人用 → False)。"""
    h = take(path)
    if h is None:
        return True
    release(h)
    return False
