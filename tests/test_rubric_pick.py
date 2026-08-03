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
from pathlib import Path

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


def _cleanup_line(items):
    """產生一行**真的**清理記錄(直接用產品的 emit_dirty)。

    ⛔ 不要在測試裡手抄那個前綴/JSON:stub 與線上格式一旦漂開,測試就會
       對著一個現實中不存在的輸出過關(R30 收尾時真的踩到:兩條舊測試的 stub
       還在吐 R30 之前的人話,而產品早就改讀機器記錄了)。"""
    import io as _io
    T = load("暫存清理")
    buf = _io.StringIO()
    T.emit_dirty(items, stream=buf)
    return buf.getvalue()


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
        stdout=(f"完整報告:{rep}\n⛔ 暫存沒清乾淨[source_snapshot]:{left}\n"
                + _cleanup_line([("source_snapshot", left)])),
        stderr=""))
    song = tmp_path / "甲.wav"
    song.write_bytes(b"RIFF")
    table, _img, _lyr, note = A.evaluate("", str(song), "", None)
    assert table, f"🔴 rc=4 的有效報告沒被讀進來(表格是空的):{note}"
    # ⭐ R30:種類與**純路徑**都要出現(舊版只查得到「快照」兩個字)
    assert f"[source_snapshot] {left}" in note, f"🔴 沒把殘留講清楚給使用者:{note}"


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
        returncode=rc,
        stdout=(f"完整報告:{bad}\n⛔ 暫存沒清乾淨[source_snapshot]:X\n"
                + _cleanup_line([("source_snapshot", "X")])), stderr=""))
    table, _img, _lyr, note = A.evaluate("", str(tmp_path / "甲.wav"), "", None)
    assert table == [] and "讀不了" in note, f"🔴 沒有收斂成產品訊息:{note!r}"
    if rc == 4:
        assert "[source_snapshot] X" in note, \
            "🔴 報告壞掉時把殘留的警告也弄丟了(那份音訊還在伺服器上)"


def test_網頁回收不可以刪掉還在跑的請求(A, tmp_path, monkeypatch):
    """🔴 Codex R27-P2-1:舊版只看 mtime —— 一個還在跑的請求(情感弧線/Ollama 詞評
    可能好幾分鐘)只要超過保留期,**下一個請求就會把它正在用的目錄刪掉**,
    使用者拿到一張不存在的圖。⛔ 要有租約:active 的目錄不可以被當成過期產物。"""
    import os as _os
    import time as _t
    root = tmp_path / "state"
    monkeypatch.setattr(A, "state_root", lambda: root)
    active = A._web_workdir()                    # 模擬「正在跑」
    (active / "歌詞.txt").write_text("還在用", encoding="utf-8")
    old = _t.time() - 3600
    _os.utime(active, (old, old))
    assert A._sweep_web_tmp(ttl=60) == 0, "🔴 把還在跑的請求刪掉了"
    assert active.exists() and (active / "歌詞.txt").exists()
    # 完成之後(解除租約)才可以被回收
    A._web_done(active)
    _os.utime(active, (old, old))
    assert A._sweep_web_tmp(ttl=60) == 1, "🔴 完成且過期了卻沒回收"
    assert not active.exists()


def test_被中斷的請求最後還是要回收(A, tmp_path, monkeypatch):
    """⚠️ 租約不可以變成「永遠不刪」的免死金牌:程序被砍時 OS 會放掉鎖,
    那份產物就該照保留期回收(否則受管目錄會無限長大)。"""
    import os as _os
    import time as _t
    root = tmp_path / "state"
    monkeypatch.setattr(A, "state_root", lambda: root)
    d = A._web_workdir()
    (d / "歌詞.txt").write_text("被中斷的", encoding="utf-8")
    # 模擬「程序死了」:放掉租約但**留著 marker**(崩潰時就是這個樣子)
    A.release(A._HELD.pop(str(d), None))
    very_old = _t.time() - 99999
    _os.utime(d, (very_old, very_old))
    assert A._sweep_web_tmp(ttl=60) == 1, "🔴 孤兒目錄永遠不會被回收"
    assert not d.exists()


