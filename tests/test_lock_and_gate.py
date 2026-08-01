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
from pathlib import Path

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
sys.path.insert(0, str(Path(sys.argv[1]).parent))   # 評審團 import 狀態目錄 要找得到
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
    flock 的經典陷阱。鎖檔一律留著。
    🔴 Codex R9:鎖檔集中管理,不放歌旁邊(歌在唯讀資料夾/網路磁碟時開鎖檔就失敗);
    🔴 Codex R10:再從 BASE/_locks 移到**使用者全域狀態目錄**(BASE 跟著副本走,
    兩份 ZIP 副本會各鎖各的)。"""
    import os as _os
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    lockf = J._lock_path_for(song)
    assert lockf.parent == Path(_os.environ["SONG_JURY_STATE_DIR"]) / "_locks", \
        f"🔴 鎖檔要在全域狀態目錄的 _locks,不是 {lockf.parent}"
    with J._job_lock(song):
        assert lockf.exists()
    assert lockf.exists(), "🔴 鎖檔被刪了 —— unlink+重建會破壞 flock 互斥"
    with J._job_lock(song):        # 而且要能立刻重新取得
        pass


def test_鎖的位置跟工具副本無關(tmp_path, monkeypatch):
    """🔴 Codex R10:鎖放 BASE/_locks → 電腦上同時存在兩份 ZIP 副本(新舊版並存、
    App 與 CLI 來自不同資料夾)時,各副本各鎖各的,對同一首歌照樣雙雙進鎖;
    Gemini 冷卻/租約也各一套,同一把 key 被兩個副本同時轟。
    鎖的身分必須只由「這台機器 + 歌的絕對路徑」決定,跟 BASE 無關。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    monkeypatch.setattr(J, "BASE", tmp_path / "copyA")
    p1 = J._lock_path_for(song)
    monkeypatch.setattr(J, "BASE", tmp_path / "copyB")
    p2 = J._lock_path_for(song)
    assert p1 == p2, "🔴 不同副本(BASE)算出不同鎖檔 → 互斥只在單一副本內成立"
    assert str(tmp_path / "copyA") not in str(p1) and str(tmp_path / "copyB") not in str(p2), \
        "鎖檔不可以在任何副本(BASE)底下"
    # Gemini 冷卻狀態同理:不可放在 repo(副本)底下
    assert G.STATE_FILE.parent != REPO, "🔴 Gemini 狀態檔還在副本目錄裡"


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
def test_bool不可以被洗成浮點數且要留下證據():
    """🔴 兩層契約(Codex 兩輪各抓到一半):
    第七輪:float(True)=1.0 在取值層洗白 → 聲學柱正式分數 1.0。
    第八輪:改成轉 None 之後,「值不合法」與「沒跑到」在報告裡分不出來 ——
            先前承諾的 invalid_numeric 證據被抹掉了。
    正確語義:取值層**保留非法原值**,閘門拒絕它並記進 invalid_numeric。
    要走完整路徑驗(引擎 dict → build_pillar_items → 閘門),不能只餵閘門。"""
    phys = {"mix_detail": {"spectral_balance": {"score": True}}}
    items = J.build_pillar_items(phys, {}, {}, {}, {}, {}, {}, {})
    v = dict((n, x) for n, w, x in items["聲學"])["頻譜平衡"]
    assert v is True, f"🔴 非法原值沒被保留(拿到 {v!r})→ 證據鏈斷掉"
    out = J.build_pillar_totals(items)
    assert out["柱分"]["聲學"]["score"] is None, "🔴 True 混進了正式分數"
    assert "頻譜平衡" in out["柱分"]["聲學"].get("invalid_numeric", {}), \
        "🔴 invalid_numeric 證據不見了 —— 讀報告的人會以為量測沒跑到"


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
        assert l1 == "ok"
        with G.key_lease("KEY-A", timeout=0.3) as l2:
            assert l2 == "busy", "🔴 同一把金鑰兩個工作同時在途"
        with G.key_lease("KEY-B", timeout=0.5) as other:
            assert other == "ok", "不同金鑰不應互相卡"


