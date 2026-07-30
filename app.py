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
import subprocess
import sys
import tempfile
from pathlib import Path

import gradio as gr
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


def _run(cmd):
    return subprocess.run(cmd, cwd=str(BASE), env=ENV, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


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
def _score_table(merged):
    p = merged["layer1_physical"]
    se = merged["layer2_songeval_1to5"]
    ab = merged["layer2_audiobox_1to10"]
    rows = [["🎚 物理技術(總分/100)", f'{p["scores"]["total"]}({p["scores"]["grade"]})']]
    for k, v in p.get("mix_detail", {}).items():
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


def _lyric_prompt(lyrics):
    std = (BASE / "評詞標準.md").read_text(encoding="utf-8")
    return ("你是專業歌曲評審。請【嚴格依照】以下《評詞標準》評這首歌的「詞」(第三關)。"
            "給七維度雙分數(作品分/爆款分,每分引原句)、情感三支柱、句級修法。只評詞。\n\n"
            f"===== 評詞標準 =====\n{std}\n\n===== 待評歌詞 =====\n{lyrics}\n")


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

    progress(0.1, desc="物理 + SongEval + Audiobox 評分中(第一次會下載模型)…")
    r = _run([_venv_py(".venv-ml"), str(BASE / "評審團.py"), str(src)])
    if r.returncode != 0:
        return [], None, "", f"❌ 音訊評分失敗:\n```\n{(r.stderr or r.stdout)[-1000:]}\n```"
    jpath = _jpath_from_stdout(r.stdout)
    if not jpath or not jpath.exists():
        return [], None, "", f"❌ 找不到結果 JSON。\n```\n{r.stdout[-600:]}\n```"
    table = _score_table(json.loads(jpath.read_text(encoding="utf-8")))

    arc_img = None
    has_lyrics = bool((lyrics or "").strip())
    if has_lyrics:
        progress(0.6, desc="情感弧線分析中…")
        tmp = Path(tempfile.mkdtemp()) / "歌詞.txt"
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
