# -*- coding: utf-8 -*-
"""下載檔名保留、單工作鎖、Gemini 冷卻狀態合併。

🔴 真實缺陷(Codex 第五輪):
  · 原子佔名直接建立正式 .mp3 → 下載失敗留下 0 byte 幽靈檔,
    下次會被當成「已下載過」,YouTube 那條路徑甚至只檢查「檔案存在」就印「已存」。
  · 同一個音檔被評兩次時,九個中間檔名字相同 → 互相覆寫/刪除。
  · Gemini 冷卻狀態讀-改-寫沒有鎖 → lost update,
    已被限流的金鑰會再被呼叫一次。
"""
import json
import threading
import pytest
from conftest import load, REPO

J = load("評審團")
G = load("Gemini曲評")


# ── 下載檔名保留 ────────────────────────────────────────────────────
def test_佔名不可以建立正式mp3(tmp_path, monkeypatch):
    """🔴 佔位檔若是正式的 .mp3,下載失敗就留下 0 byte 幽靈檔。"""
    monkeypatch.setattr(J, "BASE", tmp_path)
    stem = J._unique_stem("song")
    dl = tmp_path / "下載"
    assert not (dl / f"{stem}.mp3").exists(), \
        "🔴 佔名建立了正式 mp3 → 下載失敗會留下 0 byte 幽靈檔"
    assert (dl / f".{stem}.mp3.reserving").exists(), "應該用獨立的保留檔佔名"


def test_佔名仍然防得住併發撞名(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "BASE", tmp_path)
    got, lock = [], threading.Lock()

    def w():
        s = J._unique_stem("same-title")
        with lock:
            got.append(s)
    ts = [threading.Thread(target=w) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(got)) == 2, f"🔴 兩個工作拿到同一個名字:{got}"


def test_釋放後名字可以再被佔(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "BASE", tmp_path)
    s1 = J._unique_stem("song")
    J._release_stem(s1)
    s2 = J._unique_stem("song")
    assert s1 == s2 == "song", "釋放之後同一個名字應該可以再用,不該一直往上加版號"


def test_下載檔案太小要當失敗(tmp_path):
    """⛔ 只檢查 returncode 與『檔案存在』會放行 0 byte 佔位檔與被截斷的下載。"""
    p = tmp_path / "song.mp3"
    p.write_bytes(b"")
    with pytest.raises(SystemExit):
        J._check_audio_ok(p)
    assert not p.exists(), "無效檔應該被刪掉,不可以留給下一次誤用"


# ── 單工作鎖 ────────────────────────────────────────────────────────
def test_同一個音檔不可以同時評兩次(tmp_path):
    """🔴 九個中間檔都叫 `{音檔名}_xxx.json`,同檔並跑會互相覆寫/刪除。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    with J._job_lock(song):
        with pytest.raises(SystemExit):
            with J._job_lock(song):
                pass
    # 離開後鎖要放掉,不可以卡住下一次
    with J._job_lock(song):
        pass


def test_不同音檔可以並行(tmp_path):
    """⚠️ 鎖只擋同一個檔 —— SUNO 抽卡的各個 take 音檔不同,必須照樣能並行。"""
    a, b = tmp_path / "take1.wav", tmp_path / "take2.wav"
    for p in (a, b):
        p.write_bytes(b"x")
    with J._job_lock(a):
        with J._job_lock(b):
            pass


# ── Gemini 冷卻狀態 ─────────────────────────────────────────────────
def test_冷卻狀態不可以lost_update(tmp_path, monkeypatch):
    """🔴 兩個工作各自讀到空狀態再各寫各的,後寫的會把先寫的整個蓋掉 →
    已被限流的金鑰又會被呼叫一次。必須鎖內重讀再合併。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    sA, sB = G.load_state(), G.load_state()      # 兩邊都讀到空的
    G.cool_down(sA, "KEY-A", 3600, "429")
    G.cool_down(sB, "KEY-B", 3600, "429")
    final = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(final) == 2, f"🔴 有一把金鑰的冷卻被覆蓋掉了:{final}"


def test_合併時取較晚到期的冷卻(tmp_path, monkeypatch):
    """保守原則:寧可多冷卻,也不要誤放行一把還在限流的金鑰。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    s = G.load_state()
    G.cool_down(s, "KEY-A", 7200, "長冷卻")
    s2 = G.load_state()
    G.cool_down(s2, "KEY-A", 60, "短冷卻")       # 較早到期,不該蓋掉長的
    final = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    only = list(final.values())[0]
    assert only["reason"] == "長冷卻", f"🔴 較晚到期的冷卻被較早的蓋掉了:{only}"
