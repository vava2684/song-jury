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
from conftest import load, REPO

J = load("評審團")
G = load("Gemini曲評")


# ── 鎖:OS 諮詢鎖(msvcrt/flock)─────────────────────────────────────
# 🔴 自製鎖(PID/token/mtime)修了三輪還被 Codex 找到洞:接管不是原子操作
#    (100 次有 13 次兩個接管者同時進鎖)、O_EXCL 建檔後崩潰留空鎖擋人 6 小時。
#    改用 OS 鎖後這些邏輯整段消失,測試改驗 OS 鎖的行為契約。

_CHILD_HOLD = r"""
import sys, time, importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("J", sys.argv[1])
J = importlib.util.module_from_spec(spec); sys.modules["J"] = J
spec.loader.exec_module(J)
with J._job_lock(Path(sys.argv[2])):
    print("LOCKED", flush=True)
    time.sleep(60)
"""


def _spawn_holder(song):
    """開一個真的子程序持有鎖,等它回報 LOCKED 再交還控制權。"""
    p = subprocess.Popen([sys.executable, "-c", _CHILD_HOLD,
                          str(REPO / "評審團.py"), str(song)],
                         stdout=subprocess.PIPE, text=True, encoding="utf-8")
    line = p.stdout.readline().strip()
    assert line == "LOCKED", f"子程序沒拿到鎖:{line!r}"
    return p


def test_別的程序持有鎖時本程序拿不到(tmp_path):
    """互斥的核心契約:跨程序也要擋得住。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    p = _spawn_holder(song)
    try:
        with pytest.raises(SystemExit):
            with J._job_lock(song):
                pass
    finally:
        p.kill()
        p.wait()


def test_持有程序被強制殺掉後立刻可以再拿鎖(tmp_path):
    """🔴 OS 鎖的關鍵優勢:程序死亡**自動釋放**。
    舊自製鎖被 kill 後(finally 沒跑)會留鎖擋人;O_EXCL 剛建檔就崩潰更會留
    「全新的空鎖」被當活鎖擋 6 小時(Codex 重現)。OS 鎖沒有這些狀態。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    p = _spawn_holder(song)
    p.kill()
    p.wait()
    with J._job_lock(song):        # 不必等待、不必清理,立刻拿得到
        pass


def test_釋放鎖不刪鎖檔(tmp_path):
    """⛔ POSIX 上「unlink 再重建」會讓兩個工作鎖在不同 inode 上,互斥失效 ——
    flock 的經典陷阱。鎖檔一律留著。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    lockf = song.with_name(".song.evaluating.lock")
    with J._job_lock(song):
        assert lockf.exists()
    assert lockf.exists(), "🔴 鎖檔被刪了 —— unlink+重建會破壞 flock 互斥"
    with J._job_lock(song):        # 而且要能立刻重新取得
        pass


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


# ── 第七輪:bool 洗白、清洗共用、原子報告、狀態鎖互斥、金鑰租約 ─────────
def test_bool不可以在取值層被洗成浮點數():
    """🔴 Codex 探針:引擎 JSON 給 spectral_balance.score=True →
    float(True)=1.0 在 _g() 就洗白,中央閘門看到的是合法的 1.0 → 聲學柱正式分數 1.0。
    要走**完整路徑**驗(引擎 dict → build_pillar_items → 閘門),不能只餵閘門。"""
    phys = {"mix_detail": {"spectral_balance": {"score": True}}}
    items = J.build_pillar_items(phys, {}, {}, {}, {}, {}, {}, {})
    assert dict((n, v) for n, w, v in items["聲學"])["頻譜平衡"] is None, \
        "🔴 True 被 float() 洗成 1.0 混進聲學柱"
    out = J.build_pillar_totals(items)
    assert out["柱分"]["聲學"]["score"] is None


def test_clean_scores把非數值欄位清掉並留痕():
    """🔴 摘要層對原始值 sum()/格式化:SongEval 混一個 "N/A",報告都寫完了,
    最後摘要那行 TypeError 讓整個程序以失敗收場 → 批次/網頁版拒收這次昂貴評測。
    清洗一次、算分/JSON/主控台共用同一份。"""
    notes = []
    out = J._clean_scores({"Musicality": 4.0, "Unexpected": "N/A",
                           "Bad": float("nan"), "Flag": True}, "SongEval", notes)
    assert out == {"Musicality": 4.0}
    assert notes and "SongEval" in notes[0]
    assert sum(out.values()) == 4.0            # 清洗後 sum() 永遠安全

    assert J._clean_scores([1, 2], "X", notes) == {}    # 頂層不是 dict 也不炸


def test_報告寫出是原子的且不含NaN(tmp_path):
    """🔴 直接 write_text 覆寫:中斷留半截報告;json.dumps 預設把非有限值寫成
    NaN/Infinity 字面值,嚴格 JSON 解析器直接拒收。"""
    out = tmp_path / "x_評審團.json"
    J._write_report({"a": float("nan"), "b": [float("inf"), 1.0], "c": {"d": 2.0}}, out)
    text = out.read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text, "🔴 寫出了非標準 JSON"
    data = json.loads(text)
    assert data["a"] is None and data["b"][0] is None and data["c"]["d"] == 2.0
    assert not list(tmp_path.glob("*.tmp*")), "暫存檔要清掉"


def test_狀態鎖真的互斥(tmp_path, monkeypatch):
    """🔴 舊 _state_lock「超過 60 秒就 unlink 搶過來」有 check-then-delete 競態。
    OS 鎖版:持有中的第二個取得嘗試必須拿不到(acquired=False),而不是把對方鎖刪掉。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    with G._state_lock(timeout=5.0) as a1:
        assert a1 is True
        with G._state_lock(timeout=0.3) as a2:
            assert a2 is False, "🔴 兩個持有者同時拿到狀態鎖"
    with G._state_lock(timeout=5.0) as a3:      # 釋放後要能立刻再拿
        assert a3 is True


def test_同一把金鑰同時只准一個工作在打(tmp_path, monkeypatch):
    """🔴 冷卻只在 429 之後生效:兩個合法並行的工作仍會同時轟同一把 key
    (Codex 探針:同 key 在途=2)。租約要擋住第二個。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    with G.key_lease("KEY-A", timeout=5.0) as l1:
        assert l1 is True
        with G.key_lease("KEY-A", timeout=0.3) as l2:
            assert l2 is False, "🔴 同一把金鑰兩個工作同時在途"
        with G.key_lease("KEY-B", timeout=0.5) as other:
            assert other is True, "不同金鑰不應互相卡"
