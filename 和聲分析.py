#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
和聲分析(Chord Recognition & Harmonic Analysis)— 滿血版第一關新增元件

為什麼要做:舊的「和聲豐富度」根本沒有辨識任何一個和弦。
song_scorer.py 的 harmony_metrics() 原文是這樣:

    def harmony_metrics(y_mono, sr, beat_times):
        \"\"\"和聲豐富度:先做諧波分離去掉鼓的干擾,
        量「使用的音級數」+「半小節解析度的和聲變化率」(向量餘弦相似度)。\"\"\"
        yh = librosa.effects.harmonic(y_mono)
        chroma = librosa.feature.chroma_cqt(y=yh, sr=sr)
        mean_c = chroma.mean(axis=1)
        mean_c = mean_c / (mean_c.max() + 1e-9)
        n_pcs = int(np.sum(mean_c > 0.25))          # ← 只是「比最大值大 25% 的音級有幾格」
        ...
        sims = np.sum(sync[:, :-1] * sync[:, 1:], axis=0)
        ccr = float(np.sum(sims < 0.85)) / max(dur, 1e-9)   # ← 只是「相鄰半小節 chroma 像不像」

    也就是說:它量的是「整首用了幾個音級」和「chroma 多久變一次」。
    它不知道那是 C 還是 Am,不知道有沒有 V→I,不知道有沒有離調和弦。
    一首 C-G-Am-F 死循環的口水歌,跟一首走 ii-V-I 還帶借用和弦的歌,
    在舊指標上可以拿到幾乎一樣的分數。

本檔做的是真的和弦辨識:12 個根音 × 10 種和弦品質 + 1 個無和弦狀態 = 121 個狀態,
餘弦比對 → Viterbi 解碼(拍點同步),再從和弦序列算出真正的和聲學指標。

⚠️⚠️ 準確度誠實聲明(也已寫進輸出 JSON 的 accuracy_caveat):
   模板比對 + Viterbi 這條路線在流行樂上大約落在 MIREX maj/min 65–75% 的水準,
   離深度學習 SOTA(~83–87%)還有一段。而且:
     • 三和弦(maj/min)最可靠;
     • 七和弦、sus、dim/aug 這些延伸品質「明顯更不可靠」——
       七音常常只是主唱或旋律路過的音,會被誤判成和弦內音;
     • 所以 extended_chord_usage 這一項要當「風格傾向的粗略指標」看,不能當樂譜。
   下面所有分數都是在這個前提下的相對比較,不是絕對樂理判定。

⚠️ 設計原則(2026-07-19 使用者定調):**每個指標都要有分數,但不給總分**
   本檔對每一項都吐 raw(原始量測)+ score(0–100),但刻意不做加權總分——
   權重由日後「八家對決」決定。所有門檻都在輸出 JSON 的 thresholds 區塊裡,
   標明是啟發式起手值,待校準。

用法:
    python 和聲分析.py <音檔> [--json 輸出.json] [--stems 分軌快取夾]
                       [--model htdemucs_6s] [--no-stems]

必須用「裝了 demucs 的那個 python」跑(解析順序見 評審團.py 的 _find_demucs_py):
    <demucs 的 python> 和聲分析.py "你的歌.mp3"
