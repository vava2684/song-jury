# -*- coding: utf-8 -*-
"""使用者全域狀態目錄 —— 工作鎖與 Gemini 冷卻狀態的唯一位置。

⛔ 不放在工具資料夾(BASE)底下:電腦上同時存在新舊兩份 ZIP、App 與 CLI
   來自不同副本時,各副本的 BASE 不同 → 各鎖各的,互斥保證只在「同一份副本」
   內成立(Codex R10 實測:兩份副本對同一首歌雙雙進 _job_lock,同一把金鑰的
   兩個 key_lease 也都回 ok)。放在使用者層級的固定目錄,同一台機器上的
   **所有副本**才共用同一組鎖與冷卻。
⚠️ 誠實的邊界:跨「機器」(兩台電腦評同一個網路磁碟上的音檔)仍不在保證
   範圍 —— 檔案鎖在 NFS/SMB 上本就不可靠。中間產物寫在歌旁邊的設計未變,
   跨機同評同一檔仍會互踩;不要那樣用。
環境變數 SONG_JURY_STATE_DIR 可覆寫(測試/沙箱用)。
"""
import os
import sys
from pathlib import Path


def state_root() -> Path:
    """回使用者全域狀態目錄(會建立)。每次呼叫都重讀環境變數,測試才能覆寫。"""
    env = os.environ.get("SONG_JURY_STATE_DIR")
    if env:
        d = Path(env)
    elif sys.platform == "win32":
        d = Path(os.environ.get("LOCALAPPDATA")
                 or (Path.home() / "AppData" / "Local")) / "song-jury"
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        d = (Path(xdg) if xdg else Path.home() / ".local" / "state") / "song-jury"
    d.mkdir(parents=True, exist_ok=True)
    return d
