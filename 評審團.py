# -*- coding: utf-8 -*-
"""評審團.py — 三層歌曲評審整合器
用法: python 評審團.py 歌曲檔或連結
  三種輸入:
    1. SUNO 連結(https://suno.com/song/... 或 /s/ 短連結)→ 自動下載歌+抓歌詞
    2. YouTube 連結 → 自動下載歌(需 yt-dlp+ffmpeg);⚠️ 抓不到歌詞,請另給
    3. 本機音檔路徑(歌詞另給)/ 直接 mp3 連結
第一層 物理技術 = song_scorer(.venv)
第二層 美學情感 = SongEval 五維(.venv-ml)+ Audiobox 四軸(.venv-ml)
第三層 詞曲文本 = Claude 在對話裡評(本程式不做)
輸出: 歌名_評審團.json + 主控台摘要
"""
import json
import math
import os
import re
import shutil
import contextlib
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
import urllib.request
from pathlib import Path

_WIN = sys.platform == "win32"

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent.resolve()
ENV = {**os.environ, "PYTHONUTF8": "1"}

# Windows:子程序不要各自彈出主控台黑框(一次評測會開好幾個子程序)。Linux 無此旗標 → 空 dict。
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _WIN else {}


# ── GPU / CPU 自動切換(2026-07-19 實測定案)──────────────────────────────
# 實測(4 分鐘歌、RTX 4090 + i9-13900KS):
#   GPU 整首 67 秒 / VRAM 峰值 20.7GB   ← 快,但幾乎吃掉整張卡
#   CPU 整首 83 秒 / 限 12 執行緒(≈50%)← 只慢 16 秒,而且限到 12 核「一秒都沒慢」
#   兩者分數完全一致(SongEval 3.12);⛔分段推論會讓分數漂到 3.60,不可用(見 SongEval/eval.py 註解)
# 策略:VRAM 夠(VAVA 閒著)→ 走 GPU;不夠 → 退 CPU 並壓在半數核心,絕不跟 VAVA 搶資源。
GPU_NEED_MIB = 21000      # 要有這麼多「可用」VRAM 才敢走 GPU
                          # (實測評測自身需 ~20.1GB;卡總量 23.0GB、桌面基準佔 ~1GB → 閒置時可用約 21.3GB。
                          #  設 21000 = 留約 0.9GB 緩衝;VAVA 只要多佔 ~300MB 就會自動退 CPU,不會硬搶)
CPU_THREADS = "12"        # CPU 模式執行緒上限(13900KS 24 核 → 約 50%)

# ── 滿血版新元件(2026-07-20 接線)─────────────────────────────────────────
# 這些是「加在旁邊」的,第一關 song_scorer 與第二關 SongEval/Audiobox 的計算一行都沒動
#(她的鐵則①:舊指標一律保留,要不要淘汰由八家對決給權重決定,資料先留著)。
#
# ⚠️ demucs 只裝在 anaconda,不在 .venv/.venv-ml → 分軌類元件必須用它跑。
#    ⛔ 但 song_scorer 不能改用 anaconda:那裡沒有 parselmouth,jitter/shimmer/HNR 會靜靜變 None。
def _find_demucs_py():
    """找一個裝了 demucs 的 python。順序:環境變數 → 既有 anaconda(向下相容,存在才用)
    → 安裝腳本建的 .venv-demucs → .venv-ml。⚠️ 這條線斷掉 = 結構編曲柱 + 和聲柱一起缺
    (合計 26.2% 權重),所以 install 腳本一定要把 .venv-demucs 建起來。"""
    env_py = os.environ.get("SONG_JURY_DEMUCS_PY")
    if env_py:
        return env_py
    win = os.name == "nt"
    base = Path(__file__).resolve().parent

    def _has_demucs(py: Path) -> bool:
        """這個 python 真的裝了 demucs 嗎 —— 看 site-packages 有沒有 demucs 套件目錄。
        ⛔ 不能只看 python.exe 在不在:很多人裝了 anaconda 但裡面沒有 demucs,
           只驗檔案存在就會挑中一個跑不動的直譯器,26.2% 權重靜靜消失。"""
        if not py.exists():
            return False
        root = py.parent if win else py.parent.parent
        pats = ["Lib/site-packages/demucs"] if win else ["lib/python*/site-packages/demucs"]
        # ⚠️ 一定要 next(..., None) 或 list() 把產生器取值 —— Path.glob() 回的是**產生器,
        #    永遠是 truthy**,直接丟進 any() 會讓任何存在的 python 都被判成「有 demucs」。
        return any(next(root.glob(p), None) is not None for p in pats)

    # ⚠️ 順序:安裝腳本自己建的 .venv-demucs 要排在 conda 前面 —— 那是「這個專案裝的」,
    #    最可信;conda 只是向下相容既有使用者的退路。
    cands = [base / v / ("Scripts/python.exe" if win else "bin/python")
             for v in (".venv-demucs", ".venv-ml")]
    home = Path.home()
    for d in ("anaconda3", "miniconda3", "miniforge3"):
        cands.append(home / d / ("python.exe" if win else "bin/python"))

    for p in cands:                      # 第一輪:真的有 demucs 的才算
        if _has_demucs(p):
            return str(p)
    for p in cands:                      # 第二輪:退而求其次,至少是個存在的直譯器(錯誤訊息會更清楚)
        if p.exists():
            return str(p)
    return sys.executable   # 最後退路:當前直譯器(HF Space 這類單一環境)


DEMUCS_PY = _find_demucs_py()
DEMUCS_NEED_MIB = 4000    # htdemucs_6s 用量遠小於 SongEval,不必套 21000 那個門檻
# ⛔ FLAMINGO_NEED_MIB 已移除(Music Flamingo 2026-07-20 判死拆線)
STEMS_DIR = BASE / "_stems"   # 編曲層次與和聲分析共用同一份分軌快取 → Demucs 全程只跑一次

