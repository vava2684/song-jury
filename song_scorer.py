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
import sys
import warnings
from pathlib import Path

import numpy as np

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
DEFAULT_WEIGHTS = {
    "overall": {"vocal": 0.55, "mix": 0.45},
    "vocal": {
        "pitch": 0.30,        # 音準
        "rhythm": 0.15,       # 節奏
        "stability": 0.15,    # 長音穩定度
        "vibrato": 0.10,      # 顫音
        "dynamics": 0.10,     # 動態控制
        "voice_quality": 0.10,  # 嗓音品質
        "range": 0.10,        # 音域
    },
    "mix": {
        "loudness": 0.15,         # 整體響度
        "dynamic_range": 0.20,    # 動態範圍
        "spectral_balance": 0.20, # 頻譜平衡
        "stereo": 0.10,           # 立體聲寬度
        "clipping": 0.10,         # 削波檢測
        "structure": 0.15,        # 層次鋪陳
        "harmony": 0.10,          # 和聲豐富度
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
    """音準(無參考版):量測每顆音貼合半音格線的程度 + 落在調內的比例。"""
    if not notes:
        return None
    chrom_dev, in_scale = [], []
    for nt in notes:
        m = nt["median"]
        chrom_dev.append(abs(m - round(m)) * 100)  # 距最近半音幾音分
        pc = int(round(m)) % 12
        in_scale.append(1.0 if pc in scale_pcs else 0.0)
    mean_cents = float(np.mean(chrom_dev))
    in_scale_rate = float(np.mean(in_scale))
    intonation = piecewise(mean_cents, [(5, 100), (15, 95), (25, 85), (35, 70), (50, 45)])
    score = 0.8 * intonation + 0.2 * in_scale_rate * 100
    return {
        "score": round(score, 1),
        "mean_cents": round(mean_cents, 1),
        "in_scale_rate": round(in_scale_rate, 3),
        "n_notes": len(notes),
        "comment": f"平均偏差 {mean_cents:.0f} 音分,{in_scale_rate * 100:.0f}% 落在調內",
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
    return {"score": round(score, 1), "lufs": round(float(lufs), 1),
            "comment": f"整體響度 {lufs:.1f} LUFS(串流平台常見目標約 -14)"}


def dynamic_range_metrics(y_mono):
    peak = float(np.max(np.abs(y_mono)) + 1e-12)
    rms = float(np.sqrt(np.mean(y_mono ** 2)) + 1e-12)
    crest = 20 * np.log10(peak / rms)
    score = piecewise(crest, [(4, 40), (6, 65), (8, 85), (10, 100),
                              (16, 100), (20, 85), (26, 65)])
    return {"score": round(score, 1), "crest_db": round(crest, 1),
            "comment": f"峰值均方根比 {crest:.1f} dB" + (",壓縮偏重" if crest < 7 else "")}


def spectral_balance_metrics(y_mono, sr):
    S = np.abs(librosa.stft(y_mono, n_fft=4096)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    total = float(S.sum()) + 1e-12
    bands = {
        "low":     (60, 250,   0.12, 0.40),
        "lowmid":  (250, 2000, 0.30, 0.60),
        "highmid": (2000, 6000, 0.08, 0.35),
        "air":     (6000, 20000, 0.02, 0.20),
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
        "dynamic_range": dynamic_range_metrics(y_mono),
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


def separate_with_demucs(mix_path):
    """可選:呼叫 demucs 把人聲分離出來(需 pip install demucs)。"""
    import subprocess
    import tempfile
    out = Path(tempfile.mkdtemp())
    subprocess.run([sys.executable, "-m", "demucs", "--two-stems", "vocals",
                    "-o", str(out), str(mix_path)], check=True)
    return str(next(out.rglob("vocals.wav")))


def main():
    ap = argparse.ArgumentParser(description="原創歌曲自動評分系統(無參考基準)")
    ap.add_argument("mix", help="完整混音檔(wav/mp3/flac...)")
    ap.add_argument("--vocal", help="人聲軌檔案(提供才會評演唱表現)")
    ap.add_argument("--demucs", action="store_true", help="用 demucs 自動分離人聲")
    ap.add_argument("--json", dest="json_out", help="輸出 JSON 報告路徑")
    ap.add_argument("--weights", help="自訂權重 JSON 檔")
    args = ap.parse_args()

    weights = DEFAULT_WEIGHTS
    if args.weights:
        with open(args.weights, encoding="utf-8") as f:
            user_w = json.load(f)
        weights = {k: {**DEFAULT_WEIGHTS[k], **user_w.get(k, {})} for k in DEFAULT_WEIGHTS}

    vocal_path = args.vocal
    if args.demucs and not vocal_path:
        print("正在用 demucs 分離人聲(第一次會下載模型,較久)...")
        vocal_path = separate_with_demucs(args.mix)

    print("分析編曲混音中...")
    mix_res, beats, y22 = analyze_mix(args.mix)
    key_info = estimate_key(y22, SR_MUSIC)
    duration = len(y22) / SR_MUSIC

    vocal_res = None
    if vocal_path:
        print("分析演唱表現中(音高追蹤較花時間)...")
        vocal_res = analyze_vocal(vocal_path, key_info, beats["beat_times"])

    vocal_score = weighted_category(vocal_res, weights["vocal"]) if vocal_res else None
    mix_score = weighted_category(mix_res, weights["mix"])
    if vocal_score is not None and mix_score is not None:
        ow = weights["overall"]
        total = round((vocal_score * ow["vocal"] + mix_score * ow["mix"])
                      / (ow["vocal"] + ow["mix"]), 1)
    else:
        total = vocal_score if vocal_score is not None else mix_score

    meta = {"file": Path(args.mix).name, "key": key_info["name"],
            "key_conf": key_info["confidence"], "bpm": beats["bpm"],
            "duration": duration}
    print()
    print(render_report(meta, vocal_res, mix_res, vocal_score, mix_score, total))

    if args.json_out:
        payload = {"meta": meta, "scores": {"vocal": vocal_score, "mix": mix_score,
                                            "total": total, "grade": grade(total)},
                   "vocal_detail": vocal_res, "mix_detail": mix_res}
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nJSON 報告已存至:{args.json_out}")


if __name__ == "__main__":
    main()
