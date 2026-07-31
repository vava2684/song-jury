# -*- coding: utf-8 -*-
"""Gemini 曲評的解析:分數範圍、機器行鎖定、NA 判讀。

🔴 真實事故:模型回 `M1:99`(百分制)時沒有被擋下來,下游 ×10 換算成 0-100,
   最後真的變成 990/100。超出 0-10 不是「高分」,是模型沒照格式回答。
"""
import pytest
from conftest import load

G = load("Gemini曲評")

正常 = "===SCORES=== M1:8.0 M2:7.5 M3:8.5 M4:8.5 M5:8.0 M6:7.0 曲總分:7.9"


def test_正常0到10分數照收():
    r = G.parse_review(正常)
    assert r["scores"]["M1"] == 8.0
    assert r["reported_total"] == 7.9
    assert "out_of_range" not in r


def test_百分制輸出全部判成缺席並留痕():
    """🔴 這是 990/100 事故的重現案。"""
    r = G.parse_review("===SCORES=== M1:99 M2:88 M3:95 M4:90 M5:85 M6:80 曲總分:89")
    assert all(v is None for v in r["scores"].values())
    assert r["reported_total"] is None
    assert r["out_of_range"], "超範圍必須留痕,不可以靜靜吞掉"


def test_只有單一維度超範圍時只剔除那一項():
    r = G.parse_review("===SCORES=== M1:8.0 M2:150 M3:8.5 M4:8.5 M5:8.0 M6:7.0 曲總分:7.9")
    assert r["scores"]["M1"] == 8.0
    assert r["scores"]["M2"] is None
    assert r["out_of_range"] == ["M2=150.0"]


def test_邊界值0與10要收下():
    r = G.parse_review("===SCORES=== M1:0 M2:10 M3:5 M4:5 M5:5 M6:5 曲總分:5")
    assert r["scores"]["M1"] == 0.0 and r["scores"]["M2"] == 10.0
    assert "out_of_range" not in r


def test_超過10點0一點點也要擋():
    r = G.parse_review("===SCORES=== M1:10.1 M2:5 M3:5 M4:5 M5:5 M6:5 曲總分:5")
    assert r["scores"]["M1"] is None


def test_機器行優先鎖SCORES而不是散文裡的曲總分():
    """真實 bug:模型在 SCORES 之後又寫一句散文提到「曲總分」,
    舊碼取「最後一行含曲總分」→ 抓到散文那行,整排 M 分數變空。"""
    txt = (正常 + "\n\n總結:這首歌的曲總分我認為還可以再高一點。")
    r = G.parse_review(txt)
    assert r["scores"]["M3"] == 8.5, "不可以被後面那句散文洗掉"


def test_模型誠實說NA要跟沒回答分開():
    r = G.parse_review("===SCORES=== M1:NA M2:7.5 M3:8.5 M4:8.5 M5:8.0 M6:7.0 曲總分:7.9")
    assert r["scores"]["M1"] is None
    assert "M1" in r["na_dims"]


def test_沒給總分時退回維度平均並標明來源():
    r = G.parse_review("===SCORES=== M1:8.0 M2:8.0 M3:8.0 M4:8.0 M5:8.0 M6:8.0")
    assert r["reported_total"] == 8.0
    assert r["total_source"] == "mean_of_dims", "推算出來的總分必須標明,不可冒充模型給的"
