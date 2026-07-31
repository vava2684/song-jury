#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Gemini 曲評(Evidence-based Music Review)— 滿血版第二關新增元件

把網頁版(song-jury-hf/app.py 的 gemini_music_eval)那套「聽真音檔 → 6 維曲評」原封搬到本機,
再補上本機才需要的東西:多金鑰輪替 + 冷卻狀態 + 誠實降級。

⭐ 這個元件的獨特價值 = 抓 ML 數字看不出來的東西
   SongEval / Audiobox 給的是「這首歌大概多好聽」的統計量,說不出「副歌第 1:12 那把破音吉他
   是不是真的把能量抬起來了」。Gemini 是唯一會引時間碼、引具體樂器、引實際唱法的評審 ——
   prompt 裡那一大段「⛔嚴禁『旋律動聽』這種空話」就是這份價值的來源,一個字都不能省。

⚠️ 設計原則(2026-07-19 使用者定調):**每個指標都要有分數,權重後定**
   本檔對 M1~M6 每一維都吐 raw(0-10,模型原生尺度)與 score(0-100,線性放大),
   ⛔ 但不自己發明「本元件總分」。Gemini 自報的曲總分照收但明確標成 gemini_reported_total
   (那是模型自己講的,不是我加權出來的),要不要進總分、佔多少,等八家對決決定。

⚠️ 門檻與換算全部是啟發式起手值,已寫進輸出 JSON 的 thresholds 供日後校準。

用法:
    .venv\\Scripts\\python.exe Gemini曲評.py <音檔> [--lang zh|en|ja|ko] [--json 輸出.json]
                                              [--dry-run] [--ignore-cooldown] [--timeout 200]

金鑰(放專案根目錄的 .env,已被 .gitignore 擋住):
    GEMINI_API_KEYS=key1,key2,key3     ← 多把,會依錯誤類型輪替
    GEMINI_API_KEY=key1                ← 只有一把時的相容寫法
沒有 .env 也能跑:直接吐 degraded:true + 明確原因,不會炸掉整條產線。

⚠️ 本檔不開任何子行程(純 HTTPS 呼叫),所以沒有 CREATE_NO_WINDOW 的問題;
   若日後要加子行程,記得照 評審團.py 的慣例補 creationflags。
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")   # ⚠️ stderr 也要,否則錯誤訊息在 cp950 主控台變亂碼
except Exception:
    pass

import requests

BASE = Path(__file__).parent.resolve()
ENV_FILE = BASE / ".env"
STATE_FILE = BASE / ".gemini_key_state.json"

# ── 模型與可調門檻(啟發式起手值,非論文數據)────────────────────────────
GEMINI_MUSIC_MODEL = "gemini-3.5-flash"   # 網頁版實測:2.5-flash 對新金鑰停用,3.5-flash 才吃音檔
# ⛔ 她裁定(2026-07-25):開源 SKILL 品質至上——永遠用最好的模型,掛了就排隊等復活重跑,
#    絕不降級換 flash-lite(儀器版本混用會破壞可比性)。503 時誠實 degraded,等模型回來補跑。
TEMPERATURE = 0.4          # 網頁版產線值:低溫才不會每次評語漂掉,但仍留一點語言彈性
MAX_OUTPUT_TOKENS = 4000   # 網頁版產線值(6 維評語 + SCORES 行綽綽有餘)
ATTEMPTS_PER_KEY = 3       # 同一把金鑰的重試次數(網路抖動/429 短窗)
MAX_SLEEP_SEC = 65         # 429 就算 Google 說要等更久,也不在單把金鑰上耗超過這個秒數 → 換金鑰比較快
COOLDOWN_RATE_SEC = 3600   # 429 且等不起 → 這把金鑰冷卻 1 小時(免費額度多半是小時/日窗,1h 是保守猜)
COOLDOWN_DEAD_SEC = 86400  # 金鑰無效/被拒 → 冷卻 24 小時(這種通常要人去 console 修,不是等就會好)
MAX_INLINE_B64_MB = 19.0   # Gemini inline_data 走 generateContent 的請求上限約 20MB,base64 會膨脹 4/3
                           # → 原始檔 >~14MB 就危險;超過這條線誠實說「要改走 Files API(本檔未實作)」

# 每維 0-10 → 0-100 的換算:目前就是線性 ×10。
# 之所以不做非線性拉伸,是因為還沒有校準資料證明 Gemini 的 7 分跟 SongEval 的 3.5/5 對得上;
# 硬套曲線只會製造假精確。等八家對決有校準集再說。
RAW_TO_SCORE = 10.0

