# -*- coding: utf-8 -*-
"""批次評測的失敗處理,與訊號切窗。

🔴 真實事故:
  · 批次評測不看 returncode,只要舊的 _評審團.json 還在就讀 →
    重跑失敗時把「上一次的舊分數」當成功,錯誤字串還是空的,完全看不出來。
  · 切窗迴圈 range(0, n-win, win) 漏掉最後一個完整窗 →
    40 秒音檔只分析 1 個 20 秒窗、240 秒只分析 11 個而不是 12 個。
"""
import json
import subprocess
import types
import pytest
from conftest import load, REPO

J = load("評審團")
B = load("批次評測")


# ── 切窗 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("秒數,窗秒,應有窗數", [
    (40, 20, 2),      # 🔴 修之前只給 1
    (240, 20, 12),    # 🔴 修之前只給 11
    (60, 20, 3),
    (20, 20, 1),      # 剛好一個窗
    (19, 20, 1),      # 不足一窗 → 至少回一個起點,交給呼叫端判長度
])
def test_切窗不漏最後一個完整窗(秒數, 窗秒, 應有窗數):
    sr = 16000
    assert len(list(J.iter_windows(秒數 * sr, 窗秒 * sr))) == 應有窗數


def test_切窗起點不重疊且遞增():
    ws = list(J.iter_windows(100, 10))
    assert ws == list(range(0, 91, 10))


# ── 批次:失敗與不完整 ──────────────────────────────────────────────
def _stub_run(monkeypatch, returncode, write_json=None):
    def fake(cmd, **kw):
        if write_json is not None:
            # 模擬「程式有寫出 JSON」的情況
            import re as _re
            song = [c for c in cmd if str(c).endswith((".wav", ".mp3"))]
            if song:
                from pathlib import Path
                p = Path(song[0])
                p.with_name(p.stem + "_評審團.json").write_text(
                    json.dumps(write_json, ensure_ascii=False), encoding="utf-8")
        return types.SimpleNamespace(returncode=returncode, stdout="", stderr="boom")
    # run_one 走的是 子程序.run_tree(逾時殺整棵樹的共用實作),stub 要指到它
    monkeypatch.setattr(B, "run_tree", fake)


完整JSON = {"pillar_totals": {"完整評測": True, "缺柱": [], "曲側合成": 70.0}}
不完整JSON = {"pillar_totals": {"完整評測": False, "缺柱": ["律動", "整體"], "曲側合成": 66.4}}


def test_這輪沒產出新檔時不可以讀到上一輪的舊JSON(monkeypatch, tmp_path):
    """🔴 核心迴歸之一:**跑之前必須先刪掉舊產物**。

    ⚠️ 這裡刻意用 returncode=0 —— 若用非 0,會被 returncode 檢查先攔下來,
    測到的就不是「有沒有刪舊檔」了(變異驗證抓到過我這個錯)。
    真實情境:評審團.py 吃 SUNO 連結時,輸出檔名跟著「下載後的歌名」走,
    與批次算出來的路徑對不上 → 這輪其實沒產出,卻讀到上一輪的舊報告。"""
    song = tmp_path / "song.wav"; song.write_bytes(b"x")
    song.with_name("song_評審團.json").write_text(
        json.dumps({"pillar_totals": {"完整評測": True, "曲側合成": 99.9}}), encoding="utf-8")
    _stub_run(monkeypatch, returncode=0)           # 成功回報,但沒寫出這輪的檔
    d, err = B.run_one(song)
    assert d is None, "🔴 讀到舊 JSON 了 —— 批次表會出現上次的分數"
    assert err, "失敗必須回報原因,不可以是空字串"


def test_子程序失敗但已寫出檔案時仍要判失敗(monkeypatch, tmp_path):
    """🔴 核心迴歸之二:程式寫完 JSON 才在後面炸掉(例如報告階段),
    光看「檔案在不在」會誤判成功。**這條專門守 returncode 檢查**,
    上一條守的是刪舊檔 —— 兩條缺一不可(變異驗證證明過:少了這條,
    把 returncode 檢查拿掉測試照樣全綠)。"""
    song = tmp_path / "song.wav"; song.write_bytes(b"x")
    _stub_run(monkeypatch, returncode=1, write_json=完整JSON)   # 有寫檔,但結束碼非 0
    d, err = B.run_one(song)
    assert d is None, "🔴 結束碼非 0 卻被當成功"
    assert "結束碼" in err


def test_成功時正常回傳(monkeypatch, tmp_path):
    song = tmp_path / "song.wav"; song.write_bytes(b"x")
    _stub_run(monkeypatch, returncode=0, write_json=完整JSON)
    d, err = B.run_one(song)
    assert err == "" and d["pillar_totals"]["曲側合成"] == 70.0


def test_缺少完整性欄位時必須拒收(monkeypatch, tmp_path):
    """🔴 fail-closed:舊格式、半殘 JSON、異常子程序產出 —— 沒有 pillar_totals.完整評測
    就是**無法確認**,必須拒收。舊寫法 `if _pt and not _pt.get(...)` 反而讓這種最該擋的
    情況直接放行進批次表(fail-open)。"""
    song = tmp_path / "song.wav"; song.write_bytes(b"x")
    for 壞資料 in ({}, {"pillar_totals": {}}, {"pillar_totals": {"完整評測": "yes"}}):
        _stub_run(monkeypatch, returncode=0, write_json=壞資料)
        d, err = B.run_one(song)
        assert d is None, f"🔴 放行了無法確認完整性的結果:{壞資料}"
        assert err


