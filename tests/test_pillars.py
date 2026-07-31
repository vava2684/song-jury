# -*- coding: utf-8 -*-
"""九柱組裝:權重、缺項歸一化、缺柱完整性旗標、取值鍵名。

每條測試都對應一個**真的發生過**的事故,不是假想:
  · 取值鍵名寫錯 → Gemini 整關被靜默丟掉,還被重正規化蓋掉看不出來(2026-07)
  · 缺柱分數被印得像正常分數 → 不完整評測被當成可比較的成績(2026-07-31)
  · SongEval 缺席時 sum()/len() 除零 → 整份報告產不出來
"""
import pytest
from conftest import load

J = load("評審團")


# ── 權重 ────────────────────────────────────────────────────────────
def test_九柱權重表未被竄改():
    """⛔ 權重是十三席合議庭定的,改一格要單格重開辯論。這條是防止有人「順手調一下」。"""
    assert J.PILLAR_W == {
        "詞": 25.3, "人聲": 15.2, "和聲": 13.6, "結構編曲": 12.6, "聲學": 12.1,
        "旋律記憶": 6.1, "真實風格": 6.1, "整體": 5.1, "律動": 4.0,
    }


def test_權重加總是100點1且這是刻意的():
    """九柱加總 100.1 是各柱四捨五入的結果,不是 bug。
    這條測試的用途是:哪天有人「修好」成 100,會在這裡被擋下來要求先辯論。"""
    assert round(sum(J.PILLAR_W.values()), 4) == 100.1


def test_柱內細項權重加總為100():
    """柱內是 0-100 的配比;湊不滿或超過都代表柱內配比被改壞了。"""
    items = J.build_pillar_items({}, {}, {}, {}, {}, {}, {}, {})
    for pillar, rows in items.items():
        assert sum(w for _, w, _ in rows) == 100, f"{pillar} 柱內權重加總 != 100"


# ── 缺項與缺柱 ──────────────────────────────────────────────────────
def test_柱內缺項會重新歸一化():
    """兩項各佔 50,缺一項時剩下那項應該獨得整柱,而不是被當成 0 分拉低。"""
    items = {"和聲": [("A", 50, 80.0), ("B", 50, None)]}
    out = J.build_pillar_totals(items)
    assert out["柱分"]["和聲"]["score"] == 80.0
    assert out["柱分"]["和聲"]["missing"] == ["B"]


def test_柱內全缺時不除零且該柱無分():
    """曾經在 SongEval 缺席時 sum()/len() 直接 ZeroDivisionError,整份報告產不出來。"""
    items = {"和聲": [("A", 50, None), ("B", 50, None)]}
    out = J.build_pillar_totals(items)          # 不可以拋例外
    assert out["柱分"]["和聲"]["score"] is None


def test_全部柱都缺時曲側合成為None():
    out = J.build_pillar_totals({"律動": [("X", 100, None)]})
    assert out["曲側合成"] is None


def test_缺柱時完整評測必為False且列出缺柱():
    """⛔ 缺柱 = 換了一把尺。JSON 一定要帶得動這個事實,否則排行榜會照樣吃進去。"""
    items = J.build_pillar_items({}, {}, {}, {}, {}, {}, {}, {})   # 全空 = 全缺
    out = J.build_pillar_totals(items)
    assert out["完整評測"] is False
    assert set(out["缺柱"]) == set(J.PILLAR_W) - {"詞"}
    assert out["缺柱權重合計"] == pytest.approx(74.8, abs=0.05)
    assert "不可" in out["完整性警語"]


def test_九柱齊全時完整評測為True():
    items = {p: [("X", 100, 70.0)] for p in J.PILLAR_W if p != "詞"}
    out = J.build_pillar_totals(items)
    assert out["完整評測"] is True
    assert out["缺柱"] == []
    assert out["曲側合成"] == 70.0


def test_曲側合成是在場柱的加權平均():
    """人聲 15.2 拿 100、律動 4.0 拿 0,其餘缺 → 應為 15.2*100/(15.2+4.0)。"""
    items = {"人聲": [("X", 100, 100.0)], "律動": [("Y", 100, 0.0)]}
    out = J.build_pillar_totals(items)
    assert out["曲側合成"] == pytest.approx(15.2 * 100 / (15.2 + 4.0), abs=0.05)