_GM_LANG = {"zh": "繁體中文", "en": "English", "ja": "日本語", "ko": "한국어"}

# 維度中文標籤(給主控台摘要用;JSON 內也附一份,下游不必再硬記)
DIM_LABELS = {
    "M1": "編曲結構與能量弧線", "M2": "旋律與記憶點", "M3": "節奏與律動",
    "M4": "配器與音色", "M5": "人聲表現", "M6": "曲風執行與創新",
}

# ⭐ 網頁版的 mime 是寫死 "audio/mpeg" —— 餵 wav/flac 時等於騙 Google 說這是 mp3。
#    多數時候 Gemini 靠 sniffing 還是讀得懂,但這是實打實的 bug,本地版用副檔名查表修掉。
MIME_BY_SUFFIX = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".aiff": "audio/aiff", ".aif": "audio/aiff",
}


# ══════════════════════════════════════════════════════════════════════
#  金鑰管理:載入 / 指紋 / 冷卻狀態
#  ⛔ 鐵則:金鑰值不進 log、不進 JSON、不進例外訊息,連截斷都不印。
#     對外一律只用 sha256 前 8 碼當「指紋」,人可以對照哪把壞了,但復原不了金鑰。
# ══════════════════════════════════════════════════════════════════════
def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _parse_env_file(path: Path) -> dict:
    """極簡 .env 解析(不裝 python-dotenv,少一個相依)。支援 # 註解與 KEY=VALUE、可有引號。"""
    out = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


# song-jury 專用的 Gemini 金鑰檔。
#
# ⛔⛔ 2026-07-20 移除 vava-gemini.env(她:「你把 VAVA 的用炸了」)
#     原本它被放在池子最後當「最後備援」,想法是前面用完才退到它。
#     實際問題:**那是個沒有煞車的備援** —— 本機 SKILL 一次批次評測會打幾十次
#     (2026-07-20 跑了 29 首 SUNO + 10 首得獎歌 = 39 次),前面兩把一旦撞額度,
#     整批就會安靜地灌進海巡新聞那條產線的金鑰,把她 0800/1800 的學習任務餓死。
#     ⛔ 更糟的是**當時沒有記錄用了哪把**,事後完全無法查證有沒有燒到她的 ——
#        「查不出來」本身就是不可接受的。
#
# ⭐ 規則:song-jury 只准用 song-jury 自己的金鑰。額度不夠就讓它降級(degraded),
#    ⛔ 絕不准借用別條產線的金鑰 —— 借到就是把別人的服務弄掛。
#    要加金鑰請加進 評分\.env 的 GEMINI_API_KEYS(逗號分隔多把)。
# ⛔⛔ 2026-07-20 二度收緊:連 vava-gemini-songjury.env 也移除。
#     查證結果(她指出後才發現):
#       vava-gemini-songjury.env 的金鑰(末四碼 zxhg)與
#       線上網站 song-jury-hf\.env 的 GEMINI_API_KEY(末四碼 ubA)**是同一個帳號**。
#     → 同帳號共用同一份額度,所以本機批次評測(一次幾十次呼叫)吃的是
#       **線上 meowfullhouse.com 付費服務的額度**,被餓到的是網站的真實使用者。
#     ⭐ 三條產線的金鑰必須完全隔離,不准互相借:
#         線上網站   = ubA / zxhg(同帳號,付費)
#         VAVA 海巡  = BIOw(~/.config/vava-gemini.env)
#         本機 SKILL = 評分\.env 的 GEMINI_API_KEYS(她另外一把)
#     額度不夠就讓它 degraded,⛔ 絕不准往別條線借。要加請加進 評分\.env 的 GEMINI_API_KEYS。
EXTRA_KEY_FILES = []


