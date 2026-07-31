#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
編曲層次分析(Arrangement Layering)— 滿血版第一關新增元件

用 Demucs htdemucs_6s 把歌分成 6 軌(drums/bass/other/vocals/guitar/piano),
再從「各軌何時在響」算出真正的編曲層次指標 —— 取代舊的「層次=段落能量落差+音色變異數」粗略代理。

⚠️ 設計原則(2026-07-19 使用者定調):**指標先行、權重後定**
   本檔只負責吐乾淨的結構化指標,不給總分、不做加權。
   權重與如何進總分,等所有元件裝定位後由「八家對決」決定。

⚠️ 所有門檻都是啟發式起手值(非論文實測值),已寫進輸出 JSON 的 thresholds 供日後校準。
   驗收方式:拿 10 首編曲厚 + 10 首單薄的歌跑,看 range_N / n_unique_configs 分不分得開。

用法:
    python 編曲層次.py <音檔> [--json 輸出.json] [--stems 分軌快取夾] [--model htdemucs_6s]

必須用「裝了 demucs 的那個 python」跑(解析順序見 評審團.py 的 _find_demucs_py):
    <demucs 的 python> 編曲層次.py song.mp3
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np

# ⭐ 2026-07-20:separate() 抽到 分軌快取.py,和 和聲分析.py 共用同一份 Demucs 快取
#    (快取鍵位元組等價,舊快取照樣讀得到;兩支工具只會燒一次 GPU)。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from 分軌快取 import separate  # noqa: E402

# ── 可調門檻(啟發式起手值,非論文數據)────────────────────────────
SILENCE_FLOOR_DBFS = -55.0   # 絕對靜音地板:低於此一律當沒發聲
REL_ACTIVE_DB = -30.0        # 相對門檻:低於該軌自身 95 百分位這麼多 dB 就當沒發聲
FRAME_SEC = 0.5              # 分析窗長(秒)
SECTION_SEC = 8.0            # 「段落」粗粒度窗長,用來看編曲進出變化


def _db(x):
    return 20.0 * np.log10(np.maximum(x, 1e-10))


