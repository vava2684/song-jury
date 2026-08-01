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
    assert out["best_take"] == "take2", "曲側合成較高的那個"


def test_抽卡的最佳take有明確定義(tmp_path):
    """⛔ 舊規格說用「物理+美學綜合分最高」但從沒定義那個綜合分怎麼算 ——
    現在明確就是曲側合成(契約權重),沒有第二種解釋空間。"""
    a = _report(tmp_path, "t1", 60)
    b = _report(tmp_path, "t2", 75)
    c = _report(tmp_path, "t3", 68)
    out = C.compare_takes([a, b, c], "g1")
    assert out["best_take"] == "t2"
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
    text = json.dumps(out, ensure_ascii=False, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text
    assert json.loads(text)["compare_contract"] == "compare-v1"