def load_keys() -> list:
    """回金鑰清單(去重、保序)。優先序:環境變數 > 本機 .env > WSL 的既有金鑰檔;
    GEMINI_API_KEYS(多把) > GEMINI_API_KEY(單把)。"""
    envf = _parse_env_file(ENV_FILE)

    def _get(name):
        return (os.environ.get(name) or envf.get(name) or "").strip()

    raw = _get("GEMINI_API_KEYS")
    keys = [k.strip() for k in raw.split(",")] if raw else []
    single = _get("GEMINI_API_KEY")
    if single:
        keys.append(single)
    for p in EXTRA_KEY_FILES:                     # 撿 WSL 那邊既有的,當後補
        d = _parse_env_file(p)
        for name in ("GEMINI_API_KEYS", "GEMINI_API_KEY"):
            v = (d.get(name) or "").strip()
            if v:
                keys.extend(k.strip() for k in v.split(","))
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict):
    """冷卻狀態寫本機小檔(已列入 .gitignore)。寫不進去不算致命 —— 大不了下次再撞一次死金鑰。"""
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def cool_down(state: dict, key: str, seconds: float, reason: str):
    state[_fingerprint(key)] = {
        "cooldown_until": time.time() + seconds,
        "reason": reason,
        "marked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_state(state)


def is_cooling(state: dict, key: str):
    """回 (是否冷卻中, 還剩幾秒, 原因)。"""
    rec = state.get(_fingerprint(key))
    if not rec:
        return False, 0, ""
    left = rec.get("cooldown_until", 0) - time.time()
    return (left > 0), max(0, int(left)), rec.get("reason", "")


# ══════════════════════════════════════════════════════════════════════
#  Prompt —— 一字不改從網頁版搬過來
#  ⚠️ 這段是整個元件輸出品質的來源(逼它引時間碼/樂器/唱法、擋掉「旋律動聽」的空話、
#     並且明講暗黑/金屬曲風不准因為「不悅耳」扣分)。要改請先跑 A/B,別憑感覺潤稿。
# ══════════════════════════════════════════════════════════════════════
def gemini_music_prompt(out_lang="zh") -> str:
    lang = _GM_LANG.get(out_lang, "繁體中文")
    return f"""你是資深音樂人評審,聽這段音檔評「曲/音樂」(不評歌詞)的 6 個維度。
⭐你的獨特價值 = 抓出 ML 數字看不出的東西(副歌到底有沒有起來、全曲是否靜態、律動死不死),並糾正「主流/悅耳=好」的偏見 —— 暗黑/金屬/實驗的好,你也要聽得出。做不到的(如音準被 auto-tune 蓋、單聲道無法判空間)誠實標「無法判」,絕不硬掰。

M1 編曲結構與能量弧線:段落推進、build-up/release、副歌是否真的抬升?
M2 旋律與記憶點:主旋律辨識度、hook 有沒有真 payoff、旋律有無發展。
M3 節奏與律動:groove/pocket、律動是否咬合曲風、有無變化。
M4 配器與音色:音色選擇與曲風/情緒是否一致、頻段層次。
M5 人聲表現:音準、氣息、情感;純器樂或修音過度→「無法判」。
M6 曲風執行與創新:是否道地執行所屬曲風、公式罐頭 vs 有新意。
⚠️ 暗黑/哥德/金屬/實驗曲風:負面/沉重/高動態/嘶吼是這些曲風的正確美學,別因「不主流/不悅耳」扣分。

用【{lang}】輸出,嚴格照下列格式。⭐每維評語必須**針對這首歌的具體聽感**(引時間碼/具體樂器/段落/唱法),一句話講到重點;⛔嚴禁「旋律動聽」「編曲豐富」「節奏穩定」這種放諸四海皆準的空話 —— 要讓人一看就知道是在講「這一首」:
曲風：<判定曲風>｜整體：<這首最強點＋最弱點各一句>
M1|<0-10 或 NA>|<這首在 M1 的具體評語>
M2|<0-10 或 NA>|<這首在 M2 的具體評語>
M3|<0-10 或 NA>|<這首在 M3 的具體評語>
M4|<0-10 或 NA>|<這首在 M4 的具體評語>
M5|<0-10 或 NA>|<這首在 M5 的具體評語>
M6|<0-10 或 NA>|<這首在 M6 的具體評語>
⭐最後【務必】另起一行給機器可解析分數(缺的維度給 NA):
===SCORES=== M1:x.x M2:x.x M3:x.x M4:x.x M5:x.x M6:x.x 曲總分:x.x"""


# ══════════════════════════════════════════════════════════════════════
#  回應解析
# ══════════════════════════════════════════════════════════════════════
def _num(s):
    try:
        return float(s)
    except Exception:
        return None


def parse_review(txt: str) -> dict:
    """把模型回的純文字拆成 scores / notes / 標頭。解析不到的維度給 None,絕不亂猜補值。"""
    # ⭐ 網頁版的 bug:它把「最後一行含 曲總分 或 ===SCORES===」當機器行,
    #    萬一模型在 SCORES 後又補一句散文提到「曲總分」,就會抓到那句散文、整排 M 分數變空。
    #    這裡改成:優先鎖 ===SCORES=== 那行,沒有才退回「最後一行含 曲總分」。
    line = ""
    for ln in txt.splitlines():
        if "===SCORES===" in ln:
            line = ln
            break
    if not line:
        for ln in txt.splitlines():
            if "曲總分" in ln:
                line = ln

    def _clamp10(v):
        """M1~M6 與曲總分都是 0-10 制。⛔ 一定要夾範圍:模型偶爾會吐百分制(M1:99),
        下游 評審團.py 會再 ×10 換成 0-100 → 直接變成 990/100。
        超出範圍不是「高分」是「格式錯了」,判成 None(該項缺席)比讓它污染柱分安全。"""
        if v is None:
            return None
        return v if 0.0 <= v <= 10.0 else None

    scores = {}
    _oor = []
    for n in range(1, 7):
        mm = re.search(rf"M{n}\s*[:：]\s*(\d+(?:\.\d+)?)", line)
        raw = _num(mm.group(1)) if mm else None
        val = _clamp10(raw)
        if raw is not None and val is None:
            _oor.append(f"M{n}={raw}")
        scores[f"M{n}"] = val

    mt = re.search(r"曲總分\s*[:：]\s*(\d+(?:\.\d+)?)", line) or \
         re.search(r"曲總分[^\d]{0,8}(\d+(?:\.\d+)?)", txt)
    _traw = _num(mt.group(1)) if mt else None
    total = _clamp10(_traw)
    if _traw is not None and total is None:
        _oor.append(f"曲總分={_traw}")
    total_source = "model" if total is not None else None
    if total is None:
        # 保底:機器行沒給總分 → 用有分的維度平均(明確標成 mean_of_dims,下游要不要信自己決定)
        vals = [v for v in scores.values() if v is not None]
        if vals:
            total = round(sum(vals) / len(vals), 1)
            total_source = "mean_of_dims"

    # 每維具體評語:格式 M{n}|分數|評語(容忍全形直線)
    notes = {}
    for ln in txt.splitlines():
        nm = re.match(r"\s*M([1-6])\s*[|｜]\s*[^|｜]*[|｜]\s*(.+)", ln)
        if nm and nm.group(2).strip():
            notes[f"M{nm.group(1)}"] = nm.group(2).strip()

    # 標頭那行(曲風｜整體),成績單想單獨引用時很好用
    header = ""
    for ln in txt.splitlines():
        if ln.strip().startswith("曲風"):
            header = ln.strip()
            break

    # ⭐ 也把「NA」判讀出來:模型誠實說無法判 vs 根本沒回這一維,是兩件事,別混為一談
    na_dims = []
    for n in range(1, 7):
        if scores[f"M{n}"] is None and re.search(rf"M{n}\s*[:：]\s*NA", line, re.I):
            na_dims.append(f"M{n}")

    out = {"scores": scores, "notes": notes, "header": header,
           "reported_total": total, "total_source": total_source, "na_dims": na_dims}
    if _oor:
        # 超範圍要留痕,不能靜靜吞掉 —— 它代表模型沒照 0-10 制回答,重評才是正解
        out["out_of_range"] = _oor
        out["out_of_range_note"] = ("模型回了不在 0-10 範圍的分數,已判成缺席("
                                    + "、".join(_oor) + ");建議重跑這一關")
    return out


def build_dimensions(parsed: dict) -> dict:
    """⭐ 每個維度都要有分數:同時給 raw(0-10 模型原生)與 score(0-100)。
    無法判(NA)或沒回 → score 給 None 並標 status,絕不用 0 或平均值頂替(那會偷偷汙染下游加權)。"""
    dims = {}
    for n in range(1, 7):
        k = f"M{n}"
        raw = parsed["scores"].get(k)
        if raw is None:
            status = "not_applicable" if k in parsed["na_dims"] else "missing"
        else:
            status = "ok"
        dims[k] = {
            "label": DIM_LABELS[k],
            "raw_0to10": raw,
            "score": round(raw * RAW_TO_SCORE, 1) if raw is not None else None,
            "status": status,
            "comment": parsed["notes"].get(k, ""),
        }
    return dims


# ══════════════════════════════════════════════════════════════════════
#  主要呼叫:多金鑰、依錯誤類別輪替
# ══════════════════════════════════════════════════════════════════════
def _retry_delay_sec(resp) -> float:
    """讀 Google 建議的重試秒數(兩種寫法都試);讀不到回 30 秒的保守猜。"""
    body = ""
    try:
        body = resp.text or ""
    except Exception:
        pass
    msg = ""
    try:
        msg = resp.json().get("error", {}).get("message", "") or ""
    except Exception:
        msg = body
    m = re.search(r'retry in ([\d.]+)s', msg) or re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', body)
    return float(m.group(1)) if m else 30.0


def _err_message(resp) -> str:
    try:
        return (resp.json().get("error", {}) or {}).get("message", "") or ""
    except Exception:
        return (resp.text or "")[:300]


def call_gemini(audio_path: Path, out_lang: str, timeout: int, ignore_cooldown: bool, verbose=True):
    """回 (parsed_or_None, meta)。meta 一定說得出「這次到底發生什麼」,不做沉默失敗。"""
    meta = {"model": GEMINI_MUSIC_MODEL, "attempts": [], "keys_total": 0,
            "keys_tried": 0, "key_used": None, "degraded_reason": None}

    keys = load_keys()
    meta["keys_total"] = len(keys)
    if not keys:
        meta["degraded_reason"] = (
            f"找不到金鑰:{ENV_FILE} 不存在或沒有 GEMINI_API_KEYS / GEMINI_API_KEY。"
            "請照 .env.example 建一個 .env(該檔已被 .gitignore 擋住,不會進 repo)。")
        return None, meta

    suffix = audio_path.suffix.lower()
    mime = MIME_BY_SUFFIX.get(suffix)
    if not mime:
        meta["degraded_reason"] = f"不支援的音檔格式 {suffix};支援:{', '.join(sorted(MIME_BY_SUFFIX))}"
        return None, meta
    meta["mime_type"] = mime

    try:
        raw = audio_path.read_bytes()
    except Exception as e:
        meta["degraded_reason"] = f"讀不到音檔:{type(e).__name__}: {e}"
        return None, meta

    b64 = base64.b64encode(raw).decode()
    b64_mb = len(b64) / 1024 / 1024
    meta["audio_mb"] = round(len(raw) / 1024 / 1024, 2)
    meta["payload_b64_mb"] = round(b64_mb, 2)
    if b64_mb > MAX_INLINE_B64_MB:
        # ⚖️ 2026-07-25 修法:超大檔(如 WAV)自動轉 320k mp3 只給 Gemini 聽。
        #    依據=編碼實驗定讞(同段 AAC256 vs 128k,模型耳朵 Δ≈0):高位元率轉檔
        #    不影響內容判斷;量測各關仍吃原始檔,只有這一關的「耳朵」聽轉檔。誠實記錄於 meta。
        import subprocess as _sp
        import tempfile as _tf
        _fd, _tmppath = _tf.mkstemp(suffix=".mp3")
        os.close(_fd)                      # ⚠ Windows:fd 不關,ffmpeg 會 WinError 32
        _tmp = Path(_tmppath)
        try:
            _sp.run(["ffmpeg", "-y", "-v", "error", "-i", str(audio_path),
                     "-b:a", "320k", str(_tmp)], check=True, timeout=300,
                    **({"creationflags": 0x08000000} if os.name == "nt" else {}))
            raw = _tmp.read_bytes()
            b64 = base64.b64encode(raw).decode()
            b64_mb = len(b64) / 1024 / 1024
            mime = "audio/mpeg"
            meta["mime_type"] = mime
            meta["transcoded_for_gemini"] = f"320k mp3(原檔 base64 {meta['payload_b64_mb']}MB 超上限)"
            meta["payload_b64_mb"] = round(b64_mb, 2)
        except Exception as e:
            meta["degraded_reason"] = (
                f"音檔太大(base64 後 {meta['payload_b64_mb']:.1f}MB > {MAX_INLINE_B64_MB}MB)"
                f"且自動轉檔失敗({type(e).__name__}: {str(e)[:80]});請確認 ffmpeg 可用。")
            return None, meta
        finally:
            _tmp.unlink(missing_ok=True)
        if b64_mb > MAX_INLINE_B64_MB:
            meta["degraded_reason"] = (
                f"轉 320k 後仍超上限({b64_mb:.1f}MB)——歌太長,需 Files API(未實作)。")
            return None, meta

    body = {"contents": [{"parts": [
        {"text": gemini_music_prompt(out_lang)},
        {"inline_data": {"mime_type": mime, "data": b64}}]}],
        "generationConfig": {"temperature": TEMPERATURE, "maxOutputTokens": MAX_OUTPUT_TOKENS}}

    state = load_state()

    for key in keys:
        fp = _fingerprint(key)
        cooling, left, why = is_cooling(state, key)
        if cooling and not ignore_cooldown:
            # ⭐ 上一輪已經確認這把死了/額滿,這次就別再浪費一次來回(也避免把 429 撞得更兇)
            meta["attempts"].append({"key": fp, "result": "skipped_cooldown",
                                     "cooldown_left_sec": left, "reason": why})
            if verbose:
                print(f"  ⏭ 金鑰 {fp} 冷卻中(剩 {left}s,{why})→ 跳過", flush=True)
            continue

        meta["keys_tried"] += 1
        # ⚠️ 金鑰放 query string 是 Google 官方寫法;所以絕不把 url 印出來或寫進 JSON。
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{GEMINI_MUSIC_MODEL}:generateContent?key={key}")

        for attempt in range(ATTEMPTS_PER_KEY):
            try:
                r = requests.post(url, json=body, timeout=timeout)
            except Exception as e:
                # 網路層抖動:不算金鑰的錯,不冷卻,同一把再試
                meta["attempts"].append({"key": fp, "attempt": attempt + 1,
                                         "result": "network_error", "error": f"{type(e).__name__}"})
                if verbose:
                    print(f"  ! 金鑰 {fp} 第 {attempt+1} 次:連線失敗 {type(e).__name__}", flush=True)
                if attempt < ATTEMPTS_PER_KEY - 1:
                    time.sleep(6)
                    continue
                break

            if r.status_code == 200:
                try:
                    cand = r.json()["candidates"][0]
                    txt = cand["content"]["parts"][0]["text"]
                except Exception:
                    # 200 但沒文字:多半是安全過濾或 MAX_TOKENS 把 thinking 吃光 —— 講明白,別當通用錯誤
                    fr = ""
                    try:
                        fr = (r.json().get("candidates") or [{}])[0].get("finishReason", "")
                    except Exception:
                        pass
                    if not fr:
                        try:
                            fr = str((r.json().get("promptFeedback") or {}).get("blockReason", "") or "無 candidates")
                        except Exception:
                            fr = "回應結構非預期"
                    meta["attempts"].append({"key": fp, "attempt": attempt + 1,
                                             "result": "empty_response", "finish_reason": fr})
                    if verbose:
                        print(f"  ! 金鑰 {fp}:200 但沒拿到文字(finishReason={fr})", flush=True)
                    if attempt < ATTEMPTS_PER_KEY - 1:
                        time.sleep(4)
                        continue
                    break
                meta["attempts"].append({"key": fp, "attempt": attempt + 1, "result": "ok"})
                meta["key_used"] = fp
                # 這把是好的 → 清掉它可能殘留的舊冷卻記錄
                if fp in state:
                    state.pop(fp, None)
                    save_state(state)
                return txt, meta

            if r.status_code == 429:
                delay = _retry_delay_sec(r)
                meta["attempts"].append({"key": fp, "attempt": attempt + 1,
                                         "result": "rate_limited", "suggested_delay_sec": delay})
                if attempt < ATTEMPTS_PER_KEY - 1 and delay + 2 <= MAX_SLEEP_SEC:
                    if verbose:
                        print(f"  ⏳ 金鑰 {fp}:429,依 Google 建議等 {delay:.0f}s 後重試", flush=True)
                    time.sleep(min(delay + 2, MAX_SLEEP_SEC))
                    continue
                # 等不起(或重試用完)→ 標冷卻,換下一把
                cool_down(state, key, max(delay, COOLDOWN_RATE_SEC), "429 額度/頻率上限")
                if verbose:
                    print(f"  ↪ 金鑰 {fp}:429 且等不起 → 冷卻並換下一把", flush=True)
                break

            if r.status_code in (400, 401, 403):
                emsg = _err_message(r)
                dead = ("API_KEY_INVALID" in emsg or "PERMISSION_DENIED" in emsg
                        or "API key not valid" in emsg or r.status_code in (401, 403))
                if dead:
                    meta["attempts"].append({"key": fp, "attempt": attempt + 1,
                                             "result": "key_rejected", "http": r.status_code,
                                             "error": emsg[:200]})
                    cool_down(state, key, COOLDOWN_DEAD_SEC, f"HTTP {r.status_code} 金鑰無效/被拒")
                    if verbose:
                        print(f"  ↪ 金鑰 {fp}:HTTP {r.status_code} 金鑰無效/被拒 → 冷卻 24h,換下一把", flush=True)
                    break
                # 400 但不是金鑰問題(模型 id 打錯、payload 壞掉)→ 換金鑰也沒用,直接收工
                meta["attempts"].append({"key": fp, "attempt": attempt + 1,
                                         "result": "bad_request", "http": 400, "error": emsg[:200]})
                meta["degraded_reason"] = f"HTTP 400(非金鑰問題,換金鑰無用):{emsg[:200]}"
                return None, meta

            # 5xx / 其他:伺服器端暫時性,不冷卻金鑰
            meta["attempts"].append({"key": fp, "attempt": attempt + 1,
                                     "result": "http_error", "http": r.status_code,
                                     "error": _err_message(r)[:200]})
            if verbose:
                print(f"  ! 金鑰 {fp}:HTTP {r.status_code}", flush=True)
            if attempt < ATTEMPTS_PER_KEY - 1:
                time.sleep(6)
                continue
            break

    # 全部金鑰都走完還是沒結果 → ⛔ 絕不沉默,把原因寫死在 JSON 裡讓成績單可以誠實標示
    if meta["degraded_reason"] is None:
        if meta["keys_tried"] == 0:
            nxt = min((state.get(_fingerprint(k), {}).get("cooldown_until", 0) for k in keys), default=0)
            when = time.strftime("%H:%M:%S", time.localtime(nxt)) if nxt else "未知"
            meta["degraded_reason"] = (f"全部 {len(keys)} 把金鑰都在冷卻中(最早約 {when} 解封);"
                                       "要強制重試請加 --ignore-cooldown。")
        else:
            meta["degraded_reason"] = (f"{meta['keys_tried']}/{len(keys)} 把金鑰全部呼叫失敗;"
                                       "細節見 attempts(多為 429 額度用盡或金鑰失效)。")
    return None, meta


# ══════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="Gemini 6 維曲評(evidence-based,多金鑰輪替)")
    ap.add_argument("audio", help="音檔路徑")
    ap.add_argument("--lang", default="zh", choices=["zh", "en", "ja", "ko"], help="評語語言(預設 zh)")
    ap.add_argument("--json", dest="json_out", help="輸出 JSON 路徑")
    ap.add_argument("--timeout", type=int, default=200, help="單次 HTTP 逾時秒數(預設 200)")
    ap.add_argument("--ignore-cooldown", action="store_true", help="忽略冷卻狀態,強制重試所有金鑰")
    ap.add_argument("--dry-run", action="store_true",
                    help="不打 API:只驗金鑰載入/mime/prompt/解析路徑(用內建樣本回應驗證 parser)")
    args = ap.parse_args()

    audio = Path(args.audio).resolve()
    if not audio.exists():
        sys.exit(f"✗ 找不到音檔:{audio}")

    out = {
        "file": audio.name,
        "component": "Gemini曲評",
        "model": GEMINI_MUSIC_MODEL,
        "lang": args.lang,
        "degraded": False,
        "thresholds": {
            "temperature": TEMPERATURE,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "raw_to_score": RAW_TO_SCORE,
            "attempts_per_key": ATTEMPTS_PER_KEY,
            "cooldown_rate_sec": COOLDOWN_RATE_SEC,
            "cooldown_dead_sec": COOLDOWN_DEAD_SEC,
            "max_inline_b64_mb": MAX_INLINE_B64_MB,
            "note": "啟發式起手值,非論文實測;raw_to_score 目前為線性 ×10,校準後可改",
        },
    }

    if args.dry_run:
        # 乾跑:證明「金鑰載入 → mime 判定 → prompt 組裝 → 回應解析 → 計分」整條路徑會動,但不花配額。
        keys = load_keys()
        state = load_state()
        sample = ("曲風：Symphonic Metal｜整體：最強是 2:10 起弦樂與破音吉他疊起來的推進;最弱是 A 段鼓組太方。\n"
                  "M1|8.0|0:00 鋼琴獨走到 1:12 進鼓才真正起,副歌 2:10 弦樂疊上去有 payoff。\n"
                  "M2|7.5|副歌 \"through the storm\" 那句四度跳進是唯一記得住的 hook,主歌旋律偏窄。\n"
                  "M3|6.0|4/4 直拍到底,2:40 才有一次過門,pocket 咬得住但沒變化。\n"
                  "M4|7.0|失真吉他與弦樂在 1-3kHz 打架,低頻由貝斯獨撐、略空。\n"
                  "M5|NA|人聲修音痕跡重,音準判不出來。\n"
                  "M6|6.5|交響金屬公式執行到位,但沒有超出這個曲風的既有寫法。\n"
                  "===SCORES=== M1:8.0 M2:7.5 M3:6.0 M4:7.0 M5:NA M6:6.5 曲總分:7.0")
        parsed = parse_review(sample)
        out["dry_run"] = True
        out["degraded"] = True
        out["degraded_reason"] = "dry-run:未實際呼叫 Gemini,以下分數來自內建樣本回應,僅用於驗證解析路徑"
        out["meta"] = {
            "keys_loaded": len(keys),
            "keys_cooling": [{"key": _fingerprint(k), "left_sec": is_cooling(state, k)[1],
                              "reason": is_cooling(state, k)[2]} for k in keys if is_cooling(state, k)[0]],
            "mime_type": MIME_BY_SUFFIX.get(audio.suffix.lower(), "UNSUPPORTED"),
            "prompt_chars": len(gemini_music_prompt(args.lang)),
            "audio_mb": round(audio.stat().st_size / 1024 / 1024, 2),
        }
        out["header"] = parsed["header"]
        out["dimensions"] = build_dimensions(parsed)
        out["gemini_reported_total"] = {
            "raw_0to10": parsed["reported_total"],
            "score": round(parsed["reported_total"] * RAW_TO_SCORE, 1) if parsed["reported_total"] is not None else None,
            "source": parsed["total_source"],
            "note": "此為 Gemini 自報總分,非本元件加權;要不要進總分由八家對決決定",
        }
        out["review_text"] = sample.split("===SCORES===")[0].strip()
    else:
        print(f"[1/2] 呼叫 Gemini({GEMINI_MUSIC_MODEL},語言 {args.lang})…", flush=True)
        txt, meta = call_gemini(audio, args.lang, args.timeout, args.ignore_cooldown)
        out["meta"] = meta
        if txt is None:
            out["degraded"] = True
            out["degraded_reason"] = meta.get("degraded_reason") or "未知失敗"
            out["dimensions"] = build_dimensions({"scores": {f"M{n}": None for n in range(1, 7)},
                                                  "notes": {}, "na_dims": []})
            print(f"✗ 曲評未產出:{out['degraded_reason']}", file=sys.stderr)
        else:
            print("[2/2] 解析回應…", flush=True)
            parsed = parse_review(txt)
            out["header"] = parsed["header"]
            out["dimensions"] = build_dimensions(parsed)
            out["gemini_reported_total"] = {
                "raw_0to10": parsed["reported_total"],
                "score": round(parsed["reported_total"] * RAW_TO_SCORE, 1) if parsed["reported_total"] is not None else None,
                "source": parsed["total_source"],
                "note": "此為 Gemini 自報總分,非本元件加權;要不要進總分由八家對決決定",
            }
            out["review_text"] = txt.split("===SCORES===")[0].strip()
            # 六維全空 = 格式沒照做,雖然有文字但機器讀不到分 → 誠實標降級(網頁版這時是整包丟掉)
            if all(d["raw_0to10"] is None for d in out["dimensions"].values()):
                out["degraded"] = True
                out["degraded_reason"] = "模型有回文字但沒照 ===SCORES=== 格式給分,只有 review_text 可用"

    # ── 主控台摘要 ────────────────────────────────────────────────
    print("\n===== Gemini 曲評 =====")
    if out.get("header"):
        print(f"  {out['header']}")
    for k in [f"M{n}" for n in range(1, 7)]:
        d = out.get("dimensions", {}).get(k, {})
        sc = d.get("score")
        shown = f"{sc:>5.1f}" if sc is not None else ("  NA " if d.get("status") == "not_applicable" else "  --  ")
        print(f"  {k} {d.get('label',''):<12} {shown}  {d.get('comment','')[:60]}")
    g = out.get("gemini_reported_total") or {}
    if g.get("score") is not None:
        print(f"  ── Gemini 自報曲總分 {g['score']}/100({g.get('source')})")
    if out["degraded"]:
        print(f"  ⚠ degraded:{out.get('degraded_reason')}")

    jp = Path(args.json_out) if args.json_out else audio.with_name(audio.stem + "_Gemini曲評.json")
    jp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整報告:{jp}")


if __name__ == "__main__":
    main()