def _break_lock_backend(monkeypatch):
    """把 OS 鎖後端弄壞(ENOLCK:網路 FS 不支援鎖的典型錯誤)。"""
    import errno as _e
    if sys.platform == "win32":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking",
                            lambda fd, m, n: (_ for _ in ()).throw(OSError(_e.ENOLCK, "no locks")))
    else:
        import fcntl
        monkeypatch.setattr(fcntl, "flock",
                            lambda fd, fl: (_ for _ in ()).throw(OSError(_e.ENOLCK, "no locks")))


def test_鎖壞掉不可以被當成有人持有(tmp_path, monkeypatch):
    """🔴 Codex R9:鎖 backend 發生 error 時「照常放行」= fail-open,
    互斥保證無聲蒸發(同 key 在途又回到 2)。正確語義三分:
    error ≠ busy(不是有人持有)、error ≠ ok(fail-closed 不放行)、
    狀態鎖壞掉 → 拒寫(不寫比亂寫安全)。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    _break_lock_backend(monkeypatch)
    with G.key_lease("KEY-X", timeout=0.5) as leased:
        assert leased == "error", \
            f"🔴 鎖壞掉要三態誠實回報 error(busy=誤當有人持有;ok=fail-open):{leased!r}"
    with G._state_lock(timeout=0.5) as acq:
        assert acq is False, "狀態鎖壞掉要拒寫(不寫比亂寫安全)"


def test_鎖壞掉整條鏈fail_closed一次都不打(tmp_path, monkeypatch):
    """🔴 Codex R9 端對端:租約鎖 error → 該把 key 不打(記 lease_error),
    全部 key 都 error → 0 次 POST + degraded_reason 誠實說是租約鎖問題,
    不可以推給冷卻。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(G, "load_keys", lambda: ["KEY-BROKEN-" + "x" * 20])
    _break_lock_backend(monkeypatch)
    posts = []
    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **k: posts.append(1) or (_ for _ in ()).throw(RuntimeError("不該打到這")))
    mp3 = tmp_path / "x.mp3"
    mp3.write_bytes(b"\xff\xfb" + b"\x00" * 512)
    _, meta = G.call_gemini(mp3, "zh", 5, ignore_cooldown=False, verbose=False)
    assert posts == [], "🔴 租約鎖壞掉還是打了 API —— fail-open"
    assert [a.get("result") for a in meta["attempts"]] == ["lease_error"], \
        f"要留 lease_error 痕跡:{meta['attempts']}"
    assert meta["keys_tried"] == 0, "沒真的打出去就不可以計入 keys_tried"
    assert "租約鎖" in (meta["degraded_reason"] or ""), \
        f"🔴 原因要誠實說是租約鎖,不可推給冷卻:{meta['degraded_reason']!r}"


def test_工作鎖壞掉要硬擋不可裝沒事(tmp_path, monkeypatch):
    """🔴 Codex R9:job lock backend error → 舊版照樣進評測(fail-open),
    互斥保證直接取消。工作鎖跟金鑰租約方向不同:租約壞了可以換下一把 key,
    工作鎖沒有替代品 —— 沒有互斥就評,兩個工作的中間檔互相覆寫,
    分數錯得無聲無息。error 一律 SystemExit 硬擋。"""
    _break_lock_backend(monkeypatch)
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    with pytest.raises(SystemExit):
        with J._job_lock(song):
            pass


