# -*- coding: utf-8 -*-
"""金鑰政策 —— 「這次評測到底會用哪幾把 Gemini 金鑰」的**唯一真理來源**。

⛔ 為什麼要獨立成模組(Codex R13 兩條):
   · 驗證器(金鑰驗證.py)只讀 .env、執行期(Gemini曲評.py)還讀 process env
     → `-CheckOnly` 驗的是 A,真正評歌用的是 B。「驗過了」變成假的。
   · 產線隔離只寫在註解裡,程式一行都沒執行 —— 別條產線的金鑰只要被
     export 到環境就會被借走,把別人的付費額度打光(這個專案真的發生過)。
   兩邊改成呼叫同一個 effective_keys(),驗的就一定是跑的。

⭐ 產線隔離(硬規則,不是註解):
   1. 專用變數 SONG_JURY_GEMINI_API_KEYS 最優先 —— 明確指名「這是給 song-jury 的」。
   2. process env 裡的**一般** GEMINI_API_KEY(S) **不採用**:那多半是別條產線
      (網站後端、其他 bot)export 的,借了就是拿別人的額度。要用請寫進本專案 .env。
   3. 拒絕名單 SONG_JURY_DENY_KEY_SHA256(完整金鑰的 SHA-256,逗號分隔)——
      比對整把金鑰的雜湊,fail-closed。末四碼比對太弱,不採用。
      (雜湊不可逆,填進設定/CI 不會外洩金鑰本身。)

只用標準庫:安裝器在還沒建 venv 的機器上也要能 import 它。
"""
import hashlib
import os
from pathlib import Path

PRIMARY_ENV = "SONG_JURY_GEMINI_API_KEYS"     # 專用變數:process env 與 .env 都認
GENERIC_ENVS = ("GEMINI_API_KEYS", "GEMINI_API_KEY")   # 一般變數:只認 .env 裡的
DENY_ENV = "SONG_JURY_DENY_KEY_SHA256"


def key_fingerprint(key: str) -> str:
    """完整金鑰的 SHA-256(小寫 hex)。填進拒絕名單用;不可逆,不外洩金鑰。"""
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def looks_placeholder(k: str) -> bool:
    """.env.example 的佔位字串(含中文、太短、your/xxx 開頭)不是真金鑰。"""
    if any(ord(c) > 127 for c in k):
        return True
    if len(k) < 20:
        return True
    return k.lower().startswith(("your", "xxx", "todo", "<"))


def parse_env_file(path) -> dict:
    """讀 .env(容忍 BOM、`KEY = value` 的空白、引號、# 註解)。"""
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    try:
        text = p.read_text(encoding="utf-8-sig")
    except Exception:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # ⛔ 一定要 strip 鍵名:`GEMINI_API_KEYS = xxx` 這種寫法執行期讀得到、
        #    驗證器讀不到,兩邊就會對不同的金鑰做結論(Codex R13)。
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def deny_fingerprints(env_file=None) -> set:
    """拒絕名單(SHA-256 小寫 hex)。process env 與 .env 都可設,兩邊聯集。"""
    vals = [os.environ.get(DENY_ENV, "")]
    if env_file is not None:
        vals.append(parse_env_file(env_file).get(DENY_ENV, ""))
    out = set()
    for v in vals:
        out |= {x.strip().lower() for x in (v or "").split(",") if x.strip()}
    return out


def effective_keys(env_file, *, verbose=False):
    """回 (keys, notes) —— **執行期真正會用的那組金鑰**,順序即嘗試順序。

    notes 是給人看的說明(哪些被擋、為什麼),驗證器與執行期印同一份。
    """
    envf = parse_env_file(env_file)
    notes = []
    raw = []

    # ① 專用變數:process env 優先,其次 .env
    for src, val in (("環境變數", os.environ.get(PRIMARY_ENV)), (".env", envf.get(PRIMARY_ENV))):
        if val and val.strip():
            raw = [(k.strip(), f"{src} {PRIMARY_ENV}") for k in val.split(",")]
            break

    # ② 沒有專用變數 → 用 .env 裡的一般變數(GEMINI_API_KEYS 優先於單把;
    #    ⛔ 只在多把缺席時才吃單把,不是兩者相加 —— 相加會把沒被驗過的偷渡進來)
    if not raw:
        for name in GENERIC_ENVS:
            val = envf.get(name)
            if val and val.strip():
                raw = [(k.strip(), f".env {name}") for k in val.split(",")]
                break

    # ③ process env 裡的一般變數:**明確不採用**,但要講出來(否則使用者會以為它生效了)
    for name in GENERIC_ENVS:
        if os.environ.get(name) and not os.environ.get(PRIMARY_ENV):
            notes.append(f"⛔ 環境變數 {name} 不被採用(那通常是別條產線的金鑰,借用會吃掉別人的額度)。"
                         f"要給 song-jury 用,請寫進本專案 .env,或改用 {PRIMARY_ENV}。")

    denied = deny_fingerprints(env_file)
    seen, keys = set(), []
    n_placeholder = n_denied = 0
    for k, src in raw:
        if not k or k in seen:
            continue
        seen.add(k)
        if looks_placeholder(k):
            n_placeholder += 1
            continue
        if key_fingerprint(k) in denied:
            n_denied += 1
            notes.append(f"⛔ 有一把金鑰在拒絕名單裡({src},指紋 …{key_fingerprint(k)[:8]}),已擋下不使用。")
            continue
        keys.append(k)
    if n_placeholder and not keys:
        notes.append(f"⚠ .env 裡只有 {n_placeholder} 個佔位字串,不是真金鑰 —— "
                     f"請填 https://aistudio.google.com/apikey 申請到的值。")
    if verbose:
        for n in notes:
            print(n)
    return keys, notes