"""
import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 分軌快取 import separate, mix_stems  # noqa: E402

# ── 分析參數(啟發式起手值,非論文實測值)──────────────────────────
SR = 22050              # 跟 song_scorer.py 的 SR_MUSIC 一致,方便日後交叉比對
HOP = 512               # ≈23.2ms @22050,chroma 與拍點共用
HARMONY_STEMS = ("other", "guitar", "piano")   # 只餵和聲樂器,丟掉 drums/vocals(見 分軌快取.mix_stems 註解)
SOFTMAX_T = 0.08        # 餘弦相似度→機率的溫度。越小越果斷。
                        # 0.08 是實驗調的:太大(0.3)後驗糊成一團、Viterbi 只會挑最常見和弦;
                        # 太小(0.02)則等於沒做 Viterbi,逐拍亂跳。
N_CHORD_PENALTY = 0.88  # 「無和弦(N)」用平坦模板。但真實 chroma 本來就不稀疏,
                        # 平坦模板天生佔便宜,不壓一下會整首都判 N。0.88 是壓到
                        # 「只有真的靜音/純打擊段才會贏」的位置。這是調出來的,不是理論值。
SELF_LOOP = 0.85        # Viterbi 自轉移機率:和弦平均撐 1/(1-0.85)≈6.7 拍。
                        # 流行樂常見「一小節一和弦」= 4 拍,設高一點是刻意的:
                        # 寧可平滑過頭少抓幾個經過和弦,也不要逐拍亂跳製造假的和聲豐富度。
MIN_SEG_BEATS = 1       # 短於這個拍數的和弦段落視為雜訊(目前不過濾,保留鉤子)

ROOTS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# 10 種和弦品質的音程結構(半音)。刻意不放 9th/11th/13th:
# chroma 只有 12 格,九和弦跟七和弦在 chroma 上幾乎無法區分,加了只是製造假精度。
QUALITIES = {
    "maj":  (0, 4, 7),
    "min":  (0, 3, 7),
    "dim":  (0, 3, 6),
    "aug":  (0, 4, 8),
    "maj7": (0, 4, 7, 11),
    "min7": (0, 3, 7, 10),
    "dom7": (0, 4, 7, 10),
    "m7b5": (0, 3, 6, 10),
    "sus4": (0, 5, 7),
    "sus2": (0, 2, 7),
}

# MIREX maj/min 收斂映射:把 10 種品質壓回「大三/小三」兩類。
# 這是 MIREX 和弦辨識評測的標準做法,不是我自己發明的簡化。
# 用途:詞彙豐富度只在這一層計分,因為這層才有 65–75% 的可靠度,
#       完整品質層(七和弦/sus)的誤判會把「同一個和聲功能」拆成好幾個假和弦。
_MIREX_MAJMIN = {"maj": "maj", "maj7": "maj", "dom7": "maj", "aug": "maj",
                 "sus4": "maj", "sus2": "maj",
                 "min": "min", "min7": "min", "dim": "min", "m7b5": "min"}

# Krumhansl-Kessler 調性側寫(1982 年實驗值,這個是有文獻根據的,不是我猜的)
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _piecewise(x, pts):
    """折線映射:pts 是 [(輸入, 分數), ...] 由小到大,中間線性內插,兩端夾住。
    跟 song_scorer.py 的 piecewise() 同語意,方便她日後把兩邊門檻放一起校準。"""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return float(np.interp(float(x), xs, ys))


def build_templates():
    """建 121 個狀態的模板矩陣 (121, 12),每列 L2 normalize。

    模板用「二元 0/1」而不是加權(根音 1.0 / 五音 0.8 / 三音 0.6 那種)。
    加權版在乾淨錄音上略好,但我們吃的是 Demucs 分出來的 other 軌,
    殘留串音本來就會弄髒相對強度,加權反而是在擬合噪音。這是取捨,不是最佳解。
    """
    labels, mats = [], []
    for r in range(12):
        for q, ivs in QUALITIES.items():
            v = np.zeros(12)
            for iv in ivs:
                v[(r + iv) % 12] = 1.0
            mats.append(v / np.linalg.norm(v))
            labels.append((r, q))
    v = np.ones(12) / math.sqrt(12.0)      # N = 無和弦(平坦模板)
    mats.append(v)
    labels.append((-1, "N"))
    return np.vstack(mats), labels


def chord_name(root, qual):
    if root < 0:
        return "N"
    return ROOTS[root] + ("" if qual == "maj" else qual)


def load_harmony_audio(audio: Path, stems_dir: Path, model_name: str, use_stems: bool):
    """回 (和聲用單聲道 y, 全曲混音 y, sr, 資訊dict)。

    ⭐ 關鍵設計:chroma 吃分軌、拍點吃原混音。
       和弦要乾淨的和聲軌才不會被鼓的寬頻瞬態糊掉;
       但抓拍點反而「需要」鼓,拿掉鼓的 beat_track 會明顯變差。所以兩邊分開餵。
    """
    import librosa
    info = {"stem_mode": "stems", "stems_cached": None, "stem_error": None}

    y_mix, _ = librosa.load(str(audio), sr=SR, mono=True)

    if not use_stems:
        info["stem_mode"] = "harmonic_fallback"
        return librosa.effects.harmonic(y_mix), y_mix, SR, info

    try:
        stems, sr_st, sources, cached = separate(audio, stems_dir, model_name)
        info["stems_cached"] = cached
        info["sources"] = sources
        y_h = mix_stems(stems, HARMONY_STEMS)
        y_h = librosa.resample(y_h, orig_sr=sr_st, target_sr=SR)
        return y_h, y_mix, SR, info
    except Exception as e:
        # 降級但不放棄:退回 HPSS 諧波分量(就是舊 harmony_metrics 用的那招)。
        # 結果會變差(鼓的殘留會抬高不相關音級),所以誠實標記 degraded。
        info["stem_mode"] = "harmonic_fallback"
        info["stem_error"] = f"{type(e).__name__}: {e}"
        return librosa.effects.harmonic(y_mix), y_mix, SR, info


def decode_chords(y_h, y_mix, sr):
    """chroma → 拍點同步 → 餘弦比對 → Viterbi。回 (segments, 附帶資訊)。"""
    import librosa

    tempo, beat_frames = librosa.beat.beat_track(y=y_mix, sr=sr, hop_length=HOP)
    tempo = float(np.atleast_1d(tempo)[0])

    # CQT chroma:對數頻率軸,低音區解析度遠優於 STFT chroma,和弦辨識標配。
    C = librosa.feature.chroma_cqt(y=y_h, sr=sr, hop_length=HOP)
    n_frames = C.shape[1]
    if n_frames < 8:
        raise RuntimeError("音檔太短,chroma 幀數不足")

    # 拍點切段:自己切而不用 librosa.util.sync,因為要精確拿到每段的起訖秒數。
    bounds = [0] + [int(b) for b in beat_frames if 0 < int(b) < n_frames] + [n_frames]
    bounds = sorted(set(bounds))
    if len(bounds) < 3:
        # 抓不到拍(純氛圍/自由速度)→ 退回固定 0.5 秒窗,至少還能吐東西
        step = max(1, int(0.5 * sr / HOP))
        bounds = list(range(0, n_frames, step)) + [n_frames]

    segs_chroma, segs_time = [], []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b <= a:
            continue
        segs_chroma.append(np.median(C[:, a:b], axis=1))   # median 比 mean 抗過渡幀
        # ⚠️ 一定要轉成 python float:frames_to_time 回的是 np.float64,
        #    一路帶到最後 json.dumps 會直接 TypeError。
        segs_time.append((float(librosa.frames_to_time(a, sr=sr, hop_length=HOP)),
                          float(librosa.frames_to_time(b, sr=sr, hop_length=HOP))))
    X = np.vstack(segs_chroma).T                            # (12, n_seg)
    X = X / (np.linalg.norm(X, axis=0, keepdims=True) + 1e-9)

    T, labels = build_templates()
    sim = T @ X                                             # (121, n_seg) 餘弦相似度
    sim[-1, :] *= N_CHORD_PENALTY                           # 壓 N 狀態(見常數註解)

    # softmax 成「每格總和 1」的後驗,viterbi_discriminative 要求這個形狀
    z = (sim - sim.max(axis=0, keepdims=True)) / SOFTMAX_T
    P = np.exp(z)
    P /= P.sum(axis=0, keepdims=True)

    trans = librosa.sequence.transition_loop(len(labels), SELF_LOOP)
    path = librosa.sequence.viterbi_discriminative(P, trans)

    # 「信心」用被選中狀態的餘弦相似度,不用後驗機率。
    # 理由:121 個狀態時後驗必然被攤薄(實測平均只有 0.09),那個數字對使用者沒有意義,
    # 拿去跟別首比也沒有基準。餘弦擬合度 0–1 可解釋:~0.85 以上代表 chroma 真的長得像那個和弦,
    # 低於 ~0.7 代表整首解碼都在硬湊,該首的和聲指標要打折看。
    idx = np.arange(sim.shape[1])
    conf = float(np.mean(sim[path, idx]))
    post = float(np.mean(P[path, idx]))

    # 合併相鄰同和弦 → 段落
    segments = []
    for i, st in enumerate(path):
        root, qual = labels[st]
        t0, t1 = segs_time[i]
        if segments and segments[-1]["root"] == root and segments[-1]["quality"] == qual:
            segments[-1]["end"] = t1
            segments[-1]["beats"] += 1
        else:
            segments.append({"start": t0, "end": t1, "root": int(root),
                             "quality": qual, "name": chord_name(root, qual), "beats": 1})

    return segments, {"tempo_bpm": round(tempo, 1), "n_beats": len(segs_time),
                      "template_fit": round(conf, 4), "mean_posterior": round(post, 4),
                      "chroma_frames": n_frames, "mean_chroma": X.mean(axis=1)}


def estimate_key(mean_chroma):
    """KK 側寫相關係數估調。回 (root, mode, 相關強度)。
    跟 song_scorer.estimate_key() 同一套方法,但吃的是分軌後的 chroma,理論上更準。"""
    best = (0, "major", -2.0)
    for r in range(12):
        for prof, mode in ((KK_MAJOR, "major"), (KK_MINOR, "minor")):
            c = float(np.corrcoef(mean_chroma, np.roll(prof, r))[0, 1])
            if c > best[2]:
                best = (r, mode, c)
    return best


def diatonic_set(key_root, mode):
    """該調的「調內和弦」集合(root, quality)。

    判斷取捨(誠實說明):
      • sus4/sus2 只要蓋在調內音級上就算調內。sus 是延遲解決的裝飾,
        不該被算成「離調的冒險」,不然所有走 sus 的抒情歌都會被誤判成很前衛。
      • 小調把和聲小調的 V(maj/dom7)與 vii°算進調內。流行樂的小調幾乎必用,
        當成「借用和弦」會讓 non_diatonic_ratio 整片虛高。
    """
    out = set()
    if mode == "major":
        degs = [0, 2, 4, 5, 7, 9, 11]
        tri = ["maj", "min", "min", "maj", "maj", "min", "dim"]
        sev = ["maj7", "min7", "min7", "maj7", "dom7", "min7", "m7b5"]
    else:
        degs = [0, 2, 3, 5, 7, 8, 10]
        tri = ["min", "dim", "maj", "min", "min", "maj", "maj"]
        sev = ["min7", "m7b5", "maj7", "min7", "min7", "maj7", "dom7"]
    for d, t, s in zip(degs, tri, sev):
        r = (key_root + d) % 12
        out.add((r, t))
        out.add((r, s))
        out.add((r, "sus4"))
        out.add((r, "sus2"))
    if mode == "minor":
        v = (key_root + 7) % 12
        out.update({(v, "maj"), (v, "dom7")})
        lead = (key_root + 11) % 12
        out.add((lead, "dim"))
    return out


def find_cadences(segments, key_root, mode):
    """數終止式。只認和弦「相鄰兩個」的功能關係——
    真正的終止式要看樂句邊界(phrase boundary),我們沒有樂句切分,
    所以這裡數的是「終止式型的和弦動線」,會比實際終止式多。誠實記在 note 裡。"""
    V = (key_root + 7) % 12
    IV = (key_root + 5) % 12
    tonic_q = {"maj", "maj7"} if mode == "major" else {"min", "min7"}
    vi = (key_root + 9) % 12 if mode == "major" else (key_root + 8) % 12
    vi_q = {"min", "min7"} if mode == "major" else {"maj", "maj7"}

    auth = plagal = decept = 0
    for a, b in zip(segments, segments[1:]):
        ar, aq, br, bq = a["root"], a["quality"], b["root"], b["quality"]
        if ar == V and aq in {"maj", "dom7", "sus4"}:
            if br == key_root and bq in tonic_q:
                auth += 1
            elif br == vi and bq in vi_q:
                decept += 1
        elif ar == IV and aq in {"maj", "min", "maj7"} and br == key_root and bq in tonic_q:
            plagal += 1
    return {"authentic": auth, "plagal": plagal, "deceptive": decept}


def key_stability(segments, key_root, mode, win_sec=20.0, hop_sec=10.0):
    """滑動窗重估調性,看有幾成的窗跟全曲調一致。
    用和弦時間佔比堆出每個窗的 pitch-class 分布(而不是重跑 chroma),
    因為我們要量的是「和聲有沒有轉調」,不是「音色有沒有變」。"""
    if not segments:
        return 1.0, 0
    total = segments[-1]["end"]
    if total < win_sec:
        return 1.0, 1
    agree = n = 0
    t = 0.0
    while t + win_sec <= total + 1e-6:
        pcp = np.zeros(12)
        for s in segments:
            if s["root"] < 0:
                continue
            ov = min(s["end"], t + win_sec) - max(s["start"], t)
            if ov > 0:
                for iv in QUALITIES[s["quality"]]:
                    pcp[(s["root"] + iv) % 12] += ov
        n += 1
        if pcp.sum() > 0:
            r, m, _ = estimate_key(pcp)
            if r == key_root and m == mode:
                agree += 1
        t += hop_sec
    return (agree / n if n else 1.0), n


def analyze(segments, extra):
    """把和弦序列變成有分數的指標。每一項都有 raw + score,不給總分(規則 1)。"""
    dur_by_chord = Counter()
    for s in segments:
        dur_by_chord[s["name"]] += s["end"] - s["start"]
    total_dur = sum(dur_by_chord.values()) or 1e-9
    played = {k: v for k, v in dur_by_chord.items() if k != "N"}
    played_dur = sum(played.values()) or 1e-9

    key_root, mode, key_corr = estimate_key(extra["mean_chroma"])
    dia = diatonic_set(key_root, mode)

    # ① 和弦詞彙豐富度 ── 只算佔比 ≥1% 的和弦,濾掉 Viterbi 邊界抖出來的一次性雜訊
    #
    # ⭐ 分數建在「MIREX maj/min 收斂層」而不是完整品質層。原因是實測踩到的坑:
    #    Storm and Stars 解出 15 種「和弦」,但裡面 A# 與 A#maj7、Gmin 與 Gmin7、
    #    Csus2/Csus4/Cmin 其實是同一個和聲功能被七音/掛留音的誤判拆成好幾個。
    #    用完整品質層計分,等於拿我們自己聲明「明顯較不可靠」的那一層去灌詞彙分。
    #    所以主分用收斂後的三和弦數(可靠層),完整品質數只當附帶資訊輸出。
    sig = [k for k, v in played.items() if v / played_dur >= 0.01]
    n_unique = len(sig)

    tri_dur = Counter()
    for k, v in played.items():
        r, q = _parse(k)
        tri_dur[(r, _MIREX_MAJMIN[q])] += v
    sig_tri = {k: v for k, v in tri_dur.items() if v / played_dur >= 0.01}
    n_triads = len(sig_tri)
    p = np.array(list(sig_tri.values()), dtype=float)
    p = p / p.sum() if p.sum() > 0 else p
    H = float(-(p * np.log2(np.maximum(p, 1e-12))).sum()) if len(p) else 0.0
    H_norm = H / math.log2(24)     # 以「24 個大小三和弦」當滿分參考點
    # 門檻理由:流行樂典型 4–6 個和弦(四和弦口水歌就是 4)。低於 3 個是死循環;
    # 超過 ~12 種在 65–75% 準確度的解碼器下多半是誤判碎片,不是真的豐富,所以高端要往下修。
    s_vocab = 0.6 * _piecewise(n_triads, [(1, 20), (2, 35), (3, 50), (4, 65), (6, 84),
                                          (8, 96), (10, 100), (12, 100), (16, 84), (24, 65)]) \
            + 0.4 * _piecewise(H_norm, [(0.2, 30), (0.35, 55), (0.5, 78), (0.62, 95),
                                        (0.72, 100), (0.85, 90)])
    # ⚖️ 復權版詞彙分(重構庭 T1 13:0,2026-07-25):建在【過濾詞彙】n_triads_filtered 上
    #    (只計順階+有聲部導向解決的離調;J2 考卷實證:污染增量 +7→+2)。
    #    s_vocab(原始版)照算照顯示供對照;入分的是這個過濾版。熵項沿用(熵對污染不敏感度低)。

    # ② 和聲節奏 ── 每拍換幾次和弦
    n_changes = max(0, len(segments) - 1)
    beats = max(1, extra["n_beats"])
    chg_per_beat = n_changes / beats
    chg_per_sec = n_changes / max(total_dur, 1e-9)
    # 0.25/拍 = 一小節一和弦(流行樂中位),0.5 = 半小節一和弦(較活躍)。
    # 太低=氛圍鋪底,太高=不是真的變化就是解碼器在抖。
    s_rhythm = _piecewise(chg_per_beat, [(0.02, 25), (0.06, 45), (0.12, 65), (0.25, 92),
                                         (0.4, 100), (0.6, 92), (0.85, 70), (1.2, 45)])

    # ③ 離調/借用和弦佔比
    nd = sum(v for k, v in dur_by_chord.items() if k != "N"
             and (_parse(k) not in dia))
    nd_ratio = nd / played_dur
    # ⚖️ 2026-07-24 依十三家裁決修寫(H2 D2,11:2「修-凍結至復權」):
    #    舊版只看佔比,甜蜜點在 20% 給滿分 —— 分不出【成熟借用】與【隨機污染】,
    #    針對性 A 層實測:和弦污染破壞反而 55→67.5 加分。
    #    新版把每個非調內和弦分類:
    #      結構性 = 有「解決」(下一個和弦回到調內)或「重現」(同一離調和弦出現 ≥3 段)
    #      隨機性 = 孤立出現、不解決、不重現 → 視為污染,扣分
    #    甜蜜點曲線只適用【結構性】佔比;隨機性另計懲罰。
    nd_structured = nd_random = 0.0
    _nd_count = {}
    for _s in segments:
        if _s["name"] != "N" and _parse(_s["name"]) not in dia:
            _nd_count[_s["name"]] = _nd_count.get(_s["name"], 0) + 1
    for _i, _s in enumerate(segments):
        if _s["name"] == "N" or _parse(_s["name"]) in dia:
            continue
        _dur = _s["end"] - _s["start"]
        # v3(終版):「解決」須符合功能和聲的根音動線 —— 下行五度(屬功能解決)、
        # 半音下行(bII/bVI 型)、上行全音(bVII→I,流行/搖滾最常見借用)。
        # v2 教訓:「相鄰接回調內」太寬,隨機污染的段落邊界也能沾邊(structured 虛高 0.234)。
        _resolved = False
        if (_i + 1 < len(segments) and segments[_i + 1]["name"] != "N"
                and _parse(segments[_i + 1]["name"]) in dia):
            _r_nd = _parse(_s["name"])[0]
            _r_next = _parse(segments[_i + 1]["name"])[0]
            _motion = (_r_next - _r_nd) % 12
            _resolved = _motion in (5, 11, 2)   # 下五度 / 半音下行 / 全音上行
        # ⛔ 第一版曾有「重現≥3 次」判準 —— 復權考卷實測被移調鑽洞:
        #    整段±1半音的污染會把歌的和弦循環移調複製,離調和弦自然重現 → 全被誤判結構性。
        #    只留「解決」(功能性借用的正字標記);_nd_count 保留供診斷。
        if _resolved:
            nd_structured += _dur
        else:
            nd_random += _dur
    nd_structured_ratio = nd_structured / played_dur
    nd_random_ratio = nd_random / played_dur

    # ⚖️ chord_vocabulary 修法(2026-07-24 J 庭 J2,13:0「修-凍結」):
    #    污染實測讓詞彙分暴漲(樂團母本 67.4→97,+29.6)—— 「詞彙多=好」被垃圾灌爆。
    #    修法:詞彙只計【調內和弦 + 有聲部進行解決的離調和弦】(重用 v3 判準),
    #    污染和弦不算數。過復權考卷前,vocabulary 分數凍結不進總分。
    _filtered_triads = set()
    for _i, _s in enumerate(segments):
        if _s["name"] == "N":
            continue
        _pq = _parse(_s["name"])
        _keep = _pq in dia
        if not _keep and _i + 1 < len(segments) and segments[_i + 1]["name"] != "N"                 and _parse(segments[_i + 1]["name"]) in dia:
            _keep = ((_parse(segments[_i + 1]["name"])[0] - _pq[0]) % 12) in (5, 11, 2)
        if _keep:
            _filtered_triads.add((_pq[0], _MIREX_MAJMIN.get(_pq[1], _pq[1])))
    n_triads_filtered = len(_filtered_triads)
    s_nd = _piecewise(nd_structured_ratio, [(0.0, 55), (0.04, 70), (0.10, 86), (0.20, 100),
                                            (0.32, 90), (0.45, 68), (0.60, 45), (0.80, 30)])
    s_nd = max(10.0, s_nd - min(45.0, 250.0 * nd_random_ratio))   # 隨機污染懲罰(封頂 45 分)
    # 復權版詞彙分:同曲線、改吃過濾詞彙數(見上方 s_vocab 註)
    s_vocab_filtered = 0.6 * _piecewise(n_triads_filtered,
                                        [(1, 20), (2, 35), (3, 50), (4, 65), (6, 84),
                                         (8, 96), (10, 100), (12, 100), (16, 84), (24, 65)]) \
                     + 0.4 * _piecewise(H_norm, [(0.2, 30), (0.35, 55), (0.5, 78), (0.62, 95),
                                                 (0.72, 100), (0.85, 90)])

    # ④ 終止式
    cad = find_cadences(segments, key_root, mode)
    cad_total = cad["authentic"] + cad["plagal"] + cad["deceptive"]
    minutes = max(total_dur / 60.0, 1e-9)
    cad_pm = cad_total / minutes
    s_cad = _piecewise(cad_pm, [(0, 35), (0.5, 58), (1.0, 75), (2.0, 92),
                                (3.5, 100), (7.0, 92), (12.0, 75)])

    # ⑤ 調性穩定度
    ks, n_win = key_stability(segments, key_root, mode)
    # 1.0 不給滿分是刻意的:完全零轉調 = 沒有和聲上的戲劇性。
    s_key = _piecewise(ks, [(0.2, 35), (0.4, 55), (0.6, 72), (0.75, 88),
                            (0.88, 100), (0.96, 98), (1.0, 92)])

    # ⑥ 延伸和弦使用率(⚠️ 這項最不可靠,見檔頭聲明)
    ext_q = {"maj7", "min7", "dom7", "m7b5", "sus4", "sus2", "dim", "aug"}
    ext = sum(v for k, v in played.items() if _parse(k)[1] in ext_q)
    ext_ratio = ext / played_dur
    s_ext = _piecewise(ext_ratio, [(0.0, 50), (0.05, 64), (0.15, 82), (0.30, 100),
                                   (0.50, 95), (0.70, 78), (0.90, 58)])

    # ⑦ 五度根音動線 ── 功能和聲的骨幹(V→I、ii→V 都是下行五度)
    fifth = tot = 0
    for a, b in zip(segments, segments[1:]):
        if a["root"] < 0 or b["root"] < 0:
            continue
        tot += 1
        if (a["root"] - b["root"]) % 12 == 7:      # 下行純五度 = 上行純四度
            fifth += 1
    fifth_ratio = fifth / max(tot, 1)
    s_fifth = _piecewise(fifth_ratio, [(0.0, 40), (0.08, 58), (0.18, 78), (0.30, 96),
                                       (0.42, 100), (0.60, 90), (0.80, 75)])

    top = sorted(played.items(), key=lambda kv: -kv[1])[:10]

    return {
        "key": {"root": ROOTS[key_root], "mode": mode,
                "label": f"{ROOTS[key_root]} {'major' if mode == 'major' else 'minor'}",
                "kk_correlation": round(key_corr, 4)},
        "metrics": {
            "chord_vocabulary": {
                "n_unique_triads": n_triads, "n_unique_chords": n_unique,
                "n_triads_filtered": n_triads_filtered,
                "entropy_bits": round(H, 3), "entropy_norm": round(H_norm, 4),
                "score": round(s_vocab_filtered, 1),
                "score_raw_unfiltered": round(s_vocab, 1),
                "note": "分數建在 n_unique_triads(MIREX maj/min 收斂層,可靠);"
                        "n_unique_chords 是完整品質層,會被七音/掛留音誤判灌水,僅供參考。"
                        "兩者皆只計時間佔比≥1%;熵以 24 個大小三和弦為滿分參考"},
            "harmonic_rhythm": {
                "changes_per_beat": round(chg_per_beat, 4),
                "changes_per_sec": round(chg_per_sec, 3),
                "n_changes": n_changes, "score": round(s_rhythm, 1),
                "note": "0.25/拍≈一小節一和弦(流行樂中位)"},
            "non_diatonic": {
                "ratio": round(nd_ratio, 4),
                "structured_ratio": round(nd_structured_ratio, 4),
                "random_ratio": round(nd_random_ratio, 4),
                "score": round(s_nd, 1),
                "note": "結構性借用(有解決/重現)才進甜蜜點;隨機孤立離調視為污染扣分"
                        "(2026-07-24 修復案;復權前分數凍結不進總分)"},
            "cadence": {
                "authentic": cad["authentic"], "plagal": cad["plagal"],
                "deceptive": cad["deceptive"], "per_minute": round(cad_pm, 2),
                "score": round(s_cad, 1),
                "note": "無樂句切分,數的是「終止式型和弦動線」,會多於實際終止式"},
            "key_stability": {
                "agree_ratio": round(ks, 4), "n_windows": n_win,
                "score": round(s_key, 1),
                "note": "20 秒窗/10 秒跳;1.0 不給滿分—完全零轉調缺乏戲劇性"},
            "extended_chords": {
                "ratio": round(ext_ratio, 4), "score": round(s_ext, 1),
                "note": "⚠️ 七和弦/sus 辨識可靠度明顯低於三和弦,當風格傾向看,別當樂譜"},
            "fifth_motion": {
                "ratio": round(fifth_ratio, 4), "score": round(s_fifth, 1),
                "note": "相鄰和弦下行純五度佔比,功能和聲(V→I、ii→V)的骨幹"},
        },
        "no_chord_ratio": round(dur_by_chord.get("N", 0.0) / total_dur, 4),
        "top_chords": [{"name": k, "seconds": round(v, 1),
                        "share": round(v / played_dur, 4)} for k, v in top],
        "progression": [{"t": round(s["start"], 2), "d": round(s["end"] - s["start"], 2),
                         "chord": s["name"]} for s in segments],
        # ⚖️ 和聲柱總分(重構庭 2026-07-25 定版,T3+T4 雙樣本):
        #    chord_vocabulary 13:0 復權(入分=過濾版 s_vocab_filtered);non_diatonic 維持凍結(K1 11:2 待真值)。
        #    柱內權重:終止20/詞彙19/調性18/五度16/節奏15/延伸12(=T_定版權重.json 和聲柱)。
        "score": round((0.15 * s_rhythm + 0.20 * s_cad + 0.18 * s_key + 0.12 * s_ext
                        + 0.16 * s_fifth + 0.19 * s_vocab_filtered), 1),
        "score_note": "重構庭定版六項加權(終止20/詞彙[過濾版]19/調性18/五度16/節奏15/延伸12);"
                      "凍結中:non_diatonic(K1 11:2 待真值重考)",
        "frozen": ["non_diatonic"],
    }


def _parse(name):
    """和弦名字串 → (root, quality)。'C'→(0,'maj')、'Amin7'→(9,'min7')。"""
    if name == "N":
        return (-1, "N")
    root = name[:2] if len(name) > 1 and name[1] == "#" else name[:1]
    qual = name[len(root):] or "maj"
    return (ROOTS.index(root), qual)


def main():
    ap = argparse.ArgumentParser(description="和聲分析(真和弦辨識:模板+Viterbi)")
    ap.add_argument("audio", help="音檔路徑")
    ap.add_argument("--json", dest="json_out", help="輸出 JSON 路徑")
    ap.add_argument("--stems", default=None, help="分軌快取資料夾(預設:音檔旁 _stems)")
    ap.add_argument("--model", default="htdemucs_6s", help="demucs 模型(預設 htdemucs_6s)")
    ap.add_argument("--no-stems", action="store_true",
                    help="跳過 Demucs,直接用 HPSS 諧波分量(快但較不準)")
    args = ap.parse_args()

    audio = Path(args.audio).resolve()
    if not audio.exists():
        sys.exit(f"✗ 找不到音檔:{audio}")
    stems_dir = Path(args.stems).resolve() if args.stems else audio.parent / "_stems"

    out = {
        "file": audio.name, "model": args.model, "degraded": False,
        "method": "chroma_cqt + 121-state template matching (12 roots x 10 qualities + N) "
                  "+ librosa Viterbi (transition_loop / viterbi_discriminative), beat-synchronous",
        "accuracy_caveat": (
            "模板+Viterbi 在流行樂約 MIREX maj/min 65–75% 準確度(深度學習 SOTA 約 83–87%)。"
            "三和弦最可靠;七和弦/sus/dim/aug 等延伸品質「明顯更不可靠」,常把旋律經過音誤判為和弦內音。"
            "所有分數僅供相對比較,不是樂理判定。"),
        "contrast_with_legacy": (
            "song_scorer.py 的 harmony_metrics()「不辨識任何和弦」:它只數 mean chroma 超過最大值 25% "
            "的音級格數(n_pitch_classes),加上相鄰半小節 chroma 餘弦相似度 <0.85 的次數(changes_per_sec)。"
            "本元件是並存新增,舊指標原封不動保留。"),
        "thresholds": {
            "sr": SR, "hop": HOP, "harmony_stems": list(HARMONY_STEMS),
            "softmax_T": SOFTMAX_T, "n_chord_penalty": N_CHORD_PENALTY,
            "self_loop": SELF_LOOP,
            "note": "啟發式起手值,非論文實測;校準方式=拿已知和弦進行的歌對答案後再調"},
    }

    try:
        print(f"[1/3] 取和聲軌({'HPSS 諧波' if args.no_stems else args.model + ' 分軌'})…", flush=True)
        y_h, y_mix, sr, info = load_harmony_audio(audio, stems_dir, args.model, not args.no_stems)
        out.update({k: v for k, v in info.items() if k != "sources"})
        if "sources" in info:
            out["sources"] = info["sources"]
        if info["stem_mode"] != "stems" and not args.no_stems:
            out["degraded"] = True
            print(f"      ⚠ 分軌失敗,降級用 HPSS:{info['stem_error']}", flush=True)
        else:
            print(f"      OK{'(讀快取)' if info.get('stems_cached') else ''}", flush=True)

        print("[2/3] chroma → 拍點同步 → Viterbi 解碼…", flush=True)
        segments, extra = decode_chords(y_h, y_mix, sr)
        out["tempo_bpm"] = extra["tempo_bpm"]
        out["n_beats"] = extra["n_beats"]
        out["template_fit"] = extra["template_fit"]          # 0–1,越高代表 chroma 真的像那個和弦
        out["mean_posterior"] = extra["mean_posterior"]      # 121 狀態下必然偏低,僅供除錯
        out["n_chord_segments"] = len(segments)

        print("[3/3] 計算和聲指標…", flush=True)
        out.update(analyze(segments, extra))
    except Exception as e:
        out["degraded"] = True
        out["error"] = f"{type(e).__name__}: {e}"
        print(f"✗ 失敗:{out['error']}", file=sys.stderr)

    if "metrics" in out:
        m = out["metrics"]
        print("\n===== 和聲分析 =====")
        print(f"  調性        {out['key']['label']}(KK 相關 {out['key']['kk_correlation']})")
        print(f"  速度/拍數   {out['tempo_bpm']} BPM / {out['n_beats']} 拍,模板擬合度 {out['template_fit']}")
        print(f"  和弦詞彙    {m['chord_vocabulary']['n_unique_triads']} 種三和弦"
              f"(完整品質層 {m['chord_vocabulary']['n_unique_chords']} 種),熵 "
              f"{m['chord_vocabulary']['entropy_bits']} bits → {m['chord_vocabulary']['score']} 分")
        print(f"  和聲節奏    每拍 {m['harmonic_rhythm']['changes_per_beat']} 次換和弦 "
              f"→ {m['harmonic_rhythm']['score']} 分")
        print(f"  離調比例    {m['non_diatonic']['ratio']} → {m['non_diatonic']['score']} 分")
        print(f"  終止式      正格 {m['cadence']['authentic']} / 變格 {m['cadence']['plagal']} / "
              f"假終止 {m['cadence']['deceptive']} → {m['cadence']['score']} 分")
        print(f"  調性穩定    {m['key_stability']['agree_ratio']} → {m['key_stability']['score']} 分")
        print(f"  延伸和弦    {m['extended_chords']['ratio']} → {m['extended_chords']['score']} 分 ⚠️低可靠度")
        print(f"  五度動線    {m['fifth_motion']['ratio']} → {m['fifth_motion']['score']} 分")
        print("  最常出現   ", ", ".join(f"{c['name']}({c['share']:.0%})" for c in out["top_chords"][:6]))
        print("  ※ 總分=六活項等權(non_diatonic 凍結中)—— 2026-07-24 十三家裁決")

    jp = Path(args.json_out) if args.json_out else audio.with_name(audio.stem + "_和聲分析.json")
    jp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整報告:{jp}")


if __name__ == "__main__":
    main()
