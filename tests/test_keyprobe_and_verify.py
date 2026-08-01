# -*- coding: utf-8 -*-
"""金鑰驗證.py 與 驗證報告.py 的行為測試(安裝器的兩個裁判)。

🔴 Codex R12 四條:
· 只驗第一把 key:第一好第二壞=假陽性、第一壞第二好=假陰性;
· 429/網路/TLS 全被洗成成功 → 九柱齊全綠燈;
· -VerifyModels 只看 exit 0+檔案存在 → stub 寫個 {} 也被宣稱「完整評測=True」;
· 這些以前只有「關鍵字存在」的裝飾品測試 —— 這裡全部改成行為驗證。
"""
import json
import time

import pytest

from conftest import load

K = load("金鑰驗證")
V = load("驗證報告")

GOOD = "A" * 25
BAD = "B" * 25


def _env(tmp_path, content, encoding="utf-8"):
    p = tmp_path / ".env"
    p.write_text(content, encoding=encoding)
    return p


def _patch_probe(monkeypatch, mapping):
    monkeypatch.setattr(K, "probe_key", lambda k: mapping[k])


def test_第一把好第二把壞_要逐把驗且誠實列出(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path, f"GEMINI_API_KEYS={GOOD},{BAD}")
    _patch_probe(monkeypatch, {GOOD: ("verified", 200), BAD: ("invalid", 400)})
    rc = K.main(["金鑰驗證.py", str(env)])
    out = capsys.readouterr().out
    assert rc == 0, "至少一把有效 → 具備基本 Gemini 能力"
    assert "verified=1" in out and "invalid=1" in out and "total=2" in out, \
        f"🔴 沒有逐把驗(只驗第一把=假陽性):{out}"
    assert GOOD not in out and BAD not in out, "🔴 完整金鑰被印出來了"


def test_第一把壞第二把好_不可整組判死(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path, f"GEMINI_API_KEYS={BAD},{GOOD}")
    _patch_probe(monkeypatch, {BAD: ("invalid", 400), GOOD: ("verified", 200)})
    rc = K.main(["金鑰驗證.py", str(env)])
    assert rc == 0, "🔴 只驗第一把 → 有效的第二把被整組陪葬(假陰性)"
    assert "verified=1" in capsys.readouterr().out


def test_全部無效才判死(tmp_path, monkeypatch):
    env = _env(tmp_path, f"GEMINI_API_KEYS={GOOD},{BAD}")
    _patch_probe(monkeypatch, {GOOD: ("invalid", 401), BAD: ("invalid", 403)})
    assert K.main(["金鑰驗證.py", str(env)]) == 1


def test_全部429不可宣稱可用(tmp_path, monkeypatch):
    """🔴 Codex R12:429 被當「連不上,先當有」→ 九柱齊全綠燈。
    全部限流=現在就是不能用,回獨立碼 3(未能驗證),不给綠燈。"""
    env = _env(tmp_path, f"GEMINI_API_KEYS={GOOD},{BAD}")
    _patch_probe(monkeypatch, {GOOD: ("cooling", 429), BAD: ("cooling", 429)})
    assert K.main(["金鑰驗證.py", str(env)]) == 3


def test_網路錯誤是unknown不是verified(tmp_path, monkeypatch):
    env = _env(tmp_path, f"GEMINI_API_KEYS={GOOD}")
    _patch_probe(monkeypatch, {GOOD: ("unknown", None)})
    assert K.main(["金鑰驗證.py", str(env)]) == 3


def test_只有佔位字串等於沒金鑰(tmp_path):
    env = _env(tmp_path, "GEMINI_API_KEYS=你的第一把金鑰,short")
    assert K.main(["金鑰驗證.py", str(env)]) == 4


def test_BOM開頭的env也讀得到金鑰(tmp_path, monkeypatch):
    env = _env(tmp_path, f"﻿GEMINI_API_KEYS={GOOD}")
    _patch_probe(monkeypatch, {GOOD: ("verified", 200)})
    assert K.main(["金鑰驗證.py", str(env)]) == 0


def test_真網路分類器_HTTPError對照():
    """probe_key 的分類契約:不打真網路,只驗 HTTPError 碼的分派。"""
    import urllib.error
    import io as _io

    def _fake_open(code):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, _io.BytesIO(b""))
        return opener

    import urllib.request as _ur
    real = _ur.urlopen
    try:
        for code, want in ((400, "invalid"), (401, "invalid"), (403, "invalid"),
                           (429, "cooling"), (500, "unknown")):
            _ur.urlopen = _fake_open(code)
            got, gotcode = K.probe_key(GOOD)
            assert got == want and gotcode == code, f"HTTP{code} → {got},應為 {want}"
        # 非 HTTP 的例外(DNS/TLS/逾時)走 generic except → 必須是 unknown,不准洗成 verified
        def _neterr(req, timeout):
            raise urllib.error.URLError("dns down")
        _ur.urlopen = _neterr
        got, gotcode = K.probe_key(GOOD)
        assert (got, gotcode) == ("unknown", None), f"網路例外 → {got},應為 unknown"
    finally:
        _ur.urlopen = real


# ── 驗證報告.py ──────────────────────────────────────────────────────

def _full_report(tmp_path, **overrides):
    pt = {
        "完整評測": True, "缺柱": [], "缺柱權重合計": 0.0,
        "曲側合成": 77.7,
        "柱分": {k: {"score": 70.0} for k in
                 ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")},
    }
    pt.update(overrides)
    p = tmp_path / "x_評審團.json"
    p.write_text(json.dumps({"pillar_totals": pt}, ensure_ascii=False), encoding="utf-8")
    return p


def test_空JSON要被打回(tmp_path):
    """🔴 Codex R12 故障注入:stub 寫 `{}` + exit 0 → 安裝器宣稱「完整評測=True」。"""
    p = tmp_path / "x_評審團.json"
    p.write_text("{}", encoding="utf-8")
    assert V.validate(p) != "", "🔴 空 JSON 被當成完整評測 —— 最高等級假陽性"
    assert V.main(["驗證報告.py", str(p)]) == 1


def test_完整合格的報告才過(tmp_path):
    assert V.validate(_full_report(tmp_path)) == ""
    assert V.main(["驗證報告.py", str(_full_report(tmp_path))]) == 0


@pytest.mark.parametrize("壞法", [
    {"完整評測": False},
    {"缺柱": ["律動"]},
    {"曲側合成": float("nan")},
    {"曲側合成": True},
    {"曲側合成": 101},
    {"柱分": {"人聲": {}}},          # 八柱缺七
])
def test_各種殘缺都要被打回(tmp_path, 壞法):
    assert V.validate(_full_report(tmp_path, **壞法)) != "", f"沒擋住:{壞法}"


def test_舊檔不可冒充本輪新產物(tmp_path):
    p = _full_report(tmp_path)
    future = time.time() + 3600
    why = V.validate(p, newer_than=future)
    assert why != "" and "舊" in why, "🔴 舊報告被當成這輪 VerifyModels 的證據"
