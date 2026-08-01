# -*- coding: utf-8 -*-
"""Gemini 冷卻狀態的持久化:壞 schema 不炸、寫入失敗不裝死。

🔴 Codex R10 兩條:
· 合法 JSON、錯誤 schema(頂層 [])→ state.get() AttributeError,
  整個 Gemini subprocess 非零退出 → 評測缺柱。
· _locked_update 失敗(磁碟滿/狀態鎖壞)時 cool_down 照樣印「已冷卻」——
  其他工作看不到冷卻,立刻再轟剛被限流的 key。
"""
import json

from conftest import load, break_lock_backend

G = load("Gemini曲評")

_KEY = "KEY-FRESH-" + "x" * 20


def _corrupts(tmp_path):
    return sorted(tmp_path.glob("state.json.corrupt.*"))


def test_狀態檔頂層不是dict要隔離成corrupt不可炸(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text("[]", encoding="utf-8")
    assert G.load_state() == {}, "🔴 頂層 [] 交出去,後面 state.get() 會 AttributeError"
    assert _corrupts(tmp_path), "壞檔要隔離留證據,不是默默吞掉"
    assert not (tmp_path / "state.json").exists(), "隔離=改名,原位不留壞檔"


def test_狀態檔整份不是JSON也要隔離(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text("{oops", encoding="utf-8")
    assert G.load_state() == {}
    assert _corrupts(tmp_path)


def test_隔離前要鎖內重讀_新狀態不可被搬走(tmp_path, monkeypatch):
    """🔴 Codex R11 探針(corrupt_contains_fresh_writer_data):讀者判定「壞檔」後、
    動手隔離前,寫入者在鎖內換上了新的合法狀態 —— 無鎖 rename 會把**新狀態**搬去
    .corrupt,冷卻遺失,其他程序再轟已限流的 key。隔離必須鎖內重讀,還是壞的才動手。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    good = {"f": {"cooldown_until": 9999999999.0, "reason": "429"}}
    (tmp_path / "state.json").write_text(json.dumps(good), encoding="utf-8")
    G._quarantine_state("讀者手上的過期判定")          # 模擬:判定已過期,檔案其實是新的好狀態
    assert (tmp_path / "state.json").exists(), "🔴 寫入者剛發布的新狀態被搬走了"
    assert not _corrupts(tmp_path)
    assert G.load_state() == good


def test_隔離拿不到鎖就一根指頭都不碰(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text("[]", encoding="utf-8")
    break_lock_backend(monkeypatch)
    assert G.load_state() == {}                        # 這次先回空狀態
    assert (tmp_path / "state.json").exists(), "拿不到鎖就不可以動檔案(改天再隔離)"
    assert not _corrupts(tmp_path)


def test_連環壞檔各自留證據不互相覆蓋(tmp_path, monkeypatch):
    """固定 .corrupt 檔名會先刪舊證據才放新的 —— uuid 命名讓每份都留下來。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text("[]", encoding="utf-8")
    G.load_state()
    (tmp_path / "state.json").write_text("{oops", encoding="utf-8")
    G.load_state()
    assert len(_corrupts(tmp_path)) == 2, "兩份壞檔證據都要在"


def test_寫入端要能修復畸形record(tmp_path, monkeypatch):
    """🔴 Codex R11:磁碟預植 cooldown_until:"bad" 後,merge 的 float() 炸 →
    cool_down 永遠 False,壞資料永遠躺在磁碟上。鎖內先清洗,新 record 直接取代。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    fp = G._fingerprint(_KEY)
    (tmp_path / "state.json").write_text(json.dumps({
        fp: {"cooldown_until": "bad", "reason": "junk"},
        "其他壞蛋": {"cooldown_until": "也是壞的"},
    }), encoding="utf-8")
    assert G.cool_down({}, _KEY, 60, "429") is True, "🔴 畸形舊 record 讓寫入端永遠失敗"
    disk = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    cu = disk[fp]["cooldown_until"]
    assert isinstance(cu, float) and cu > 0, f"新 record 要真的取代壞資料:{disk[fp]}"
    assert "其他壞蛋" not in disk, "鎖內重寫時,其他畸形 record 也要一併清掉"


def test_merge對畸形舊record要直接取代不可炸(monkeypatch):
    """第二道防線的直接觀測:就算上游清洗(_read_state_locked)被繞過,
    merge 遇到畸形舊 record 也不可以 float() 炸掉 —— 新合法 record 直接取代。"""
    captured = {}
    def fake_locked_update(mutator):
        cur = {"fp1": {"cooldown_until": "bad", "reason": "junk"}}   # 模擬清洗被繞過
        mutator(cur)                                                  # ⛔ 這裡不准炸
        captured.update(cur)
        return True
    monkeypatch.setattr(G, "_locked_update", fake_locked_update)
    assert G.merge_cooldown("fp1", {"cooldown_until": 123.0, "reason": "429"}) is True
    assert captured["fp1"]["cooldown_until"] == 123.0, "畸形舊值要被新 record 取代"


def test_狀態檔超過大小上限要隔離不吃記憶體(tmp_path, monkeypatch):
    """🔴 Codex R11:16MiB 垃圾檔先整檔 read+parse 吃掉 80MB+ 記憶體。
    先看 stat,超過 1MiB 直接隔離 —— 就算內容是「合法」的超大 JSON 也一樣。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    big = {"junk": {"cooldown_until": 1.0, "reason": "x" * 2_000_000}}
    (tmp_path / "state.json").write_text(json.dumps(big), encoding="utf-8")
    assert G.load_state() == {}, "🔴 超大檔被整份讀進來了"
    assert _corrupts(tmp_path), "超大檔要隔離留證據"


def test_狀態檔單筆壞只丟單筆不整檔陪葬(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    good = {"cooldown_until": 9999999999.0, "reason": "429"}
    (tmp_path / "state.json").write_text(json.dumps({
        "good": good,
        "bad_str": "hi",                                  # record 不是 dict
        "bad_cu_str": {"cooldown_until": "abc"},          # cooldown_until 不是數字
        "bad_cu_bool": {"cooldown_until": True},          # bool 不是分數也不是時間
        "bad_cu_inf": {"cooldown_until": float("inf")},   # 非有限 → 永久冷卻的假象
    }), encoding="utf-8")
    assert G.load_state() == {"good": good}
    assert not _corrupts(tmp_path), "單筆壞不需要隔離整檔"


def test_狀態檔不存在回空不隔離(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    assert G.load_state() == {}
    assert not _corrupts(tmp_path)


def test_冷卻寫入失敗不可宣稱已冷卻(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(G, "_locked_update", lambda m: False)
    state = {}
    ok = G.cool_down(state, _KEY, 60, "429")
    assert ok is False, "🔴 磁碟沒寫成卻回報成功 → 其他工作會再轟這把 key"
    assert G._fingerprint(_KEY) in state, "本程序自己的記憶體 state 還是要記住"


def test_429冷卻寫入失敗要留cooldown_persist_error(tmp_path, monkeypatch):
    """走真的 call_gemini:429 → 冷卻寫不進磁碟 → attempts 要有 cooldown_persist_error,
    不可以只在主控台印「冷卻並換下一把」然後 JSON 裡乾乾淨淨。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(G, "load_keys", lambda: [_KEY])
    monkeypatch.setattr(G, "_locked_update", lambda m: False)
    monkeypatch.setattr(G.time, "sleep", lambda *_: None)

    class R429:
        status_code = 429
        text = ""
        def json(self):
            return {}
    monkeypatch.setattr(G.requests, "post", lambda *a, **k: R429())

    mp3 = tmp_path / "x.mp3"
    mp3.write_bytes(b"\xff\xfb" + b"\x00" * 512)
    _, meta = G.call_gemini(mp3, "zh", 5, ignore_cooldown=False, verbose=False)
    assert any(a.get("result") == "cooldown_persist_error" for a in meta["attempts"]), \
        f"🔴 冷卻沒寫進磁碟卻無痕:{meta['attempts']}"
