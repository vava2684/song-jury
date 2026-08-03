#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批次評測 + 鑑別力分析 —— Phase 2「八家對決定權重」的證據產生器。

為什麼需要這支:
    權重要有依據,不能靠印象投票。判斷「某個指標值不值得高權重」的前提是
    ——【它在一批歌之間分得出高低嗎】。只有一首歌的分數,任何權重討論都是空談。
    本工具把 N 首歌跑過完整七關,再算出每個指標的離散程度與相關性,
    產出一份「哪些指標有鑑別力、哪些是常數」的證據表交給評審。

⛔ 必須循序跑,不可平行:SongEval 尖峰約 20.1GB、Music Flamingo 約 16.7GB,
   同時上卡會 OOM。一首約 5-8 分鐘。

用法:
    python 批次評測.py <歌單.txt 或 資料夾>  [--out 結果夾] [--skip-existing]
    歌單.txt = 一行一個音檔路徑(# 開頭是註解;可在路徑後加 ` | 標籤` 註明她的主觀評價)

輸出:
    <結果夾>/批次結果.json    每首的完整七關數據
    <結果夾>/鑑別力報告.md    給八家評審看的證據表
"""
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean, pstdev

from 暫存清理 import parse_dirty
from 子程序 import run_tree
from 驗證報告 import REQUIRED_PILLARS, validate

os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent.resolve()
_WIN = sys.platform == "win32"
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _WIN else {}
VENV_PY = BASE / (".venv/Scripts/python.exe" if _WIN else ".venv/bin/python")

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}


def collect_songs(target: Path):
    """歌單檔或資料夾 → [(音檔路徑, 標籤)]。標籤是她可選填的主觀評價,用來對照指標準不準。"""
    out = []
    if target.is_dir():
        for p in sorted(target.iterdir()):
            if p.suffix.lower() in AUDIO_EXT:
                out.append((p, ""))
        return out
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path, _, label = line.partition("|")
        p = Path(path.strip())
        if p.exists():
            out.append((p, label.strip()))
        else:
            print(f"⚠ 找不到,跳過:{p}", file=sys.stderr)
    return out


class ContractMismatch(RuntimeError):
    """進度檔的批次契約與這次執行的模式不同 —— 兩把尺不可混在同一個 store。"""


def _load_store(store: Path) -> dict:
    """讀進度檔並**驗批次契約**。舊的裸 mapping 一律拒絕續跑(不知道是哪把尺)。"""
    d = json.loads(store.read_text(encoding="utf-8"))
    want = FULL_CONTRACT if FULL_MODE else LOCAL_CONTRACT
    if not isinstance(d, dict) or "results" not in d or "batch_contract" not in d:
        raise ContractMismatch(
            f"{store.name} 是舊格式(沒有 batch_contract)—— 不知道裡面是哪一把尺的資料,"
            f"不能續跑。請改個 --out 目錄重跑,或先把舊檔移走。")
    got = d["batch_contract"]
    if got != want:
        raise ContractMismatch(
            f"{store.name} 是 {got} 的資料,這次是 {want} —— ⛔ 兩把尺不可混用。"
            f"(切到完整模式請用另一個 --out 目錄;混在一起的鑑別力表會是假結論)")
    res = d["results"]
    if not isinstance(res, dict):
        raise ContractMismatch(f"{store.name} 的 results 不是 dict")
    return res


def _save_store(store: Path, results: dict):
    """原子寫入批次進度檔,並留一份上一版備份。

    ⛔ 直接覆寫的話,寫到一半斷電/被 Ctrl-C 就留下半截 JSON,
       下次 --skip-existing 讀它會 JSONDecodeError 而且**永遠修不好**。
       做法:寫暫存檔 → flush+fsync(確定真的落地)→ 舊檔轉備份 → os.replace 原子換上。
    """
    tmp = store.with_suffix(f".json.tmp{os.getpid()}")
    # ⭐ 進度檔要自報自己是哪一種批次(Codex R16-8):
    #    舊格式是 path→report 的裸 mapping,於是 full 模式 --skip-existing
    #    會直接沿用 local-metrics 的舊結果(使用者以為補跑了 Gemini,其實沒有),
    #    下游 曲評測清單.py 也會把兩把尺混在一張鑑別力表裡。
    envelope = {"batch_contract": FULL_CONTRACT if FULL_MODE else LOCAL_CONTRACT,
                "results": results}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(envelope, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    # ⛔ 只有在主檔**確定解析得開**時才拿它去覆蓋備份。
    #    否則會出現這種雙毀:主檔壞了 → 我們從 .bak 救回來 → 這裡又把壞主檔複製成 .bak
    #    → 唯一一份好備份被毀掉。備份本身也要原子更新,免得複製到一半被中斷。
    if store.exists():
        try:
            json.loads(store.read_text(encoding="utf-8"))     # 先確認舊主檔是好的
            bak = store.with_suffix(".json.bak")
            btmp = store.with_suffix(f".json.bak.tmp{os.getpid()}")
            shutil.copy2(store, btmp)
            os.replace(btmp, bak)
        except Exception:
            pass          # 舊主檔壞掉 → 保留現有備份不動(它才是好的那份)
    os.replace(tmp, store)


# ⭐ 兩種批次契約,輸出會明寫是哪一種(Codex R15)
FULL_MODE = os.environ.get("SONG_JURY_BATCH_GEMINI") == "1"
LOCAL_CONTRACT = "local-metrics-v1"
FULL_CONTRACT = "full-nine-pillars-v1"
# 「只由 Gemini 供分」的柱:略過 Gemini 時它一定缺,那是**預期**而非安裝壞掉。
# ⚠️ 其餘柱(和聲/結構編曲…)缺席一律是安裝問題,照樣拒收。
GEMINI_ONLY_PILLARS = {"律動"}


def run_one(song: Path, timeout=3600):
    """跑六關,回 (merged dict 或 None, 錯誤說明)。

    ⛔ 預設【略過 Gemini】—— 2026-07-20 教訓:批次是一首接一首零間隔跑,
       39 首 = 39 次連續呼叫 → 金鑰被 Google 擋掉,**約 28 天才恢復**。
       批次的用途是算本機確定性指標的鑑別力,不該把外部 API 綁進來。
       真的要跑完整九柱:設環境變數 SONG_JURY_BATCH_GEMINI=1(自負風險)。
    ⭐ 收件標準跟著模式走(見 FULL_MODE / LOCAL_CONTRACT):
       預設模式收「只缺 Gemini 柱」的結果並標上 local-metrics-v1 契約,
       ⛔ 那不是九柱總分、不可拿去排行或 PK。
    """
    out_json = song.with_name(song.stem + "_評審團.json")
    env = {**os.environ, "PYTHONUTF8": "1"}
    if os.environ.get("SONG_JURY_BATCH_GEMINI") != "1":
        env["SONG_JURY_SKIP_GEMINI"] = "1"
    # ⛔ 先刪掉上一輪的產物:留著它,這輪失敗時會被當成「成功」讀進來,
    #    批次表格拿到的是上次的舊分數,而且錯誤字串是空的 —— 完全看不出來。
    if out_json.exists():
        out_json.unlink()
    # ⛔ 逾時要殺整棵程序樹(run_tree):subprocess.run 只殺直屬的 評審團.py,
    #    它開的 Demucs/torch 孫程序會活著繼續吃 GPU、寫分軌快取(Codex R12)。
    r = run_tree([str(VENV_PY), str(BASE / "評審團.py"), str(song)],
                 cwd=BASE, env=env, timeout=timeout,
                 extra_creationflags=_NO_WINDOW.get("creationflags", 0))
    # ⛔ 也要看 returncode:程式中途炸掉但檔案已寫出時,光看檔案在不在會誤判成功。
    #    2 是「報告已完整發布但缺柱」的專用碼(Codex R11)—— 要繼續往下讀,
    #    交給下面的完整性檢查給出「缺柱:…」的誠實訊息,不是當成炸掉。
    # ⛔ 4 = 「報告已產出,但來源快照沒收乾淨」(Codex R24-P1-1 的新碼)——
    #    那是**隱私/清理**問題,不是評測失敗。丟掉一份跑了幾十分鐘的有效報告
    #    才是錯的(Codex R25-P1-1 實測:舊版連 JSON 都不讀就丟)。
    if r.returncode not in (0, 2, 4):
        return None, f"評審團 結束碼 {r.returncode}:" + (r.stderr or r.stdout or "")[-260:]
    # ⛔ 殘留路徑要**結構化地帶出去**(Codex R26-P1-2):只印在即時輸出的話,
    #    幾十首之後就被推走了,批次結束後沒人知道要刪哪裡;--skip-existing 下次
    #    跳過這首時更不會再提醒一次。→ 寫進結果 dict → 進 store → 進總結。
    # ⛔ 讀**機器記錄**,不切人話(Codex R30-P2-1):舊版切出來的是
    #    `C:/Temp/stems-left(裡面是一整份分軌,請手動刪掉)` —— 那不是一個路徑,
    #    而且種類整個不見了。⛔ 解析不到 ≠ 乾淨,rc=4 一律 fail-closed 記一筆。
    _dirty = []
    if r.returncode == 4:
        _dirty = parse_dirty(r.stdout or "") or [
            {"kind": "unknown", "path": "(路徑不明 —— 見上面輸出)"}]
        print("      ⛔ 暫存殘留(請手動刪掉):"
              + "；".join(f"[{x['kind']}] {x['path']}" for x in _dirty), flush=True)
    if not out_json.exists():
        return None, (r.stderr or r.stdout or "")[-300:]
    # ⛔ 讀不開的報告要收斂成一則錯誤,不可以讓整批炸掉(R25 新測試踩到):
    #    批次是「一首接一首」跑幾十首,其中一份半殘 JSON 不該讓前面幾十首的
    #    結果一起消失。
    try:
        d = json.loads(out_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"結果 JSON 讀不了({type(e).__name__}),拒收"
    if _dirty:
        # ⚠️ 這**不是**評測失敗:報告有效,只是有暫存沒清掉。分開記,不混進失敗數。
        d["_cleanup_dirty"] = _dirty
    # ⛔ 缺柱的結果不可以進批次表 —— 那是另一把尺,拿去算鑑別力會得到假結論。
    #    這裡必須 **fail-closed**:欄位不存在、型別不對,一律拒收。
    #    舊寫法是 `if _pt and not _pt.get("完整評測", True)` —— 完全沒有 pillar_totals 的
    #    舊格式或半殘 JSON 反而會被放行進表(fail-open),等於最該擋的情況擋不住。
    _pt = d.get("pillar_totals")
    if not isinstance(_pt, dict):
        return None, "結果缺少 pillar_totals(舊格式或產出不完整),拒收"
    _ok = _pt.get("完整評測")
    if not isinstance(_ok, bool):
        return None, "結果的『完整評測』欄位缺失或型別不對,拒收"
    lost = set(_pt.get("缺柱") or [])

    # ⛔ 兩種契約,絕不混用(Codex R15 抓到的死鎖:預設略過 Gemini → 必缺律動
    #    → 又用「完整評測」當收件標準 → **預設批次每一首都被拒收**,不可能產生
    #    任何可比較的資料,跟註解宣稱的用途自相矛盾)。
    #    · full(SONG_JURY_BATCH_GEMINI=1):要求真正的九柱完整,可拿來排行/PK。
    #    · local(預設):明講這是「本機確定性指標」——允許缺 Gemini 造成的柱,
    #      但其他柱(和聲/結構編曲…那些是安裝問題)缺一個都不收;
    #      而且輸出會標記 contract,⛔ 絕不可冒充九柱總分或拿去 PK。
    if FULL_MODE:
        if not _ok:
            return None, f"不完整評測,缺柱:{'、'.join(sorted(lost))}(補齊安裝後重跑)"
        # ⛔ 「完整評測: true」只是產出端的自述 —— full 批次是正式資料入口,
        #    必須過**獨立裁判**(Codex R16-7:stub 只寫 {"完整評測":true,"缺柱":[]}
        #    就被收進表,八柱 score、items/missing、合成自洽、契約全沒驗)。
        # ⭐ 正式批次用 declared(Codex R22-P2-1):s64 之類的來源產品**刻意**
        #    不發布解碼身分,不該因此連完整九柱的正式結果都不算數;
        #    但「受支援格式卻漏寫 PCM」仍然照擋(那是產出端迴歸)。
        why = validate(out_json, require_contract=True, require_identity="declared")
        if why:
            return None, f"獨立裁判拒收:{why}"
        d["_batch_contract"] = FULL_CONTRACT
        return d, ""
    extra = lost - GEMINI_ONLY_PILLARS
    if extra:
        return None, (f"缺了不該缺的柱:{'、'.join(sorted(extra))} —— 那是安裝問題,"
                      f"不是略過 Gemini 造成的(補齊安裝後重跑)")
    why = _validate_local(d)
    if why:
        return None, f"local-metrics 契約不合格:{why}"
    d["_batch_contract"] = LOCAL_CONTRACT
    return d, ""


def _validate_local(d: dict) -> str:
    """local-metrics-v1 的最低要求:契約版本 + 在場柱的分數要是真數字。
    ⛔ 不能只看「完整評測」與缺柱集合 —— 那組欄位全是產出端自述(Codex R16-7)。"""
    if not d.get("scoring_contract"):
        return "報告沒有 scoring_contract(這個 store 只收有版本證據的報告)"
    pt = d.get("pillar_totals") or {}
    柱分 = pt.get("柱分")
    if not isinstance(柱分, dict):
        return "柱分不是 dict"
    lost = set(pt.get("缺柱") or [])
    for name in REQUIRED_PILLARS:
        if name in lost:
            continue                      # 缺柱是預期的(只准 Gemini 造成的)
        det = 柱分.get(name)
        if not isinstance(det, dict):
            return f"柱分[{name}] 不是 dict"
        s = det.get("score")
        if isinstance(s, bool) or not isinstance(s, (int, float))                 or not math.isfinite(s) or not (0 <= s <= 100):
            return f"柱分[{name}].score 不是 0-100 的有限數字:{s!r}"
    return ""


def flatten(m: dict):
    """把七關的 merged JSON 壓成 {指標名: 數值},方便跨歌比較。
    只取數值型,且明確標出來源關卡,免得評審看不出誰是誰。"""
    f = {}
    p = (m.get("layer1_physical") or {})
    for k, v in (p.get("scores") or {}).items():
        if isinstance(v, (int, float)):
            f[f"物理.{k}"] = v
    for k, v in (p.get("mix_detail") or {}).items():
        if isinstance(v, dict) and isinstance(v.get("score"), (int, float)):
            f[f"物理.混音.{k}"] = v["score"]
    for k, v in (p.get("vocal_detail") or {}).items():
        if isinstance(v, dict) and isinstance(v.get("score"), (int, float)):
            f[f"演唱.{k}"] = v["score"]

    a = m.get("layer1_arrangement") or {}
    for grp in ("layers", "arrangement", "spectrum"):
        for k, v in (a.get(grp) or {}).items():
            if isinstance(v, (int, float)):
                f[f"編曲.{k}"] = v

    h = m.get("layer1_harmony") or {}
    for k, v in (h.get("metrics") or {}).items():
        if isinstance(v, dict) and isinstance(v.get("score"), (int, float)):
            f[f"和聲.{k}"] = v["score"]

    for k, v in (m.get("layer2_songeval_1to5") or {}).items():
        if isinstance(v, (int, float)):
            f[f"SongEval.{k}"] = v
    for k, v in (m.get("layer2_audiobox_1to10") or {}).items():
        if isinstance(v, (int, float)):
            f[f"Audiobox.{k}"] = v

    g = m.get("layer2_gemini") or {}
    for k, v in (g.get("dimensions") or {}).items():
        if isinstance(v, dict) and isinstance(v.get("score"), (int, float)):
            f[f"Gemini.{k}"] = v["score"]

    mf = m.get("layer2_flamingo") or {}
    for k, v in (mf.get("scored_items") or {}).items():
        if isinstance(v, dict) and isinstance(v.get("score"), (int, float)):
            f[f"Flamingo.{k}"] = v["score"]
    return f


def discrimination_report(rows, out_md: Path):
    """算每個指標的鑑別力,寫成給評審看的證據表。

    ⭐ 判準:一個在所有歌上都給幾乎相同分數的指標,不管理論多漂亮,
       實務上都無法區分作品 —— 給它高權重等於把總分交給雜訊。
       這裡只呈現事實(全距/標準差/是否常數),【不自行給權重建議】,
       權重由八家對決裁定。
    """
    keys = sorted({k for _, f in rows for k in f})
    stats = []
    for k in keys:
        vals = [f[k] for _, f in rows if k in f and f[k] is not None]
        if len(vals) < 2:
            continue
        lo, hi = min(vals), max(vals)
        sd = pstdev(vals)
        rng = hi - lo
        # 以該指標自身的量尺正規化(0-100 / 1-5 / 1-10 混在一起不能直接比)
        scale = 100.0 if hi > 10 else (10.0 if hi > 5 else 5.0)
        stats.append((k, len(vals), lo, hi, rng, sd, rng / scale))
    stats.sort(key=lambda x: x[6], reverse=True)

    L = [f"# 指標鑑別力報告(N = {len(rows)} 首)", "",
         "> 這份表回答一個問題:**哪些指標在這批歌之間真的分得出高低?**",
         "> 全距接近 0 的指標 = 對任何歌都給差不多的分數,無論理論多完備,",
         "> 給它高權重就是把總分交給雜訊。⚠️ 本表只陳述事實,不建議權重 —— 權重由八家對決裁定。", "",
         "| 指標 | 樣本 | 最低 | 最高 | 全距 | 標準差 | 正規化全距 |",
         "|---|---|---|---|---|---|---|"]
    for k, n, lo, hi, rng, sd, nr in stats:
        flag = "  ⛔常數" if nr < 0.02 else ("  ⚠️低" if nr < 0.10 else "")
        L.append(f"| {k}{flag} | {n} | {lo:.2f} | {hi:.2f} | {rng:.2f} | {sd:.2f} | {nr:.3f} |")

    dead = [s for s in stats if s[6] < 0.02]
    weak = [s for s in stats if 0.02 <= s[6] < 0.10]
    L += ["", f"## 摘要", "",
          f"- 共 {len(stats)} 個數值指標",
          f"- **無鑑別力(正規化全距 < 0.02):{len(dead)} 個** —— {', '.join(s[0] for s in dead[:12]) or '無'}",
          f"- 鑑別力偏低(< 0.10):{len(weak)} 個 —— {', '.join(s[0] for s in weak[:12]) or '無'}",
          "",
          "## 這批歌", "",
          "| 歌名 | 她的標籤 |", "|---|---|"]
    for name, _ in rows:
        L.append(f"| {name} |  |")
    out_md.write_text("\n".join(L), encoding="utf-8")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1])
    out_dir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else BASE / "_批次結果"
    skip = "--skip-existing" in sys.argv
    out_dir.mkdir(parents=True, exist_ok=True)

    songs = collect_songs(target)
    if not songs:
        sys.exit("沒有找到任何音檔")
    print(f"共 {len(songs)} 首,循序評測(GPU 不可平行)。預估 {len(songs) * 6} 分鐘\n")

    results, rows = {}, []
    store = out_dir / "批次結果.json"
    if skip and store.exists():
        # ⛔ 進度檔可能是上一輪寫到一半被中斷的殘檔 → 直接 json.loads 會 JSONDecodeError
        #    整個批次當場退出,而且**永遠修不好**(每次重跑都撞同一個壞檔)。
        #    壞掉就退回備份;備份也壞就從頭跑,不要讓一個殘檔卡死整批。
        try:
            results = _load_store(store)
        except ContractMismatch as e:
            sys.exit(f"⛔ {e}")
        except Exception as e:
            bak = store.with_suffix(".json.bak")
            print(f"⚠ 進度檔損壞({type(e).__name__}),嘗試用備份:{bak.name}", file=sys.stderr)
            try:
                results = _load_store(bak)
                print("  ↳ 已用備份續跑", file=sys.stderr)
            except Exception:
                results = {}
                print("  ↳ 備份也不可用,這批從頭跑", file=sys.stderr)

    for i, (song, label) in enumerate(songs, 1):
        # ⛔ 結果鍵不可以只用檔名:歌單可以引用不同資料夾,a/song.wav 與 b/song.wav
        #    會撞在一起 —— 第二首被當成「已有結果」直接跳過,整首歌靜靜漏評。
        #    用正規化後的絕對路徑當鍵,顯示名另外存。
        key = str(song.resolve()).replace("\\", "/")
        if key in results:
            print(f"[{i}/{len(songs)}] {song.name} — 已有結果,跳過")
        else:
            t0 = time.time()
            print(f"[{i}/{len(songs)}] {song.name} … ", end="", flush=True)
            try:
                m, err = run_one(song)
            except subprocess.TimeoutExpired:
                m, err = None, "逾時"
            if m is None:
                print(f"✗ {err[:70]}")
                results[key] = {"error": err, "label": label, "_name": song.name}
            else:
                m["_label"] = label
                m["_name"] = song.name          # 顯示名(鍵是路徑,不能拿來當標題)
                results[key] = m
                # ⛔ 有殘留的不可以印成普通的 ✓(那會讓人以為一切乾淨)
                _dty = m.get("_cleanup_dirty") or []
                print(f"{'⚠ DIRTY' if _dty else '✓'} {time.time()-t0:.0f}s")
            _save_store(store, results)

        m = results.get(key)
        if m and "error" not in m:
            rows.append((key, flatten(m)))

    if len(rows) >= 2:
        discrimination_report(rows, out_dir / "鑑別力報告.md")
        print(f"\n鑑別力報告:{out_dir / '鑑別力報告.md'}")
    else:
        print("\n⚠ 成功的歌少於 2 首,無法算鑑別力")
    # ⛔ 暫存殘留要在**結尾**再講一次,而且要有完整路徑(Codex R26-P1-2):
    #    即時那行早就被幾十首的輸出推走了,而那些目錄裡是一整份音訊。
    _dirty_rows = [(k, v.get("_cleanup_dirty") or [])
                   for k, v in results.items()
                   if isinstance(v, dict) and v.get("_cleanup_dirty")]
    if _dirty_rows:
        print(f"\n⛔ 有 {len(_dirty_rows)} 首留下了暫存(評測本身有效,請手動刪掉):")
        for k, paths in _dirty_rows:
            print(f"   · {Path(k).name}")
            for x in paths:
                # ⚠️ 舊 store 存的是純字串,新的是 {kind, path} —— 兩種都要印得出來
                print(f"     [{x['kind']}] {x['path']}" if isinstance(x, dict)
                      else f"     {x}")
    print(f"完整數據:{store}")


if __name__ == "__main__":
    main()