def test_拿到租約後要重讀冷卻不可沿用舊快照(tmp_path, monkeypatch):
    """🔴 Codex 探針(post_count=2):A、B 同讀「沒冷卻」→ A 拿租約、吃 429、
    寫入冷卻、釋放 → B 等到租約後**沿用等待前的舊快照**直接再打一發。
    真的走 call_gemini:進場快照是空的、磁碟上有冷卻 → 必須 0 次 POST。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(G, "load_keys", lambda: ["KEY-FRESH-" + "x" * 20])
    key = "KEY-FRESH-" + "x" * 20
    G.cool_down({}, key, 3600, "429(別的工作剛寫入)")      # 磁碟上有冷卻

    real_load = G.load_state
    snapshots = [{}]                     # call_gemini 進場的第一次 load_state 讀到舊快照
    monkeypatch.setattr(G, "load_state",
                        lambda: snapshots.pop(0) if snapshots else real_load())

    posts = []
    def no_post(*a, **k):
        posts.append(1)
        raise RuntimeError("不該打到這")
    monkeypatch.setattr(G.requests, "post", no_post)

    mp3 = tmp_path / "x.mp3"
    mp3.write_bytes(b"\xff\xfb" + b"\x00" * 512)             # 小假 mp3,夠編 base64 就好
    _, meta = G.call_gemini(mp3, "zh", 5, ignore_cooldown=False, verbose=False)
    assert posts == [], \
        "🔴 沿用等待前的舊快照,對著剛被限流的 key 又打了一發"
    assert any(a.get("result") == "skipped_cooldown_after_wait"
               for a in meta["attempts"]), f"要留痕:{meta['attempts']}"
    assert meta["keys_tried"] == 0, "沒真的打出去就不可以計入 keys_tried"


def test_clean_scores驗來源量尺範圍():
    """🔴 Codex 探針:SongEval Musicality=99 → 主控台印「平均 99.0 / 5」、
    正式柱分卻拒絕 1980 —— 兩邊互相矛盾。載入時就按來源量尺清掉越界值。"""
    notes = []
    out = J._clean_scores({"Musicality": 99.0, "Coherence": 4.2}, "SongEval",
                          notes, lo=0, hi=5)
    assert out == {"Coherence": 4.2}, f"🔴 越界值沒被清掉:{out}"
    assert any("超出量尺" in n for n in notes), "越界要留痕"


def test_深層欄位格式化不可以炸掉():
    """🔴 vocal_detail 混一個 score="N/A":報告寫完了,摘要 f"{v:.1f}" ValueError
    收場 → 批次/網頁版拒收這次昂貴評測。_fmt 對非法值回 None,呼叫端跳過那行。"""
    assert J._fmt(88.04) == "88.0"
    assert J._fmt("N/A") is None
    assert J._fmt(True) is None
    assert J._fmt(float("nan")) is None
    assert J._fmt(None) is None


def test_留言欄位不是字串時要當空字串():
    """🔴 Codex R9:Gemini dims 摘要對 comment 直接 .replace ——
    引擎異常吐 dict/None 時 AttributeError,報告寫完了摘要卻炸掉
    → 批次/網頁版拒收這次昂貴評測。非字串一律當空字串顯示。"""
    assert J._text_or_empty("好") == "好"
    assert J._text_or_empty(None) == ""
    assert J._text_or_empty({"x": 1}) == ""
    assert J._text_or_empty(3.5) == ""
    assert J._text_or_empty(True) == ""


def test_報告發布失敗要保住舊報告且不留暫存(tmp_path, monkeypatch):
    """🔴 兩個契約:(a) 發布失敗時舊報告保持完整(不是半截);
    (b) 成敗都不留 *.json.tmpPID(Codex:失敗會累積暫存檔)。
    模擬磁碟壞掉:寫檔寫到一半就炸。"""
    from pathlib import Path as _P
    out = tmp_path / "song_評審團.json"
    out.write_text(json.dumps({"舊報告": "完整"}), encoding="utf-8")

    real_write = _P.write_text
    def disk_full(self, data, *a, **k):
        with open(self, "w", encoding="utf-8") as f:
            f.write(data[:10])            # 寫一半
        raise OSError("disk full")
    monkeypatch.setattr(_P, "write_text", disk_full)

    with pytest.raises(OSError):
        J._write_report({"新報告": 1}, out)
    monkeypatch.setattr(_P, "write_text", real_write)

    assert json.loads(out.read_text(encoding="utf-8")) == {"舊報告": "完整"}, \
        "🔴 發布失敗把舊報告毀了(直接覆寫的症狀)"
    assert not list(tmp_path.glob("*.tmp*")), "🔴 失敗後留下暫存檔"