def frame_rms_db(mono: np.ndarray, sr: int, frame_sec: float):
    n = max(1, int(sr * frame_sec))
    total = (len(mono) // n) * n
    if total == 0:
        return np.array([])
    f = mono[:total].reshape(-1, n)
    return _db(np.sqrt((f ** 2).mean(axis=1)))


def analyze(stems, sr, sources):
    """把分軌波形變成編曲層次指標。"""
    mono = {s: (w.mean(axis=0) if w.ndim > 1 else w) for s, w in stems.items()}
    rms_db = {s: frame_rms_db(m, sr, FRAME_SEC) for s, m in mono.items()}
    n_frames = min((len(v) for v in rms_db.values()), default=0)
    if n_frames == 0:
        return None
    rms_db = {s: v[:n_frames] for s, v in rms_db.items()}

    # 各軌「在響」的判定:同時過絕對地板與自身相對門檻
    active = {}
    for s, v in rms_db.items():
        ref = np.percentile(v, 95) if len(v) else -120.0
        thr = max(SILENCE_FLOOR_DBFS, ref + REL_ACTIVE_DB)
        active[s] = v > thr

    A = np.vstack([active[s] for s in sources])          # (stem, frame) 布林
    N = A.sum(axis=0).astype(float)                       # 每個時間點同時發聲層數

    # 層數分佈的熵(層數變化越豐富越高)
    vals, counts = np.unique(N, return_counts=True)
    p = counts / counts.sum()
    H_N = float(-(p * np.log2(np.maximum(p, 1e-12))).sum())
    H_N_norm = float(H_N / math.log2(len(sources) + 1)) if len(sources) else 0.0

    # 段落粒度的編曲組態變化
    per_sec = 1.0 / FRAME_SEC
    win = max(1, int(SECTION_SEC * per_sec))
    n_sec = max(1, n_frames // win)
    sec_cfg = []
    for i in range(n_sec):
        seg = A[:, i * win:(i + 1) * win]
        if seg.size == 0:
            continue
        sec_cfg.append(tuple((seg.mean(axis=1) > 0.5).astype(int)))
    uniq_cfg = len(set(sec_cfg))
    deltas = [sum(a != b for a, b in zip(sec_cfg[i], sec_cfg[i + 1])) for i in range(len(sec_cfg) - 1)]
    mean_delta = float(np.mean(deltas)) if deltas else 0.0

    # intro → 最厚段的成長
    sec_N = [float(sum(c)) for c in sec_cfg] or [0.0]
    intro_growth = float(max(sec_N) - sec_N[0])

    # 頻譜覆蓋 & 軌間頻段重疊
    bands = [(20, 120), (120, 400), (400, 1200), (1200, 3500), (3500, 8000), (8000, 16000)]
    occ = np.zeros(len(bands))
    band_of_stem = {}
    for s in sources:
        m = mono[s]
        if not np.any(active[s]):
            band_of_stem[s] = np.zeros(len(bands), dtype=bool)
            continue
        spec = np.abs(np.fft.rfft(m[: sr * 30] if len(m) > sr * 30 else m))
        freqs = np.fft.rfftfreq(len(m[: sr * 30] if len(m) > sr * 30 else m), 1 / sr)
        e = np.array([spec[(freqs >= lo) & (freqs < hi)].sum() for lo, hi in bands])
        e = e / (e.sum() + 1e-12)
        hot = e > 0.10
        band_of_stem[s] = hot
        occ += hot.astype(float)
    spectral_coverage = float((occ > 0).sum() / len(bands))
    overlaps = []
    live = [s for s in sources if np.any(active[s])]
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            a, b = band_of_stem[live[i]], band_of_stem[live[j]]
            u = (a | b).sum()
            overlaps.append(float((a & b).sum() / u) if u else 0.0)
    mean_overlap = float(np.mean(overlaps)) if overlaps else 0.0

    per_stem = {}
    for s in sources:
        v = rms_db[s]
        per_stem[s] = {
            "activity_ratio": round(float(active[s].mean()), 4),
            "mean_level_db": round(float(v[active[s]].mean()), 2) if np.any(active[s]) else None,
            "peak_level_db": round(float(v.max()), 2) if len(v) else None,
        }

    return {
        "per_stem": per_stem,
        "layers": {
            "mean_N": round(float(N.mean()), 3),
            "min_N": int(N.min()),
            "max_N": int(N.max()),
            "range_N": int(N.max() - N.min()),
            "H_N": round(H_N, 4),
            "H_N_norm": round(H_N_norm, 4),
        },
        "arrangement": {
            # ⚖️ n_sections 已廢(2026-07-24 十三家如何評案 V3,11:2):
            #    它 = 總幀數 ÷ 固定窗長,量的是【歌曲長度】不是音樂段落 ——
            #    單調循環破壞下 33→33 一動不動。從指標中移除,不再輸出。
            "n_unique_configs": int(uniq_cfg),
            "mean_arrangement_delta": round(mean_delta, 3),
            "intro_to_peak_growth": round(intro_growth, 2),
        },
        "spectrum": {
            # ⚖️ spectral_coverage 已廢(2026-07-24 G 庭 G4,9:4):
            #    SUNO 鑑別力 0.033、真實得獎歌 0.033 —— 兩邊都是常數,與 n_sections 同標準處理。
            "mean_overlap": round(mean_overlap, 4),
        },
        # ⚖️ 編曲總分(2026-07-24 十三家 H4 案,B 案 11:1:1):
        #    只計【通過針對性 A 層驗證】的兩項等權;其餘指標照算照顯示但不進分,
        #    逐項通過受控破壞驗證後才可加入(與 rhythm/non_diatonic 凍結-復權同構)。
        #    映射 = 對 29 首 B 層基準分佈取百分位(方向明確:成長/變化越大越好),
        #    夾 5..95 避免極端;基準庫擴充時更新 _REF 常數即自動校準。
        "score": _arr_score(intro_growth, mean_delta),
        # ⚖️ 重構庭(2026-07-25)九柱組裝需要兩項【分開】的百分位分數
        #    (結構與編曲柱內:能量成長32/編制變化18,不再等權合併)——
        #    score 保留為兩項等權的舊值供對照,入柱的是下面兩個。
        "score_growth": _pct_score(intro_growth, _GROWTH_REF),
        "score_delta": _pct_score(mean_delta, _DELTA_REF),
        "score_note": "已驗證兩項等權(intro_to_peak_growth+mean_arrangement_delta);"
                      "其餘指標未過針對性驗證,不進分",
    }


# 29 首 B 層實測基準(2026-07-20 批次;擴充基準庫時重抓)
_GROWTH_REF = [1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3,
               4, 4, 4, 4, 4, 4, 4, 4, 4, 5]
_DELTA_REF = [0.35, 0.467, 0.474, 0.6, 0.676, 0.824, 0.833, 1.0, 1.043, 1.111,
              1.125, 1.138, 1.143, 1.167, 1.182, 1.19, 1.2, 1.2, 1.286, 1.304,
              1.321, 1.333, 1.353, 1.395, 1.419, 1.643, 1.76, 1.882, 2.053]


def _pct(v, ref):
    """v 在基準分佈中的百分位(0-100),夾 5..95。"""
    below = sum(1 for x in ref if x < v) + 0.5 * sum(1 for x in ref if x == v)
    return float(np.clip(below / len(ref) * 100.0, 5.0, 95.0))


def _arr_score(growth, delta):
    return round((_pct(growth, _GROWTH_REF) + _pct(delta, _DELTA_REF)) / 2.0, 1)


def _pct_score(v, ref):
    return round(_pct(v, ref), 1)


def main():
    ap = argparse.ArgumentParser(description="編曲層次分析(Demucs 6 軌)")
    ap.add_argument("audio", help="音檔路徑")
    ap.add_argument("--json", dest="json_out", help="輸出 JSON 路徑")
    ap.add_argument("--stems", default=None, help="分軌快取資料夾(預設:音檔旁 _stems)")
    ap.add_argument("--model", default="htdemucs_6s", help="demucs 模型(預設 htdemucs_6s)")
    args = ap.parse_args()

    audio = Path(args.audio).resolve()
    if not audio.exists():
        sys.exit(f"✗ 找不到音檔:{audio}")
    stems_dir = Path(args.stems).resolve() if args.stems else audio.parent / "_stems"

    out = {"file": audio.name, "model": args.model, "degraded": False,
           "thresholds": {"silence_floor_dbfs": SILENCE_FLOOR_DBFS, "rel_active_db": REL_ACTIVE_DB,
                          "frame_sec": FRAME_SEC, "section_sec": SECTION_SEC,
                          "note": "啟發式起手值,非論文實測;校準後可調"}}
    try:
        print(f"[1/2] Demucs 分軌({args.model})…", flush=True)
        stems, sr, sources, cached = separate(audio, stems_dir, args.model)
        out["sources"] = sources
        out["stems_cached"] = cached
        # ⭐ 交接契約:把分軌結果的位置寫進 JSON,下游才拿得到。
        #    評審團.py 靠 vocal_stem 把人聲軌交給 song_scorer --vocal 評「演唱表現」,
        #    和聲分析.py 靠 stems_dir 讀同一份快取 → Demucs 全程只跑一次。
        #    (2026-07-20 實測踩過:沒有這兩個欄位,演唱那一整關會靜靜被跳過,
        #     畫面只寫「無人聲軌」,不會報錯,很容易以為是正常的。)
        cache = stems_dir / f"{audio.stem}__{args.model}"
        out["stems_dir"] = str(cache)
        vs = cache / "vocals.flac"
        out["vocal_stem"] = str(vs) if ("vocals" in sources and vs.exists()) else None
        print(f"      軌:{sources}{'(讀快取)' if cached else ''}", flush=True)

        print("[2/2] 計算編曲層次指標…", flush=True)
        m = analyze(stems, sr, sources)
        if m is None:
            raise RuntimeError("音檔太短或分軌失敗")
        out.update(m)
    except Exception as e:
        out["degraded"] = True
        out["error"] = f"{type(e).__name__}: {e}"
        print(f"✗ 失敗:{out['error']}", file=sys.stderr)

    if not out["degraded"]:
        L, A2, S = out["layers"], out["arrangement"], out["spectrum"]
        print("\n===== 編曲層次 =====")
        print(f"  同時發聲層數  平均 {L['mean_N']}(最少 {L['min_N']} / 最多 {L['max_N']},落差 {L['range_N']})")
        print(f"  層數變化熵    {L['H_N']}(標準化 {L['H_N_norm']})")
        print(f"  編曲組態      {A2['n_unique_configs']} 種,段間平均變動 {A2['mean_arrangement_delta']} 軌")   # n_sections 已廢(V3)
        print(f"  intro→最厚    +{A2['intro_to_peak_growth']} 層")
        print(f"  軌間重疊      {S['mean_overlap']}")   # 頻譜覆蓋已廢(G4 9:4),她令:廢=零顯示
        print("  各軌活躍佔比:", {k: v["activity_ratio"] for k, v in out["per_stem"].items()})

    jp = Path(args.json_out) if args.json_out else audio.with_name(audio.stem + "_編曲層次.json")
    jp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整報告:{jp}")


if __name__ == "__main__":
    main()
