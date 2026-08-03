# -*- coding: utf-8 -*-
"""四把語言尺的選尺規則。

⛔ 判定順序固定 韓 → 日 → 中 → 英。日文含漢字,必須排在中文前面,
   否則日文歌會被中文尺攔截,套上倒字規則(日文根本沒有聲調)。
⛔ 四把尺絕不混讀 —— 韓文尺明文廢掉日文的音高重音與中文的倒字。

🔴 真實缺陷:網頁版 app.py 的詞評從不載入任何一把尺,還寫死「給七維度分數」——
   而英文尺與日文尺只有 6 維,一律要七維會逼模型硬湊一個出來。
"""
import sys
import types
import pytest
from conftest import load, REPO


class _Any:
    """萬用替身:怎麼呼叫、取屬性、當 context manager 都不會爆。
    app.py 在模組層級就用 `with gr.Blocks() as demo:` 建整個 UI,
    所以 import 它時 gradio 的行為必須被完整頂替。"""
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, _): return _Any()
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture(scope="module")
def A():
    """app.py 頂層 import gradio;CI 不裝它(它很重且與本測試無關),用假模組頂替。
    我們只測選尺與提示詞組裝,不測 UI。"""
    if "gradio" not in sys.modules:
        gr = types.ModuleType("gradio")
        gr.__getattr__ = lambda name: _Any        # 任何 gr.X 都給萬用替身
        sys.modules["gradio"] = gr
    return load("app")


@pytest.mark.parametrize("歌詞,預期語言,預期檔,預期維度", [
    ("夜風吹過窗縫 帶來輕輕的呢喃", "中文", "ZH_lyric_rubric_v5.md", 7),
    ("君の名前を呼んだ 夜の街で", "日文", "JA_lyric_rubric_v3.md", 6),
    ("그대의 이름을 부르며 걸었어", "韓文", "KO_lyric_rubric_v4.md", 7),
    ("I called your name in the rain", "英文", "EN_lyric_rubric_v2.md", 6),
    # ⚠️ 關鍵案例:日文含大量漢字,不可以被中文尺攔截
    ("東京の夜、君と歩いた季節", "日文", "JA_lyric_rubric_v3.md", 6),
    # ⚠️ 韓文夾漢字/英文,韓文優先
    ("사랑 in the 夜", "韓文", "KO_lyric_rubric_v4.md", 7),
])
def test_選對尺(A, 歌詞, 預期語言, 預期檔, 預期維度):
    path, lang, ndim = A._pick_rubric(歌詞)
    assert lang == 預期語言
    assert path.name == 預期檔
    assert ndim == 預期維度


def test_成績表對畸形巢狀容器不可炸(A):
    """🔴 Codex R10:引擎異常時 scores 可能是 []、mix_detail 可能是 list ——
    _score_table 直接 p["scores"]["total"] 會 TypeError,昂貴評測已寫進 JSON,
    App 卻在顯示層把它炸掉。全部要先投影成 dict、數字驗過才格式化。"""
    rows = A._score_table({
        "layer1_physical": {"scores": [], "mix_detail": ["bad"]},
        "layer2_songeval_1to5": [],
        "layer2_audiobox_1to10": {"PQ": "N/A", "CE": float("nan"), "CU": 8.4},
    })
    flat = " ".join(str(c) for r in rows for c in r)
    assert "8.40" in flat, "合法數字還是要顯示出來"
    assert "nan" not in flat.lower(), "NaN 不可以被格式化進表"
    # 整份 merged 都是垃圾也不可以炸
    rows2 = A._score_table({"layer1_physical": "oops"})
    assert rows2, "至少要回一張(標示缺席的)表,不是例外"


def test_四把尺都真的存在():
    for f in ("ZH_lyric_rubric_v5.md", "EN_lyric_rubric_v2.md",
              "JA_lyric_rubric_v3.md", "KO_lyric_rubric_v4.md"):
        assert (REPO / "rubrics" / f).exists(), f"少了 {f},詞柱會評不出來"


def test_詞評提示詞有把尺餵進去(A):
    """🔴 舊版只餵評詞標準(那是報告格式),維度定義在 rubrics/ 裡 ——
    沒餵尺等於沒有評分依據,模型會自己編維度。"""
    p = A._lyric_prompt("夜風吹過窗縫 帶來輕輕的呢喃")
    assert "ZH_lyric_rubric_v5.md" in p
    assert "評詞標準" in p


def _指令段(prompt: str) -> str:
    """只取「我們自己下的指令」那一段 —— 後面 ===== 之後是嵌入的尺與標準全文,
    那些文件本身會提到歷史沿革(例如舊的七維度加權表),不該被當成指令來檢查。"""
    return prompt.split("=====", 1)[0]


@pytest.mark.parametrize("歌詞,應含,不應含", [
    ("I called your name in the rain", "6 個維度", "七維度"),   # 英文尺 6 維
    ("君の名前を呼んだ", "6 個維度", "七維度"),                  # 日文尺 6 維
    ("夜風吹過窗縫", "7 個維度", None),                          # 中文尺 7 維
])
def test_提示詞的維度數要跟那把尺一致(A, 歌詞, 應含, 不應含):
    """🔴 舊版寫死「給七維度分數」;英文尺與日文尺只有 6 維,會逼模型硬湊一個出來。"""
    seg = _指令段(A._lyric_prompt(歌詞))
    assert 應含 in seg
    if 不應含:
        assert 不應含 not in seg


def test_提示詞明文禁止平均雙分(A):
    seg = _指令段(A._lyric_prompt("夜風吹過窗縫"))
    assert "禁止平均" in seg


