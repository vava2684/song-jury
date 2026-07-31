#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""伴奏混音 —— 從 Demucs 分軌快取產「鼓+貝斯」節奏軌(rhythm 修復案 H2 D1 的配套)。

用途:song_scorer 的人聲節奏網格改建於此軌(人聲不再污染自己的參照系)。
必須用「裝了 demucs 的那個 python」跑(見 分軌快取.py 的說明);快取命中時零 GPU、秒級完成。

用法:python 伴奏混音.py <音檔> <輸出.wav> [--stems 快取夾]
"""
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 分軌快取 import separate, mix_stems  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("out")
    ap.add_argument("--stems", default=None)
    a = ap.parse_args()
    audio = Path(a.audio).resolve()
    stems_dir = Path(a.stems).resolve() if a.stems else audio.parent / "_stems"
    stems, sr, sources, cached = separate(audio, stems_dir, "htdemucs_6s")
    y = mix_stems(stems, ("drums", "bass"))
    import soundfile as sf
    sf.write(a.out, y, sr)
    print(f"伴奏軌(drums+bass)→ {a.out}(快取命中:{cached})")


if __name__ == "__main__":
    main()
