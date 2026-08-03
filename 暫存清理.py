# -*- coding: utf-8 -*-
"""暫存目錄的共用契約:**確實刪掉**,以及**證明還有人在用**。

⛔ 為什麼要獨立成一支(而不是各自寫一份):
   這套系統裡有五種暫存,每一種裡面都可能是「一整份音訊/分軌/歌詞」——
   評測快照、上傳補正檔、song_scorer 的分軌、網頁請求的產物、分軌快取的 .tmp_。
   R24~R29 每一輪都在同一個地方跌倒:**刪不掉卻當作刪掉了**、或**還在用卻被刪掉**。
   規則只寫一次,大家共用,才不會有人漏掉哪一半。

兩個原語:
  · force_rmtree() —— 唯讀先 chmod、短暫重試、最後**回報還在的路徑**(不吞)。
    ⛔ 判「還在不在」一律用 lexists:`exists()` 會跟隨 symlink,dangling symlink
       的 exists() 是 False —— 那會讓 helper 宣告「乾淨了」但目錄項還在
       (Codex R29-P2-2 在 WSL 實證)。
  · take()/take_ex() —— 用 OS 鎖(Windows msvcrt / POSIX flock)證明「還有人在用」。
    ⛔ 用時間判死是錯的(Codex R28-P2-1):時鐘往前跳、機器休眠、或工作本來就比
       預設放棄期長,都會把**還活著**的工作誤判成孤兒。
    ⛔ 開鎖檔一律走 狀態目錄.safe_open_lock(Codex R29-P1-3):普通的
       `open(path, "a+b")` 會跟隨 symlink、也不驗 hardlink —— 攻擊者預植
       `x.lock -> victim` 就能讓我們對別的 inode 上鎖(甚至建出空檔)。
       這個專案早就有一份合格的開檔原語,不可以另外寫一份比較弱的。
    ⛔ 「拿不到」有兩種完全不同的意思:**有人在用**(busy)與**鎖後端壞掉**
       (backend_error,例如某些網路磁碟不支援 flock)。混在一起的話,寫入端
       會在沒有租約的情況下開始寫(Codex R29-P2-1)。三態分開回報。
"""
import contextlib
import os
import json
import shutil
import stat
import sys
import time
from pathlib import Path

from 狀態目錄 import safe_open_lock

# take_ex() 的三種結果
ACQUIRED = "acquired"        # 拿到了(原本沒有人持有)
BUSY = "busy"                # 有人正在用
BACKEND_ERROR = "backend_error"   # 開檔/上鎖機制本身壞掉(權限、不支援、被預植…)