def test_還活著的請求就算看起來很舊也不可以刪(A, tmp_path, monkeypatch):
    """🔴 Codex R28-P2-1:第一版的 `.active` 只是「檔案在不在」的哨兵,判死仍然
    靠牆鐘 —— 時鐘往前跳、機器休眠、或工作本來就比放棄期長
    (SONG_JURY_WEB_TIMEOUT 允許到 24 小時)都會把**還活著**的工作誤判成孤兒,
    使用者拿到一張不存在的圖。⛔ 判準只能是「鎖拿不拿得到」。"""
    import os as _os
    import time as _t
    root = tmp_path / "state"
    monkeypatch.setattr(A, "state_root", lambda: root)
    d = A._web_workdir()                       # 這個請求「還在跑」(持有租約)
    (d / "歌詞.txt").write_text("還在跑", encoding="utf-8")
    very_old = _t.time() - 999999              # 時鐘往前跳 / 睡了很久 / 工作很長
    _os.utime(d, (very_old, very_old))
    # ⚠️ 要走「**別的**程序持有租約」那條路:sweep 對自己簿子上的目錄會直接跳過,
    #    那樣不管判準是鎖還是時間都不會刪 —— 測試就驗不到真正的防線
    #    (變異驗證抓到我這條是裝飾品)。把它從簿子拿掉、但**不放鎖**。
    holder = A._HELD.pop(str(d))
    try:
        assert A._sweep_web_tmp(ttl=60) == 0, "🔴 把還活著的請求刪掉了"
        assert (d / "歌詞.txt").exists()
        # 而且別的程序看到的也是「有人在用」(不是只有自己這個程序知道)
        assert A.is_busy(d / A._ACTIVE), "🔴 租約不是真的 OS 鎖,別的程序看不到"
    finally:
        A.release(holder)
    assert not A.is_busy(d / A._ACTIVE), "🔴 放掉之後鎖還鎖著"


def test_解除租約失敗要講出來(A, tmp_path, monkeypatch, capsys):
    """🔴 Codex R28-P2-2:`except OSError: pass` —— marker 刪不掉時完全沒有聲音,
    那份產物會多留到「下次鎖拿得到」為止,而維運完全不知道。"""
    root = tmp_path / "state"
    monkeypatch.setattr(A, "state_root", lambda: root)
    d = A._web_workdir()
    real_unlink = Path.unlink

    def _boom(self, *a, **k):
        if self.name == A._ACTIVE:
            raise OSError(13, "Permission denied")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _boom)
    A._web_done(d)
    out = capsys.readouterr().out
    assert "租約" in out and str(d) in out, f"🔴 沒有講出哪個目錄解不掉:{out!r}"


