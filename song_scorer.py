#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
song_scorer.py — 原創歌曲自動評分系統(無參考基準版)

原理:程式無法「聽懂」音樂,但可以量測聲音的物理特徵。
本系統先自動偵測歌曲的調性(key)與節拍(beat grid)作為內部基準,
再量測演唱與編曲混音的各項客觀指標,換算成 0-100 分後加權。

用法:
    python song_scorer.py mix.wav                     # 只評「編曲混音」
    python song_scorer.py mix.wav --vocal vocal.wav   # 加上「演唱表現」完整評分
    python song_scorer.py mix.wav --demucs            # 自動人聲分離(需另安裝 demucs)
    python song_scorer.py mix.wav --json report.json  # 輸出 JSON 報告
    python song_scorer.py mix.wav --weights my.json   # 自訂權重

相依套件:librosa、numpy、soundfile、pyloudnorm(響度)、praat-parselmouth(嗓音品質,可選)
"""

import argparse
import json
import shutil
import sys
import warnings
from pathlib import Path

import numpy as np

from 暫存清理 import force_rmtree

# Windows 繁中主控台預設 cp950,印不出報告的全形符號,強制改用 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore")

import librosa  # noqa: E402

try:
    import pyloudnorm as pyln
    HAS_LOUDNORM = True
except ImportError:
    HAS_LOUDNORM = False

try:
    import parselmouth
    HAS_PRAAT = True
except ImportError:
    HAS_PRAAT = False

SR_MUSIC = 22050   # 音樂特徵分析用取樣率(音高、節拍、和聲)
SR_MIX = 44100     # 混音品質分析用取樣率(響度、頻譜、立體聲)

# ---------------------------------------------------------------------------
# 權重設定(可用 --weights 覆寫)
# ---------------------------------------------------------------------------
# ⚖️ 2026-07-24 起為【十三家評審團辯論定版】(非手訂暫定值):
#    對外版沿革見 docs/權重沿革.md(完整辯論紀錄與 52+ 份答卷不隨本 repo 散布)。
#    - 混音:權重案 Q4(雙樣本)——三死項歸零(loudness/clipping 轉「異常時警報」,
#      dynamic_range 已改 LRA 但凍結至過 A 層複驗+單格重開)。
#    - 演唱:權重案 Q5 + H3 更正案(13:0)——rhythm 判「修-凍結」權重 0
#      (修好節拍網格、過「人聲單獨錯位」A 層才復權),其餘六項按 Q5 比例攤回。
#    ⛔ 改任何一格 = 推翻評審團裁決,須單格重開辯論,不准手改。
DEFAULT_WEIGHTS = {
    "overall": {"vocal": 0.55, "mix": 0.45},   # (--blend-vocal 路徑用;新總分架構见 評審團.py)
    "vocal": {
        "pitch": 0.3529,        # 音準(Q5 30 ÷ 0.85;曲線已依 V4 裁決重寫為逐幀 p70)
        "rhythm": 0.0,          # ⛔ 凍結(H2 D1:修-凍結至復權;分數照算照顯示,標「凍結中」)
        "stability": 0.1765,    # 長音穩定度(Q5 15 ÷ 0.85)
        "vibrato": 0.1176,      # 顫音(Q5 10 ÷ 0.85)
        "dynamics": 0.1176,     # 動態控制
        "voice_quality": 0.1176,  # 嗓音品質
        "range": 0.1176,        # 音域
    },
    "mix": {
        # ⚖️ 2026-07-24 G 庭更新(13:0):dynamic_range(LRA)復權 ——
        #    三證齊全:A 層過(20:1 壓縮 100→32)+ SUNO 鑑別力 0.278 + 真實得獎歌 0.618。
        #    loudness/clipping 維持體檢角色(G3,13:0):真實音樂上它們量的是母帶美學
        #    (54 首中 crest 最低=五座葛萊美的 Not Like Us),排名權重=曲風歧視。
        "loudness": 0.0,          # 體檢警報(異常時顯示,見 顯示規則.py),不進排名
        "dynamic_range": 0.15,    # ⭐ LRA 復權(G1 十三席中位配比)
        "spectral_balance": 0.31, # 頻譜平衡
        "stereo": 0.18,           # 立體聲寬度
        "clipping": 0.0,          # 體檢警報,不進排名
        "structure": 0.21,        # 層次鋪陳
        "harmony": 0.15,          # 和聲豐富度
    },
}

# Krumhansl-Kessler 調性側寫,用於自動偵測 key
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def piecewise(x, points):
    """分段線性換分:points = [(量測值, 分數), ...],超出範圍取端點值。"""
    xs, ys = zip(*points)
    return float(np.interp(x, xs, ys))


# ===========================================================================
# 一、基準偵測:調性與節拍
# ===========================================================================

def estimate_key(y, sr):
    """用 chroma 與 Krumhansl-Kessler 側寫的相關係數估計調性。"""
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)
    best = (-2.0, 0, "major")
    for mode, tpl in (("major", KS_MAJOR), ("minor", KS_MINOR)):
        for shift in range(12):
            r = np.corrcoef(profile, np.roll(tpl, shift))[0, 1]
            if r > best[0]:
                best = (float(r), shift, mode)
    conf, tonic, mode = best
    scale = MAJOR_SCALE if mode == "major" else MINOR_SCALE
    scale_pcs = sorted((tonic + s) % 12 for s in scale)
    name = f"{NOTE_NAMES[tonic]} {'大調' if mode == 'major' else '小調'}"
    return {"tonic": tonic, "mode": mode, "name": name,
            "scale_pcs": scale_pcs, "confidence": round(conf, 3)}


def estimate_beats(y, sr):
    """節拍追蹤。beat_track 可能只鎖到鼓最強的段落,
    因此用拍距中位數把網格外插到整首歌,避免頭尾沒有拍點可對。"""
    duration = len(y) / sr
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bt = librosa.frames_to_time(beat_frames, sr=sr)
    if len(bt) >= 2:
        ibi = float(np.median(np.diff(bt)))
        pre = np.arange(bt[0] - ibi, 0, -ibi)[::-1]
        post = np.arange(bt[-1] + ibi, duration, ibi)
        grid = np.concatenate([pre, bt, post])
        bpm = 60.0 / ibi
    else:
        grid = bt
        bpm = float(np.atleast_1d(tempo)[0])
    return {"bpm": round(bpm, 1), "beat_times": grid}


# ===========================================================================
# 二、演唱分析(需要人聲軌)
# ===========================================================================

def extract_f0(y, sr):
    """pyin 抽取基頻曲線。"""
    f0, voiced, _ = librosa.pyin(
        y, sr=sr,
        fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C6"),
        frame_length=2048,
    )
    times = librosa.times_like(f0, sr=sr)
    hop_time = float(times[1] - times[0]) if len(times) > 1 else 512 / sr
    return f0, voiced, times, hop_time


def segment_notes(f0, voiced, times, min_dur=0.10):
    """把連續且音高相近的有聲幀切成一顆顆音符。"""
    midi = librosa.hz_to_midi(f0)
    notes = []
    i, n = 0, len(f0)
    while i < n:
        if not voiced[i] or np.isnan(midi[i]):
            i += 1
            continue
        j = i
        while (j + 1 < n and voiced[j + 1] and not np.isnan(midi[j + 1])
               and abs(midi[j + 1] - np.nanmedian(midi[i:j + 2])) < 0.8):
            j += 1
        seg = midi[i:j + 1]
        seg = seg[~np.isnan(seg)]
        dur = float(times[j] - times[i])
        if dur >= min_dur and len(seg) >= 3:
            notes.append({
                "start": float(times[i]), "dur": dur,
                "midi": seg, "median": float(np.median(seg)),
            })
        i = j + 1
    return notes


def pitch_metrics(notes, scale_pcs):
    """音準(無參考版)。

    ⚖️ 2026-07-24 依十三家評審團裁決重寫(如何評案 V4,13:0 修-立即):
       舊版只看「每顆音的中位音高」貼不貼半音格 —— ±40 音分、5Hz 的嚴重抖動被中位數
       整顆吸收,針對性 A 層實測只扣 2.6 分(97.3→94.7)。
       新版改【逐幀偏差的時間加權分佈】:取全部有聲幀距最近半音的音分偏差,
       以 p70(第 70 百分位)重罰持續性走音;驗收錨點=±40 音分抖動應落 60 分帶。
       音符中心準度與調內率降為次要成分;自然顫音(深度 ≤25 音分)不會觸及重罰帶。"""
    if not notes:
        return None
    frame_dev, chrom_dev, in_scale = [], [], []
    for nt in notes:
        seg = np.asarray(nt["midi"], dtype=float)
        frame_dev.extend(np.abs(seg - np.round(seg)) * 100)   # 逐幀:抖動藏不住
        m = nt["median"]
        chrom_dev.append(abs(m - round(m)) * 100)
        pc = int(round(m)) % 12
        in_scale.append(1.0 if pc in scale_pcs else 0.0)
    p70_cents = float(np.percentile(frame_dev, 70)) if frame_dev else 0.0
    mean_cents = float(np.mean(chrom_dev))
    in_scale_rate = float(np.mean(in_scale))
    inton_frame = piecewise(p70_cents, [(8, 100), (15, 92), (25, 78), (35, 62), (45, 45), (60, 28)])
    inton_center = piecewise(mean_cents, [(5, 100), (15, 95), (25, 85), (35, 70), (50, 45)])
    score = 0.6 * inton_frame + 0.2 * inton_center + 0.2 * in_scale_rate * 100
    return {
        "score": round(score, 1),
        "p70_cents": round(p70_cents, 1),
        "mean_cents": round(mean_cents, 1),
        "in_scale_rate": round(in_scale_rate, 3),
        "n_notes": len(notes),
        "comment": f"逐幀偏差 p70={p70_cents:.0f} 音分・音符中心 {mean_cents:.0f} 音分,"
                   f"{in_scale_rate * 100:.0f}% 落在調內",
    }


def vibrato_stability_metrics(notes, hop_time):
    """長音穩定度 + 顫音(速率、深度)。有顫音的音符不計入穩定度,避免誤罰。"""
    stab_pool, vib_notes, long_notes = [], [], 0
    for nt in notes:
        cents = (nt["midi"] - np.median(nt["midi"])) * 100.0
        if nt["dur"] >= 0.35 and len(cents) >= 12:
            long_notes += 1
            x = np.arange(len(cents))
            detr = cents - np.polyval(np.polyfit(x, cents, 1), x)  # 去趨勢
            win = np.hanning(len(detr))
            spec = np.abs(np.fft.rfft(detr * win))
            freqs = np.fft.rfftfreq(len(detr), d=hop_time)
            band = (freqs >= 3.5) & (freqs <= 8.5)
            if band.any():
                spec_b = np.where(band, spec, 0.0)
                k = int(np.argmax(spec_b))
                amp_fft = 2.0 * spec[k] / max(np.sum(win), 1e-9)  # 判定用
                med = float(np.median(spec)) + 1e-9
                if amp_fft > 6.0 and spec[k] > 2.5 * med:  # 判定有顫音
                    # 深度改用百分位數(半峰對峰),對漸入式顫音較穩健
                    extent = float((np.percentile(detr, 95) - np.percentile(detr, 5)) / 2)
                    vib_notes.append({"rate": float(freqs[k]), "extent": extent})
                    continue
        if nt["dur"] >= 0.15 and len(cents) >= 5:
            stab_pool.append(float(np.median(np.abs(cents - np.median(cents)))))

    stability = None
    if stab_pool:
        mad = float(np.mean(stab_pool))
        stability = {
            "score": round(piecewise(mad, [(5, 100), (15, 92), (30, 75), (60, 50), (100, 30)]), 1),
            "mad_cents": round(mad, 1),
            "comment": f"直音平均波動 {mad:.0f} 音分",
        }

    vibrato = None
    if long_notes > 0:
        if vib_notes:
            rate = float(np.median([v["rate"] for v in vib_notes]))
            extent = float(np.median([v["extent"] for v in vib_notes]))
            presence = len(vib_notes) / long_notes
            q_rate = piecewise(rate, [(3.5, 60), (4.5, 85), (5.0, 100), (7.0, 100), (7.5, 85), (9.0, 60)])
            q_ext = piecewise(extent, [(5, 55), (15, 85), (25, 100), (80, 100), (120, 70), (200, 40)])
            score = 0.4 * (presence * 100) + 0.6 * (q_rate + q_ext) / 2
            vibrato = {
                "score": round(score, 1),
                "rate_hz": round(rate, 2), "extent_cents": round(extent, 1),
                "presence": round(presence, 2),
                "comment": f"速率 {rate:.1f} Hz、深度 {extent:.0f} 音分,{presence * 100:.0f}% 長音有顫音",
            }
        else:
            vibrato = {"score": 55.0, "rate_hz": None, "extent_cents": None, "presence": 0.0,
                       "comment": "長音幾乎無顫音(直音路線,酌情參考)"}
    return stability, vibrato


def rhythm_metrics(notes, beat_times):
    """節奏:用音高追蹤切出的「音符起點」對齊節拍網格(含八分音符細分)。
    比 onset_detect 可靠,因為顫音的振幅波動會產生假 onset。"""
    if len(notes) < 4 or len(beat_times) < 4:
        return None
    starts = np.array([nt["start"] for nt in notes])
    ibi = float(np.median(np.diff(beat_times)))
    halves = beat_times[:-1] + np.diff(beat_times) / 2
    grid = np.sort(np.concatenate([beat_times, halves]))
    signed = np.array([s - grid[np.argmin(np.abs(grid - s))] for s in starts])
    offset = float(np.median(signed))   # 系統性提前/延後 = 演唱習慣或偵測延遲,不扣分
    resid_ms = float(np.mean(np.abs(signed - offset))) * 1000  # 一致性才是重點
    score = piecewise(resid_ms, [(15, 100), (30, 92), (60, 75), (100, 55), (160, 30)])
    style = "延後" if offset > 0 else "提前"
    return {
        "score": round(score, 1),
        "consistency_ms": round(resid_ms, 1),
        "systematic_offset_ms": round(offset * 1000, 1),
        "n_onsets": len(starts), "beat_ms": round(ibi * 1000, 1),
        "comment": f"對拍一致性偏差 {resid_ms:.0f} 毫秒(整體習慣性{style} {abs(offset) * 1000:.0f} 毫秒)",
    }


def vocal_dynamics_metrics(y_vocal, sr, f0, voiced):
    """動態控制:有聲段的響度起伏範圍。"""
    rms = librosa.feature.rms(y=y_vocal, frame_length=2048, hop_length=512)[0]
    n = min(len(rms), len(voiced))
    v = np.asarray(voiced[:n], dtype=bool)
    r = rms[:n][v]
    r = r[r > 1e-6]
    if len(r) < 10:
        return None
    db = 20 * np.log10(r)
    spread = float(np.percentile(db, 90) - np.percentile(db, 10))
    score = piecewise(spread, [(2, 55), (4, 70), (6, 85), (9, 100), (15, 100), (20, 80)])
    return {"score": round(score, 1), "spread_db": round(spread, 1),
            "comment": f"強弱起伏 {spread:.1f} dB"}


def voice_quality_metrics(y_vocal, sr):
    """嗓音品質:jitter / shimmer / HNR(需 praat-parselmouth)。"""
    if not HAS_PRAAT:
        return None
    try:
        snd = parselmouth.Sound(y_vocal.astype(np.float64), sampling_frequency=sr)
        pp = parselmouth.praat.call(snd, "To PointProcess (periodic, cc)", 75, 600)
        jitter = parselmouth.praat.call(pp, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3) * 100
        shimmer = parselmouth.praat.call([snd, pp], "Get shimmer (local)",
                                         0, 0, 0.0001, 0.02, 1.3, 1.6) * 100
        harm = parselmouth.praat.call(snd, "To Harmonicity (cc)", 0.01, 75, 0.1, 1.0)
        hnr = parselmouth.praat.call(harm, "Get mean", 0, 0)
        if any(np.isnan(v) for v in (jitter, shimmer, hnr)):
            return None
        s_j = piecewise(jitter, [(0.3, 100), (0.8, 92), (1.5, 78), (3, 55), (5, 35)])
        s_s = piecewise(shimmer, [(2, 100), (5, 90), (8, 72), (12, 50)])
        s_h = piecewise(hnr, [(8, 50), (12, 72), (17, 90), (22, 100)])
        score = (s_j + s_s + s_h) / 3
        return {"score": round(score, 1), "jitter_pct": round(jitter, 2),
                "shimmer_pct": round(shimmer, 2), "hnr_db": round(hnr, 1),
                "comment": f"jitter {jitter:.2f}%、shimmer {shimmer:.1f}%、HNR {hnr:.1f} dB"}
    except Exception:
        return None


def range_metrics(notes):
    """音域:實際唱到的音高跨度。"""
    if len(notes) < 4:
        return None
    meds = np.array([nt["median"] for nt in notes])
    lo, hi = float(np.percentile(meds, 5)), float(np.percentile(meds, 95))
    span = hi - lo
    score = piecewise(span, [(5, 55), (8, 70), (12, 85), (16, 95), (22, 100), (30, 95)])
    lo_name = librosa.midi_to_note(int(round(lo)))
    hi_name = librosa.midi_to_note(int(round(hi)))
    return {"score": round(score, 1), "span_semitones": round(span, 1),
            "low": lo_name, "high": hi_name,
            "comment": f"{lo_name} 到 {hi_name},約 {span:.0f} 個半音"}


def analyze_vocal(vocal_path, key_info, beat_times):
    y, sr = librosa.load(vocal_path, sr=SR_MUSIC, mono=True)
    f0, voiced, times, hop_time = extract_f0(y, sr)
    notes = segment_notes(f0, voiced, times)
    stability, vibrato = vibrato_stability_metrics(notes, hop_time)
    return {
        "pitch": pitch_metrics(notes, key_info["scale_pcs"]),
        "rhythm": rhythm_metrics(notes, beat_times),
        "stability": stability,
        "vibrato": vibrato,
        "dynamics": vocal_dynamics_metrics(y, sr, f0, voiced),
        "voice_quality": voice_quality_metrics(y, sr),
        "range": range_metrics(notes),
    }


# ===========================================================================
# 三、編曲混音分析(用完整混音即可)
# ===========================================================================

def loudness_metrics(y_stereo, sr):
    if not HAS_LOUDNORM:
        return None
    data = y_stereo.T if y_stereo.ndim == 2 else y_stereo
    lufs = pyln.Meter(sr).integrated_loudness(data)
    if not np.isfinite(lufs):
        return None
    score = piecewise(lufs, [(-30, 40), (-22, 70), (-18, 90), (-16, 100),
                             (-9, 100), (-7, 85), (-4, 60)])
    if lufs > -13.5:
        note = "建議發行前正規化到串流標準 -14 LUFS"
    elif lufs >= -14.6:
        note = "已在串流標準 -14 附近,發行就緒"
    else:
        note = "略低於串流標準 -14"
    return {"score": round(score, 1), "lufs": round(float(lufs), 1),
            "comment": f"整體響度 {lufs:.1f} LUFS——{note}"}


def dynamic_range_metrics(y_mono, y_stereo=None, sr=None):
    """動態範圍。

    ⚖️ 2026-07-24 依十三家評審團裁決重寫(如何評案 V5,13:0 採納 LRA):
       舊版用峰值均方根比(crest),曲線 10~16dB 全給滿分 —— A 層 20:1 過度壓縮
       實測 100→100 一分沒掉(現代母帶把峰值與 RMS 一起推高,比值不動)。
       新版改 EBU R128 精神的 LRA(Loudness Range):3 秒短時響度、1 秒跳,
       絕對閘 -70 LUFS + 相對閘(均值 -20 LU),LRA = p95 − p10。
       壓縮壓的就是響度域的分佈寬度 —— 20:1 壓平必然重傷 LRA。
    ⛔ 權重凍結為 0:須通過 A 層複驗(20:1 壓縮大幅掉分)後,由評審團單格重開才復權。
       crest 保留當參考欄位,不再驅動分數。"""
    peak = float(np.max(np.abs(y_mono)) + 1e-12)
    rms = float(np.sqrt(np.mean(y_mono ** 2)) + 1e-12)
    crest = 20 * np.log10(peak / rms)
    lra = None
    if HAS_LOUDNORM and y_stereo is not None and sr:
        try:
            data = y_stereo.T if y_stereo.ndim == 2 else y_stereo
            meter = pyln.Meter(sr, block_size=0.400)
            win, hop = int(3.0 * sr), int(1.0 * sr)
            st = []
            for i in range(0, max(1, len(data) - win), hop):
                v = meter.integrated_loudness(data[i:i + win])
                if np.isfinite(v) and v > -70.0:           # 絕對閘
                    st.append(float(v))
            if len(st) >= 8:
                gate = float(np.mean(st)) - 20.0            # 相對閘
                st = [v for v in st if v >= gate] or st
                lra = float(np.percentile(st, 95) - np.percentile(st, 10))
        except Exception:
            lra = None
    if lra is not None:
        score = piecewise(lra, [(1.0, 25), (2.5, 50), (4.0, 75), (6.0, 92),
                                (8.0, 100), (13.0, 100), (18.0, 88), (25.0, 70)])
        cm = f"LRA {lra:.1f} LU(短時響度 p95−p10)・crest {crest:.1f} dB(參考)"
        if lra < 3.0:
            cm += ",動態被壓得很緊"
        return {"score": round(score, 1), "lra_lu": round(lra, 1),
                "crest_db": round(crest, 1), "comment": cm}
    # 退路:無 pyloudnorm/立體聲資料 → 沿用舊 crest 曲線,並明白標示
    score = piecewise(crest, [(4, 40), (6, 65), (8, 85), (10, 100),
                              (16, 100), (20, 85), (26, 65)])
    return {"score": round(score, 1), "crest_db": round(crest, 1),
            "comment": f"峰值均方根比 {crest:.1f} dB(LRA 不可用,退回舊法)"}


# ⚖️ 頻譜健康帶 —— 依 profile 分套(2026-07-24 J 庭 J1,11:2「成品側以 54 首得獎分佈重錨」):
#   SUNO 帶  = 原流行常模(SUNO profile 沿用,29 首實測全在帶內,行為不變)
#   成品帶   = 54 首葛萊美/金曲/KMA 得獎歌實測 p5–p95(數據驅動零手訂)。
#             實證病灶:真實母帶過半落在舊帶外(low 中位 .391 貼上限 .40、highmid 中位 .043
#             低於下限 .08、air 中位 .016 低於 .02)→ 金曲獎作品被扣到 36.9,
#             且「砍低頻」兩曲風都反而加分(+27.8/+23.6)。
#   ⚠️ air 上界例外:語料是 128k MP3(16kHz 以上被低通),air 分佈被壓抑 →
#      上界不可用此數據錨(會冤枉真無損母帶),沿用舊值 0.20;下界取數據 p5。
#   切換:環境變數 SONG_JURY_PROFILE=release → 成品帶;預設/其他 → SUNO 帶。
_BANDS_SUNO = {
    "low":     (60, 250,   0.12, 0.40),
    "lowmid":  (250, 2000, 0.30, 0.60),
    "highmid": (2000, 6000, 0.08, 0.35),
    "air":     (6000, 20000, 0.02, 0.20),
}
_BANDS_RELEASE = {
    # 2026-07-24 AAC256 試聽語料複驗(43 首指紋確證,_試聽驗證/重錨報告.md):
    #   low/lowmid/highmid 同曲相關 r=.84-.88、偏差≤.012 → 低/中頻錨「已複驗成立」。
    #   air:真實母帶 p5-p95=0.003-0.030(全頻寬量測),遠低於現行上界 0.20(沿用 SUNO 的權宜值);
    #       數據支持收緊上界至 ~0.05,惟改格=單格重開辯論,未裁決前維持 0.20。
    #   語料=AAC 256k 非無損;無損母帶到手前為最高可得證據等級。
    "low":     (60, 250,   0.17, 0.60),
    "lowmid":  (250, 2000, 0.13, 0.60),
    "highmid": (2000, 6000, 0.01, 0.12),
    "air":     (6000, 20000, 0.004, 0.20),
}


def spectral_balance_metrics(y_mono, sr):
    import os as _os
    _profile = (_os.environ.get("SONG_JURY_PROFILE") or "suno").lower()
    _b = _BANDS_RELEASE if _profile == "release" else _BANDS_SUNO
    S = np.abs(librosa.stft(y_mono, n_fft=4096)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    total = float(S.sum()) + 1e-12
    bands = {
        "low":     (60, 250,   _b["low"][2], _b["low"][3]),
        "lowmid":  (250, 2000, _b["lowmid"][2], _b["lowmid"][3]),
        "highmid": (2000, 6000, _b["highmid"][2], _b["highmid"][3]),
        "air":     (6000, 20000, _b["air"][2], _b["air"][3]),
    }
    fracs, penalty = {}, 0.0
    for name, (f1, f2, lo, hi) in bands.items():
        m = (freqs >= f1) & (freqs < f2)
        frac = float(S[m].sum()) / total
        fracs[name] = round(frac, 3)
        penalty += max(0.0, lo - frac, frac - hi)
    score = float(np.clip(100 - 250 * penalty, 30, 100))
    return {"score": round(score, 1), "fractions": fracs,
            "comment": "低頻 {:.0f}%|中頻 {:.0f}%|中高頻 {:.0f}%|高頻 {:.0f}%".format(
                fracs["low"] * 100, fracs["lowmid"] * 100,
                fracs["highmid"] * 100, fracs["air"] * 100)}


def stereo_metrics(y_stereo):
    if y_stereo.ndim != 2 or y_stereo.shape[0] < 2:
        return {"score": 60.0, "width": 0.0, "corr": 1.0,
                "comment": "單聲道檔案,無立體聲資訊"}
    L, R = y_stereo[0], y_stereo[1]
    mid, side = (L + R) / 2, (L - R) / 2
    rms = lambda x: float(np.sqrt(np.mean(x ** 2)) + 1e-12)
    width = rms(side) / rms(mid)
    corr = float(np.corrcoef(L, R)[0, 1])
    score = piecewise(width, [(0.02, 55), (0.1, 75), (0.2, 90), (0.35, 100),
                              (0.7, 100), (0.9, 85), (1.2, 65)])
    warn = ",左右相關係數偏低,注意反相" if corr < 0.2 else ""
    return {"score": round(score, 1), "width": round(width, 3), "corr": round(corr, 3),
            "comment": f"寬度指數 {width:.2f}{warn}"}


def clipping_metrics(y_stereo):
    frac = float(np.mean(np.abs(y_stereo) > 0.999))
    score = piecewise(frac, [(0, 100), (1e-4, 95), (1e-3, 75), (1e-2, 40), (0.05, 15)])
    return {"score": round(score, 1), "clip_fraction": frac,
            "comment": "無削波" if frac < 1e-5 else f"{frac * 100:.2f}% 取樣點觸頂,有破音風險"}


def structure_metrics(y_mono, sr):
    """層次鋪陳:把歌切成 8 段,量能量與音色的段落對比。"""
    n_sec = 8
    seg = np.array_split(y_mono, n_sec)
    rms_db, cents = [], []
    for s in seg:
        r = float(np.sqrt(np.mean(s ** 2)) + 1e-12)
        rms_db.append(20 * np.log10(r))
        c = librosa.feature.spectral_centroid(y=s, sr=sr)[0]
        cents.append(float(np.mean(c)))
    e_contrast = float(np.max(rms_db) - np.min(rms_db))
    c_var = float(np.std(cents) / (np.mean(cents) + 1e-9))
    s_e = piecewise(e_contrast, [(1, 55), (3, 70), (6, 90), (9, 100), (15, 100), (20, 90)])
    s_c = piecewise(c_var, [(0.02, 60), (0.06, 80), (0.12, 100), (0.35, 100), (0.5, 85)])
    score = 0.6 * s_e + 0.4 * s_c
    return {"score": round(score, 1), "energy_contrast_db": round(e_contrast, 1),
            "timbre_variation": round(c_var, 3),
            "comment": f"段落能量落差 {e_contrast:.1f} dB,音色變化係數 {c_var:.2f}"}


def harmony_metrics(y_mono, sr, beat_times):
    """和聲豐富度:先做諧波分離去掉鼓的干擾,
    量「使用的音級數」+「半小節解析度的和聲變化率」(向量餘弦相似度)。"""
    yh = librosa.effects.harmonic(y_mono)
    chroma = librosa.feature.chroma_cqt(y=yh, sr=sr)
    mean_c = chroma.mean(axis=1)
    mean_c = mean_c / (mean_c.max() + 1e-9)
    n_pcs = int(np.sum(mean_c > 0.25))
    dur = len(y_mono) / sr
    ccr = 0.0
    if len(beat_times) >= 6:
        g2 = np.asarray(beat_times)[::2]  # 半小節解析度,降低旋律干擾
        bfr = librosa.time_to_frames(g2, sr=sr)
        bfr = bfr[(bfr >= 0) & (bfr < chroma.shape[1])]
        if len(bfr) >= 3:
            sync = librosa.util.sync(chroma, bfr, aggregate=np.median)
            sync = sync / (np.linalg.norm(sync, axis=0, keepdims=True) + 1e-9)
            sims = np.sum(sync[:, :-1] * sync[:, 1:], axis=0)
            ccr = float(np.sum(sims < 0.85)) / max(dur, 1e-9)
    s_n = piecewise(n_pcs, [(3, 55), (4, 70), (5, 85), (6, 95), (7, 100), (9, 100), (11, 80)])
    s_r = piecewise(ccr, [(0.05, 55), (0.15, 75), (0.35, 95), (0.5, 100), (1.3, 100), (2.0, 80), (3.0, 60)])
    score = (s_n + s_r) / 2
    return {"score": round(score, 1), "n_pitch_classes": n_pcs,
            "changes_per_sec": round(ccr, 2),
            "comment": f"使用 {n_pcs} 個音級,和聲變化約每秒 {ccr:.2f} 次"}


def analyze_mix(mix_path, beat_times_ref=None):
    y_st, sr = librosa.load(mix_path, sr=SR_MIX, mono=False)
    if y_st.ndim == 1:
        y_st = y_st[np.newaxis, :]
    y_mono = librosa.to_mono(y_st)
    y22 = librosa.resample(y_mono, orig_sr=sr, target_sr=SR_MUSIC)
    beats = estimate_beats(y22, SR_MUSIC) if beat_times_ref is None else beat_times_ref
    return {
        "loudness": loudness_metrics(y_st, sr),
        "dynamic_range": dynamic_range_metrics(y_mono, y_stereo=y_st, sr=sr),
        "spectral_balance": spectral_balance_metrics(y_mono, sr),
        "stereo": stereo_metrics(y_st),
        "clipping": clipping_metrics(y_st),
        "structure": structure_metrics(y_mono, sr),
        "harmony": harmony_metrics(y22, SR_MUSIC, beats["beat_times"]),
    }, beats, y22


# ===========================================================================
# 四、加權計分與報告
# ===========================================================================

LABELS = {
    "pitch": "音準", "rhythm": "節奏", "stability": "長音穩定",
    "vibrato": "顫音", "dynamics": "動態控制", "voice_quality": "嗓音品質",
    "range": "音域",
    "loudness": "整體響度", "dynamic_range": "動態範圍",
    "spectral_balance": "頻譜平衡", "stereo": "立體聲寬度",
    "clipping": "削波檢測", "structure": "層次鋪陳", "harmony": "和聲豐富度",
}


def weighted_category(results, weights):
    """類別加權平均;缺項(None)自動剔除並重新正規化權重。"""
    avail = {k: v for k, v in results.items() if v is not None and "score" in v}
    if not avail:
        return None
    total_w = sum(weights[k] for k in avail)
    score = sum(avail[k]["score"] * weights[k] for k in avail) / total_w
    return round(score, 1)


def grade(score):
    if score is None:
        return "-"
    for th, g in [(90, "S"), (80, "A"), (70, "B"), (60, "C")]:
        if score >= th:
            return g
    return "D"


def render_report(meta, vocal_res, mix_res, vocal_score, mix_score, total):
    lines = []
    w = lines.append
    w("=" * 58)
    w("  歌曲自動評分報告(原創/無參考基準模式)")
    w("=" * 58)
    w(f"檔案:{meta['file']}")
    w(f"偵測調性:{meta['key']}(信心 {meta['key_conf']:.2f})|節奏:{meta['bpm']} BPM|長度:{meta['duration']:.0f} 秒")
    w("")
    if vocal_res is not None:
        w(f"【演唱表現】 {vocal_score} 分  等級 {grade(vocal_score)}")
        for k in DEFAULT_WEIGHTS["vocal"]:
            r = vocal_res.get(k)
            if r is None:
                w(f"  ・{LABELS[k]}:無法量測(略過,不計分)")
            else:
                w(f"  ・{LABELS[k]}:{r['score']:.0f} 分 — {r['comment']}")
        w("")
    else:
        w("【演唱表現】 未提供人聲軌,略過(可用 --vocal 或 --demucs)")
        w("")
    w(f"【編曲混音】 {mix_score} 分  等級 {grade(mix_score)}")
    for k in DEFAULT_WEIGHTS["mix"]:
        r = mix_res.get(k)
        if r is None:
            w(f"  ・{LABELS[k]}:無法量測(略過,不計分)")
        else:
            w(f"  ・{LABELS[k]}:{r['score']:.0f} 分 — {r['comment']}")
    w("")
    w("-" * 58)
    w(f"  總分:{total} / 100   等級:{grade(total)}")
    w("-" * 58)
    w("附註:情感表達與編曲創意屬主觀維度,本系統不評;")
    w("      音準以「貼合偵測到的調性」為準,爵士藍調等刻意離調曲風請斟酌。")
    return "\n".join(lines)


def separate_with_demucs(mix_path, owned_out=None):
    """可選:呼叫 demucs 把人聲分離出來(需 pip install demucs)。

    回 (人聲軌路徑, 暫存目錄)。

    🔴 Codex R27-P1-2:舊版 `tempfile.mkdtemp()` 之後沒有任何人負責刪 ——
       而那裡面不是一個小 JSON,是 Demucs 產出的**完整分軌**(等於整首歌再一份)。
       ⛔ 暫存目錄登記給呼叫端,由 main 的最外層 finally 清掉;
          demucs 失敗時這裡就地清乾淨(那時候呼叫端還沒拿到任何東西)。"""
    import subprocess
    import tempfile
    out = Path(tempfile.mkdtemp(prefix="song-jury-demucs-"))
    if owned_out is not None:
        owned_out.append(out)
    try:
        subprocess.run([sys.executable, "-m", "demucs", "--two-stems", "vocals",
                        "-o", str(out), str(mix_path)], check=True)
        return str(next(out.rglob("vocals.wav"))), out
    except Exception:
        # ⛔ **確認真的刪掉了才可以放掉 owner**(Codex R28-P1-1):舊版先移除再
        #    ignore_errors 刪 —— 刪除也失敗時目錄還在、卻已經沒有人負責它,
        #    外層 finally 不會再試,也沒有人告訴使用者那裡有一整份分軌。
        left = force_rmtree(out)
        if not left and owned_out is not None and out in owned_out:
            owned_out.remove(out)
        raise


def main():
    ap = argparse.ArgumentParser(description="原創歌曲自動評分系統(無參考基準)")
    ap.add_argument("mix", help="完整混音檔(wav/mp3/flac...)")
    ap.add_argument("--vocal", help="人聲軌檔案(提供才會評演唱表現)")
    ap.add_argument("--accomp", help="伴奏節奏軌(鼓+貝斯混音檔)。⚖️ rhythm 修復案(H2 D1):"
                                     "提供時人聲節奏網格改建於它,不再用全混音(人聲會污染自己的參照系)")
    ap.add_argument("--demucs", action="store_true", help="用 demucs 自動分離人聲")
    ap.add_argument("--blend-vocal", action="store_true",
                    help="舊行為:把演唱分依 55/45 併進總分。預設關閉——演唱各項照樣列分,"
                         "但它在總分裡的權重待八家對決裁定(不開就不會改變 total 的既有語義)")
    ap.add_argument("--json", dest="json_out", help="輸出 JSON 報告路徑")
    ap.add_argument("--weights", help="自訂權重 JSON 檔")
    args = ap.parse_args()

    weights = DEFAULT_WEIGHTS
    if args.weights:
        with open(args.weights, encoding="utf-8") as f:
            user_w = json.load(f)
        weights = {k: {**DEFAULT_WEIGHTS[k], **user_w.get(k, {})} for k in DEFAULT_WEIGHTS}

    # ⛔ 從這裡開始都要在 try 裡(Codex R27-P1-2):demucs 產出的是**整份分軌**,
    #    後面任何一步炸掉都不可以把它留在 TEMP。
    _owned_tmp = []
    try:
        vocal_path = args.vocal
        if args.demucs and not vocal_path:
            print("正在用 demucs 分離人聲(第一次會下載模型,較久)...")
            vocal_path, _ = separate_with_demucs(args.mix, _owned_tmp)

        print("分析編曲混音中...")
        mix_res, beats, y22 = analyze_mix(args.mix)
        key_info = estimate_key(y22, SR_MUSIC)
        duration = len(y22) / SR_MUSIC

        vocal_res = None
        if vocal_path:
            print("分析演唱表現中(音高追蹤較花時間)...")
            # ⚖️ rhythm 修復(2026-07-24 H2 D1 裁決「網格改建於鼓+貝斯」):
            #    舊法拿「全混音」抓拍 —— 人聲自己就在混音裡,等於拿自己當自己的節奏參照,
            #    加上追蹤雜訊,乾淨歌只得 55.6、同內容能盪 28 分。
            #    有 --accomp(鼓+貝斯)時改用它建網格;沒有則沿用舊法(分數仍凍結不進總分)。
            vocal_grid = beats["beat_times"]
            if getattr(args, "accomp", None):
                try:
                    y_acc, sr_acc = librosa.load(args.accomp, sr=SR_MUSIC, mono=True)
                    vocal_grid = estimate_beats(y_acc, SR_MUSIC)["beat_times"]
                    print("  (節奏網格:鼓+貝斯伴奏軌)")
                except Exception as e:
                    print(f"  (伴奏軌讀取失敗,退回全混音網格:{type(e).__name__})")
            vocal_res = analyze_vocal(vocal_path, key_info, vocal_grid)

        vocal_score = weighted_category(vocal_res, weights["vocal"]) if vocal_res else None
        mix_score = weighted_category(mix_res, weights["mix"])

        # ⭐ 演唱分要不要併進總分,由 --blend-vocal 決定(預設【不併】)。
        #
        # 為什麼預設不併:在接上 Demucs 之前,從來沒有人傳 --vocal,所以 scores.total 一直等於混音分,
        # 而那個數字正是報告上「物理技術 X/100」以及 web 總分裡物理 10% 的來源。一旦開始餵人聲軌,
        # 若照舊自動套 55/45,total 會【無聲改變語義】,新舊報告與排行榜就再也對不起來。
        # 演唱各項照樣完整列分(見 vocal_detail 與報告的【演唱表現】段),只是先不進這個 total;
        # 它在總分裡佔多少,跟其他新元件一起等八家對決裁定。
        #
        # ⚠️ else 分支的順序是【混音優先】,不可寫回舊的 vocal 優先:舊順序在兩者都有值時本來不可達,
        #    加了開關之後會踩到,寫錯就會讓沒開 blend 的情況變成「總分=演唱分」。
        if args.blend_vocal and vocal_score is not None and mix_score is not None:
            ow = weights["overall"]
            total = round((vocal_score * ow["vocal"] + mix_score * ow["mix"])
                          / (ow["vocal"] + ow["mix"]), 1)
            blended = True
        else:
            total = mix_score if mix_score is not None else vocal_score
            blended = False

        meta = {"file": Path(args.mix).name, "key": key_info["name"],
                "key_conf": key_info["confidence"], "bpm": beats["bpm"],
                "duration": duration}
        print()
        print(render_report(meta, vocal_res, mix_res, vocal_score, mix_score, total))

        if args.json_out:
            payload = {"meta": meta, "scores": {"vocal": vocal_score, "mix": mix_score,
                                                "total": total, "grade": grade(total)},
                       # 讓讀 JSON 的人一眼看出這個 total 是不是含演唱,免得跨版本比較時被誤導
                       "weighting": {"vocal_blended_into_total": blended,
                                     "overall_weights_applied": weights["overall"] if blended else None,
                                     "note": "各項均已列分;演唱在總分中的權重待八家對決裁定,"
                                             "未 blend 時 total = 混音分(與歷來報告同義)"},
                       "vocal_detail": vocal_res, "mix_detail": mix_res}
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            print(f"\nJSON 報告已存至:{args.json_out}")
    finally:
        # ⛔ 收尾失敗不可以無聲、也不可以沿用「一切正常」的退出碼(Codex R28-P1-1):
        #    那裡面是一整份分軌。有例外正在往外傳時就只報告(退出碼本來就非零),
        #    正常跑完卻清不掉 → 用專屬的 4(與 評審團.py 同一個語意)。
        _dirty = [x for x in (force_rmtree(_d) for _d in _owned_tmp) if x]
        if _dirty:
            for _x in _dirty:
                print(f"⛔ 分軌暫存沒清乾淨:{_x}(裡面是一整份分軌,請手動刪掉)",
                      file=sys.stderr)
            # ⚠️ 「正常結束」包含 sys.exit(0)(CLI 的正常收場)——
            #    只看 exc_info 是不是 None 的話,那條路會靜靜地回 0(自己踩到)。
            _exc = sys.exc_info()[1]
            if _exc is None or (isinstance(_exc, SystemExit)
                                and _exc.code in (None, 0)):
                sys.exit(4)


if __name__ == "__main__":
    main()