def test_提示詞明講只評詞不評曲(A):
    seg = _指令段(A._lyric_prompt("夜風吹過窗縫"))
    assert "只評詞" in seg


def test_網頁版要處理快照殘留的退出碼(A, tmp_path, monkeypatch):
    """🔴 Codex R25-P1-1 實測:4 =「報告已產出,但來源快照沒收乾淨」——
    舊版把它當成 `❌ 音訊評分失敗`,表格 0 列、報告完全不讀。
    ⛔ 那是把一份跑了幾十分鐘的**有效**評測丟掉,而真正的問題(TEMP 裡留了
       一整份音訊)反而沒有講清楚。"""
    import json
    import types as _t
    P8 = ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")
    pt = {"完整評測": True, "缺柱": [], "缺柱權重合計": 0.0, "曲側合成": 70.0,
          "柱分": {k: {"score": 70.0, "items": {"x": 70.0}, "missing": []} for k in P8},
          "曲側含柱": list(P8)}
    rep = tmp_path / "甲_評審團.json"
    rep.write_text(json.dumps({"scoring_contract": "2026-07-25-v1", "pillar_totals": pt,
                               "scores": {"total": 70.0}}, ensure_ascii=False),
                   encoding="utf-8")
    left = "C:/Temp/song-jury-src-xxxx"
    monkeypatch.setattr(A, "_run", lambda *a, **k: _t.SimpleNamespace(
        returncode=4,
        stdout=f"完整報告:{rep}\n⛔ 來源快照沒清乾淨:{left}\n", stderr=""))
    song = tmp_path / "甲.wav"
    song.write_bytes(b"RIFF")
    table, _img, _lyr, note = A.evaluate("", str(song), "", None)
    assert table, f"🔴 rc=4 的有效報告沒被讀進來(表格是空的):{note}"
    assert "快照" in note and left in note, f"🔴 沒把殘留路徑講給使用者:{note}"


def test_網頁版的歌詞與圖要放進受管暫存並會被回收(A, tmp_path, monkeypatch):
    """🔴 Codex R26-P1-1:網頁版直接 `tempfile.mkdtemp()` 寫歌詞與弧線圖,
    沒有 finally、沒有 TTL、沒有主人 —— 實測每評一首就在系統 TEMP 永久留下
    `歌詞.txt` 與 `_情感弧線.png`。⛔ 那可能是**還沒公開的歌詞**。"""
    import json
    import time as _t
    import types as _ty
    from pathlib import Path as _P
    root = tmp_path / "state"
    monkeypatch.setattr(A, "state_root", lambda: root)
    bare = tmp_path / "bare-temp"
    bare.mkdir()
    monkeypatch.setattr(A.tempfile, "tempdir", str(bare))
    P8 = ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")
    pt = {"完整評測": True, "缺柱": [], "缺柱權重合計": 0.0, "曲側合成": 70.0,
          "柱分": {k: {"score": 70.0, "items": {"x": 70.0}, "missing": []} for k in P8},
          "曲側含柱": list(P8)}
    rep = tmp_path / "甲_評審團.json"
    rep.write_text(json.dumps({"scoring_contract": "2026-07-25-v1", "pillar_totals": pt,
                               "scores": {"total": 70.0}}, ensure_ascii=False),
                   encoding="utf-8")
    monkeypatch.setattr(A, "_run", lambda *a, **k: _ty.SimpleNamespace(
        returncode=0, stdout=f"完整報告:{rep}\n", stderr=""))
    A.evaluate("", str(tmp_path / "甲.wav"), "夜風吹過窗縫 帶來輕輕的呢喃", None)
    made = sorted(x.name for x in (root / "web-tmp").iterdir())
    assert made, "🔴 產物沒有放進產品自己的受管目錄"
    assert sorted(x.name for x in bare.iterdir()) == [], \
        f"🔴 還是往系統 TEMP 亂丟:{sorted(x.name for x in bare.iterdir())}"
    # ⭐ 回收契約:超過 TTL 的舊產物,下一個請求會清掉
    old = root / "web-tmp" / "req-old"
    old.mkdir()
    (old / "歌詞.txt").write_text("舊的", encoding="utf-8")
    import os as _os
    _os.utime(old, (_t.time() - 99999, _t.time() - 99999))
    assert A._sweep_web_tmp(ttl=60) >= 1, "🔴 過期的產物沒有被回收"
    assert not old.exists(), "🔴 回收沒有真的刪掉"


@pytest.mark.parametrize("rc", [0, 4])
def test_網頁版遇到損壞的報告不可以炸掉request(A, tmp_path, monkeypatch, rc):
    """🔴 Codex R26-P2-1:`json.loads(jpath.read_text())` 沒有保護 —— 半份 JSON、
    編碼錯誤、讀取競速都會讓整個 Gradio request 以例外結束,使用者只看到紅框。"""
    import types as _ty
    bad = tmp_path / "壞_評審團.json"
    bad.write_text("這不是 JSON", encoding="utf-8")
    monkeypatch.setattr(A, "_run", lambda *a, **k: _ty.SimpleNamespace(
        returncode=rc, stdout=f"完整報告:{bad}\n⛔ 來源快照沒清乾淨:X\n", stderr=""))
    table, _img, _lyr, note = A.evaluate("", str(tmp_path / "甲.wav"), "", None)
    assert table == [] and "讀不了" in note, f"🔴 沒有收斂成產品訊息:{note!r}"
    if rc == 4:
        assert "快照" in note, "🔴 報告壞掉時把快照殘留的警告也弄丟了"
