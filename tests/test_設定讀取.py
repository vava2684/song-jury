# -*- coding: utf-8 -*-
"""環境變數當設定用時的解析 —— ⛔ 這支壞掉的代價是「設定 typo 被說成缺套件」。

🔴 Codex R18-3 實測(SONG_JURY_DEMUCS_PROBE_TIMEOUT):
   abc → 模組載入時 ValueError;nan → 傳到 subprocess.run(timeout=nan) 才炸;
   inf → OverflowError;0/-1 被當成「體檢逾時」。而這些未捕捉例外的 rc 都是 1,
   安裝器把 1 讀成「缺套件」→ 叫使用者去重裝幾 GB 的 requirements。
"""
import pytest

from conftest import load

S = load("設定讀取")
NAME = "SONG_JURY_TEST_SECONDS"


def test_沒設或空字串就是用預設():
    assert S.positive_finite(NAME, 900, env={}) == 900.0
    assert S.positive_finite(NAME, 900, env={NAME: "   "}) == 900.0


def test_正常數字照收():
    assert S.positive_finite(NAME, 900, env={NAME: "120"}) == 120.0
    assert S.positive_finite(NAME, 900, env={NAME: " 12.5 "}) == 12.5


def test_不是數字要當場講清楚():
    with pytest.raises(S.ConfigError) as e:
        S.positive_finite(NAME, 900, env={NAME: "abc"})
    assert NAME in str(e.value) and "秒數" in str(e.value)


@pytest.mark.parametrize("bad", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_NaN與Infinity不是秒數(bad):
    """⛔ 它們是合法的 float(),卻會在很後面的 subprocess/time 層才炸 ——
    錯誤訊息離現場十萬八千里,而且退出碼會被讀成別的意思。"""
    with pytest.raises(S.ConfigError):
        S.positive_finite(NAME, 900, env={NAME: bad})


@pytest.mark.parametrize("bad", ["0", "-1", "-0.5"])
def test_零與負數不是不限時間(bad):
    with pytest.raises(S.ConfigError) as e:
        S.positive_finite(NAME, 900, env={NAME: bad})
    assert "大於" in str(e.value)


def test_太大也要擋():
    """打錯位數(999999999 秒 ≈ 31 年)多半是手滑,不是真的想等那麼久。"""
    with pytest.raises(S.ConfigError):
        S.positive_finite(NAME, 900, env={NAME: "999999999"})
