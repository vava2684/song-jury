# -*- coding: utf-8 -*-
"""環境變數當設定用時的唯一入口 —— ⛔ 不可以直接 float(os.environ[...])。

🔴 Codex R18-3 實測(SONG_JURY_DEMUCS_PROBE_TIMEOUT):
   · `abc` → 模組載入時就 `ValueError: could not convert string to float`
   · `nan` → 一路傳到 subprocess.run(timeout=nan) 才 `ValueError: Invalid value NaN`
   · `inf` → `OverflowError: timestamp out of range for platform time_t`
   · `0` / `-1` → 被當成「體檢逾時」,而不是「你設定打錯了」
   而這些未捕捉例外的退出碼都是 1,安裝器又把 1 解讀成「缺套件」——
   於是**一個設定 typo 會讓兩個平台都叫使用者去重裝 requirements**,
   正好把 R17-1 剛修好的「不要把非缺套件說成缺套件」整條繞回來。

規矩:設定值壞掉是**設定錯誤**,要當場、明確、可讀地講出來,而且跟其他失敗分開。
"""
import math
import os


class ConfigError(ValueError):
    """設定值不合法 —— 訊息要能讓人直接照著改,不需要看原始碼。"""


def finite_number(name: str, raw) -> float:
    """CLI 參數版:必須是**有限**數字,沒得預設 —— 缺值/亂填都要當場講清楚。

    🔴 Codex R22-P2-2 實測:`驗證報告.py --newer-than nan` 會讓
       `mtime <= nan` 永遠為 false,於是一份一天前的舊報告照樣印
       「VERIFY_OK …本輪新產物」。`-inf` 同理。⛔ 那是**證據標籤**被繞過,
       不是顯示問題:自動化就是靠這行字判斷「這份是這次跑出來的」。
    ⚠️ 這裡不限正負(mtime 門檻可以是任何 epoch),只擋 NaN/Infinity 與非數字。"""
    if raw is None:
        raise ConfigError(f"{name} 少了值 —— 請填 epoch 秒數(例如 `--{name} 1785600000`)")
    txt = str(raw).strip()
    try:
        val = float(txt)
    except (TypeError, ValueError):
        raise ConfigError(f"{name}={txt!r} 不是數字 —— 請填 epoch 秒數")
    if not math.isfinite(val):
        raise ConfigError(f"{name}={txt!r} 不是有限的數字"
                          f"(NaN/Infinity 會讓所有比較都不成立,等於沒檢查)")
    return val


def positive_finite(name: str, default: float, lo: float = 0.0,
                    hi: float = 86400.0, env=None) -> float:
    """讀一個「秒數」設定:必須是有限、> lo、<= hi 的數字。

    ⛔ 一定要擋 NaN/Infinity:它們是合法的 float(),卻會在很後面的
       subprocess/time 層才炸,錯誤訊息離現場十萬八千里。
    ⚠️ 沒設(或空字串)就回 default —— 那不是錯誤,是「沒特別指定」。"""
    raw = (env if env is not None else os.environ).get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    txt = str(raw).strip()
    try:
        val = float(txt)
    except (TypeError, ValueError):
        raise ConfigError(f"{name}={txt!r} 不是數字 —— 請填秒數(例如 900)")
    if not math.isfinite(val):
        raise ConfigError(f"{name}={txt!r} 不是有限的數字(NaN/Infinity 不能當秒數)")
    if val <= lo:
        raise ConfigError(f"{name}={txt!r} 要大於 {lo:g} —— 0 或負數不是「不限時間」")
    if val > hi:
        raise ConfigError(f"{name}={txt!r} 太大(上限 {hi:g} 秒)—— 這通常是打錯位數")
    return val
