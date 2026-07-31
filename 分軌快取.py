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
import shutil
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

    **整檔串流 SHA-256**。
    ⛔ 曾經為了省時間只雜湊「大小 + 頭尾各 1MB」,結果 3MB 以上的檔案**中段改動測不出來**
       —— 兩首大小相同、頭尾相同、只有中間不一樣的歌會共用同一份分軌,分數全錯還不報錯。
       實測:整檔雜湊 3.3MB 只要 0.014 秒,而 Demucs 分軌同一首要幾十秒 → 成本可忽略,
       沒有任何理由為了這點時間換來一個會靜默算錯分的快取。
    ⛔ 不用 mtime:複製/下載會讓它改變,會無謂地讓正確的快取失效。
    """
    st = p.stat()
    h = hashlib.sha256()
    h.update(str(st.st_size).encode())
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"name": p.name, "size": st.st_size, "fingerprint": h.hexdigest()}


def cache_dir_of(audio_path: Path, stems_dir: Path, model_name: str) -> Path:
    """這首歌在這個模型下的快取資料夾實際位置。

    ⛔ 呼叫端**不可以自己重組這個路徑** —— 編曲層次.py 曾經自己拼
       `{stem}__{model}`,結果快取命名一改(加上指紋)就對不上,
       vocal_stem 變成 None → 人聲柱整根靜靜消失。規則只能有一份。
    """
    fp8 = _source_ident(audio_path)["fingerprint"][:8]
    newp = stems_dir / f"{audio_path.stem}__{model_name}__{fp8}"
    if newp.is_dir():
        return newp
    legacy = stems_dir / f"{audio_path.stem}__{model_name}"   # 舊版共用名(向下相容)
    return legacy if legacy.is_dir() else newp


def separate(audio_path: Path, stems_dir: Path, model_name: str):
    """用 demucs 分軌;已有快取就直接讀。回 (dict[stem]=波形, sr, sources順序, from_cache)。

    波形 shape 為 (channels, samples) 的 float32 numpy。
    分軌檔放在 cache_dir_of() 指的資料夾;要拿路徑請呼叫它,不要自己拼。
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
    #
    # 現行做法:**資料夾名從一開始就帶指紋**,而不是「先用共用名、撞到才換」。
    #   ⛔ 舊版是後者,併發時會出事:兩首同名不同曲同時首跑,雙方都看到「快取不存在」,
    #      於是都選了同一個共用資料夾,分軌檔互相覆寫、_source.json 只符合其中一首。
    #      名字一開始就不同,就沒有這個競賽條件。
    ident = _source_ident(audio_path)
    fp8 = ident["fingerprint"][:8]
    cache = stems_dir / f"{audio_path.stem}__{model_name}__{fp8}"
    side = cache / "_source.json"
    have_all = cache.is_dir() and all((cache / f"{s}.flac").exists() for s in sources)

    if not have_all:
        # 相容舊版留下的共用名資料夾(她本機有 7.4GB,不強迫重跑數小時的 Demucs)。
        # 有身分紀錄就驗;沒有(更舊的版本)就沿用並補寫,提醒一次。
        legacy = stems_dir / f"{audio_path.stem}__{model_name}"
        if legacy.is_dir() and all((legacy / f"{s}.flac").exists() for s in sources):
            rec = {}
            lside = legacy / "_source.json"
            if lside.exists():
                try:
                    rec = json.loads(lside.read_text(encoding="utf-8"))
                except Exception:
                    rec = {}
            if not lside.exists():
                print(f"      ⚠ 舊版快取沒有來源紀錄,沿用並補寫身分:{legacy.name}", flush=True)
                try:
                    lside.write_text(json.dumps(ident, ensure_ascii=False, indent=1), encoding="utf-8")
                except Exception:
                    pass
                cache, side, have_all = legacy, lside, True
            elif rec.get("fingerprint") == ident["fingerprint"]:
                cache, side, have_all = legacy, lside, True
            # 指紋不符 → 那份是別首歌的,維持用帶指紋的新資料夾(have_all 仍為 False)

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

    # ── 原子發佈:先寫進本程序專屬的暫存夾,全部寫完才改名成正式快取 ──────────
    # ⛔ 不可以直接往正式資料夾邊算邊寫:同一首歌被兩個程序同時跑時(批次 + 手動、
    #    或評審團同時要編曲與和聲),讀取端會看到「檔案數量夠了但內容還沒寫完」的半成品。
    #    改名在同一個檔案系統上是原子操作 → 讀取端只會看到「還沒有」或「完整的」。
    tmp = stems_dir / f".tmp_{cache.name}_{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    stems = {}
    try:
        for i, s in enumerate(sources):
            arr = est[i].cpu()
            torchaudio.save(str(tmp / f"{s}.flac"), arr, sr)
            stems[s] = arr.numpy()
        tmp.joinpath("_source.json").write_text(
            json.dumps(ident, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            os.replace(tmp, cache)          # 原子發佈
        except OSError:
            # 目標已存在 = 另一個程序先發佈了同一首歌(資料夾名含指紋,所以內容等價)
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)   # 中途炸掉不留半成品
        raise
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
