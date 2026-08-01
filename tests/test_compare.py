# -*- coding: utf-8 -*-
"""比較器(PK / 抽卡)的行為契約。

🔴 Codex R15:README 把 PK 與抽卡列為三種模式之二,repo 卻沒有任何比較程式、
schema 或公式 —— 同一批資料在不同對話裡可以合法地得出不同冠軍。
這支測的是「規則真的寫死在程式裡」,不是文件說說而已。
"""
import json

import pytest

from conftest import load

C = load("比較")
V = load("驗證報告")

PILLARS = V.REQUIRED_PILLARS


def _report(tmp_path, name, scores, contract="2026-07-25-v1"):
    """造一份會通過獨立裁判的完整報告(scores: 柱名→分數,或單一數字)。"""
    if isinstance(scores, (int, float)):
        scores = {k: float(scores) for k in PILLARS}
    w = V.CANON_PILLAR_W
    comp = round(sum(w[k] * scores[k] for k in PILLARS) / sum(w.values()), 1)
    pt = {
        "完整評測": True, "缺柱": [], "缺柱權重合計": 0.0, "曲側合成": comp,
        "柱分": {k: {"score": scores[k], "items": {"x": scores[k]}, "missing": []}
                 for k in PILLARS},
        "曲側含柱": list(PILLARS),
    }
    p = tmp_path / f"{name}_評審團.json"
    p.write_text(json.dumps({"scoring_contract": contract, "pillar_totals": pt},
                            ensure_ascii=False), encoding="utf-8")
    return p


def test_PK要指定語言(tmp_path):
    """⛔ 四把語言尺維度數與軸不可共量 —— 語言不能用猜的,必須明確宣告。"""
    a = _report(tmp_path, "甲", 70)
    b = _report(tmp_path, "乙", 80)
    with pytest.raises(C.CompareError):
        C.compare_pk([a, b], None)
    out = C.compare_pk([a, b], "zh")
    assert out["language"] == "zh" and out["n"] == 2


def test_PK排名照曲側合成且高分在前(tmp_path):
    a = _report(tmp_path, "低", 60)
    b = _report(tmp_path, "高", 85)
    out = C.compare_pk([a, b], "zh")
    assert [r["song"] for r in out["ranking"]] == ["高", "低"]
    assert out["ranking"][0]["rank"] == 1 and out["ranking"][1]["rank"] == 2


def test_差距很小要標並列不是硬排名次(tmp_path):
    """⚠️ 系統沒有重複量測的變異數,給不出真的信賴區間 ——
    所以用**保守的固定門檻**顯示並列,而且要在輸出裡講清楚它是什麼。"""
    a = _report(tmp_path, "甲", 70.0)
    b = _report(tmp_path, "乙", 70.3)
    out = C.compare_pk([a, b], "zh")
    assert out["ranking"][1]["tied_with_previous"] is True
    assert out["ranking"][1]["rank"] == 1, "並列就該同名次"
    assert "不是統計檢定" in out["note"]


def test_不同計分契約不可比(tmp_path, monkeypatch):
    """🔴 尺換了就不能比 —— 這正是 scoring_contract 存在的理由。

    ⚠️ 這裡要用**兩個都被認得**的契約,才真的測到比較器自己的檢查:
       用一個「不認得」的版本,獨立裁判會先擋下來(冗餘防線),
       比較器把檢查拔掉測試照樣過 = 裝飾品(變異驗證抓到過)。"""
    # ⚠️ conftest.load() 對每個測試檔各載入一份模組 —— V.CONTRACTS 與
    #    比較.py 內部 `from 驗證報告 import CONTRACTS` 拿到的**不是同一個物件**。
    #    要 patch 的是比較器自己看到的那份(還有它拿去驗的那份裁判)。
    fake = dict(C.CONTRACTS["2026-07-25-v1"])
    monkeypatch.setitem(C.CONTRACTS, "2027-01-01-v2", fake)
    monkeypatch.setitem(V.CONTRACTS, "2027-01-01-v2", fake)
    import 驗證報告 as _vr
    monkeypatch.setitem(_vr.CONTRACTS, "2027-01-01-v2", fake)
    a = _report(tmp_path, "甲", 70)
    b = _report(tmp_path, "乙", 80, contract="2027-01-01-v2")
    assert _vr.validate(a) == "" and _vr.validate(b) == "", "兩份都要能單獨通過裁判"
    with pytest.raises(C.CompareError):
        C.compare_pk([a, b], "zh")


def test_不完整的報告不可以進比較(tmp_path):
    """⛔ 比較器要先過獨立裁判:缺柱/schema 壞的報告一律拒絕,不做「盡量比一比」。"""
    a = _report(tmp_path, "甲", 70)
    bad = tmp_path / "壞_評審團.json"
    d = json.loads(a.read_text(encoding="utf-8"))
    d["pillar_totals"]["完整評測"] = False
    d["pillar_totals"]["缺柱"] = ["律動"]
    bad.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(C.CompareError):
        C.compare_pk([a, bad], "zh")


