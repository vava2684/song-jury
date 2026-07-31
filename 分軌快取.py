#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分軌快取(Shared Demucs Stem Cache)— 滿血版共用底層元件

原本 separate() 寫死在 編曲層次.py 裡。和聲分析.py 也需要同一份分軌
(它只要 other+guitar+piano,不要 drums/vocals),兩邊各跑一次 Demucs
等於白燒兩趟 GPU。所以把 separate() 抽出來放這裡,兩支工具共用。

⚠️ 快取鍵必須跟舊版「位元組等價」:
       {stems_dir}/{音檔stem}__{model_name}/{軌名}.flac
   路徑或檔名只要動一個字,舊快取就全部失效、GPU 成本直接翻倍。
   所以下面 cache 那三行是照抄 編曲層次.py 的原始寫法,不要「順手美化」。

⚠️ 已知且可接受的小差異:第一次(現場分軌)用的是記憶體裡的浮點波形,
   之後的run讀的是存成 flac 的版本,兩者有極微量量化差。實測 Storm and Stars
   在和聲分析上只造成 1 個和弦段落邊界翻轉(五度動線 0.415→0.366)。
   也就是「首跑」與「快取跑」數值穩定但非位元等價 —— 要對兩首歌做嚴格 PK 時,
   請確保兩首都已經有快取(或都沒有),不要一首首跑一首讀快取。

⚠️ sources 一律從 model.sources 動態讀,絕不寫死。
   htdemucs_6s 是 drums,bass,other,vocals,guitar,piano;
   換成 htdemucs(4 軌)時就會少 guitar/piano,呼叫端要自己容錯。

必須用「裝了 demucs 的那個 python」跑。呼叫端不要自己猜路徑 ——
評審團.py 的 _find_demucs_py() 是唯一真理來源(環境變數 SONG_JURY_DEMUCS_PY
→ .venv-demucs → .venv-ml → 家目錄的 anaconda/miniconda/miniforge)。
"""
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np


def _source_ident(p: Path) -> dict:
    """算音檔身分指紋 —— 給分軌快取驗「這份快取真的是這首歌的嗎」。

    用「檔案大小 + 頭尾各 1MB 的 sha256」而不是整檔雜湊:整檔對幾百 MB 的歌太慢,
    頭尾+大小已足以擋掉「同名不同曲」與「同名檔被換成新版」這兩種真實情境。
    ⛔ 不用 mtime:複製/下載會讓它改變,會無謂地讓正確的快取失效。
    """
    st = p.stat()
    h = hashlib.sha256()
    h.update(str(st.st_size).encode())
    CHUNK = 1024 * 1024
    with open(p, "rb") as f:
        h.update(f.read(CHUNK))
        if st.st_size > CHUNK * 2:
            f.seek(-CHUNK, os.SEEK_END)
            h.update(f.read(CHUNK))
    return {"name": p.name, "size": st.st_size, "fingerprint": h.hexdigest()}


def separate(audio_path: Path, stems_dir: Path, model_name: str):
    """用 demucs 分軌;已有快取就直接讀。回 (dict[stem]=波形, sr, sources順序, from_cache)。

    波形 shape 為 (channels, samples) 的 float32 numpy。
    ⚠️ 這個函式的行為與快取路徑必須跟 2026-07-19 版 編曲層次.py 完全一致。
    """
    import torch
    import torchaudio
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    from demucs.audio import AudioFile

    model = get_model(model_name)
    sources = list(model.sources)          # ⚠️ 動態讀,絕不寫死(6s 是 drums,bass,other,vocals,guitar,piano)
    sr = model.samplerate

    # ⛔ 快取鍵不可以只有「檔名+模型名」:不同資料夾的兩首 song.mp3、或同名檔被換成新版,
    #    會直接讀到另一首歌的分軌 —— 人聲/和聲/編曲全部算錯而且不會報錯。
    #    這裡在資料夾旁放一份 _source.json 記下來源身分,命中時先驗身分。
    ident = _source_ident(audio_path)
    cache = stems_dir / f"{audio_path.stem}__{model_name}"
    side = cache / "_source.json"
    have_all = cache.is_dir() and all((cache / f"{s}.flac").exists() for s in sources)

    if have_all:
        if side.exists():
            try:
                rec = json.loads(side.read_text(encoding="utf-8"))
            except Exception:
                rec = {}
            if rec.get("fingerprint") != ident["fingerprint"]:
                # 撞名了 → 這份快取不是這首歌的。改用帶指紋的資料夾,兩首各自有快取。
                cache = stems_dir / f"{audio_path.stem}__{model_name}__{ident['fingerprint'][:8]}"
                side = cache / "_source.json"
                have_all = cache.is_dir() and all((cache / f"{s}.flac").exists() for s in sources)
        else:
            # 舊版留下來的快取沒有身分紀錄。沿用(不強迫使用者重跑數小時的 Demucs),
            # 但補寫身分並提醒一次 —— 之後再撞名就驗得出來了。
            print(f"      ⚠ 舊版快取沒有來源紀錄,已補寫身分:{cache.name}", flush=True)
            try:
                side.write_text(json.dumps(ident, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass

    if have_all:
        stems = {}
        for s in sources:
            w, _sr = torchaudio.load(str(cache / f"{s}.flac"))
            stems[s] = w.numpy()
        return stems, sr, sources, True   # from_cache=True

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device).eval()
    wav = AudioFile(str(audio_path)).read(streams=0, samplerate=sr, channels=model.audio_channels)
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)
    with torch.no_grad():
        est = apply_model(model, wav[None].to(device), device=device, split=True, overlap=0.25)[0]
    est = est * ref.std() + ref.mean()

    cache.mkdir(parents=True, exist_ok=True)
    cache.joinpath("_source.json").write_text(
        json.dumps(ident, ensure_ascii=False, indent=1), encoding="utf-8")
    stems = {}
    for i, s in enumerate(sources):
        arr = est[i].cpu()
        torchaudio.save(str(cache / f"{s}.flac"), arr, sr)
        stems[s] = arr.numpy()
    return stems, sr, sources, False


def mix_stems(stems: dict, keep, missing="ignore") -> np.ndarray:
    """把指定的幾軌相加成一條單聲道波形。

    和聲分析要的是「和聲樂器」:other + guitar + piano。
    刻意丟掉 drums(寬頻瞬態會把 chroma 每一格都填滿)與 vocals
    (獨唱旋律是單音、又有大量滑音/顫音,會把和弦模板比對帶偏)。
    bass 也不加 —— 貝斯多半只彈根音,加進去會讓所有和弦都往「根音強」偏,
    反而壓過三音/七音這些真正決定品質(maj/min/7)的音級。

    missing="ignore":模型不含該軌(例如 4 軌版沒有 guitar/piano)就跳過,不報錯。
    """
    picked = []
    for s in keep:
        if s in stems:
            picked.append(stems[s])
        elif missing != "ignore":
            raise KeyError(f"分軌結果缺少 {s}")
    if not picked:
        raise RuntimeError(f"沒有任何可用的和聲軌(要 {list(keep)},實際只有 {list(stems)})")
    n = min(w.shape[-1] for w in picked)
    acc = np.zeros(n, dtype=np.float64)
    for w in picked:
        acc += (w.mean(axis=0) if w.ndim > 1 else w)[:n]
    return acc.astype(np.float32)