def test_不同路徑的同名歌不可以共用結果鍵(tmp_path):
    """🔴 批次歌單可以引用不同資料夾:a/song.wav 與 b/song.wav 都叫 song.wav。
    用檔名當結果鍵的話,第二首會被當成「已有結果」直接跳過 —— 整首歌靜靜漏評。"""
    import 批次評測 as _B
    a = tmp_path / "a" / "song.wav"; b = tmp_path / "b" / "song.wav"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")
    src = (REPO / "批次評測.py").read_text(encoding="utf-8")
    assert "key = song.name" not in src, "🔴 結果鍵還在用檔名 → 同名不同路徑會撞在一起"
    assert "song.resolve()" in src, "結果鍵應該用正規化後的絕對路徑"


def test_損壞主檔不可以覆蓋好備份(tmp_path):
    """🔴 雙毀情境(Codex 第五輪):主檔壞了 → 從 .bak 救回來 →
    存檔時又把「壞掉的主檔」複製成 .bak → 唯一一份好備份被毀,之後永遠救不回來。
    只有確定解析得開的主檔才可以拿去當備份。"""
    store = tmp_path / "批次結果.json"
    bak = store.with_suffix(".json.bak")
    store.write_text("{壞掉的半截", encoding="utf-8")          # 主檔損壞
    bak.write_text(json.dumps({"好資料": 1}), encoding="utf-8")  # 備份是好的

    B._save_store(store, {"新資料": 2})

    assert json.loads(bak.read_text(encoding="utf-8")) == {"好資料": 1}, \
        "🔴 好備份被損壞的主檔蓋掉了"
    assert json.loads(store.read_text(encoding="utf-8")) == {"新資料": 2}


def test_不完整評測不可以進批次表(monkeypatch, tmp_path):
    """⛔ 缺柱是另一把尺,拿去算鑑別力會得到假結論。
    ⚠️ R15 起分兩種契約:完整模式要求九柱齊全;預設(local)模式只放行
    「Gemini 造成的缺柱」。這條驗的是完整模式的收件標準。"""
    song = tmp_path / "song.wav"; song.write_bytes(b"x")
    monkeypatch.setattr(B, "FULL_MODE", True)
    _stub_run(monkeypatch, returncode=0, write_json=不完整JSON)
    d, err = B.run_one(song)
    assert d is None
    assert "不完整" in err and "律動" in err


def test_退出碼2的缺柱報告要讀進來不可當成程式炸掉(monkeypatch, tmp_path):
    """🔴 Codex R12(⑨ 行為版):評審團 exit 2 = 報告已完整發布但缺柱。
    批次要**照樣讀 JSON**,不可以停在「評審團 結束碼 2」讓人以為程式炸了。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    不完整JSON = {"pillar_totals": {"完整評測": False, "缺柱": ["律動"],
                                    "缺柱權重合計": 4.0, "柱分": {}}}
    _stub_run(monkeypatch, returncode=2, write_json=不完整JSON)
    d, err = B.run_one(song)
    assert "結束碼" not in (err or ""), f"🔴 exit 2 被當成程式炸掉:{err!r}"


def test_預設批次收得到結果而不是每首都拒收(monkeypatch, tmp_path):
    """🔴 Codex R15 的死鎖:預設略過 Gemini → 律動必缺 → 又用「完整評測」當收件
    標準 → **每一首都被拒收**,預設批次不可能產生任何可比較資料。
    改成 local-metrics 契約:只缺 Gemini 柱的結果照收,但明確標記契約。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    只缺律動 = {"pillar_totals": {"完整評測": False, "缺柱": ["律動"],
                                  "缺柱權重合計": 4.0, "柱分": {}}}
    monkeypatch.setattr(B, "FULL_MODE", False)
    _stub_run(monkeypatch, returncode=2, write_json=只缺律動)
    d, err = B.run_one(song)
    assert d is not None, f"🔴 預設批次連一筆都收不到:{err!r}"
    assert d["_batch_contract"] == B.LOCAL_CONTRACT, "要明寫這不是九柱總分"


def test_缺了安裝問題造成的柱仍要拒收(monkeypatch, tmp_path):
    """⛔ 放寬只放給「Gemini 造成的缺柱」:和聲/結構編曲缺席是安裝壞了,
    那種結果進表會讓鑑別力算出假結論。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    缺和聲 = {"pillar_totals": {"完整評測": False, "缺柱": ["律動", "和聲"],
                                "缺柱權重合計": 17.6, "柱分": {}}}
    monkeypatch.setattr(B, "FULL_MODE", False)
    _stub_run(monkeypatch, returncode=2, write_json=缺和聲)
    d, err = B.run_one(song)
    assert d is None and "和聲" in err, f"沒擋住安裝問題造成的缺柱:{err!r}"


def test_完整模式仍然要求九柱齊全(monkeypatch, tmp_path):
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    只缺律動 = {"pillar_totals": {"完整評測": False, "缺柱": ["律動"],
                                  "缺柱權重合計": 4.0, "柱分": {}}}
    monkeypatch.setattr(B, "FULL_MODE", True)
    _stub_run(monkeypatch, returncode=2, write_json=只缺律動)
    d, err = B.run_one(song)
    assert d is None and "不完整" in err
