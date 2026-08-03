# -*- coding: utf-8 -*-
"""app.py — song-jury 地端 Gradio 網頁版
三關全在本機網頁跑完:
  第一關 物理(song_scorer) + 第二關 美學(SongEval/Audiobox) + 情感弧線
  第三關 詞評 → 串本機 Ollama(免費、離線)直接秀結果。Ollama 為必裝依賴,沒裝就提示去裝(不再產生可複製 prompt)。
啟動: .venv\\Scripts\\python.exe app.py   (需 gradio;見 requirements-web.txt)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import gradio as gr

from 子程序 import run_tree
from 狀態目錄 import state_root
from 設定讀取 import ConfigError, positive_finite
import requests
from urllib.parse import urlparse

BASE = Path(__file__).parent.resolve()
_WIN = sys.platform == "win32"
ENV = {**os.environ, "PYTHONUTF8": "1"}


def _ollama_base():
    """把 OLLAMA_HOST(伺服器綁定值,常見 0.0.0.0:11434 或無 scheme)正規化成可連線的 URL。"""
    raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return "http://127.0.0.1:11434"
    if "://" not in raw:
        raw = "http://" + raw
    u = urlparse(raw)
    host = u.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):   # 綁定全介面 → 用回環位址連線
        host = "127.0.0.1"
    return f"http://{host}:{u.port or 11434}"


OLLAMA = _ollama_base()
_SESS = requests.Session()
_SESS.trust_env = False   # localhost 不走系統代理(否則有 HTTP_PROXY 會連不到)


def _venv_py(venv):
    return str(BASE / venv / ("Scripts/python.exe" if _WIN else "bin/python"))


# 網頁版子程序的整體上限。⛔ 沒有 timeout 的話:某階段卡住(解碼器、模型下載、
#    GPU 排隊)會讓那個 worker 與 GPU 被無限占用,而使用者關掉頁面也叫不停它。
#    預設 2 小時(整首歌走完九柱含首次模型下載的悲觀值),可用環境變數調。
# ⛔ 不可以直接 int(env)(Codex R19-5):打錯字會在載入階段變成裸 ValueError,
#    網頁版連起不起得來都看不出原因。設定值一律走共用解析器。
try:
    # ⛔ 不可以直接 int():0.5 秒是合法的正數,int() 會截成 0 —— 又變回
    #    「非正數逾時」那個 bug(Codex R20-P2-3)。至少留 1 秒。
    _JOB_TIMEOUT = max(1, round(positive_finite("SONG_JURY_WEB_TIMEOUT", 7200.0,
                                                lo=0.0, hi=86400.0)))
except ConfigError as _e:
    raise SystemExit(f"⛔ 設定值有問題:{_e}")


def _run(cmd, timeout=None):
    """跑子程序;逾時就把**整棵程序樹**殺掉(實作在 子程序.run_tree,三處共用)。

    ⛔ 歷史教訓都沉澱在 run_tree 裡:TimeoutExpired 沒有 .pid、POSIX 要開新
       session 否則 killpg 連 Gradio 自己一起殺 —— 評審團/批次/app 不再各寫各的
       (Codex R12:另外兩處就是因為自己寫,孫程序殺不乾淨)。"""
    lim = timeout or _JOB_TIMEOUT
    try:
        return run_tree(cmd, cwd=BASE, env=ENV, timeout=lim)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            cmd, 1, stdout=e.output or "",
            stderr=(e.stderr or "") + f"\n逾時:超過 {lim} 秒仍未完成,已中止整棵程序樹。"
                                      f"(可設環境變數 SONG_JURY_WEB_TIMEOUT 調整)")


# ── Ollama(本機免費 AI)────────────────────────────────────────
def ollama_models():
    try:
        r = _SESS.get(f"{OLLAMA}/api/tags", timeout=3)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def ollama_judge(model, prompt):
    # 關鍵:Ollama 預設 num_ctx 只 2048 會截斷評詞標準;思考型模型(qwen3/deepseek-r1)
    # 還要大量 token 思考+吐長答案 → 上下文若不夠,response 會是空的。給足空間 + 保證輸出量。
    num_ctx = min(49152, max(16384, len(prompt) + 12288))   # CJK 保守以 1 字≈1 token 估
    r = _SESS.post(f"{OLLAMA}/api/generate", timeout=1200, json={
        "model": model, "prompt": prompt, "stream": False,
        "options": {"num_ctx": num_ctx, "num_predict": 8192, "temperature": 0.4},
    })
    r.raise_for_status()
    d = r.json()
    txt = (d.get("response") or "").strip()
    if not txt and d.get("thinking"):        # 有些模型內容只放 thinking、response 留空
        txt = d["thinking"].strip()
    return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()  # 剝內嵌式思考段(deepseek 舊式)


# ── 成績單整理 ─────────────────────────────────────────────────
# ── 網頁版產物的受管暫存(Codex R26-P1-1)────────────────────────
# 🔴 舊版直接 `tempfile.mkdtemp()` 寫歌詞與弧線圖,沒有 finally、沒有 TTL、
#    沒有主人 —— 實測每評一首就在系統 TEMP 永久留下 `歌詞.txt` 與 `_情感弧線.png`。
#    ⛔ 那可能是**還沒公開的歌詞**,不可以靠「作業系統某天也許會清 TEMP」當生命週期。
# ⚠️ 回傳前不能刪(Gradio 要讀那張圖),所以契約是:放進**產品自己的**目錄,
#    每次新請求先回收超過 TTL 的舊產物 —— 可量測、可測試。
_WEB_TMP_TTL = max(60.0, positive_finite("SONG_JURY_WEB_TMP_TTL", 3600.0,
                                         lo=0.0, hi=86400.0))


def _web_tmp_root() -> Path:
    d = state_root() / "web-tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 一個請求最久允許「還在跑」多久 —— 超過就當成被中斷的孤兒,可以回收。
# ⚠️ 它必須 **>> 一次評測的時間**(首次還會下載模型),預設 6 小時。
_WEB_ABANDON = max(_WEB_TMP_TTL, positive_finite("SONG_JURY_WEB_ABANDON_AGE",
                                                 21600.0, lo=0.0, hi=604800.0))
_ACTIVE = ".active"          # 還在跑的請求會有這個標記


def _sweep_web_tmp(ttl: float = None, abandon: float = None) -> int:
    """回收「已完成而且過了保留期」的舊產物;回幾個目錄被清掉。

    🔴 Codex R27-P2-1:舊版只看 mtime —— 一個還在跑的請求(情感弧線/Ollama 詞評
       可能好幾分鐘)只要超過保留期,**下一個請求就會把它正在用的目錄刪掉**,
       使用者拿到一張不存在的圖。目錄的 mtime 也不會因為裡面的檔案被寫入而更新。
    ⭐ 所以改成租約:請求開始時放 `.active`,回傳前拿掉(= completed)。
       · completed 且超過保留期 → 回收;
       · 還 active 但超過**放棄期**(預設 6 小時)→ 當成被中斷的孤兒,才回收。
    ⛔ 刪不掉不可以靜靜吞掉(那又回到「以為清了、其實沒有」)—— 印出完整路徑。"""
    ttl = _WEB_TMP_TTL if ttl is None else ttl
    abandon = _WEB_ABANDON if abandon is None else abandon
    now = time.time()
    n = 0
    for d in _web_tmp_root().iterdir():
        try:
            if not d.is_dir():
                continue
            age = now - d.stat().st_mtime
            active = (d / _ACTIVE).exists()
            if age <= (abandon if active else ttl):
                continue
            shutil.rmtree(d, ignore_errors=True)
            if d.exists():
                print(f"⛔ 網頁暫存清不掉:{d}(請手動刪掉)", flush=True)
            else:
                n += 1
        except OSError as e:
            print(f"⛔ 網頁暫存回收失敗:{d}({type(e).__name__}: {e})", flush=True)
    return n


def _web_workdir() -> Path:
    """給這一次請求用的目錄(先回收舊的,再開新的;開出來的先標成 active)。"""
    _sweep_web_tmp()
    d = Path(tempfile.mkdtemp(prefix="req-", dir=_web_tmp_root()))
    (d / _ACTIVE).write_text("1", encoding="utf-8")
    return d


def _web_done(d) -> None:
    """請求結束:拿掉 active 標記 → 之後就照保留期回收。"""
    try:
        if d is not None:
            (Path(d) / _ACTIVE).unlink(missing_ok=True)
    except OSError:
        pass


# ⭐ 啟動時也掃一次(Codex R27-P2-2):只在「下一個請求」才回收的話,
#    最後一位使用者離開之後,那份歌詞可以留到下次有人來為止 —— 可能是好幾個月。
#    ⚠️ 誠實邊界:這是**機會性**回收(啟動 + 每次請求),不是背景排程的時間保證。
try:
    _sweep_web_tmp()
except Exception:            # noqa: BLE001 —— 回收失敗絕不可以擋住服務啟動
    pass


def _score_table(merged):
    # ⛔ 巢狀容器不可假定型別:引擎異常時 scores 可能是 []、mix_detail 可能是 list
    #    (Codex R10 探針:scores: [] → TypeError)。昂貴評測已寫進 JSON,
    #    顯示層不可以再把它炸掉 —— 全部先投影成 dict、數字驗過才格式化。
    import math as _math
    def _d(v):
        return v if isinstance(v, dict) else {}
    def _num(v):
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and _math.isfinite(v)
        return v if ok else None
    p = _d(merged.get("layer1_physical"))
    se = {k: _num(v) for k, v in _d(merged.get("layer2_songeval_1to5")).items()
          if _num(v) is not None}
    ab = {k: _num(v) for k, v in _d(merged.get("layer2_audiobox_1to10")).items()
          if _num(v) is not None}
    sc = _d(p.get("scores"))
    rows = [["🎚 物理技術(總分/100)", f'{sc.get("total", "—")}({sc.get("grade", "—")})']]
    for k, v in _d(p.get("mix_detail")).items():
        v = _d(v)
        rows.append([f"　・{k}", f'{v.get("score","")}｜{v.get("comment","")}'])
    se_lab = {"Coherence": "整體連貫性", "Musicality": "整體音樂性", "Memorability": "記憶點",
              "Clarity": "結構清晰度", "Naturalness": "人聲自然度"}
    rows.append(["🎓 SongEval 平均/5", f'{sum(se.values())/len(se):.2f}' if se else "—"])
    for k, v in se.items():
        rows.append([f"　・{se_lab.get(k,k)}", f"{v:.2f}"])
    ab_lab = {"PQ": "製作品質", "CE": "內容感染力", "CU": "內容實用性", "PC": "製作複雜度"}
    rows.append(["🏭 Audiobox(1–10)", ""])
    for k in ("PQ", "CE", "CU", "PC"):
        if k in ab:
            rows.append([f"　・{ab_lab[k]}", f"{ab[k]:.2f}"])
    return rows


def _pick_rubric(lyrics):
    """依歌詞語言挑那把尺。判定順序固定 韓 → 日 → 中 → 英
    (日文含漢字,所以必須排在中文前面,否則日文歌會被中文尺攔截)。
    回 (尺檔路徑, 語言名, 維度數)。⛔ 四把尺絕不混讀 —— 它們的軸互斥。"""
    t = lyrics or ""
    if re.search(r"[가-힯]", t):                       # 韓文字母
        return BASE / "rubrics/KO_lyric_rubric_v4.md", "韓文", 7
    if re.search(r"[぀-ゟ゠-ヿ]", t):           # 平假名/片假名
        return BASE / "rubrics/JA_lyric_rubric_v3.md", "日文", 6
    if re.search(r"[一-鿿]", t):                        # 漢字
        return BASE / "rubrics/ZH_lyric_rubric_v5.md", "中文", 7
    return BASE / "rubrics/EN_lyric_rubric_v2.md", "英文", 6


def _lyric_prompt(lyrics):
    """⛔ 這裡一定要把「對應語言的那把尺」也餵進去。
    評詞標準.md 規範的是報告格式與情感框架,**維度定義在 rubrics/ 那四把尺裡**。
    只餵評詞標準等於沒有評分依據,模型會自己編維度(舊版就是這樣,還寫死「七維度」——
    英文/日文尺只有 6 維,一律要七維會逼模型硬湊一個出來)。"""
    std = (BASE / "評詞標準.md").read_text(encoding="utf-8")
    rp, lang, ndim = _pick_rubric(lyrics)
    try:
        rubric = rp.read_text(encoding="utf-8")
    except Exception:
        rubric, ndim = "(找不到對應語言的尺,請確認 rubrics/ 是否完整)", "N"
    return (f"你是專業歌曲評審。這首歌詞判定為【{lang}】,請【嚴格依照】下面那把{lang}尺評詞。\n"
            f"給 {ndim} 個維度的雙分數(作品分 Craft / 爆款分 Reach,每分引原句)、"
            "情感三支柱、句級修法。⛔ 兩個分數禁止平均。只評詞,不要評曲。\n\n"
            f"===== {lang}尺({rp.name})=====\n{rubric}\n\n"
            f"===== 報告格式與情感框架(評詞標準)=====\n{std}\n\n"
            f"===== 待評歌詞 =====\n{lyrics}\n")


def _jpath_from_stdout(stdout):
    # 錨定「完整報告」標籤 + 緊接的冒號,取後面全部路徑;路徑本身含冒號也不切(歌名如「鳳儀亭:一柄畫戟」)。
    for line in reversed(stdout.splitlines()):
        m = re.match(r"\s*完整報告[：:]\s*(.+)", line)
        if m:
            return Path(m.group(1).strip())
    return None


# ── 主流程 ─────────────────────────────────────────────────────
def evaluate(link, audio_file, lyrics, model, progress=gr.Progress()):
    src = (link or "").strip() or (audio_file or None)
    if not src:
        return [], None, "", "⚠️ 請給 SUNO/YouTube 連結,或上傳音檔。"

    progress(0.1, desc="曲側八柱評分中(第一次會下載模型)…")
    # ⛔ 一定要用 .venv 跑 評審團.py:它自己的相依(含 yt-dlp)裝在 .venv,
    #    而且它會用 sys.executable 去呼叫 yt-dlp。用 .venv-ml 跑會找不到已裝好的 yt-dlp。
    r = _run([_venv_py(".venv"), str(BASE / "評審團.py"), str(src)])
    # ⛔ 2 = 報告已完整發布但缺柱(Codex R11 專用碼):照樣往下讀,
    #    下面的完整性區塊會掛「⛔ 這不是一份完整評測」;丟掉昂貴產物才是錯的。
    # ⛔ 4 = 報告已產出但來源快照沒收乾淨(Codex R24-P1-1):照樣往下讀,
    #    但要把殘留路徑顯示出來 —— 那是一整份音訊留在伺服器的 TEMP 裡。
    if r.returncode not in (0, 2, 4):
        return [], None, "", f"❌ 音訊評分失敗:\n```\n{(r.stderr or r.stdout)[-1000:]}\n```"
    _snap_warn = ""
    if r.returncode == 4:
        _lines = [ln for ln in (r.stdout or "").splitlines() if "快照沒清乾淨" in ln]
        _snap_warn = ("\n\n⛔ **來源快照沒清乾淨**(評測本身有效):"
                      f"{_lines[-1] if _lines else '見伺服器輸出'} —— 請手動刪掉。")
    jpath = _jpath_from_stdout(r.stdout)
    if not jpath or not jpath.exists():
        return [], None, "", f"❌ 找不到結果 JSON。\n```\n{r.stdout[-600:]}\n```"
    # ⛔ 讀不開要收斂成產品訊息(Codex R26-P2-1):半份 JSON / 編碼錯誤 /
    #    讀取競速都會讓整個 Gradio request 以例外結束,使用者只看到紅框。
    try:
        _data = json.loads(jpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], None, "", (f"❌ 報告讀不了({type(e).__name__})——"
                              f"評測可能被中斷或檔案損壞。{_snap_warn}")
    table = _score_table(_data)

    # ⛔ 完整性警語一定要在網頁版也顯示出來 —— CLI 印得很大聲,網頁版原本整段吃掉,
    #    使用者只會看到「✅ 完成」跟一個看似正常的分數。缺柱=無效評測,不可以藏。
    # ⛔ 這裡要 **fail-closed**:欄位不存在或型別不對,一律當成不完整。
    #    舊寫法 `_pt.get("完整評測", True)` 在 pillar_totals 整個不見時會回 True(fail-open)
    #    → 最該示警的情況反而顯示「✅ 完成」。
    _pt = _data.get("pillar_totals")
    _ok = _pt.get("完整評測") if isinstance(_pt, dict) else None
    incomplete_md = ""
    if _ok is not True:
        if _ok is False:
            _lost = _pt.get("缺柱") or []
            incomplete_md = (f"\n\n---\n### ⛔ 這不是一份完整評測\n"
                             f"缺了 **{len(_lost)} 根柱**、合計 "
                             f"**{_pt.get('缺柱權重合計')}%** 權重(缺:{'、'.join(_lost)})。\n\n"
                             f"分數是用剩下的柱重新歸一化算的,**不可與完整評測互比、不可拿去排行、"
                             f"不可當作品的評測結果**。請把安裝補齊後重評。\n")
        else:
            incomplete_md = ("\n\n---\n### ⛔ 無法確認這份評測是否完整\n"
                             "結果裡沒有 `pillar_totals.完整評測` 欄位(可能是舊格式或產出不完整)。"
                             "**這個分數不可拿去比較或排行。**\n")

    # 未跑到的項目另外攤開 —— ⚠️ 這是**細項提醒**,不是「不完整」。
    #    舊寫法把它接在 incomplete_md 後面,而下面又用 incomplete_md 是否非空來決定要不要
    #    掛「⛔ 評測不完整」→ 九柱齊全但有任何一則 note 就被誤報成不完整。兩者必須分開。
    _notes = _data.get("stage_notes") or []
    notes_md = ""
    if _notes:
        notes_md = ("\n\n<details><summary>本次未完全跑到的項目(細項提醒)</summary>\n\n"
                    + "\n".join(f"- {n}" for n in _notes) + "\n\n</details>\n")

    arc_img = None
    # ⭐ SUNO 連結會自動抓到歌詞(評審團.py 存在 fetched_lyrics)—— 原本只看文字框,
    #    導致「只貼連結」時明明抓到詞卻說「沒給歌詞」,白白跳過詞評與情感弧線。
    if not (lyrics or "").strip():
        _fl = _data.get("fetched_lyrics")          # 評審團.py 寫在 JSON 頂層
        if _fl and str(_fl).strip():
            lyrics = str(_fl)
    has_lyrics = bool((lyrics or "").strip())
    if has_lyrics:
        progress(0.6, desc="情感弧線分析中…")
        _reqdir = _web_workdir()
        tmp = _reqdir / "歌詞.txt"
        tmp.write_text(lyrics, encoding="utf-8")
        _run([_venv_py(".venv"), str(BASE / "情感弧線.py"), str(tmp)])
        cand = tmp.with_name(tmp.stem + "_情感弧線.png")
        arc_img = str(cand) if cand.exists() else None

    lyric_eval = ""
    if not has_lyrics:
        note = "✅ 音訊完成。沒給歌詞→跳過情感弧線與詞評。"
    elif not model:
        note = ("✅ 音訊+情感完成。**第三關詞評需要本機 Ollama**——請裝 "
                "[Ollama](https://ollama.com)、`ollama pull qwen3`,再重開本頁選模型評分。")
    else:
        progress(0.75, desc=f"第三關詞評中(本機 {model},27B 需 1–3 分鐘)…")
        try:
            lyric_eval = ollama_judge(model, _lyric_prompt(lyrics))
            note = (f"✅ 三關完成。第三關詞評由本機 **{model}** 產出(免費/離線)。"
                    if lyric_eval else
                    f"⚠️ **{model}** 詞評回傳空白——換個模型再試(建議 qwen3 系列)。")
        except Exception as e:
            note = f"⚠️ 本機詞評失敗({e})。確認 Ollama 有在跑、模型已 `ollama pull`。"
    if incomplete_md:
        note = "⛔ **評測不完整**(詳見下方)——" + note.lstrip("✅⚠️ ") + incomplete_md
    note += notes_md          # 細項提醒獨立附加,不影響上面的完整性判定
    note += _snap_warn        # ⛔ 快照殘留是伺服器上的一整份音訊,不可以只留在 log
    # ⭐ 產物已經交給 Gradio 了 → 解除租約,之後就照保留期回收(R27-P2-1)
    _web_done(_reqdir if has_lyrics else None)
    return table, arc_img, lyric_eval, note


# ── UI ─────────────────────────────────────────────────────────
_models = ollama_models()
_default = next((m for m in _models if "qwen" in m.lower()), _models[0] if _models else None)

with gr.Blocks(title="song-jury 歌曲三關評審團") as demo:
    gr.Markdown("# 🎼 song-jury — 歌曲三關評審團\n"
                "物理量測 + 音樂家美學模型 + 情感弧線,全在本機。第三關詞評串本機 Ollama(免費/離線)。")
    if not _models:
        gr.Markdown("> ⚠️ **沒偵測到 Ollama** —— 第三關詞評需要它。請裝 [Ollama](https://ollama.com) 後 "
                    "`ollama pull qwen3`,再重開本頁。(前兩關物理+美學+情感弧線不裝也能跑。)")
    with gr.Row():
        with gr.Column():
            link = gr.Textbox(label="SUNO / YouTube 連結", placeholder="https://suno.com/song/… 或 youtube.com/…")
            audio = gr.Audio(label="或上傳音檔", type="filepath")
            lyrics = gr.Textbox(label="歌詞(SUNO 連結多半自動抓;YT/本機檔請貼)", lines=8,
                                placeholder="段落可用【】標記")
            model = gr.Dropdown(label="第三關詞評用的本機模型(Ollama;建議 qwen3)", choices=_models, value=_default)
            btn = gr.Button("開始評分", variant="primary")
        with gr.Column():
            note = gr.Markdown()
            table = gr.Dataframe(headers=["項目", "分數 / 說明"], label="成績單(物理+美學)", wrap=True)
            arc = gr.Image(label="情感弧線圖", type="filepath")
            lyric_eval = gr.Markdown(label="第三關 詞評結果")
    btn.click(evaluate, [link, audio, lyrics, model], [table, arc, lyric_eval, note])

if __name__ == "__main__":
    demo.launch(inbrowser=True)