def test_抽卡要指定組別且比全部八柱(tmp_path):
    """🔴 舊規格只比物理/SongEval/Audiobox,還宣稱「只有這些會隨 take 變」——
    不同 take 的人聲、和聲、編曲、律動當然都會變。八柱全部都要看落差。"""
    s1 = {k: 70.0 for k in PILLARS}
    s2 = {k: 70.0 for k in PILLARS}
    s2["律動"] = 90.0            # 只有律動不同
    a = _report(tmp_path, "take1", s1)
    b = _report(tmp_path, "take2", s2)
    with pytest.raises(C.CompareError):
        C.compare_takes([a, b], None)
    out = C.compare_takes([a, b], "抽卡A")
    assert set(out["pillar_spread"]) == set(PILLARS), "八柱全部都要給落差"
    assert out["pillar_spread"]["律動"] == 20.0
    assert out["most_volatile_pillar"] == "律動"
    assert out["best_takes"] == ["take2"] and out["best_take_tie"] is False


def test_抽卡的最佳take有明確定義(tmp_path):
    """⛔ 舊規格說用「物理+美學綜合分最高」但從沒定義那個綜合分怎麼算 ——
    現在明確就是曲側合成(契約權重),沒有第二種解釋空間。"""
    a = _report(tmp_path, "t1", 60)
    b = _report(tmp_path, "t2", 75)
    c = _report(tmp_path, "t3", 68)
    out = C.compare_takes([a, b, c], "g1")
    assert out["best_takes"] == ["t2"] and out["best_take_tie"] is False
    assert out["best_composite"] == 75.0
    assert out["composite_spread"] == 15.0


def test_少於兩份不能比(tmp_path):
    a = _report(tmp_path, "甲", 70)
    with pytest.raises(C.CompareError):
        C.compare_pk([a], "zh")
    with pytest.raises(C.CompareError):
        C.compare_takes([a], "g")


def test_CLI退出碼_不能比時非零(tmp_path, capsys):
    a = _report(tmp_path, "甲", 70)
    assert C.main(["pk", str(a)]) == 2          # 只有一首 + 沒給語言
    assert C.main(["pk", "--lang", "zh", str(a), str(_report(tmp_path, "乙", 80))]) == 0


def test_輸出是合法JSON且不含NaN(tmp_path):
    a = _report(tmp_path, "甲", 70)
    b = _report(tmp_path, "乙", 80)
    out = C.compare_pk([a, b], "zh")
    # allow_nan=False 本身就是保證:有 NaN/Infinity 的話這行會拋 ValueError。
    # ⚠️ 不可以再用字串 grep "NaN" —— 輸出含 report_id(絕對路徑),
    #    而 pytest 的 tmp_path 目錄名就叫 test_..._不含NaN0(自己踩到)。
    text = json.dumps(out, ensure_ascii=False, allow_nan=False)
    assert json.loads(text)["compare_contract"] == "compare-v1"


# ── Codex R16:同名覆蓋、重複灌票、鏈式並列、同分、TOCTOU ──────────────

def test_不同資料夾的同名報告不可互相覆蓋(tmp_path):
    """🔴 Codex R16-1 實測:x/same 與 y/same 一起比,n=2 但 per_pillar 只剩一筆
    (高分那份被低分那份用同一個 key 蓋掉),pillar_winners 也認不出是誰。
    現在同名一律拒絕,而且逐柱表以不可碰撞的 report_id 為鍵。"""
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    a = _report(tmp_path / "x", "same", 60)
    b = _report(tmp_path / "y", "same", 80)
    with pytest.raises(C.CompareError) as ei:
        C.compare_pk([a, b], "zh")
    assert "同名" in str(ei.value)
    # 改名之後照樣要能比,而且逐柱表要能對回來源檔案
    b2 = _report(tmp_path / "y", "other", 80)
    out = C.compare_pk([a, b2], "zh")
    assert len(out["per_pillar"]["人聲"]) == 2, "兩份都要在,不可覆蓋"
    assert set(out["per_pillar"]["人聲"]) == {str(a.resolve()), str(b2.resolve())}


def test_同一份報告不可以重複上場(tmp_path):
    """🔴 Codex R16-2:compare_pk([a, a]) 被接受,n=2,A 對 A 也算合法 PK。

    ⚠️ 要驗**是哪一道防線攔的**:同名檢查也會擋下來,但它給的指示是
    「請把檔案改成不同名字再比」—— 對「同一份放兩次」的人那是**錯的指示**
    (改名字照樣是同一份)。只寫 `pytest.raises(CompareError)` 的版本
    被變異驗證證明抓不到這條迴歸(兩道防線互相掩護)。"""
    a = _report(tmp_path, "甲", 70)
    for call in (lambda: C.compare_pk([a, a], "zh"),
                 lambda: C.compare_takes([a, a], "g")):
        with pytest.raises(C.CompareError) as e:
            call()
        assert "自己跟自己" in str(e.value), \
            f"🔴 攔下來的是別道防線,訊息會把人帶錯方向:{e.value}"


