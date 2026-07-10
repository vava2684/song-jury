#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_demo_song.py — 合成一首 24 秒的測試歌曲,驗證 song_scorer.py 能跑通。
產出 demo_vocal.wav(人聲軌)與 demo_mix.wav(完整混音)。
人聲刻意加入:每顆音 ±8 音分的隨機微走音、長音 5.5Hz/25 音分顫音、±15ms 節奏抖動。
"""
import numpy as np
import soundfile as sf

rng = np.random.default_rng(42)
SR = 44100
BPM = 100
BEAT = 60 / BPM          # 0.6 秒
BAR = 4 * BEAT           # 2.4 秒
N_BARS = 10
DUR = N_BARS * BAR       # 24 秒
N = int(DUR * SR)
t_all = np.arange(N) / SR


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def synth_note(midi, start, dur_beats, amp, vibrato=False):
    """合成一顆人聲音符:基頻 + 兩個泛音,含微走音與顫音。"""
    start += rng.normal(0, 0.015)                       # 節奏抖動 ±15ms
    start = max(0.0, start)
    dur = dur_beats * BEAT - 0.05
    n = int(dur * SR)
    i0 = int(start * SR)
    if i0 + n > N:
        n = N - i0
    if n <= 0:
        return
    t = np.arange(n) / SR
    detune = rng.normal(0, 8)                           # 微走音 ±8 音分
    f0 = midi_hz(midi + detune / 100)
    f_t = np.full(n, f0)
    if vibrato and dur > 0.5:
        depth = 25 / 100                                # 25 音分
        ramp = np.clip((t - 0.15) / 0.2, 0, 1)          # 顫音漸入
        f_t = f0 * 2 ** (depth * ramp * np.sin(2 * np.pi * 5.5 * t) / 12)
    phase = 2 * np.pi * np.cumsum(f_t) / SR
    sig = np.sin(phase) + 0.30 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase)
    env = np.minimum(np.minimum(t / 0.03, 1), np.minimum((dur - t) / 0.08, 1))
    env = np.clip(env, 0, 1)
    vocal[i0:i0 + n] += amp * sig * env


def synth_pad(chord_midis, start, dur_beats, amp, detune_cents):
    dur = dur_beats * BEAT
    n = int(dur * SR)
    i0 = int(start * SR)
    if i0 + n > N:
        n = N - i0
    t = np.arange(n) / SR
    env = np.clip(np.minimum(t / 0.25, (dur - t) / 0.3), 0, 1)
    out = np.zeros(n)
    for m in chord_midis:
        f = midi_hz(m + detune_cents / 100)
        out += np.sin(2 * np.pi * f * t) + 0.2 * np.sin(4 * np.pi * f * t)
    return i0, amp * out * env / len(chord_midis)


def synth_kick(start, amp):
    n = int(0.18 * SR)
    i0 = int(start * SR)
    if i0 + n > N:
        return
    t = np.arange(n) / SR
    f = 90 * np.exp(-t * 18) + 45
    sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 14)
    drums[i0:i0 + n] += amp * sig


def synth_hat(start, amp):
    n = int(0.04 * SR)
    i0 = int(start * SR)
    if i0 + n > N:
        return
    noise = rng.normal(0, 1, n)
    noise = np.diff(noise, prepend=0)                   # 高通化
    hatsL[i0:i0 + n] += amp * 0.6 * noise * np.exp(-np.arange(n) / (0.012 * SR))
    hatsR[i0:i0 + n] += amp * 0.4 * noise * np.exp(-np.arange(n) / (0.012 * SR))


vocal = np.zeros(N)
drums = np.zeros(N)
hatsL = np.zeros(N)
hatsR = np.zeros(N)

# ---- 和弦進行(C 大調):verse C-F-Am-G、chorus F-G-C-Am、outro C ----
C, F, Am, G = [60, 64, 67], [53, 57, 60], [57, 60, 64], [55, 59, 62]
progression = [C, F, Am, G, F, G, C, Am, C, C]
padL = np.zeros(N)
padR = np.zeros(N)
bass = np.zeros(N)
for bar, chord in enumerate(progression):
    amp = 0.10 if bar < 4 or bar >= 8 else 0.15         # 副歌較大聲
    lo = [m - 12 for m in chord]
    i0, sig = synth_pad(lo, bar * BAR, 4, amp, +3)
    padL[i0:i0 + len(sig)] += sig
    i0, sig = synth_pad(lo, bar * BAR, 4, amp, -3)
    padR[i0:i0 + len(sig)] += sig
    # 貝斯:根音低兩個八度,每小節長音
    i0, sig = synth_pad([chord[0] - 24], bar * BAR, 4, amp * 1.9, 0)
    bass[i0:i0 + len(sig)] += sig

# ---- 鼓 ----
for bar in range(N_BARS):
    verse = bar < 4 or bar >= 8
    for b in range(4):
        synth_kick(bar * BAR + b * BEAT, 0.28 if verse else 0.42)
        synth_hat(bar * BAR + b * BEAT, 0.10 if verse else 0.20)
        synth_hat(bar * BAR + b * BEAT + BEAT / 2, 0.07 if verse else 0.20)

# ---- 旋律(C 大調音階,(midi, 拍數) )----
verse_mel = [(60, 1), (62, 1), (64, 1), (62, 1), (64, 1), (65, 1), (67, 2),
             (64, 1), (62, 1), (60, 2), (0, 4)]
chorus_mel = [(67, 1), (69, 1), (71, 0.5), (69, 0.5), (67, 1), (65, 1), (64, 1),
              (67, 2), (65, 1), (64, 1), (62, 1), (60, 2), (0, 2)]
outro_mel = [(64, 1), (62, 1), (60, 4), (0, 2)]

pos = 0.0
for seq, amp in [(verse_mel, 0.34), (chorus_mel, 0.48), (outro_mel, 0.30)]:
    if seq is chorus_mel:
        pos = 4 * BAR
    if seq is outro_mel:
        pos = 8 * BAR
    for midi, beats in seq:
        if midi > 0:
            synth_note(midi, pos, beats, amp, vibrato=(beats >= 1))
        pos += beats * BEAT

# ---- 輸出 ----
vocal_out = vocal / max(np.max(np.abs(vocal)), 1e-9) * 0.8
sf.write("demo_vocal.wav", vocal_out, SR)

mixL = vocal * 1.0 + padL + bass + drums + hatsL
mixR = vocal * 1.0 + padR + bass + drums + hatsR
peak = max(np.max(np.abs(mixL)), np.max(np.abs(mixR)), 1e-9)
mix = np.stack([mixL, mixR]) / peak * 0.90
sf.write("demo_mix.wav", mix.T, SR)
print(f"已產生 demo_vocal.wav 與 demo_mix.wav({DUR:.0f} 秒,{BPM} BPM,C 大調)")