def test_request中途拋例外也要放掉租約(A, tmp_path, monkeypatch):
    """🔴 Codex R29-P1-2:`_web_done()` 只在正常尾端呼叫 —— 情感弧線子程序或任何
    未收斂例外都會讓 request 中止,但**服務程序還活著**,holder 的 fd 與 `_HELD`
    會留到整個 Gradio 重啟為止;而 sweep 對 `_HELD` 內的路徑一律跳過
    → 那份還沒公開的歌詞永遠不會被回收。"""
    import json
    import types as _ty
    root = tmp_path / "state"
    monkeypatch.setattr(A, "state_root", lambda: root)
    P8 = ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")
    pt = {"完整評測": True, "缺柱": [], "缺柱權重合計": 0.0, "曲側合成": 70.0,
          "柱分": {k: {"score": 70.0, "items": {"x": 70.0}, "missing": []} for k in P8},
          "曲側含柱": list(P8)}
    rep = tmp_path / "甲_評審團.json"
    rep.write_text(json.dumps({"scoring_contract": "2026-07-25-v1", "pillar_totals": pt,
                               "scores": {"total": 70.0}}, ensure_ascii=False),
                   encoding="utf-8")
    calls = {"n": 0}

    def _run(cmd, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:                       # 評審團:正常回報告
            return _ty.SimpleNamespace(returncode=0, stdout=f"完整報告:{rep}\n", stderr="")
        raise RuntimeError("情感弧線炸了")          # 第二段:未收斂例外

    monkeypatch.setattr(A, "_run", _run)
    with pytest.raises(RuntimeError):
        A.evaluate("", str(tmp_path / "甲.wav"), "夜風吹過窗縫 帶來輕輕的呢喃", None)
    assert A._HELD == {}, f"🔴 例外之後租約還握在手上:{list(A._HELD)}"
    left = list((root / "web-tmp").iterdir())
    assert left, "這次沒有建出 request 目錄,等於沒驗到"
    for d in left:
        assert not A.is_busy(d / A._ACTIVE), f"🔴 {d.name} 的租約沒放掉 —— 永遠不會被回收"


def test_拿不到租約就不可以寫網頁產物(A, tmp_path, monkeypatch):
    """⛔ writer fail-closed(Codex R29-P2-1):沒有互斥保護就產出檔案,
    之後可能被別的請求當成孤兒回收 —— 使用者拿到一張不存在的圖。"""
    T = load("暫存清理")
    root = tmp_path / "state"
    monkeypatch.setattr(A, "state_root", lambda: root)
    monkeypatch.setattr(A, "take_ex", lambda p: (None, T.BACKEND_ERROR, "假裝鎖壞了"))
    with pytest.raises(RuntimeError) as e:
        A._web_workdir()
    assert "租約" in str(e.value)
    assert sorted(x.name for x in (root / "web-tmp").iterdir()) == [], \
        "🔴 拿不到租約卻留下了 request 目錄"


def test_啟動回收失敗時錯誤處理不可以再踩同一顆地雷(tmp_path):
    """🔴 Codex R29-P2-3:`except` 為了印路徑又呼叫一次 `state_root()` ——
    如果原始失敗正是「狀態目錄不能用」,錯誤處理器自己再拋一次,
    模組 import 直接裸爆,而註解還寫著「絕不擋住服務啟動」。"""
    import subprocess as _sp
    import sys as _sys
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys, types\n"
        f"sys.path.insert(0, r'{REPO}')\n"
        "class _Any:\n"
        "    def __init__(self, *a, **k): pass\n"
        "    def __call__(self, *a, **k): return _Any()\n"
        "    def __getattr__(self, _): return _Any()\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *a): return False\n"
        "gr = types.ModuleType('gradio')\n"
        "gr.__getattr__ = lambda n: _Any\n"
        "sys.modules['gradio'] = gr\n"
        "import 狀態目錄\n"
        "狀態目錄.state_root = lambda: (_ for _ in ()).throw("
        "PermissionError('狀態目錄不能用'))\n"
        "import app\n"
        "print('IMPORT_OK')\n", encoding="utf-8")
    r = _sp.run([_sys.executable, str(probe)], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    assert "IMPORT_OK" in out, f"🔴 狀態目錄壞掉時 import 直接裸爆:\n{out[-600:]}"
    assert "回收失敗" in out, f"🔴 沒有印出具名告警:\n{out[-400:]}"


# ── R30 ──────────────────────────────────────────────────────────
def _web_eval(A, tmp_path, monkeypatch, *, jury_stdout_extra="", rc=0,
              arc_ok=True, lease_ok=True, model=None):
    """跑一次 app.evaluate(),回 (成績表, 弧線圖, 詞評, 說明)。"""
    import json
    import types as _ty
    root = tmp_path / "state"
    monkeypatch.setattr(A, "state_root", lambda: root)
    P8 = ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")
    pt = {"完整評測": True, "缺柱": [], "缺柱權重合計": 0.0, "曲側合成": 70.0,
          "柱分": {k: {"score": 70.0, "items": {"x": 70.0}, "missing": []} for k in P8},
          "曲側含柱": list(P8)}
    rep = tmp_path / "甲_評審團.json"
    rep.write_text(json.dumps({"scoring_contract": "2026-07-25-v1", "pillar_totals": pt,
                               "scores": {"total": 70.0}}, ensure_ascii=False),
                   encoding="utf-8")

    def _run(cmd, timeout=None):
        joined = " ".join(str(c) for c in cmd)
        if "情感弧線.py" in joined:
            if arc_ok:                       # 真的產出那張圖
                src = Path(cmd[-1])
                src.with_name(src.stem + "_情感弧線.png").write_bytes(b"PNG")
            return _ty.SimpleNamespace(returncode=0, stdout="", stderr="")
        return _ty.SimpleNamespace(returncode=rc,
                                   stdout=f"完整報告:{rep}\n" + jury_stdout_extra,
                                   stderr="")

    monkeypatch.setattr(A, "_run", _run)
    if not lease_ok:
        T = load("暫存清理")
        monkeypatch.setattr(A, "take_ex", lambda _p: (None, T.BACKEND_ERROR,
                                                      "filesystem locking unsupported"))
    if model:
        monkeypatch.setattr(A, "ollama_judge", lambda *_a, **_k: "(詞評內容)")
    return A.evaluate("", str(tmp_path / "甲.wav"), "夜風吹過窗縫 帶來輕輕的呢喃", model)


def test_弧線沒跑出來就不可以說音訊加情感完成(A, tmp_path, monkeypatch):
    """🔴 Codex R30-P2-2(實跑重現):`_web_workdir()` 回 BACKEND_ERROR 時弧線
    整段沒跑、`arc_image` 是 None,訊息卻照樣寫「✅ 音訊+情感完成」,
    只在下面附一行小字「情感弧線跳過」—— ⛔ 上下矛盾,而使用者只會看那個 ✅。"""
    _t, arc, _l, note = _web_eval(A, tmp_path, monkeypatch, lease_ok=False)
    assert arc is None, "這個情境本來就不該有圖(不然沒驗到)"
    assert "音訊+情感完成" not in note, f"🔴 弧線沒跑卻說完成了:\n{note}"
    assert "情感弧線" in note and ("跳過" in note or "未完成" in note), \
        f"🔴 也沒有講清楚弧線怎麼了:\n{note}"


def test_弧線沒跑出來時選了模型也不可以說三關完成(A, tmp_path, monkeypatch):
    """同一件事的另一支訊息:選了 Ollama 模型時寫「✅ 三關完成」——
    第二關根本沒跑完(Codex R30-P2-2 指的 app.py:396 那條路徑)。"""
    _t, arc, lyric, note = _web_eval(A, tmp_path, monkeypatch,
                                     lease_ok=False, model="qwen3")
    assert arc is None and lyric, "要驗的是「詞評有、弧線沒有」"
    assert "三關完成" not in note, f"🔴 只跑了兩關卻說三關完成:\n{note}"


def test_弧線跑了卻沒產出圖也要降級(A, tmp_path, monkeypatch):
    """⚠️ 租約拿到了、子程序也回 0,但就是沒有那張 png(磁碟滿、字型缺、
    子程序自己吞掉錯誤)。⛔ 這時候一樣不可以說「情感完成」。"""
    _t, arc, _l, note = _web_eval(A, tmp_path, monkeypatch, arc_ok=False)
    assert arc is None
    assert "音訊+情感完成" not in note, f"🔴 沒有圖卻說情感完成:\n{note}"


def test_弧線正常時才說音訊加情感完成(A, tmp_path, monkeypatch):
    """反向:一切正常時訊息**不可以**被降級講法蓋掉(不然這組測試只證明了
    「永遠不說完成」——那是另一種不誠實)。"""
    _t, arc, _l, note = _web_eval(A, tmp_path, monkeypatch)
    assert arc is not None, "正常情境要有圖"
    assert "音訊+情感完成" in note, f"🔴 正常跑完卻沒說完成:\n{note}"


def test_網頁的殘留提示要有種類與純路徑(A, tmp_path, monkeypatch):
    """🔴 Codex R30-P2-1:網頁抓的是含「沒清乾淨」的**人話那一行**,
    `[demucs_stems]` 不見、說明文字還被當成路徑的一部分。"""
    extra = ("⛔ 分軌暫存沒清乾淨:C:/Temp/stems-left(裡面是一整份分軌,請手動刪掉)\n"
             + _cleanup_line([("demucs_stems", "C:/Temp/stems-left")]))
    _t, _a, _l, note = _web_eval(A, tmp_path, monkeypatch, rc=4, jury_stdout_extra=extra)
    assert "[demucs_stems] C:/Temp/stems-left" in note, \
        f"🔴 種類或純路徑沒送到網頁:\n{note}"
    assert "裡面是一整份分軌" not in note.split("[demucs_stems]", 1)[-1].split("」")[0], \
        f"🔴 說明文字被併進路徑了:\n{note}"


def test_網頁讀不到清理記錄也要照樣示警(A, tmp_path, monkeypatch):
    """⛔ fail-closed:rc=4 但沒有機器記錄(舊版子程序/輸出被截斷)——
    不可以因為「解析不到」就不提醒,那份音訊還在伺服器上。"""
    _t, _a, _l, note = _web_eval(A, tmp_path, monkeypatch, rc=4,
                                 jury_stdout_extra="(這裡什麼記錄都沒有)\n")
    assert "有暫存沒清乾淨" in note, f"🔴 讀不到記錄就整個不提了:\n{note}"
    # ⛔ 光是留著那句抬頭不算數:抬頭後面如果是空的,使用者看到
    #    「有暫存沒清乾淨: —— 請手動刪掉。」等於沒講,他不知道要去哪裡刪。
    assert "路徑不明" in note, f"🔴 只留了抬頭、沒說「路徑不明去看伺服器輸出」:\n{note}"


def test_web_tmp的父目錄也要是私人普通目錄(A, tmp_path, monkeypatch):
    """🔴 Codex R30-P2-3(WSL 實證):`.active` 這個**葉節點**走了 safe_open_lock,
    可是 `web-tmp` 這層只用 `mkdir(exist_ok=True)` 就開了 —— 預植
    `web-tmp -> 外面` 之後租約照樣拿得到,而 request 目錄與**還沒公開的歌詞**
    已經寫到狀態目錄外面。⛔ 產品承諾的是「自己的受管私人目錄」,每一層都要算數。"""
    import os as _os
    root = tmp_path / "state"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        _os.symlink(outside, root / "web-tmp", target_is_directory=True)
    except OSError:
        pytest.skip("這台建不了 symlink(Windows 需要管理員;junction 版見另一條)")
    monkeypatch.setattr(A, "state_root", lambda: root)
    # ⚠️ 用「基底類別 + 類別名」比對:conftest 的 load() 每次都重新 exec 模組,
    #    load("狀態目錄").StateDirError 跟 app 當初 import 到的**不是同一個類別物件**。
    with pytest.raises(RuntimeError) as e:
        A._web_workdir()
    assert type(e.value).__name__ == "StateDirError", type(e.value).__name__
    assert "web-tmp" in str(e.value)
    assert list(outside.iterdir()) == [], \
        f"🔴 已經寫到狀態目錄外面去了:{[x.name for x in outside.iterdir()]}"


def test_web_tmp是junction也要拒絕(A, tmp_path, monkeypatch):
    """Windows 版的同一件事:junction 不需要管理員就建得出來,
    所以這條在一般 Windows 開發機/CI 上都真的會跑。"""
    import subprocess as _sp
    import sys as _sys
    if _sys.platform != "win32":
        pytest.skip("junction 是 Windows 的東西")
    root = tmp_path / "state"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # ⚠️ 一定要指定 encoding:mklink 在中文 Windows 會吐 cp950 以外的位元組,
    #    text=True 用系統預設編碼解 → reader thread 裡 UnicodeDecodeError(自己踩到)。
    r = _sp.run(["cmd", "/c", "mklink", "/J", str(root / "web-tmp"), str(outside)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        pytest.skip(f"這台建不了 junction:{r.stdout or r.stderr}")
    monkeypatch.setattr(A, "state_root", lambda: root)
    with pytest.raises(RuntimeError) as e:
        A._web_workdir()
    assert type(e.value).__name__ == "StateDirError", type(e.value).__name__
    assert "web-tmp" in str(e.value)
    assert list(outside.iterdir()) == [], \
        f"🔴 已經寫到狀態目錄外面去了:{[x.name for x in outside.iterdir()]}"