# 新元件全部是「可選」:任何一個失敗都只記 error,不影響原本三關的結果產出。
def _optional_stage(cmd, label, env=None, timeout=1800, cwd=None):
    """跑一個新元件,回 (完成的 process 或 None, 說明)。絕不讓它中斷主流程。
    ⚠️ 不能只看 returncode:這些工具把例外包起來後照樣寫 JSON 並回 0
       (和聲分析.py 就是這樣),所以要檢查 degraded 與有沒有實際內容。
    ⚠️ cwd 不存在時 subprocess 在 Windows 丟 NotADirectoryError、POSIX 丟 FileNotFoundError,
       兩者都由下面那個 except Exception 接住 → 回 (None, 說明),不會炸掉主流程。"""
    try:
        r = subprocess.run(cmd, cwd=str(cwd or BASE), env=env or ENV, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, **_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return None, f"{label}:逾時({timeout}s)"
    except Exception as e:
        return None, f"{label}:{type(e).__name__}"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return None, f"{label}:結束碼 {r.returncode} {tail[-1][:80] if tail else ''}"
    return r, ""


# ══════════════════════════════════════════════════════════════════════
#  ⚖️ 九柱組裝(重構庭 2026-07-25 定版,T1-T4 完整程序)
#
#  總分(滿分100)= 詞 25.3 + 曲側八柱 74.7(缺項柱內/柱間自動重正規化並留痕)。
#  ⛔ 凍結:演唱.rhythm(T2b 10:3)、和聲.non_diatonic(9:4);SONICS=顯示軸不入分(19:7)。
#
#  ⚠️ 這九個數字加起來是 100.1 不是 100 —— 是合議庭各柱四捨五入到小數一位的結果,
#     **不是 bug,也不影響任何分數**:曲側合成是「除以在場柱的權重和」自我歸一化,
#     總分公式 0.253×詞 + 0.747×曲側 兩係數本身正好合 1。
#     ⛔ 不可以為了湊 100 私自改任何一格 —— 權重是十三席合議庭定的,改一格要單格重開辯論。
#
#  ⚠️ 這段刻意放在模組層級(不是埋在 main 裡)—— 這樣測試才測得到。
#     tests/test_pillars.py 會驗權重表、缺項歸一化、全缺不除零、缺柱完整性旗標。
# ══════════════════════════════════════════════════════════════════════
PILLAR_W = {"詞": 25.3, "人聲": 15.2, "和聲": 13.6, "結構編曲": 12.6, "聲學": 12.1,
            "旋律記憶": 6.1, "真實風格": 6.1, "整體": 5.1, "律動": 4.0}


def _g(d, *ks):
    """安全取巢狀鍵,拿不到或不是數字就回 None(絕不亂猜補值)。"""
    for k in ks:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    return float(d) if isinstance(d, (int, float)) else None


def _vnum(x):
    """vocal_detail 的值可能是 dict{score} 或直接是數字。"""
    return _g(x, "score") if isinstance(x, dict) else (float(x) if isinstance(x, (int, float)) else None)


def build_pillar_items(physical, harmony, arrangement, gemini, songeval, audiobox, singmos, realdist):
    """把各引擎的產出攤成「柱 → [(細項名, 柱內權重, 值 or None)]」。

    ⚠️ 取值鍵名必須跟各引擎實際寫出的 JSON 一致。這個 repo 有前科:
       舊版取 gemini["total"] 但實際鍵是 gemini_reported_total → Gemini 整關被靜默丟掉,
       還被重正規化蓋掉看不出來。tests/test_pillars.py 有一條專門守這件事。
    """
    _md = ((physical or {}).get("mix_detail") or {})
    _vd = ((physical or {}).get("vocal_detail") or {})
    _hm = ((harmony or {}).get("metrics") or {})
    _gd = ((gemini or {}).get("dimensions") or {})
    _gt = _g(gemini, "gemini_reported_total", "raw_0to10")
    # 1-5 → 0-100。⛔ 只換算真的數字:SongEval 版本異常吐出字串時 "abc"*20.0 會
    #    TypeError 炸掉整份組裝;非數字一律當缺席,交給數值閘門(_valid_score)留痕。
    _se = {k: (v * 20.0) for k, v in (songeval or {}).items()
           if isinstance(v, (int, float)) and not isinstance(v, bool)}
    _pq = _g(audiobox, "PQ")

    return {
        "聲學": [("頻譜平衡", 26, _g(_md, "spectral_balance", "score")),
                 ("混音結構", 18, _g(_md, "structure", "score")),
                 ("結構清晰(SongEval)", 18, _se.get("Clarity")),
                 ("立體聲", 10, _g(_md, "stereo", "score")),
                 ("諧波", 10, _g(_md, "harmony", "score")),
                 ("動態LRA", 10, _g(_md, "dynamic_range", "score")),
                 ("製作品質(Audiobox)", 8, (_pq * 10) if _pq is not None else None)],
        "人聲": [("嗓音品質", 23, _vnum(_vd.get("voice_quality"))),
                 ("演唱聽感(SingMOS)", 12, _g(singmos, "score")),
                 ("動態控制", 11, _vnum(_vd.get("dynamics"))),
                 ("音準", 10, _vnum(_vd.get("pitch"))),
                 ("顫音", 10, _vnum(_vd.get("vibrato"))),
                 ("音域", 10, _vnum(_vd.get("range"))),
                 ("人聲表現(Gemini M5)", 10, _g(_gd, "M5", "score")),
                 ("人聲自然(SongEval)", 8, _se.get("Naturalness")),
                 ("長音穩定", 6, _vnum(_vd.get("stability")))],
        "和聲": [("終止式", 20, _g(_hm, "cadence", "score")),
                 ("和弦詞彙(過濾版·復權)", 19, _g(_hm, "chord_vocabulary", "score")),
                 ("調性穩定", 18, _g(_hm, "key_stability", "score")),
                 ("五度動線", 16, _g(_hm, "fifth_motion", "score")),
                 ("和聲節奏", 15, _g(_hm, "harmonic_rhythm", "score")),
                 ("延伸和弦", 12, _g(_hm, "extended_chords", "score"))],
        "結構編曲": [("能量成長", 32, _g(arrangement, "score_growth")),
                     ("編制變化", 18, _g(arrangement, "score_delta")),
                     ("結構弧線(Gemini M1)", 18, _g(_gd, "M1", "score")),
                     ("連貫(SongEval)", 18, _se.get("Coherence")),
                     ("配器音色(Gemini M4)", 14, _g(_gd, "M4", "score"))],
        "旋律記憶": [("旋律記憶(Gemini M2)", 52, _g(_gd, "M2", "score")),
                     ("記憶點(SongEval)", 48, _se.get("Memorability"))],
        "律動": [("節奏律動(Gemini M3)", 100, _g(_gd, "M3", "score"))],
        "整體": [("Gemini 總分", 51, (_gt * 10) if _gt is not None else None),
                 ("音樂性(SongEval)", 49, _se.get("Musicality"))],
        "真實風格": [("真實距離(馬氏)", 60, _g(realdist, "score")),
                     ("曲風創新(Gemini M6)", 40, _g(_gd, "M6", "score"))],
    }


def _valid_score(v):
    """最後一道數值閘門:必須是「非 bool 的有限數字,且落在 0–100」。

    ⛔ 為什麼不能只判 `is not None`(Codex 探針四種值全數穿透):
       NaN      → 柱分與曲側合成整個變 NaN
       Infinity → 寫出非標準 JSON
       101 / -1 → 直接進正式分數
       True     → Python 的 bool 是 int,會被當成 1 分
       任何一個引擎(Gemini/SongEval/Audiobox/量測)版本異常都可能吐這些,
       閘門設在組裝層才能一次擋住所有來源。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return math.isfinite(v) and 0.0 <= v <= 100.0


def build_pillar_totals(pillar_items):
    """柱內缺項重正規化 → 柱分;柱間缺柱重正規化 → 曲側合成;並算完整性旗標。

    回 dict,結構與寫進 _評審團.json 的 pillar_totals 相同。
    ⛔ 缺柱時「完整評測」必為 False —— 那是換了一把尺,不可與完整評測互比或排行。
    ⛔ 非法數值(NaN/∞/超範圍/bool)= 該項缺席 + invalid_numeric 留痕,絕不進分。
    """
    pillar_scores, pillar_detail = {}, {}
    for pname, items in pillar_items.items():
        have = [(n, w, v) for n, w, v in items if _valid_score(v)]
        miss = [n for n, w, v in items if v is None]
        invalid = {n: repr(v) for n, w, v in items
                   if v is not None and not _valid_score(v)}
        if have:
            wsum = sum(w for _, w, _ in have)
            pillar_scores[pname] = round(sum(w * v for _, w, v in have) / wsum, 1)
        detail = {"score": pillar_scores.get(pname),
                  "items": {n: round(v, 1) for n, w, v in have},
                  "missing": miss + sorted(invalid)}
        if invalid:
            detail["invalid_numeric"] = invalid   # 留痕:是「值不合法」,不是「沒跑到」
        pillar_detail[pname] = detail

    wsum_song = sum(PILLAR_W[p] for p in pillar_scores)
    song_side = (round(sum(PILLAR_W[p] * s for p, s in pillar_scores.items()) / wsum_song, 1)
                 if pillar_scores else None)          # ⛔ 全缺時不可除零

    lost = [p for p in PILLAR_W if p != "詞" and p not in pillar_scores]
    return {
        "完整評測": not lost,
        "缺柱": lost,
        "缺柱權重合計": round(sum(PILLAR_W[p] for p in lost), 1),
        "完整性警語": ("九柱齊全,可與其他完整評測互比" if not lost else
                       "⛔ 不完整評測:曲側合成是用剩下的柱重新歸一化算的,"
                       "不可與完整評測互比、不可排行、不可當評測結果。請補齊安裝後重評。"),
        "柱分": pillar_detail,
        "柱權重": PILLAR_W,
        "曲側合成": song_side,
        "曲側含柱": sorted(pillar_scores),
    }


def iter_windows(n_samples, win):
    """把長度 n_samples 的訊號切成不重疊的完整窗,回每個窗的起點。

    ⚠️ 上界必須是 n-win+1。寫成 range(0, n-win, win) 會漏掉最後一個完整窗
       —— 實測 40 秒音檔只分析 1 個 20 秒窗、240 秒只分析 11 個而不是 12 個。
       演唱聽感.py 與 真實距離.py 共用這個函式,免得同一個 off-by-one 犯兩次。
    """
    return range(0, max(1, n_samples - win + 1), win)


def _load_stage_json(path, label):
    """讀元件產出的 JSON,順便把它自己標的 degraded 帶出來。"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"{label}:讀不到 JSON({type(e).__name__})"
    # ⛔ 「解析得開」不代表「格式對」:引擎異常吐出 [] 或 "error" 時,json.loads 會成功,
    #    但下一行 .get() 直接 AttributeError → 整份評審團退出。頂層必須是 dict,
    #    否則標成該階段格式錯誤(= 缺席),不准炸掉整份報告。
    if not isinstance(d, dict):
        return None, f"{label}:JSON 頂層是 {type(d).__name__},不是預期的物件(格式錯誤,視為缺席)"
    if d.get("degraded"):
        return d, f"{label}:降級模式({d.get('error') or d.get('stem_error') or '見 JSON'})"
    return d, ""


def _free_vram_mib():
    """目前可用 VRAM(MiB);查不到回 -1(視為不可用 → 走 CPU)。"""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().splitlines()[0].strip())
    except Exception:
        pass
    return -1