def test_語言只認四把尺(tmp_path):
    """🔴 --lang 沒有 choices,任何字串都被接受 —— 「不同語言直接拒絕」就成了空話。
    ⚠️ 誠實邊界:報告本身沒有語言欄位,程式只能擋掉不存在的語言,
       無法證明這批歌真的是那個語言(輸出的 note 有寫)。"""
    a = _report(tmp_path, "甲", 70)
    b = _report(tmp_path, "乙", 80)
    with pytest.raises(C.CompareError):
        C.compare_pk([a, b], "definitely-not-a-language")
    assert "無法代為證明" in C.compare_pk([a, b], "zh")["note"]


def test_三首以上的並列不可以鏈式擴張(tmp_path):
    """🔴 Codex R16-3:100 / 99.2 / 98.4 在「跟前一名比」的規則下會全部 rank 1,
    但頭尾差 1.6 早就超過門檻 —— 並列關係不具傳遞性。
    改成與**該並列組最高分**比:前兩首並列第 1,第三首第 3。"""
    a = _report(tmp_path, "A", 100.0)
    b = _report(tmp_path, "B", 99.2)
    c = _report(tmp_path, "C", 98.4)
    out = C.compare_pk([a, b, c], "zh")
    ranks = {r["song"]: r["rank"] for r in out["ranking"]}
    assert ranks == {"A": 1, "B": 1, "C": 3}, f"鏈式擴張了:{ranks}"


def test_柱冠軍與最佳take同分時要全部列出(tmp_path):
    """🔴 Codex R16-4:同分時用單一 max,會依無關排序/輸入順序任選一個,
    把真正的平手偽裝成唯一冠軍。"""
    s1 = {k: 70.0 for k in PILLARS}
    s2 = {k: 70.0 for k in PILLARS}
    s1["律動"], s2["律動"] = 90.0, 50.0      # 人聲同分,只有律動不同
    a = _report(tmp_path, "甲", s1)
    b = _report(tmp_path, "乙", s2)
    out = C.compare_pk([a, b], "zh")
    assert out["pillar_winners"]["人聲"]["tie"] is True
    assert sorted(out["pillar_winners"]["人聲"]["songs"]) == ["乙", "甲"]
    assert out["pillar_winners"]["律動"] == {"songs": ["甲"], "tie": False}
    # 抽卡的 best_take 同理:完全同分時兩個都要列出來
    c = _report(tmp_path, "t1", 70)
    d = _report(tmp_path, "t2", 70)
    tk = C.compare_takes([c, d], "g")
    assert tk["best_take_tie"] is True and sorted(tk["best_takes"]) == ["t1", "t2"]


def test_驗過的內容就是排名用的內容_TOCTOU(tmp_path, monkeypatch):
    """🔴 Codex R16-6:舊版先 validate(path) 再 read_text() 第二次 ——
    兩次之間檔案被原子換掉,排名用的是**沒被驗過**的內容(人聲被改成 999)。
    只讀一次 bytes 就沒有這個窗口。"""
    a = _report(tmp_path, "甲", 70)
    b = _report(tmp_path, "乙", 80)
    evil = json.loads(a.read_text(encoding="utf-8"))
    evil["pillar_totals"]["柱分"]["人聲"]["score"] = 999.0

    real_validate = C.validate_data
    def swap_then_validate(raw, name="<memory>", require_contract=False):
        # 驗完之後、比較器要用之前,把磁碟上的檔案換成惡意版本
        a.write_text(json.dumps(evil, ensure_ascii=False), encoding="utf-8")
        return real_validate(raw, name, require_contract=require_contract)
    monkeypatch.setattr(C, "validate_data", swap_then_validate)

    out = C.compare_pk([a, b], "zh")
    scores = list(out["per_pillar"]["人聲"].values())
    assert 999.0 not in scores, "🔴 排名用到了沒被驗過的內容(TOCTOU)"


def test_舊格式報告不可以進比較(tmp_path):
    """🔴 Codex R16-5:legacy 在單檔裁判可過,但比較必須要有版本證據 ——
    不然 old+new 混比時,舊版的尺是用猜的。"""
    a = _report(tmp_path, "甲", 70)
    old = tmp_path / "舊_評審團.json"
    d = json.loads(_report(tmp_path, "乙", 80).read_text(encoding="utf-8"))
    d.pop("scoring_contract")
    old.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    import 驗證報告 as _vr
    assert _vr.validate(old) == "", "單檔檢視仍相容(這是刻意的)"
    with pytest.raises(C.CompareError) as ei:
        C.compare_pk([a, old], "zh")
    assert "scoring_contract" in str(ei.value)