# ── 取值鍵名(這是 Gemini 被靜默丟掉的那個 bug)──────────────────────
def test_Gemini總分取的是gemini_reported_total而不是total():
    """🔴 真實事故:舊碼取 gemini["total"],但引擎寫出的鍵是 gemini_reported_total
    → 整體柱的 Gemini 那一項永遠是 None,被重正規化蓋掉,報告上看不出來。"""
    gem = {"gemini_reported_total": {"raw_0to10": 7.9},
           "dimensions": {f"M{i}": {"score": 80.0} for i in range(1, 7)}}
    items = J.build_pillar_items({}, {}, {}, gem, {}, {}, {}, {})
    整體 = dict((n, v) for n, w, v in items["整體"])
    assert 整體["Gemini 總分"] == 79.0, "0-10 制要 ×10 換成 0-100"

    # 而放在錯誤的鍵名下,絕不可以被取到(否則等於預設值亂猜)
    items2 = J.build_pillar_items({}, {}, {}, {"total": 7.9}, {}, {}, {}, {})
    assert dict((n, v) for n, w, v in items2["整體"])["Gemini 總分"] is None


def test_Gemini總分是bool時不可以被洗成10分():
    """🔴 Codex R9:整體柱那行舊寫法 `raw * 10.0` —— True*10 == 10.0,
    bool 在 _ev/_evnum 防線之外的最後一條小路又被洗白一次。
    合法數字才縮放;非法原值原樣進柱,由中央閘門記進 invalid_numeric。"""
    gem = {"gemini_reported_total": {"raw_0to10": True}}
    items = J.build_pillar_items({}, {}, {}, gem, {}, {}, {}, {})
    v = dict((n, x) for n, w, x in items["整體"])["Gemini 總分"]
    assert v is True, f"🔴 True 被轉成 {v!r}(True*10==10 = bool 又洗白一次)"
    out = J.build_pillar_totals(items)
    assert "Gemini 總分" in out["柱分"]["整體"].get("invalid_numeric", {}), \
        "值不合法要留痕 invalid_numeric —— 不可以拿 10 分,也不可以裝成「沒跑到」"


def test_SongEval是1到5制要換算成0到100():
    items = J.build_pillar_items({}, {}, {}, {}, {"Memorability": 4.7}, {}, {}, {})
    assert dict((n, v) for n, w, v in items["旋律記憶"])["記憶點(SongEval)"] == pytest.approx(94.0)


def test_Audiobox是1到10制要換算成0到100():
    items = J.build_pillar_items({}, {}, {}, {}, {}, {"PQ": 8.43}, {}, {})
    assert dict((n, v) for n, w, v in items["聲學"])["製作品質(Audiobox)"] == pytest.approx(84.3)


def test_Audiobox為0時不可被誤判成缺席():
    """`(x or 0)*10` 這種寫法會讓 0 分變成 falsy;0 是有效分數,不是缺席。"""
    items = J.build_pillar_items({}, {}, {}, {}, {}, {"PQ": 0.0}, {}, {})
    assert dict((n, v) for n, w, v in items["聲學"])["製作品質(Audiobox)"] == 0.0


def test_演唱項可吃dict也可吃純數字():
    phys = {"vocal_detail": {"pitch": {"score": 88.0}, "range": 94.2}}
    items = J.build_pillar_items(phys, {}, {}, {}, {}, {}, {}, {})
    人聲 = dict((n, v) for n, w, v in items["人聲"])
    assert 人聲["音準"] == 88.0 and 人聲["音域"] == 94.2


def test_凍結項不在計分細項裡():
    """⛔ 演唱.rhythm 與 和聲.non_diatonic 是凍結項:照列不計分。
    它們若出現在 PILLAR_ITEMS,就是被偷偷復權了。"""
    items = J.build_pillar_items({}, {}, {}, {}, {}, {}, {}, {})
    names = [n for rows in items.values() for n, _, _ in rows]
    assert not any("節奏準度" in n or "rhythm" in n.lower() for n in names)
    assert not any("離調" in n or "non_diatonic" in n.lower() for n in names)