def _pick_device_env(need_mib=None):
    """依當下 VRAM 決定這次走 GPU 還是 CPU,回 (env, 說明字串)。
    need_mib:這個階段實際需要多少 VRAM。不給就用 SongEval 的 21000。
    ⚠️ 各階段用量差很多(Demucs 約 4GB、SongEval 約 20.1GB),
       一律套 21000 會讓輕量階段被無謂地趕去 CPU。"""
    need = GPU_NEED_MIB if need_mib is None else need_mib
    free = _free_vram_mib()
    if free >= need:
        env = {**ENV}
        env.pop("CUDA_VISIBLE_DEVICES", None)      # 放行 GPU
        return env, f"GPU(可用 VRAM {free} MiB)"
    env = {**ENV, "CUDA_VISIBLE_DEVICES": "-1",
           "OMP_NUM_THREADS": CPU_THREADS, "MKL_NUM_THREADS": CPU_THREADS}
    why = "查不到 GPU" if free < 0 else f"VRAM 只剩 {free} MiB,讓路給其他工作"
    return env, f"CPU · {CPU_THREADS} 執行緒({why})"

VOCAL_LABELS = {
    "pitch": "音準", "rhythm": "節奏準度", "stability": "長音穩定度", "vibrato": "顫音",
    "dynamics": "動態控制", "voice_quality": "嗓音品質", "range": "音域",
}
HARMONY_LABELS = {
    "chord_vocabulary": "和弦詞彙", "harmonic_rhythm": "和聲節奏", "non_diatonic": "非調內和弦",
    "cadence": "終止式", "key_stability": "調性穩定", "extended_chords": "延伸和弦",
    "fifth_motion": "五度動線",
}
SONGEVAL_LABELS = {
    "Coherence": "整體連貫性", "Musicality": "整體音樂性",
    "Memorability": "記憶點", "Clarity": "結構清晰度", "Naturalness": "人聲自然度",
}
AUDIOBOX_LABELS = {
    "PQ": "製作品質", "PC": "製作複雜度", "CE": "內容感染力", "CU": "內容實用性",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_UUID_RE = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"


def _follow_short_link(url):
    """SUNO 短連結(/s/xxxx)只轉址一次就會露出帶 UUID 的正式網址。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=30)
        return resp.geturl()
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if loc:
            return urllib.parse.urljoin(url, loc)
        return url


def _lyric_score(t):
    """0=不是歌詞;2=有段落標記(最可信);1=多行文字。先擋網頁程式碼雜訊。"""
    if not t or len(t) < 60:
        return 0
    if '"$"' in t or "_next/static" in t or '"src":' in t or '{"children"' in t:
        return 0
    if re.search(r"\[(intro|verse|chorus|bridge|hook|pre[- ]?chorus|outro)", t, re.I) or "【" in t:
        return 2
    return 1 if t.count("\n") >= 6 else 0


def _collect_strings(obj, out, depth=0):
    """遞迴收集 JSON 裡所有夠長的字串。
    ⚠️ 不寫死欄位名 —— 2026-07-25 實證:SUNO v5.5 Preview 把歌詞塞進 metadata.tags、
       prompt 只剩標題(「我的朋友頭上開滿鮮花」案)。寫死欄位=SUNO 一改版就抓不到詞。"""
    if depth > 6:
        return
    if isinstance(obj, str):
        if len(obj) >= 60:
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, out, depth + 1)


def _suno_from_api(uuid):
    """官方 clip API(公開端點,不需登入):回 (title, lyrics);任何失敗回 (None, None)。
    比 HTML 爬取穩,故排在第一順位;掛掉自動退回 HTML。"""
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    for api in (f"https://studio-api.prod.suno.com/api/clip/{uuid}",
                f"https://studio-api.suno.ai/api/clip/{uuid}"):
        try:
            with urllib.request.urlopen(urllib.request.Request(api, headers=ua), timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
        title = (d.get("title") or "").strip() or None
        cands = []
        _collect_strings(d.get("metadata") or {}, cands)
        best = max(((_lyric_score(c), len(c), c) for c in cands), default=(0, 0, None))
        if title or best[0] > 0:
            return title, (best[2].strip() if best[0] > 0 else None)
    return None, None


def fetch_suno_meta(uuid):
    """抓 SUNO 歌名與歌詞。順序:官方 clip API → 頁面 HTML(三策略)。失敗回 (None, None)。

    ⚖️ 2026-07-25 修:她回報「明明有歌詞卻抓不到」——根因是 SUNO v5.5 Preview 換欄位
       (歌詞進 metadata.tags、prompt 只剩標題),舊碼只翻 prompt 必然落空。
       新法=全欄位掃描 + 由 _lyric_score 挑最像歌詞的那段,SUNO 再換欄位也不怕。
    """
    t, ly = _suno_from_api(uuid)
    if ly:
        return t, ly
    api_title = t
    try:
        req = urllib.request.Request(
            f"https://suno.com/song/{uuid}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return api_title, None
    title = api_title
    if not title:
        mt = re.search(r"<title>(.*?)\s+by\s", html)
        if mt:
            title = mt.group(1).strip().strip("《》〈〉\"' ")

    def decode_js(s):
        try:
            return json.loads('"' + s + '"')
        except Exception:
            return None

    candidates = []
    # 策略一:prompt 欄位(雙層 JSON 逸出,自訂歌詞模式)
    idx = html.find('\\"prompt\\":\\"')
    if idx >= 0:
        peeled = re.sub(r"\\(.)", r"\1", html[idx:idx + 60000])
        m = re.search(r'"prompt":"((?:[^"\\]|\\.)*)"', peeled, re.S)
        if m:
            d = decode_js(m.group(1))
            if d:
                candidates.append(d.strip())
    # 策略二(2026-07-25 新增):整塊 metadata JSON → 全欄位掃描(治 v5.5 Preview 把詞塞 tags)
    mi = html.find('\\"metadata\\":')
    if mi >= 0:
        peeled = re.sub(r"\\(.)", r"\1", html[mi:mi + 200000])
        j0 = peeled.find("{", peeled.find('"metadata"'))
        if j0 >= 0:
            depth = 0
            for k in range(j0, min(len(peeled), j0 + 200000)):
                if peeled[k] == "{":
                    depth += 1
                elif peeled[k] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            _collect_strings(json.loads(peeled[j0:k + 1]), candidates)
                        except Exception:
                            pass
                        break
    # 策略三:Next.js flight 推送字串(單層逸出)
    for m in re.finditer(r'\.push\(\[\d+,"((?:[^"\\]|\\.)*)"', html, re.S):
        d = decode_js(m.group(1))
        if d:
            candidates.append(d.strip())

    best = max(((_lyric_score(c), len(c), c) for c in candidates), default=(0, 0, None))
    lyrics = best[2] if best[0] > 0 else None
    return title, lyrics


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def _safe_name(s):
    """檔名安全化:去非法字元、控空白、擋空名與 Windows 保留名。"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (s or "").strip())[:60].strip(" .")   # 加濾換行/控制字元
    if not s:
        return "untitled"
    if s.upper().split(".")[0] in _WIN_RESERVED:
        return "_" + s
    return s


def _venv_py(venv):
    """venv 內的 python(跨平台);venv 不存在(如 HF Space/單一環境)則退回當前直譯器。"""
    p = BASE / venv / ("Scripts/python.exe" if _WIN else "bin/python")
    return str(p) if p.exists() else sys.executable


def _venv_exe(venv, name):
    """venv 內的 CLI 執行檔(跨平台);venv 不存在則退回當前環境同名 console script,再退回 PATH。"""
    p = BASE / venv / (f"Scripts/{name}.exe" if _WIN else f"bin/{name}")
    if p.exists():
        return str(p)
    alt = Path(sys.executable).parent / (f"{name}.exe" if _WIN else name)
    return str(alt) if alt.exists() else name


def _run_stage(cmd, cwd, label, env=None):
    """跑一個評分子程序;失敗時印出工具名+stderr 尾段+自救提示再退出(不再吞錯讓用戶無從下手)。
    env=None 用預設;吃 GPU 的階段請傳 _pick_device_env() 挑好的那份。"""
    try:
        return subprocess.run(cmd, cwd=str(cwd), env=(env or ENV), check=True,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        sys.exit(f"✗ {label}:找不到執行檔 `{cmd[0]}`。\n"
                 f"→ 對應的 venv 可能沒建好(用 uv 建 .venv / .venv-ml)。")
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or e.stdout or "").strip()[-600:]
        sys.exit(f"✗ {label} 失敗(exit {e.returncode})。\n{tail}\n"
                 f"→ 檢查:venv 依賴是否裝齊(uv pip install)、音檔是否可讀、記憶體/GPU 是否足夠。")


def _last_json(text):
    """從子程序 stdout 取最後一行合法 JSON(容忍前面夾雜 log/warning);單行找不到就整段當一個 JSON(pretty-print 保底)。"""
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    try:
        return json.loads((text or "").strip())
    except Exception:
        raise ValueError("子程序沒有輸出可解析的 JSON(可能崩潰或版本不合)")


def _unique_stem(base):
    """在 下載/ 內取一個不撞名的 stem(同名重抽/多版自動加 v2/v3…,不覆蓋)。

    ⛔ 不可以只「檢查存在就回傳」(check-then-use):兩個程序同時評同一首歌時,
       雙方都會看到「還沒有」而拿到同一個名字,接著共用同一個 .part 互相覆寫。
       這裡用 O_CREAT|O_EXCL **原子地把名字佔起來**(先建一個 0 byte 的佔位檔),
       誰先建成功誰就擁有這個名字,另一個會拿到下一個編號。
    """
    dl = BASE / "下載"
    dl.mkdir(parents=True, exist_ok=True)
    stem, k = base, 2
    while True:
        target = dl / f"{stem}.mp3"
        lock = dl / f".{stem}.mp3.reserving"
        if not target.exists():
            try:
                # ⛔ 佔位**不可以直接建立正式的 .mp3**:下載失敗時會留下一個 0 byte 的
                #    幽靈 mp3,下一次跑會把它當成「這首已經下載過」,而 YouTube 那條路徑
                #    只檢查「檔案存在」就印「已存」→ 拿 0 byte 檔去評分。
                #    改用獨立的保留檔:它不是音檔,不會被誤認。
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return stem
            except FileExistsError:
                pass                      # 別人正在下載這個名字 → 換下一個編號
        stem = f"{base} v{k}"
        k += 1
        if k > 999:                       # 保險:不讓它無限繞
            raise RuntimeError(f"下載/ 裡 {base} 的版本號已超過 999")


def _release_stem(stem):
    """放掉 _unique_stem 佔下的名字(下載成功或失敗都要呼叫)。"""
    try:
        (BASE / "下載" / f".{stem}.mp3.reserving").unlink(missing_ok=True)
    except Exception:
        pass


def _check_audio_ok(p: Path, label=""):
    """下載完的檔要能用:不可以是 0 byte,也不可以小到不像音檔。
    ⛔ 只檢查 returncode 與「檔案存在」是不夠的 —— 佔位檔、被截斷的下載都會通過。"""
    if not p.exists():
        sys.exit(f"{label}下載失敗:檔案不存在({p.name})")
    sz = p.stat().st_size
    if sz < 4096:
        p.unlink(missing_ok=True)
        sys.exit(f"{label}下載失敗:檔案只有 {sz} bytes,不是有效音檔(已刪除)")
    return p


def _is_youtube(url):
    return bool(re.search(r"(?:youtube\.com|youtu\.be)", url, re.I))


def _yt_run(extra):
    """呼叫 yt-dlp:用跑本程式的同一個直譯器 -m yt_dlp(開源時 pip 裝進 venv 即通用)。"""
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-playlist", *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _download_youtube(url):
    """YT 連結 → 下載 bestaudio 轉 mp3 到 下載\\。YT 抓不到歌詞,需使用者另給。"""
    dl_dir = BASE / "下載"
    dl_dir.mkdir(exist_ok=True)
    if _yt_run(["--version"]).returncode != 0:
        sys.exit("找不到 yt-dlp——YouTube 輸入需要它:先安裝(uv pip install yt-dlp)+ 確認 ffmpeg 可用;\n"
                 "或改用方式 3:自行下載 YT 音訊成檔,再把檔案路徑給我。")
    r = _yt_run(["--skip-download", "--print", "%(title)s", url])
    title = (r.stdout.strip().splitlines()[-1].strip()
             if r.returncode == 0 and r.stdout.strip() else "youtube_audio")
    stem = _unique_stem(_safe_name(title))
    print(f"⬇ 從 YouTube 下載中: {title}")
    try:
        r2 = _yt_run(["-x", "--audio-format", "mp3", "--audio-quality", "0",
                      "-o", str(dl_dir / f"{stem}.%(ext)s"), url])
        mp3 = dl_dir / f"{stem}.mp3"
        if r2.returncode != 0:
            mp3.unlink(missing_ok=True)          # 別留半截檔給下一次誤用
            sys.exit(f"YT 下載失敗:{(r2.stderr or '')[-400:]}\n"
                     f"(需 yt-dlp+ffmpeg;私人/受限影片抓不到,請改用方式 3:自行下載成檔再給)")
        _check_audio_ok(mp3, "YT ")          # ⛔ 不可以只看 returncode 與檔案存在
    finally:
        _release_stem(stem)
    print(f"已存: {mp3}")
    print("📝 YouTube 無法自動抓歌詞——請另外提供歌詞(貼文字,或給 .txt 路徑)")
    return mp3


def resolve_input(arg):
    """本機路徑直接用;SUNO/YouTube 連結、直連 mp3 先下載到 下載\\ 再評。"""
    if not re.match(r"^https?://", arg, re.I):
        p = Path(arg).resolve()
        if not p.exists():
            sys.exit(f"找不到檔案: {p}")
        # 上傳檔:gradio 暫存路徑常沒有(或非小寫)音檔副檔名 → SongEval 靠「小寫 .wav/.mp3 結尾」
        # 判斷是不是音檔,對不到就誤把音檔當清單檔用文字讀 → UnicodeDecodeError 0xff 崩。
        # 對不到就複製一份成 .mp3(librosa/soundfile 依內容解碼,副檔名只給 SongEval 判斷用)。
        if not str(p).endswith((".wav", ".mp3")):
            fixed = Path(tempfile.mkdtemp(prefix="song_jury_up_")) / (_safe_name(p.stem or "upload") + ".mp3")
            shutil.copy(p, fixed)
            print(f"📎 上傳檔補正音檔副檔名: {fixed}")
            return fixed
        return p
    if _is_youtube(arg):
        return _download_youtube(arg)
    lyrics = None
    uuid = re.search(_UUID_RE, arg)
    if not uuid and "suno.com" in arg.lower():
        arg = _follow_short_link(arg)
        uuid = re.search(_UUID_RE, arg)
    if arg.lower().split("?")[0].endswith(".mp3"):
        url = arg
        base = _safe_name(Path(urllib.parse.urlparse(arg).path).stem or "download")
        name = f"{_unique_stem(base)}.mp3"
    elif uuid:
        url = f"https://cdn1.suno.ai/{uuid.group(1)}.mp3"
        title, lyrics = fetch_suno_meta(uuid.group(1))
        base = _safe_name(title) if title else f"suno_{uuid.group(1)[:8]}"
        name = f"{_unique_stem(base)}.mp3"  # 同名重抽自動加版號,不覆蓋
        if not lyrics:
            print("📝 頁面抓不到歌詞(可能純音樂或頁面改版),請手動提供")
    else:
        sys.exit("看不懂的連結。請給 SUNO 歌曲頁連結(https://suno.com/song/...)或直接的 mp3 連結")
    dl_dir = BASE / "下載"
    dl_dir.mkdir(exist_ok=True)
    dest = dl_dir / name
    part = dest.with_name(dest.name + ".part")
    print(f"⬇ 從 SUNO 下載中: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # ⛔ 不可以無上限 copyfileobj:對方若回一個持續串流(或超大檔),會把磁碟塞爆。
    #    500MB 遠大於任何一首歌(4 分鐘 320kbps ≈ 10MB),超過就是不對勁。
    MAX_BYTES = 500 * 1024 * 1024
    # ⛔ 清理必須集中在一個外層 finally,不可以在每個錯誤分支各自手動清:
    #    之前漏掉「part.replace(dest) 本身失敗」(權限/磁碟/防毒鎖檔)這條路徑,
    #    保留檔與 .part 會雙雙洩漏 —— Codex 探針重現過。
    #    finally 的規則:保留檔一律釋放;沒發布成功就把 .part 刪掉。
    try:
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
                got = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_BYTES:
                        raise RuntimeError(f"下載超過上限 {MAX_BYTES // 1024 // 1024}MB,已中止")
                    f.write(chunk)
        except urllib.error.HTTPError as e:
            sys.exit(f"下載失敗(HTTP {e.code})。歌曲可能不是「公開」狀態——"
                     f"私人歌曲請先在 SUNO 網站下載,再用方式 3(直接給檔)評。")
        except SystemExit:
            raise
        except Exception as e:
            sys.exit(f"下載失敗:{type(e).__name__}: {e}(網路問題或連結失效)")
        if part.stat().st_size < 10240:  # <10KB 幾乎必是錯誤頁/壞檔,不是音檔
            sys.exit("下載到的檔案過小,不像有效音檔(可能是私人歌、連結失效或被擋)。")
        part.replace(dest)  # 完整下載才 rename 成正式檔,中斷不留壞檔
    finally:
        _release_stem(dest.stem)
        if not dest.exists():
            part.unlink(missing_ok=True)
    if lyrics:  # 下載成功才寫歌詞,下載失敗不留孤兒歌詞檔
        res_dir = dl_dir / f"{dest.stem}_評分結果"
        res_dir.mkdir(parents=True, exist_ok=True)
        (res_dir / f"{dest.stem}_歌詞.txt").write_text(lyrics + "\n", encoding="utf-8")
        print(f"📝 歌詞已自動抓取: {res_dir / (dest.stem + '_歌詞.txt')}")
    print(f"已存: {dest}\n")
    return dest


def _pid_alive(pid: int):
    """這個 PID 的程序還活著嗎。判不出來回 None(呼叫端退回 mtime 判斷)。

    ⛔ Windows **不可以**用 os.kill(pid, 0):Python 在 Windows 上的 os.kill
       對非 CTRL 訊號走 TerminateProcess —— 「檢查」會變成「殺掉對方」。
       要走 ctypes OpenProcess + GetExitCodeProcess(STILL_ACTIVE=259)。
    """
    if pid <= 0:
        return False
    if _WIN:
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if not h:
                return False          # 開不了多半是不存在(權限問題極少見於本機自己的鎖)
            try:
                code = ctypes.c_ulong()
                if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return None
                return code.value == 259      # STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:
            return None
    try:
        os.kill(pid, 0)               # POSIX:訊號 0 = 只檢查不送
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                   # 存在但不是我們的 → 算活著
    except Exception:
        return None


@contextlib.contextmanager
def _job_lock(song: Path):
    """同一個音檔同時只准有一個評測工作。

    ⛔ 為什麼需要:所有中間檔都叫 `{音檔名}_編曲層次.json`、`_評分.json`、`_和聲分析.json`…
       同一個檔被評兩次(批次在跑 + 手動再評、.bat 開兩次、CLI 與網頁同時評)時,
       兩邊會共用這些檔 —— 一邊讀完 unlink() 之後另一邊就找不到檔案、
       或讀到正在覆寫的半截 JSON。
    ⚠️ 這把鎖只擋「同一個音檔」,不同的歌(含 SUNO 抽卡的各個 take,音檔不同)照樣可以並行。
    """
    lockf = song.with_name(f".{song.stem}.evaluating.lock")
    # ⭐ 持有者代號:誰建的鎖,鎖檔裡就寫誰的 token。
    #    ⛔ 沒有它會「刪錯人的鎖」(Codex 在 POSIX 實測重現):
    #       A 的鎖被判定陳舊 → B 接管建了自己的鎖 → A(其實還活著)結束時無條件刪鎖
    #       → 把 B 的鎖刪掉 → C 在 B 還在跑時也拿到鎖,三方同檔互踩。
    #       解法:刪除前重讀鎖檔,token 是自己的才准刪。
    token = uuid.uuid4().hex
    my_rec = json.dumps({"pid": os.getpid(), "token": token,
                         "at": time.strftime("%Y-%m-%d %H:%M:%S")})

    def _try_acquire():
        f = os.open(str(lockf), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(f, my_rec.encode("utf-8"))
        return f

    fd = None
    try:
        try:
            fd = _try_acquire()
        except FileExistsError:
            # 陳舊判定:**看持有者的 PID 還活不活著**,不是只看 mtime。
            # ⛔ 只看 mtime 的問題(Codex 指出):網頁版逾時是強制 kill,finally 不會跑,
            #    鎖會留著 —— 等 6 小時才能接管等於把使用者卡住近半天。
            #    PID 死了 → 立刻接管;PID 活著 → 不管跑多久都不搶(它是合法的長工作)。
            holder_alive = None
            try:
                rec = json.loads(lockf.read_text(encoding="utf-8"))
                holder_alive = _pid_alive(int(rec.get("pid", -1)))
            except Exception:
                pass          # 讀不到/舊格式 → 退回 mtime 判斷
            if holder_alive is None:
                try:
                    holder_alive = (time.time() - lockf.stat().st_mtime) <= 6 * 3600
                except Exception:
                    holder_alive = False
            if holder_alive:
                sys.exit(f"⛔ 這個檔正在被另一個評測工作處理中:{song.name}\n"
                         f"   (中間檔會互相覆寫,所以同一個檔不允許同時評兩次)\n"
                         f"   → 等它跑完再試;確定沒有其他工作在跑的話,刪掉 {lockf.name} 即可。")
            # 持有者已死 → 接管。兩個程序同時接管時只有一個 O_EXCL 會成功;
            # 輸的那個拿到 FileExistsError → 當成「別人已接管」明確退出,不硬搶。
            lockf.unlink(missing_ok=True)
            try:
                fd = _try_acquire()
            except FileExistsError:
                sys.exit(f"⛔ 另一個工作剛接管了這個檔的評測:{song.name},請稍後再試。")
        yield
    finally:
        # ⛔ 兩道防線:(a) 只有真的拿到鎖的人(fd 不為 None)才走到這;
        #    (b) 刪除前重讀鎖檔驗 token —— 萬一自己的鎖曾被誤判陳舊而遭接管,
        #        現在檔案裡是別人的 token,就絕不能刪。
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                cur = json.loads(lockf.read_text(encoding="utf-8"))
                if cur.get("token") == token:
                    lockf.unlink(missing_ok=True)
            except Exception:
                pass          # 讀不到 = 檔案已不在或已是別人的,都不動它


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python 評審團.py <歌曲檔路徑 或 SUNO/YouTube 連結>\n"
                 "  含空白的路徑請用引號括起。")
    song = resolve_input(sys.argv[1])
    with _job_lock(song):
        _evaluate(song)


def _evaluate(song: Path):
    print(f"🎵 評審對象: {song.name}\n")

    notes = []          # 新元件若失敗/降級,理由收在這裡,最後誠實印出來

    # ── 第 0.5 層: 編曲層次(Demucs 六軌)──────────────────────────────
    # 必須排在物理技術【之前】:它順便產出人聲軌,下一關的演唱分析要用。
    # Demucs 全程只跑這一次,和聲分析會讀同一份快取(_stems),不重複燒 GPU。
    arr_json = song.with_name(song.stem + "_編曲層次.json")
    dem_env, dem_why = _pick_device_env(DEMUCS_NEED_MIB)
    print(f"[1/6] 編曲層次(Demucs 六軌分軌)… 裝置:{dem_why}")
    _, err = _optional_stage([DEMUCS_PY, str(BASE / "編曲層次.py"), str(song),
                              "--json", str(arr_json), "--stems", str(STEMS_DIR)],
                             "編曲層次", env=dem_env)
    arrangement, vocal_stem = None, None
    degraded_raw = {}          # 降級但不計分的原始資料,只寫進 JSON 供除錯
    if err:
        notes.append(err)
    else:
        arrangement, err2 = _load_stage_json(arr_json, "編曲層次")
        if err2:
            notes.append(err2)
            # ⛔ 降級結果不計分(理由同和聲那段:那是另一套量測,不可與正常模式互比)
            degraded_raw["編曲層次"] = arrangement
            arrangement = None
            notes.append("編曲層次:降級結果不計分(相關細項視為缺席)")
        if arrangement:
            vs = arrangement.get("vocal_stem")
            if vs and Path(vs).exists():
                vocal_stem = vs
        arr_json.unlink(missing_ok=True)

    # ── 第一層: 物理技術(含演唱表現,若拿得到人聲軌)──
    print("[2/6] 物理技術評分(song_scorer)…" + ("(含演唱表現)" if vocal_stem else "(無人聲軌,只評混音)"))
    phys_json = song.with_name(song.stem + "_評分.json")
    cmd = [_venv_py(".venv"), str(BASE / "song_scorer.py"), str(song), "--json", str(phys_json)]
    if vocal_stem:
        cmd += ["--vocal", vocal_stem]
        # ⚖️ rhythm 修復配套(H2 D1「網格改建於鼓+貝斯」):從分軌快取產伴奏軌傳給 song_scorer。
        #    快取命中零 GPU;失敗只影響 rhythm 參照系(它反正凍結中),不擋主流程。
        _acc = song.with_name(song.stem + "_伴奏節奏軌.wav")
        _, _acc_err = _optional_stage([DEMUCS_PY, str(BASE / "伴奏混音.py"), str(song),
                                       str(_acc), "--stems", str(STEMS_DIR)],
                                      "伴奏節奏軌", timeout=1200)
        if not _acc_err and _acc.exists():
            cmd += ["--accomp", str(_acc)]
        else:
            notes.append(_acc_err or "伴奏節奏軌:未產出")
    _run_stage(cmd, cwd=BASE, label="物理技術(song_scorer)")
    physical = json.loads(phys_json.read_text(encoding="utf-8"))
    phys_json.unlink()  # 內容已併入 _評審團.json,不留中間檔
    if vocal_stem:
        song.with_name(song.stem + "_伴奏節奏軌.wav").unlink(missing_ok=True)   # 臨時軌,用完即清

    # ── 第 1.5 層: 和聲分析(真和弦辨識)─────────────────────────────
    # 舊的「和聲豐富度」只數 chroma 音級、不認得任何和弦;這關並存不取代(鐵則①)。
    harmony = None
    if arrangement is not None:          # 分軌成功才有快取可用,失敗就跳過免得重跑 Demucs
        hjson = song.with_name(song.stem + "_和聲分析.json")
        print("[3/6] 和聲分析(和弦辨識)…")
        _, err = _optional_stage([DEMUCS_PY, str(BASE / "和聲分析.py"), str(song),
                                  "--json", str(hjson), "--stems", str(STEMS_DIR)],
                                 "和聲分析", env=dem_env)
        if err:
            notes.append(err)
        else:
            harmony, err2 = _load_stage_json(hjson, "和聲分析")
            if err2:
                notes.append(err2)
                # ⛔ 降級結果不可以計分:分軌失敗時 和聲分析.py 會退回 HPSS,
                #    那是另一套量測,數值與正常模式不可互比。照九柱制的完整性原則,
                #    這種情況要當成「和聲柱缺席」而不是「和聲柱有分」。
                #    原始數值仍寫進 JSON 供除錯,只是不入分。
                degraded_raw["和聲分析"] = harmony
                harmony = None
                notes.append("和聲分析:降級結果不計分(和聲柱視為缺席)")
            hjson.unlink(missing_ok=True)
    else:
        notes.append("和聲分析:跳過(分軌未成功)")

    # ── 第二層 A: SongEval 五維美學(唯一暫存夾,並行安全、崩潰自清)──
    # 這兩顆是唯一會吃 GPU 的階段 → 開跑前依當下 VRAM 決定走 GPU 還是退 CPU(見檔頭 _pick_device_env)
    dev_env, dev_why = _pick_device_env()
    # ⚠️ 這兩關**不可以用 _run_stage**(那會 sys.exit 掉整份報告)。
    #    README 明列 --skip-ml 是支援的安裝方式,安裝腳本也承諾「SongEval 沒裝好,分數仍會出來、
    #    只是缺細項」。用致命版的話,--skip-ml 的人與 clone 失敗的人會直接吃到一段 traceback,
    #    一份報告都拿不到 —— 跟文件承諾相反。缺了就給空 dict,走 PILLAR_ITEMS 既有的缺項歸一化。
    print(f"[4/6] SongEval 美學評分(音樂人訓練模型)… 裝置:{dev_why}")
    songeval = {}
    _se_dir = BASE / "SongEval"
    if not (_se_dir / "eval.py").exists():
        print("      ↳ 跳過:SongEval 沒安裝(五個模型聽感細項會缺,不影響其餘柱)")
        notes.append("SongEval:跳過(SongEval/eval.py 不存在;跑 install 腳本或手動 clone 可補齊)")
    else:
        tmp_out = Path(tempfile.mkdtemp(prefix="_songeval_", dir=BASE))
        try:
            _, _se_err = _optional_stage([_venv_py(".venv-ml"), "eval.py", "-i", str(song), "-o", str(tmp_out)],
                                         "SongEval 美學", env=dev_env, cwd=_se_dir)
            if _se_err:
                notes.append(f"SongEval:{_se_err}")
            else:
                se_raw = json.loads((tmp_out / "result.json").read_text(encoding="utf-8"))
                songeval = list(se_raw.values())[0] if se_raw else {}
        except Exception as e:
            notes.append(f"SongEval:讀取結果失敗({e})")
        finally:
            shutil.rmtree(tmp_out, ignore_errors=True)

    # ── 第二層 B: Audiobox 四軸 ──
    print("[5/6] Audiobox 美學評分(Meta 模型)...")
    audiobox = {}
    tmp_lst = BASE / f"_tmp_audiobox_{os.getpid()}.jsonl"
    tmp_lst.write_text(json.dumps({"path": str(song)}) + "\n", encoding="utf-8")
    try:
        p, _ab_err = _optional_stage([_venv_exe(".venv-ml", "audio-aes"), str(tmp_lst), "--batch-size", "1"],
                                     "Audiobox 美學", env=dev_env)   # 跟 SongEval 同一個裝置決策
        if _ab_err:
            print("      ↳ 跳過:Audiobox 沒安裝或執行失敗(製作品質等細項會缺)")
            notes.append(f"Audiobox:{_ab_err}")
        else:
            audiobox = _last_json(p.stdout) or {}
    finally:
        tmp_lst.unlink(missing_ok=True)

    # ── 第二層 C: Gemini 曲評(唯一會「聽」並說明理由的一關)──────────
    #
    # ⛔⛔ 2026-07-20 加開關(我把她的金鑰打爆之後才補的):
    #     批次評測是「一首接一首、中間零間隔」地跑。單首沒事,批次就變成對自己金鑰連發。
    #     實例:當天跑 29 首 SUNO + 10 首得獎歌 = **39 次連續 Flash 呼叫** →
    #           金鑰被 Google 擋掉,回 503「high demand」。⚠️ 那句罐頭訊息寫「usually temporary」,
    #           **但實務上她的帳號要約 28 天才恢復** —— 別把 Google 的客套話當真。
    #     → 批次工具預設不碰任何外部 API。要 Gemini 就單首跑,或明確設 SONG_JURY_BATCH_GEMINI=1。
    gemini = None
    _skip_gm = os.environ.get("SONG_JURY_SKIP_GEMINI") == "1"
    if _skip_gm:
        print("[6/6] Gemini 曲評:略過(批次模式,避免連發打爆金鑰)")
        notes.append("Gemini 曲評:批次模式略過(SONG_JURY_SKIP_GEMINI=1)")
    else:
        gm_json = song.with_name(song.stem + "_Gemini曲評.json")
        print("[6/6] Gemini 曲評(六維·聽真音檔·引時間碼)…")
        _, err = _optional_stage([_venv_py(".venv"), str(BASE / "Gemini曲評.py"), str(song),
                                  "--lang", "zh", "--json", str(gm_json)], "Gemini 曲評")
        if err:
            notes.append(err)
        else:
            gemini, err2 = _load_stage_json(gm_json, "Gemini 曲評")
            if err2:
                notes.append(err2)
            gm_json.unlink(missing_ok=True)

    # ── ⛔ Music Flamingo 已移除(2026-07-20,她拍板「沒有參考價值就整個模型拿掉」)──────
    #
    # 29 首實測後判定零貢獻,四項證據:
    #   ①29 首只有 19 首出得了分(34%)—— 根因是本區塊的 VRAM 門檻:SongEval 峰值 20.1GB
    #     沒放開卡時就跳過。⚠️這一項理論上可靠排程修好,不是模型本身的錯,單獨不足以判死。
    #   ②八項裡兩項是常數:structure_completeness 全 19 首恆 100、genre_idiom_fit 恆 60。
    #   ③三個主觀項在 19 首上只吐得出 2-3 個不同值(development_payoff 只有 60/70),
    #     解析度不足以排名。
    #   ④與確定性量測全面對不起來(Spearman,n=19):樂器豐富度↔實際同時樂器數 r=+0.17、
    #     演唱表現↔嗓音品質 r=+0.00、段落變化↔實際編曲變化量 r=-0.39、
    #     製作質感↔Audiobox 製作品質 r=-0.61。十組沒有一組超過 +0.24。
    #     → 它給的不是重複答案,是**矛盾**答案。
    #   ⭐ 是 ②③④ 一起成立才判死,不是只憑 ①。
    #
    # 它唯一的賣點(段落地圖/樂器清單)編曲層次已用 Demucs 六軌【量】出來,不必用模型【猜】。
    #
    # 要復活:把本區塊換回 git 歷史版本即可,MusicFlamingo.py / mf_infer.py 都還留在磁碟上沒刪。
    #        復活前請先拿新資料證明 ②③④ 已改善,不要只修好 ①(VRAM)就當它能用。
    flamingo = None

    # ── 新柱管線(重構庭 2026-07-25 定版)──────────────────────────
    # 演唱聽感(SingMOS,人聲柱 12%)+ 真實距離(MuQ 馬氏,真實柱 60%)+ AI 感(SONICS,顯示軸)
    # 跑在 .venv-audition;venv 或權重不在就 degraded 留痕,不炸產線。
    singmos = None
    realdist = None
    # ⛔ 不可以寫死 Scripts/python.exe:POSIX 上 uv 建的是 bin/python,
    #    寫死 Windows 路徑會讓 Linux/macOS 使用者「明明裝好了卻永遠被判不存在」。
    _aud_py = BASE / ".venv-audition" / ("Scripts/python.exe" if _WIN else "bin/python")
    if not _aud_py.exists():
        notes.append("新柱管線:跳過(.venv-audition 不存在,SingMOS/馬氏/SONICS 缺席)")
    else:
        if vocal_stem:
            print("[7/8] 演唱聽感(SingMOS)…")
            _sj = song.with_name(song.stem + "_演唱聽感.json")
            _, err = _optional_stage([str(_aud_py), str(BASE / "演唱聽感.py"),
                                      str(vocal_stem), "--json", str(_sj)], "演唱聽感")
            if err:
                notes.append(err)
            else:
                singmos, err2 = _load_stage_json(_sj, "演唱聽感")
                if err2:
                    notes.append(err2)
                _sj.unlink(missing_ok=True)
        else:
            notes.append("演唱聽感:跳過(無人聲分軌)")
        print("[8/8] 真實距離(MuQ 馬氏)+ AI 感(SONICS,顯示軸)…")
        _rj = song.with_name(song.stem + "_真實距離.json")
        _, err = _optional_stage([str(_aud_py), str(BASE / "真實距離.py"),
                                  str(song), "--json", str(_rj)], "真實距離")
        if err:
            notes.append(err)
        else:
            realdist, err2 = _load_stage_json(_rj, "真實距離")
            if err2:
                notes.append(err2)
            _rj.unlink(missing_ok=True)

    # ── 整合輸出 ──
    # SUNO 連結自動抓到的歌詞(若有)一併放進 JSON,供下游(情感弧線/詞評)取用
    _lyr_f = song.parent / f"{song.stem}_評分結果" / f"{song.stem}_歌詞.txt"
    _fetched = _lyr_f.read_text(encoding="utf-8").strip() if _lyr_f.exists() else ""
    merged = {
        "file": song.name,
        "layer1_physical": physical,
        "layer1_arrangement": arrangement,      # 新:編曲層次(六軌活躍度/段落組合/頻譜重疊)
        "layer1_harmony": harmony,              # 新:真和弦辨識(與舊「和聲豐富度」並存)
        "layer2_songeval_1to5": songeval,
        "layer2_audiobox_1to10": audiobox,
        "layer2_gemini": gemini,                # 新:六維證據型曲評
        "layer2_flamingo": flamingo,            # 新:段落地圖/樂器/製作質感
        "layer2_singmos": singmos,              # 重構庭:演唱聽感(人聲柱 12%)
        "layer2_realdist": realdist,            # 重構庭:真實距離(真實柱 60%)+SONICS 顯示軸
        # 降級但「不計分」的原始資料放這裡:留著給除錯,⛔ 絕不可以拿去算分或排行
        "degraded_not_scored": degraded_raw or None,
        "layer3_lyrics": "由 Claude 依 rubrics\\ 四把尺評(八家五輪對抗定版,非即興判斷)",
        "fetched_lyrics": _fetched,
        "vocal_stem_used": vocal_stem,          # 有值 = 演唱各項有列分(見 layer1_physical.vocal_detail)
        "stage_notes": notes,                   # 哪些新元件失敗/降級/跳過,誠實留痕
    }

    # ── ⚖️ 九柱組裝 —— 實作在模組層級的 build_pillar_items / build_pillar_totals(見檔案上方)
    _items = build_pillar_items(physical, harmony, arrangement, gemini,
                                songeval, audiobox, singmos, realdist)
    _pt = build_pillar_totals(_items)
    pillar_detail = _pt["柱分"]
    pillar_scores = {p: d["score"] for p, d in pillar_detail.items() if d["score"] is not None}
    _have_p = pillar_scores
    _song_side = _pt["曲側合成"]

    # ⛔ 完整性旗標一定要寫進 JSON:別人(或後續程式/排行榜)拿到這份檔案時,
    #    必須一眼看得出它是不是完整評測 —— 只印在主控台是不夠的。
    merged["pillar_totals"] = {
        **_pt,
        "公式": "總分 = 25.3%×詞(報告階段依四把尺合成)+ 74.7%×曲側八柱加權(缺柱重正規化)",
        "凍結中": ["演唱.rhythm(T2b 10:3)", "和聲.non_diatonic(9:4)"],
        "顯示軸": {"AI感 SONICS P(AI)": _g(realdist, "sonics_p_ai"),
                   "註": "不入分(19:7);新版SUNO漏抓~31%"},
        "出處": "重構庭 2026-07-25 定版(T1-T4;沿革見 docs/權重沿革.md)",
    }
    out_path = song.with_name(song.stem + "_評審團.json")
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    se_avg = (sum(songeval.values()) / len(songeval)) if songeval else None   # SongEval 沒裝時為 None
    print()
    print("=" * 54)
    print("  評審團總表(九柱制,重構庭 2026-07-25 定版)")
    print("=" * 54)
    for pname in ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動"):
        pd = pillar_detail.get(pname) or {}
        sc = pd.get("score")
        tail = f"(缺:{'/'.join(pd['missing'])})" if pd.get("missing") else ""
        print(f"【{pname}柱 {PILLAR_W[pname]:>4.1f}%】 {sc if sc is not None else '—'} / 100 {tail}")
    # ⛔ 缺柱時**不可以把合成分印得像一個正常分數**。
    #    九柱制的滿分定義是「九根柱子都在」;少一根就是換了一把尺,那個數字不能拿去跟別人比,
    #    也不能拿去排行。這裡把「不完整」講在分數同一行,而不是塞在下面的小字裡。
    _lost_p = _pt["缺柱"]
    _lost_w = _pt["缺柱權重合計"]
    if _song_side is not None and not _lost_p:
        print(f"【曲側合成】 {_song_side} / 100(八柱加權;詞柱 25.3% 由報告階段依四把尺合成)")
    elif _song_side is not None:
        print()
        print("!" * 54)
        print(f"  ⛔ 這不是一份完整評測 —— 缺了 {len(_lost_p)} 根柱、合計 {_lost_w}% 權重")
        print(f"     缺柱:{'、'.join(_lost_p)}")
        print(f"     下面這個 {_song_side} 是「只用剩下幾根柱重新歸一化」算出來的,")
        print("     ⛔ 不可與完整評測互比、不可拿去排行、不可當作品的評測結果。")
        print("     → 把安裝補齊(重跑安裝檔或 install 腳本 -CheckOnly 看缺什麼)再評一次。")
        print("!" * 54)
        print(f"【曲側合成(不完整・僅供除錯)】 {_song_side} / 100")
    _pai = _g(realdist, "sonics_p_ai")
    if _pai is not None:
        print(f"【AI 感(顯示軸,不入分)】 P(AI)={_pai:.2f}(新版 SUNO 漏抓~31%,判「真」不保證非 AI)")
    print(f"【凍結中】 演唱.rhythm(T2b 10:3)・和聲.non_diatonic(9:4)—— 過考+單格重開才復權")
    print(f"【物理技術(舊制參考)】 {physical['scores']['total']} / 100(等級 {physical['scores']['grade']})")

    # 演唱各項照常列分;它是否併進上面那個總分,看 weighting.vocal_blended_into_total
    vd = physical.get("vocal_detail") or {}
    if vd:
        print(f"【演唱表現】(人聲柱量測項;柱內權重=重構庭定版)")
        for k, v in vd.items():
            if isinstance(v, dict) and v.get("score") is not None:
                print(f"  ・{VOCAL_LABELS.get(k, k)}:{v['score']:.1f}")

    if arrangement and not arrangement.get("degraded"):
        a = arrangement.get("arrangement") or {}
        lay = arrangement.get("layers") or {}
        print("【編曲層次】(Demucs 六軌;能量成長/編制變化入結構編曲柱,其餘顯示)")
        print(f"  ・樂器組合種類:{a.get('n_unique_configs')}")   # 段落數已廢(V3 11:2)=零顯示
        print(f"  ・前奏→高潮成長:{a.get('intro_to_peak_growth')}  段間變化:{a.get('mean_arrangement_delta')}")
        if lay:
            print(f"  ・同時在響的軌數:{json.dumps(lay, ensure_ascii=False)[:70]}")

    if harmony and not harmony.get("degraded"):
        hm = harmony.get("metrics") or {}
        key = harmony.get("key") or {}
        print(f"【和聲分析】(真和弦辨識;舊「和聲豐富度」仍在物理關內並存)")
        print(f"  ・調性:{key.get('label')}  和弦段落:{harmony.get('n_chord_segments')}")
        for k, v in hm.items():
            if isinstance(v, dict) and v.get("score") is not None:
                print(f"  ・{HARMONY_LABELS.get(k, k)}:{v['score']:.1f}")

    if se_avg is None:
        print("【美學-SongEval】 缺席(沒安裝 SongEval;相關細項不計分,見文末未跑到清單)")
    else:
        print(f"【美學-SongEval】 平均 {se_avg:.2f} / 5")
        for k, v in songeval.items():
            print(f"  ・{SONGEVAL_LABELS.get(k, k)}:{v:.2f}")
    if not audiobox:
        print("【美學-Audiobox】 缺席(沒安裝 Audiobox;相關細項不計分)")
    else:
        print("【美學-Audiobox】(1–10)")
        for k in ("PQ", "CE", "CU", "PC"):
            if k in audiobox:
                print(f"  ・{AUDIOBOX_LABELS[k]}:{audiobox[k]:.2f}")

    if gemini and not gemini.get("degraded"):
        dims = gemini.get("dimensions") or {}
        print("【Gemini 曲評】(聽真音檔,每維引時間碼)")
        for k, v in dims.items():
            if isinstance(v, dict):
                sc = v.get("score")
                cm = (v.get("comment") or "").replace("\n", " ")
                print(f"  ・{k}:{sc if sc is not None else '無法判'}　{cm[:52]}")

    # ⛔ Music Flamingo 成績單區塊已移除(見上方 flamingo = None 處的判死依據)

    print(f"【詞曲文本】 把歌詞貼給 Claude 說「評詞」,依 rubrics\\ 四把尺評")
    if notes:
        print("-" * 54)
        print("⚠️ 本次未完全跑到的項目:")
        for n in notes:
            print(f"  ・{n}")
    print("-" * 54)
    print(f"完整報告:{out_path}")


if __name__ == "__main__":
    main()
