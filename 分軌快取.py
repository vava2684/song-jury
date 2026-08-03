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
import time
import uuid
from pathlib import Path

from 暫存清理 import force_rmtree, is_busy, release, take

# ⛔ 沒有身分紀錄的舊快取預設**不採信**(它可能是另一首同名歌的分軌,一旦認領就永遠錯下去)。
#    確知舊快取正確的人,才用這個環境變數明確授權沿用。
_TRUST_LEGACY = os.environ.get("SONG_JURY_TRUST_LEGACY_STEMS") == "1"

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


def _cache_name(audio_path: Path, model_name: str, fingerprint: str) -> str:
    """快取資料夾名。⛔ **命名規則只能有這一份** —— 同一條規則寫兩份必定漂移
    (編曲層次.py 自己拼過一次,加指紋後沒跟著改 → 人聲柱整根靜靜消失)。

    用**完整** SHA-256,不是前幾碼:前 8 碼只有 32 位元,生日碰撞期望約 65,536 個檔案
    —— 實測 70,698 次就撞到,第二首會被判 from_cache 讀到第一首的分軌。
    歌名截短是為了避開 Windows 的路徑長度上限。"""
    return f"{audio_path.stem[:40]}__{model_name}__{fingerprint}"


def _cache_is_valid(cache: Path, sources, fingerprint: str) -> bool:
    """這個快取夾是否「檔案齊全 **且** 身分完全相符」。

    ⛔ 不可以只信資料夾名:名字可能碰撞、也可能被人手動搬動。
       每次命中都要比對 _source.json 的**完整**指紋。"""
    if not cache.is_dir():
        return False
    if not all((cache / f"{s}.flac").exists() for s in sources):
        return False
    try:
        rec = json.loads((cache / "_source.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    return rec.get("fingerprint") == fingerprint


def _legacy_dir(audio_path: Path, stems_dir: Path, model_name: str) -> Path:
    """舊版共用名(沒有指紋)的快取位置。"""
    return stems_dir / f"{audio_path.stem}__{model_name}"


def _sidecar_fp(cache: Path):
    try:
        return json.loads((cache / "_source.json").read_text(encoding="utf-8")).get("fingerprint")
    except Exception:
        return None


def _sidecar_complete(cache: Path, fingerprint: str) -> bool:
    """身分相符 **且** sidecar 記錄的每一軌 flac 都在,才算可用。

    ⛔ 曾經只驗 `any(*.flac)`:「有任何一個 flac」不等於「完整」——
       Codex 造出「sidecar 正確 + 只有 drums.flac」的殘缺夾,cache_dir_of 照樣選它,
       下游拿不到 vocals.flac → 人聲柱又消失。這種殘缺真的會發生:
       rmtree(ignore_errors=True) 刪到一半失敗、或發佈中途被砍都會留下部分內容。
    sidecar 沒記軌清單(更舊版本寫的)→ 至少要求下游真正用的 vocals.flac 在。
    """
    try:
        rec = json.loads((cache / "_source.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    if rec.get("fingerprint") != fingerprint:
        return False
    srcs = rec.get("sources")
    if isinstance(srcs, list) and srcs:
        return all((cache / f"{s}.flac").exists() for s in srcs)
    return (cache / "vocals.flac").exists()


def cache_dir_of(audio_path: Path, stems_dir: Path, model_name: str) -> Path:
    """這首歌在這個模型下的快取資料夾實際位置。

    ⛔ 呼叫端**不可以自己重組這個路徑** —— 編曲層次.py 曾經自己拼 `{stem}__{model}`,
       快取命名一改就對不上,vocal_stem 變成 None → 人聲柱整根靜靜消失。
    ⛔ 這個函式與 separate() 的採用判斷**必須一致**:曾經 separate() 接受了身分相符的
       舊快取,這裡卻回傳不存在的新 SHA 路徑 → 同一個 bug 又發生一次。
       現在 separate() 會把合法舊快取**搬到新路徑**,所以正常情況只會有一個位置;
       萬一搬不動(權限/跨磁碟),下面兩個 fallback 讓兩邊仍然對得起來。
    """
    ident = _source_ident(audio_path)
    newp = stems_dir / _cache_name(audio_path, model_name, ident["fingerprint"])
    legacy = _legacy_dir(audio_path, stems_dir, model_name)

    # ⛔ 不可以只看 newp.is_dir():新資料夾可能存在但**殘缺**(上一輪中途被砍),
    #    這時 separate() 會退去用合法的舊快取,而這裡卻回傳那個殘缺的新路徑
    #    → 下游拿不到 vocals.flac,人聲柱又一次靜默消失(這已經是第三次了)。
    #    判斷順序必須跟 separate() 完全一致:新的「有內容」才算數,否則看舊的。
    if newp.is_dir() and _sidecar_complete(newp, ident["fingerprint"]):
        return newp
    if legacy.is_dir():
        if _sidecar_complete(legacy, ident["fingerprint"]):
            return legacy          # 身分相符且有內容 → 跟 separate() 一致
        if _TRUST_LEGACY and _sidecar_fp(legacy) is None:
            return legacy          # 無身分但使用者明確授權沿用
    return newp                    # 都沒有 → 回新路徑(等著被建立)


# 舊的 .tmp_ 至少要這麼舊才回收(預設 1 小時)——⚠️ 這是**第二道**保險,
# 第一道是鎖:鎖拿不到就完全不碰。兩道都過才刪。
TMP_GRACE = 3600.0


def sweep_stale_tmp(stems_dir: Path, grace: float = None) -> int:
    """回收 `_stems` 裡沒人在寫、而且夠舊的 `.tmp_*`;回收幾個回幾。

    🔴 Codex R28-P2-3:舊版只清「自己這次的 tmp」,而且全部 ignore_errors ——
       程序被強制終止(或 rmtree 自己失敗)之後,那份 `.tmp_*` 裡可能是**整套分軌**,
       而它是隱藏目錄,使用者幾乎不可能發現。
    ⛔ 判「還有沒有人在寫」只能靠鎖,不能靠 PID:PID 會被重用,而且跨機器共享
       目錄時別台的 PID 在這台毫無意義。"""
    grace = TMP_GRACE if grace is None else grace
    now = time.time()
    n = 0
    try:
        entries = list(Path(stems_dir).glob(".tmp_*"))
    except OSError:
        return 0
    for d in entries:
        try:
            if not d.is_dir():
                continue
            # ⚠️ 年紀要**先量**:探鎖會建出 .lock,那會更新目錄的 mtime,
            #    年紀瞬間變成 0 → 永遠不會被回收(自己踩到)。
            age = now - d.stat().st_mtime
            lock = d / ".lock"
            if lock.exists() and is_busy(lock):
                continue                     # ⛔ 有人正在寫 → 不管多舊都不碰
            if age <= grace:
                continue
            left = force_rmtree(d)
            if left:
                print(f"      ⛔ 舊的分軌暫存清不掉:{left}(請手動刪掉)", flush=True)
            else:
                n += 1
        except OSError as e:
            print(f"      ⛔ 舊的分軌暫存回收失敗:{d}({type(e).__name__}: {e})",
                  flush=True)
    return n


def separate(audio_path: Path, stems_dir: Path, model_name: str):
    """用 demucs 分軌;已有快取就直接讀。回 (dict[stem]=波形, sr, sources順序, from_cache)。

    波形 shape 為 (channels, samples) 的 float32 numpy。
    分軌檔放在 cache_dir_of() 指的資料夾;要拿路徑請呼叫它,不要自己拼。
    """
    # ⭐ 每次分軌前先收掉沒人在寫的舊 .tmp_(它們可能是整套分軌)
    # ⛔ 要排在重量級 import **之前**:回收不該取決於 demucs 裝好了沒 ——
    #    裝壞的機器反而最容易留下半份分軌(自己踩到:放在後面時測試根本沒跑到)。
    sweep_stale_tmp(stems_dir)

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
    fp = ident["fingerprint"]
    cache = stems_dir / _cache_name(audio_path, model_name, fp)
    # ⛔ 命中一定要驗**完整**指紋,不能只看資料夾在不在(名字會碰撞、也可能被手動搬動)
    have_all = _cache_is_valid(cache, sources, fp)

    if not have_all:
        legacy = _legacy_dir(audio_path, stems_dir, model_name)       # 舊版共用名
        if legacy.is_dir() and all((legacy / f"{s}.flac").exists() for s in sources):
            if _cache_is_valid(legacy, sources, fp):
                # ⭐ 身分相符 → **搬到新路徑**,而不是原地沿用。
                #    留在舊路徑的話,cache_dir_of() 與這裡就得各自維護一套「要不要接受舊名」
                #    的判斷,兩份規則遲早漂移(已經漂過一次:vocal_stem 變 None)。
                #    搬過去之後全系統只有一個位置,整類問題消失。改名是 metadata 操作,很快。
                try:
                    # ⛔ 搬之前要先處理掉「已存在但殘缺」的新目標,否則 os.replace 會失敗,
                    #    退去用舊路徑,而 cache_dir_of() 又看到新資料夾在 → 兩邊指到不同地方。
                    if cache.exists() and not _cache_is_valid(cache, sources, fp):
                        shutil.rmtree(cache, ignore_errors=True)
                    os.replace(legacy, cache)
                    print(f"      ↳ 舊快取身分相符,已搬到帶指紋的新路徑:{cache.name}", flush=True)
                    have_all = True
                    try:      # 舊 sidecar 沒記軌清單 → 補寫,cache_dir_of 才驗得了「完整」
                        (cache / "_source.json").write_text(
                            json.dumps({**ident, "sources": sources},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
                    except Exception:
                        pass
                except OSError:
                    cache, have_all = legacy, True     # 搬不動(權限/跨磁碟)就原地用
            elif not (legacy / "_source.json").exists():
                # ⛔ 沒有身分紀錄的舊快取**不可以自動採信、更不可以蓋章成本首的身分**:
                #    它有可能是另一首同名歌的分軌,一旦認領就把錯的分軌變成「正確快取」,
                #    之後所有分數都錯而且再也查不出來。預設重跑分軌(慢但正確)。
                #    確知那些舊快取是對的,才用環境變數明確授權沿用。
                if _TRUST_LEGACY:
                    print(f"      ⚠ 依 SONG_JURY_TRUST_LEGACY_STEMS=1 沿用無身分的舊快取:"
                          f"{legacy.name}(風險自負)", flush=True)
                    try:
                        (legacy / "_source.json").write_text(
                            json.dumps({**ident, "sources": sources},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
                    except Exception:
                        pass
                    cache, have_all = legacy, True
                else:
                    print(f"      ⚠ 舊快取 {legacy.name} 沒有來源紀錄,無法證明是這首歌的 → 重新分軌。"
                          f"(確定那些舊快取正確的話,設 SONG_JURY_TRUST_LEGACY_STEMS=1 可沿用)",
                          flush=True)
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
    # ⛔ 暫存名要帶 UUID,不能只用 PID:同一個程序裡的兩個執行緒 PID 相同,
    #    會共用同一個暫存夾互相覆寫。
    tmp = stems_dir / f".tmp_{cache.name}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    if tmp.exists():
        force_rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    # ⭐ 這一份正在寫 → 持有租約(Codex R28-P2-3):別的程序掃到它時,
    #    鎖拿不到就知道「有人正在寫」,不會把寫到一半的分軌刪掉。
    _hold = take(tmp / ".lock")
    stems = {}
    try:
        for i, s in enumerate(sources):
            arr = est[i].cpu()
            torchaudio.save(str(tmp / f"{s}.flac"), arr, sr)
            stems[s] = arr.numpy()
        # ⭐ sidecar 一併記錄軌清單:cache_dir_of 靠它驗「完整」而不是「有任一 flac」
        #    (只驗 any 的話,殘缺夾會被誤認可用 → 下游拿不到 vocals.flac)
        tmp.joinpath("_source.json").write_text(
            json.dumps({**ident, "sources": sources}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        # ⛔ 發佈前一定要先放掉鎖並刪掉鎖檔(自己踩到):Windows 不讓你改名一個
        #    「裡面還有開啟中的檔案」的目錄 → os.replace 會 PermissionError;
        #    而且鎖檔也不該被一起發佈進正式快取。
        release(_hold)
        _hold = None
        (tmp / ".lock").unlink(missing_ok=True)
        try:
            os.replace(tmp, cache)          # 原子發佈
        except OSError as e:
            # ⛔ 只可以吞「目標已存在」這一種:權限不足、磁碟滿、路徑太長都必須拋出來,
            #    否則使用者會以為快取寫好了,下一輪又整首重跑,而且永遠查不出原因。
            if not cache.exists():
                raise
            if _cache_is_valid(cache, sources, fp):
                # 另一個程序先發佈了同一首歌(名字含完整指紋,內容等價)→ 丟掉自己的暫存
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                # 目標存在但殘缺(上一輪中途被砍)→ 用自己這份完整的取代它,否則永遠修不好
                shutil.rmtree(cache, ignore_errors=True)
                os.replace(tmp, cache)
    except BaseException:
        release(_hold)
        _hold = None
        left = force_rmtree(tmp)                 # 中途炸掉不留半成品
        if left:
            print(f"      ⛔ 分軌暫存清不掉:{left}(裡面是半份分軌,請手動刪掉)",
                  flush=True)
        raise
    finally:
        release(_hold)
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
