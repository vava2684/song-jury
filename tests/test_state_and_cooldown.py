# -*- coding: utf-8 -*-
"""Gemini 冷卻狀態的持久化:壞 schema 不炸、寫入失敗不裝死。

🔴 Codex R10 兩條:
· 合法 JSON、錯誤 schema(頂層 [])→ state.get() AttributeError,
  整個 Gemini subprocess 非零退出 → 評測缺柱。
· _locked_update 失敗(磁碟滿/狀態鎖壞)時 cool_down 照樣印「已冷卻」——
  其他工作看不到冷卻,立刻再轟剛被限流的 key。
"""
import json

from conftest import load

G = load("Gemini曲評")

_KEY = "KEY-FRESH-" + "x" * 20


def test_狀態檔頂層不是dict要隔離成corrupt不可炸(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text("[]", encoding="utf-8")
    assert G.load_state() == {}, "🔴 頂層 [] 交出去,後面 state.get() 會 AttributeError"
    assert (tmp_path / "state.json.corrupt").exists(), "壞檔要隔離留證據,不是默默吞掉"
    assert not (tmp_path / "state.json").exists(), "隔離=改名,原位不留壞檔"


def test_狀態檔整份不是JSON也要隔離(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text("{oops", encoding="utf-8")
    assert G.load_state() == {}
    assert (tmp_path / "state.json.corrupt").exists()


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
    assert not (tmp_path / "state.json.corrupt").exists(), "單筆壞不需要隔離整檔"


def test_狀態檔不存在回空不隔離(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    assert G.load_state() == {}
    assert not (tmp_path / "state.json.corrupt").exists()


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
