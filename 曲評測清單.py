#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""產出「曲」這一側的完整評測清單 —— 給八家對決裁權重用的底稿。

回答她的問題:「我們現在到底有哪些『曲』的模型跟參數」

⭐ 全部從實際檔案與實測資料產生,不從記憶背:
   - 有哪些關卡  → 讀 評審團.py 的實際呼叫
   - 有哪些指標  → 讀 _批次結果/批次結果.json(29 首實測)
   - 鑑別力      → 現算(正規化全距)
   - 顯示與否    → 讀 顯示規則.py
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from statistics import pstdev

os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent.resolve()


def _load(name, path):
    s = importlib.util.spec_from_file_location(name, BASE / path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


B = _load("b", "批次評測.py")
D = _load("d", "顯示規則.py")

# 指標中文名。⚠️ Gemini 的 M1~M6 是代號,名稱唯一真理來源是 Gemini曲評.py 的 DIM_NAMES —— 不可自行翻譯。
LABELS = {
    "Gemini.M1": "編曲結構與能量弧線", "Gemini.M2": "旋律與記憶點", "Gemini.M3": "節奏與律動",
    "Gemini.M4": "配器與音色", "Gemini.M5": "人聲表現", "Gemini.M6": "曲風執行與創新",
    "SongEval.Coherence": "整體連貫性", "SongEval.Musicality": "整體音樂性",
    "SongEval.Memorability": "記憶點", "SongEval.Clarity": "結構清晰度",
    "SongEval.Naturalness": "人聲自然度",
    "Audiobox.PQ": "製作品質", "Audiobox.PC": "製作複雜度",
    "Audiobox.CE": "內容感染力", "Audiobox.CU": "內容實用性",
    "和聲.chord_vocabulary": "和弦詞彙", "和聲.harmonic_rhythm": "和聲節奏",
    "和聲.non_diatonic": "非調內和弦", "和聲.cadence": "終止式",
    "和聲.key_stability": "調性穩定", "和聲.extended_chords": "延伸和弦",
    "和聲.fifth_motion": "五度動線",
    "演唱.pitch": "音準", "演唱.rhythm": "節奏準度", "演唱.stability": "長音穩定度",
    "演唱.vibrato": "顫音", "演唱.dynamics": "動態控制", "演唱.voice_quality": "嗓音品質",
    "演唱.range": "音域",
    "物理.混音.spectral_balance": "頻譜平衡", "物理.混音.structure": "層次鋪陳",
    "物理.混音.harmony": "和聲豐富度", "物理.混音.stereo": "立體聲寬度",
    "物理.混音.dynamic_range": "動態範圍", "物理.混音.clipping": "削波", "物理.混音.loudness": "整體響度",
    "物理.mix": "混音總分", "物理.total": "物理總分", "物理.vocal": "演唱總分",
    "編曲.intro_to_peak_growth": "開場到高潮成長", "編曲.mean_arrangement_delta": "段落間編曲變化量",
    "編曲.mean_N": "平均同時樂器數", "編曲.n_sections": "段落數", "編曲.min_N": "最少同時樂器數",
    "編曲.max_N": "最多同時樂器數", "編曲.range_N": "樂器數跨度", "編曲.H_N": "樂器數熵",
    "編曲.H_N_norm": "樂器數熵(正規化)", "編曲.n_unique_configs": "樂器組合種類",
    "編曲.mean_overlap": "樂器重疊度", "編曲.spectral_coverage": "頻譜覆蓋率",
}

# 每個來源:引擎是什麼、跑在哪、量尺、本質(量測 vs 模型判斷)
SOURCES = {
    "編曲": ("Demucs htdemucs_6s 六軌分離 + 訊號統計", "anaconda", "各異", "🔬 量測"),
    "和聲": ("librosa chroma_cqt + 121 態模板 + Viterbi 和弦辨識", "anaconda", "0-100", "🔬 量測"),
    "物理": ("librosa + pyloudnorm(EBU R128)+ parselmouth", ".venv", "0-100", "🔬 量測"),
    "演唱": ("Demucs 人聲軌 + parselmouth(jitter/shimmer/HNR)", ".venv", "0-100", "🔬 量測"),
    "SongEval": ("SongEval(16 位音樂人 × 2400 首訓練)", ".venv-ml", "1-5", "🤖 模型"),
    "Audiobox": ("Meta Audiobox Aesthetics", ".venv-ml", "1-10", "🤖 模型"),
    "Gemini": ("Gemini(聽真音檔、引時間碼)", ".venv + API", "0-100", "🤖 模型"),
}


def main():
    store = BASE / "_批次結果" / "批次結果.json"
    if not store.exists():
        sys.exit("找不到 _批次結果/批次結果.json")
    d = json.loads(store.read_text(encoding="utf-8"))
    rows = [B.flatten(v) for v in d.values() if "error" not in v]

    stats = {}
    for k in sorted({k for r in rows for k in r}):
        vals = [r[k] for r in rows if r.get(k) is not None]
        if len(vals) < 2:
            continue
        hi, lo = max(vals), min(vals)
        scale = 100.0 if hi > 10 else (10.0 if hi > 5 else 5.0)
        stats[k] = dict(n=len(vals), lo=lo, hi=hi, nr=(hi - lo) / scale, sd=pstdev(vals))

    L = ["# 「曲」側評測清單(現況)", "",
         f"> 由 `曲評測清單.py` 從實際檔案與 {len(rows)} 首實測資料產生,非人工維護。",
         "> ⛔ Music Flamingo 已於 2026-07-20 整個移除(模型+腳本已刪,釋出 15GB)。", "",
         "## 一、六個來源", "",
         "| 來源 | 引擎 | 跑在哪 | 量尺 | 本質 | 指標數 |",
         "|---|---|---|---|---|---|"]
    counts = {}
    for k in stats:
        counts[k.split(".")[0]] = counts.get(k.split(".")[0], 0) + 1
    for src, (eng, where, scale, kind) in SOURCES.items():
        L.append(f"| **{src}** | {eng} | {where} | {scale} | {kind} | {counts.get(src, 0)} |")

    L += ["", "⭐ **🔬量測 vs 🤖模型** 是這份清單最重要的一欄:",
          "量測類可以被受控破壞驗證(A 層);模型類只能看它跟量測對不對得起來。", "",
          "## 二、逐項指標(依鑑別力排序)", "",
          "鑑別力 = 正規化全距(該指標在 29 首之間的最大差距 ÷ 量尺)。",
          "接近 0 = 對每首歌都給差不多的分,給它權重等於把總分交給雜訊。", "",
          "| 指標 | 來源 | 本質 | 最低 | 最高 | 鑑別力 | 顯示 |",
          "|---|---|---|---|---|---|---|"]
    for k, s in sorted(stats.items(), key=lambda x: -x[1]["nr"]):
        src = k.split(".")[0]
        if src not in SOURCES:
            continue
        show, mode, _ = D.verdict(k, None)
        disp = {"show": "✅", "hide": "🔇 隱藏", "exception": "🩺 只在異常時"}.get(mode, "✅")
        flag = " ⛔常數" if s["nr"] < 0.02 else (" ⚠️低" if s["nr"] < 0.10 else "")
        L.append(f"| `{k}`{flag} | {src} | {SOURCES[src][3][0]} | {s['lo']:.2f} | {s['hi']:.2f} | "
                 f"{s['nr']:.3f} | {disp} |")

    # 來源層級平均
    agg = {}
    for k, s in stats.items():
        src = k.split(".")[0]
        if src in SOURCES:
            agg.setdefault(src, []).append(s["nr"])
    L += ["", "## 三、來源平均鑑別力", "",
          "| 來源 | 本質 | 指標數 | 平均鑑別力 | 最強項 |", "|---|---|---|---|---|"]
    for src, vs in sorted(agg.items(), key=lambda x: -sum(x[1]) / len(x[1])):
        best = max((k for k in stats if k.startswith(src + ".")), key=lambda k: stats[k]["nr"])
        L.append(f"| {src} | {SOURCES[src][3]} | {len(vs)} | **{sum(vs)/len(vs):.3f}** | "
                 f"`{best.split('.',1)[1]}` {stats[best]['nr']:.3f} |")

    L += ["", "## 四、目前的權重狀態(重構庭 2026-07-25 九柱定版;唯一真理來源=評審團.py pillar_totals)", "",
          "| 柱 | 權重% |", "|---|---|",
          "| 詞(四把尺,報告階段合成) | 25.3 |",
          "| 人聲 15.2 / 和聲 13.6 / 結構編曲 12.6 / 聲學 12.1 | 曲側主幹 |",
          "| 旋律記憶 6.1 / 真實風格 6.1 / 整體 5.1 / 律動 4.0 | 曲側輔翼 |",
          "| 凍結(權重0):演唱.rhythm(T2b 10:3)、和聲.non_diatonic(9:4) | 復權須過考+單格重開 |",
          "| 復權:和弦詞彙(13:0,過濾版)、LRA;顯示軸:SONICS P(AI) 不入分(19:7) | - |",
          "| 已廢(零顯示):編曲.n_sections、編曲.spectral_coverage | - |",
          "",
          "機讀定版:多語詞評計畫\\權重辯論_20260723\\T_定版權重.json;⛔ 改任何一格=單格重開辯論。", ""]

    out = BASE / "_曲評測清單.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:6]))
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