def _is_link(p: Path) -> bool:
    """symlink 或 Windows 的 reparse point(junction)。"""
    try:
        if p.is_symlink():
            return True
        attrs = getattr(os.lstat(str(p)), "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return False


def force_rmtree(d, retries: int = 3, pause: float = 0.25) -> str:
    """盡力刪掉整個目錄;回「還在的路徑」(空字串 = 真的乾淨了)。

    🔴 Codex R24-P1-1:`shutil.rmtree(d, ignore_errors=True)` 在 Windows 上兩種
       很普通的情況都會失敗,而那個旗標把失敗**整個吞掉**:
       ① 來源是唯讀檔(copy2 會把唯讀屬性一起複製過來);
       ② 檔案還被某個 handle 開著(防毒、索引、剛結束的子程序)。
    🔴 Codex R29-P2-2:用 `exists()` 判乾淨會漏掉 dangling symlink ——
       它的 exists() 是 False,於是回報「乾淨」但目錄項還在。
    ⛔ 目標本身是連結時**只刪連結**,絕不沿著它遞迴(那會刪到別人的東西)。
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
        if not os.path.lexists(d):
            return ""
        try:
            if _is_link(d):
                # ⛔ 連結只 unlink 本身(POSIX 的 symlink-to-dir 要用 unlink,
                #    Windows 的 junction 要用 rmdir)
                try:
                    os.unlink(d)
                except (OSError, IsADirectoryError, PermissionError):
                    os.rmdir(d)
            else:
                shutil.rmtree(d, **kw)
        except OSError:
            pass
        if not os.path.lexists(d):
            return ""
        time.sleep(pause)
    return str(d) if os.path.lexists(d) else ""


def _lock_fd(fd) -> bool:
    """對已開啟的 fd 拿**非阻塞**獨占鎖;拿到回 True,拿不到回 False。"""
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return True


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


# ⛔ 「哪些暫存沒清乾淨」是**機器要讀的東西**,不可以叫下游去切人話
#    (Codex R30-P2-1 實測:App 與批次各自 split 中文字串,結果種類整個不見、
#     說明文字還被併進「路徑」——store 裡存的是
#     `C:/Temp/stems-left(裡面是一整份分軌,請手動刪掉)`,那不是一個路徑)。
#    → 產出端發布**一行、固定前綴、有 schema** 的記錄;人話只給終端機看。
CLEANUP_TAG = "##SONG_JURY_CLEANUP##"


def emit_dirty(items, stream=None):
    """發布一次清理記錄。items = [(kind, path), ...]。"""
    rec = {"cleanup_dirty": [{"kind": str(k), "path": str(p)} for k, p in items]}
    print(CLEANUP_TAG + json.dumps(rec, ensure_ascii=False),
          file=sys.stdout if stream is None else stream, flush=True)


def parse_dirty(text):
    """從子程序的輸出取出那筆記錄 → [{"kind":…, "path":…}, …]。

    ⛔ 沒有可用記錄時回 **None**(不是 [])——呼叫端必須 fail-closed:
       「解析不到」跟「乾淨」是兩件事,混在一起就會把一整份分軌講成沒事。
    ⚠️ 取**最後一筆**:子程序自己也會發一筆,最外層那筆才是完整清單。
    """
    found = None
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln.startswith(CLEANUP_TAG):
            continue
        try:
            items = json.loads(ln[len(CLEANUP_TAG):])["cleanup_dirty"]
        except (ValueError, KeyError, TypeError):
            continue                      # 半行/被截斷 → 當成沒有記錄
        if not isinstance(items, list):
            continue
        got = []
        for it in items:
            if not isinstance(it, dict):
                continue
            k, p = it.get("kind"), it.get("path")
            if isinstance(k, str) and isinstance(p, str) and p:
                got.append({"kind": k, "path": p})
        found = got
    return found


def take_ex(path):
    """試著取得 path 這個鎖檔。回 (持有物或 None, 狀態, 說明)。

    狀態:ACQUIRED(拿到了)/ BUSY(有人在用)/ BACKEND_ERROR(機制本身壞掉)。
    ⛔ 呼叫端**不可以**把 BACKEND_ERROR 當成「沒人用」:那等於在沒有互斥保護的
       情況下開始寫(Codex R29-P2-1)。寫入端遇到 BUSY 或 BACKEND_ERROR 都要停。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return None, BACKEND_ERROR, f"{type(e).__name__}: {e}"
    try:
        # ⛔ 只用專案唯一的安全開檔原語:O_NOFOLLOW、普通檔、st_nlink==1、
        #    owner、Windows reparse point —— 一份真理(Codex R29-P1-3)。
        f = safe_open_lock(path)
    except OSError as e:
        return None, BACKEND_ERROR, f"{type(e).__name__}: {e}"
    try:
        f.seek(0)
        _lock_fd(f.fileno())
    except OSError as e:
        f.close()
        # ⚠️ 這裡分不出「被別人鎖住」與「這個檔案系統不支援鎖」——
        #    POSIX 用 EWOULDBLOCK/EACCES 區分,Windows 的 msvcrt 只給 EDEADLOCK。
        #    保守起見:認得的「已被鎖住」才算 BUSY,其餘一律 BACKEND_ERROR。
        import errno
        busy_errnos = {errno.EACCES, errno.EAGAIN, getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
                       getattr(errno, "EDEADLOCK", 36), getattr(errno, "EDEADLK", 35)}
        state = BUSY if e.errno in busy_errnos else BACKEND_ERROR
        return None, state, f"{type(e).__name__}: {e}"
    except BaseException:
        f.close()
        raise
    return f, ACQUIRED, ""


def take(path):
    """相容介面:拿到回持有物,拿不到(busy 或壞掉)回 None。"""
    return take_ex(path)[0]


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
    """context manager 版:with lease(p) as ok: ... (ok=False 表示不可以動它)"""
    h, state, _why = take_ex(path)
    try:
        yield state == ACQUIRED
    finally:
        release(h)


def is_busy(path) -> bool:
    """那個鎖檔現在可不可以動 —— ⚠️ **保守**:機制壞掉也算「不可以動」。

    回收端專用:拿得到鎖(ACQUIRED)才回 False。
    """
    h, state, _why = take_ex(path)
    if state == ACQUIRED:
        release(h)
        return False
    return True
