# -*- coding: utf-8 -*-
"""報告轉PDF 的純邏輯關卡(不需要字型檔,CI 也跑得動)。

🔴 Codex R10 兩條:
· NFD 分解式韓文(整段 Jamo)不觸發韓文字型 → 轉檔 exit 0、零警告,
  渲染整行 □□□□(最糟的靜默失敗)。
· 圖片只限寬不限高 → 1×10000 畸形圖讓 doc.build() LayoutError,
  一張圖毀掉整份 PDF。
"""
import unicodedata

import pytest

pytest.importorskip("reportlab", reason="requirements-dev 有 reportlab,裝了就會跑")
from conftest import load

P = load("報告轉PDF")


def test_NFD分解式韓文也要觸發韓文字型():
    nfd = unicodedata.normalize("NFD", "한국어 노래 평가")
    # 前提自檢:NFD 後整段都是 Jamo,一個預組合音節都沒有(不然這條測試沒在測東西)
    assert not any("가" <= c <= "힣" for c in nfd)
    assert P._contains_hangul(nfd), \
        "🔴 Jamo 沒被當韓文 → 韓文字型不註冊、缺字檢查也漏掉 → 整行變 □"


def test_預組合韓文照樣觸發():
    assert P._contains_hangul("한국어")
    assert not P._contains_hangul("純中文報告")
    assert not P._contains_hangul("")
    assert not P._contains_hangul(None)


def test_圖片要同時限寬限高等比縮放():
    assert P._fit_image(1000, 500, 100, 100) == (100.0, 50.0)   # 寬邊頂到寬限
    w, h = P._fit_image(500, 1000, 100, 100)                     # 高邊頂到高限
    assert (w, h) == (50.0, 100.0), \
        f"🔴 高度沒被限制:{(w, h)} —— 直圖會爆出頁框,doc.build() LayoutError"


def test_畸形長寬比或壞尺寸要略過不可毀掉整份PDF():
    assert P._fit_image(1, 10000, 100, 100) is None      # Codex 探針:1×10000
    assert P._fit_image(10000, 1, 100, 100) is None
    assert P._fit_image(0, 100, 100, 100) is None
    assert P._fit_image(100, 0, 100, 100) is None
    assert P._fit_image("x", 100, 100, 100) is None
