# -*- coding: utf-8 -*-
"""鎖的所有權、PID 存活接管、數值閘門、Gemini 冷卻刪除、階段 JSON 型別。

🔴 真實缺陷(Codex 第六輪):
  · 鎖沒有持有者代號 → A 的鎖被 B 接管後,A 結束時把 B 的鎖刪掉,C 又拿到鎖,三方互踩。
  · 陳舊判定只看 mtime → 被強制 kill 的工作(finally 沒跑)要卡別人最多 6 小時。
  · NaN / Infinity / 101 / -1 / True 全部穿過 `is not None` 進到正式柱分。
  · Gemini 成功清除冷卻用「merge」表達不了刪除 → 磁碟上的舊冷卻永遠清不掉;
    拿不到鎖時照樣無鎖寫入 → lost update 從正門回來。
  · 引擎吐出頂層是 list 的合法 JSON → d.get() AttributeError 炸掉整份評測。
"""
import json
import math
import subprocess
import sys
import pytest
from conftest import load

J = load("評審團")
G = load("Gemini曲評")


# ── 鎖:持有者代號 ──────────────────────────────────────────────────
def test_鎖被接管後原持有者不可以刪掉新鎖(tmp_path):
    """🔴 A 的鎖曾被判陳舊而遭 B 接管;A 結束時 finally 若無條件刪鎖,
    刪掉的是 B 的鎖 → C 可在 B 還在跑時拿到鎖(Codex 在 POSIX 重現)。
    刪除前必須驗 token 是自己的。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    lockf = song.with_name(".song.evaluating.lock")

    with J._job_lock(song):
        # 模擬「A 的鎖被 B 接管」:鎖檔內容換成別人的 token
        lockf.write_text(json.dumps({"pid": 99999999, "token": "somebody-else"}),
                         encoding="utf-8")
    # A 退出後,B 的鎖必須還在
    assert lockf.exists(), "🔴 原持有者把接管者的鎖刪掉了 —— 鎖形同虛設"
    lockf.unlink()


def test_持有者死掉的鎖立刻可接管(tmp_path):
    """🔴 只看 mtime 的話,被強制 kill 的工作(finally 沒跑)要卡別人最多 6 小時。
    改成看 PID 存活:死了就立刻接管,不必等。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    lockf = song.with_name(".song.evaluating.lock")
    # 造一個「真的死掉的 PID」:開個子程序讓它立刻結束
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    lockf.write_text(json.dumps({"pid": p.pid, "token": "dead-holder"}), encoding="utf-8")

    with J._job_lock(song):        # mtime 是新的,但 PID 已死 → 必須能立刻接管
        pass
    assert not lockf.exists()


def test_持有者還活著時不可以被接管(tmp_path):
    """反向保護:PID 活著的鎖,不管 mtime 多舊都不准搶(它是合法的長工作)。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    lockf = song.with_name(".song.evaluating.lock")
    import os
    lockf.write_text(json.dumps({"pid": os.getpid(), "token": "alive-holder"}),
                     encoding="utf-8")
    os.utime(lockf, (1, 1))        # mtime 極舊
    with pytest.raises(SystemExit):
        with J._job_lock(song):
            pass
    assert lockf.exists()
    lockf.unlink()


def test_pid_alive_在windows不可以誤殺對方():
    """⛔ Windows 的 os.kill(pid, 0) 是 TerminateProcess ——「檢查」會殺掉對方。
    驗自己的 PID 應回 True,而且驗完自己還活著(廢話,但這正是要保證的)。"""
    import os
    assert J._pid_alive(os.getpid()) is True


# ── 數值閘門 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("壞值", [float("nan"), float("inf"), float("-inf"),
                                  101.0, -1.0, True, "80", None])
def test_非法數值不可以進柱分(壞值):
    """🔴 Codex 探針:NaN → 柱分變 NaN;Infinity → 非標準 JSON;101/-1 → 照收;
    True → 被當 1 分。全部要判缺席。"""
    items = {"和聲": [("好項", 50, 70.0), ("壞項", 50, 壞值)]}
    out = J.build_pillar_totals(items)
    assert out["柱分"]["和聲"]["score"] == 70.0, \
        f"🔴 {壞值!r} 汙染了柱分:{out['柱分']['和聲']}"
    assert "壞項" in out["柱分"]["和聲"]["missing"]
    if 壞值 is not None:
        assert "壞項" in out["柱分"]["和聲"].get("invalid_numeric", {}), \
            "值不合法要留痕(invalid_numeric),不能跟「沒跑到」混在一起"


def test_邊界值0和100是合法分數():
    items = {"和聲": [("零分", 50, 0.0), ("滿分", 50, 100.0)]}
    out = J.build_pillar_totals(items)
    assert out["柱分"]["和聲"]["score"] == 50.0


def test_songeval非數字值不可以炸掉組裝():
    """🔴 SongEval 異常吐字串時,"abc"*20.0 是 TypeError → 整份組裝當場死。"""
    items = J.build_pillar_items({}, {}, {}, {},
                                 {"Memorability": "N/A", "Musicality": 4.0},
                                 {}, {}, {})
    d = dict((n, v) for n, w, v in items["旋律記憶"])
    assert d["記憶點(SongEval)"] is None      # 字串 → 缺席,不炸
    assert dict((n, v) for n, w, v in items["整體"])["音樂性(SongEval)"] == 80.0


# ── 階段 JSON 型別 ──────────────────────────────────────────────────
def test_頂層是list的JSON要當格式錯誤不是炸掉(tmp_path):
    """🔴 json.loads("[]") 成功,但下一行 d.get() 直接 AttributeError → 整份評測退出。"""
    p = tmp_path / "stage.json"
    p.write_text("[]", encoding="utf-8")
    d, err = J._load_stage_json(p, "測試階段")
    assert d is None and "格式錯誤" in err, "頂層非 dict 應標成該階段缺席,不准炸"


# ── Gemini 冷卻:刪除語義與鎖的尊重 ─────────────────────────────────
def test_成功清除冷卻要真的從磁碟消失(tmp_path, monkeypatch):
    """🔴 merge 表達不了刪除:state.pop 之後再 save,磁碟上的舊冷卻還在,
    實測清不掉 → 好金鑰永遠被當成冷卻中。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    s = G.load_state()
    G.cool_down(s, "KEY-A", 3600, "429")
    fp = G._fingerprint("KEY-A")
    assert fp in json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))

    G.delete_cooldown(fp)
    final = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert fp not in final, "🔴 冷卻清不掉 —— 好金鑰會永遠被跳過"


def test_拿不到鎖絕不無鎖寫入(tmp_path, monkeypatch):
    """🔴 舊版等 10 秒拿不到鎖就照樣無鎖讀改寫 → lost update 從正門回來。
    拿不到鎖必須跳過保存(冷卻只是最佳化,寧可少存也不能亂寫)。"""
    import contextlib as _ctx
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text(json.dumps({"KEEP": {"cooldown_until": 9e9}}),
                                         encoding="utf-8")

    @_ctx.contextmanager
    def fake_lock(timeout=10.0):
        yield False                    # 模擬搶不到鎖
    monkeypatch.setattr(G, "_state_lock", fake_lock)

    G.merge_cooldown("NEW", {"cooldown_until": 1.0})
    cur = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "NEW" not in cur and "KEEP" in cur, \
        "🔴 沒拿到鎖卻寫了檔 —— lost update 防線失效"
