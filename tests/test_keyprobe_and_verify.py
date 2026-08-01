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
        # ⚠️ 預設值要自洽:八柱全 70 → 加權合成就是 70.0。
        #    (舊 fixture 隨手寫 77.7,新裁判會重算並正確地拒收它。)
        "曲側合成": 70.0,
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


@pytest.mark.parametrize("柱值", [None, {}, {"score": True}, {"score": 999},
                                   {"score": -1}, {"score": "80"}, {"score": None}])
def test_柱值畸形也要被打回(tmp_path, 柱值):
    """🔴 Codex R13 五連探針:柱名都在、柱值卻是 None/{}/true/999 —— 舊裁判全部 PASS。
    每一柱的 score 都要是非 bool、有限、0-100 的數字,才算「九柱真的算出來了」。
    (NaN 那個形態由 test_非標準JSON常數要被拒收 顧,它更早在解析層就被擋掉。)"""
    pt_柱分 = {k: {"score": 70.0} for k in
               ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")}
    pt_柱分["和聲"] = 柱值
    why = V.validate(_full_report(tmp_path, 柱分=pt_柱分))
    assert why != "", f"🔴 柱值 {柱值!r} 被當成有效柱分"
    assert "和聲" in why, f"要指出是哪一柱壞掉:{why}"


def test_非標準JSON常數要被拒收(tmp_path):
    """🔴 json.loads 預設吃 NaN/Infinity —— 那不是合法 JSON,別人的解析器會炸,
    NaN 混進柱分還會無聲汙染。裁判要用 parse_constant 直接拒收。"""
    p = tmp_path / "x_評審團.json"
    p.write_text('{"pillar_totals": {"完整評測": true, "缺柱": [], "曲側合成": NaN,'
                 ' "柱分": {}}}', encoding="utf-8")
    why = V.validate(p)
    assert why != "" and "JSON" in why, f"🔴 NaN 被吃進來了:{why!r}"


def test_舊檔不可冒充本輪新產物(tmp_path):
    p = _full_report(tmp_path)
    future = time.time() + 3600
    why = V.validate(p, newer_than=future)
    assert why != "" and "舊" in why, "🔴 舊報告被當成這輪 VerifyModels 的證據"


# ── Codex R14:裁判要驗「內部自洽」,不只是八個 score 各自像數字 ─────────

def test_八柱全0卻宣稱合成100要拒收(tmp_path):
    """🔴 Codex R14:裁判只驗「每柱 score 是數字」,產出端把合成算錯照樣蓋章。
    裁判要用**自己凍結的權重**重算,不信報告裡可被一起改壞的權重。"""
    柱分 = {k: {"score": 0.0} for k in V.REQUIRED_PILLARS}
    why = V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=100.0))
    assert why != "" and "重算" in why, f"🔴 合成與柱分矛盾卻通過:{why!r}"


def test_合成正確就過(tmp_path):
    柱分 = {k: {"score": 70.0} for k in V.REQUIRED_PILLARS}
    assert V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=70.0)) == ""


def test_完整評測卻有缺柱權重要拒收(tmp_path):
    """🔴 完整=true、缺柱=[] 卻寫 缺柱權重合計=99.9 —— 完整性欄位自相矛盾。"""
    why = V.validate(_full_report(tmp_path, 缺柱權重合計=99.9))
    assert why != "" and "缺柱權重" in why, f"沒擋住:{why!r}"


@pytest.mark.parametrize("壞內層", [
    {"score": 70.0, "items": []},
    {"score": 70.0, "items": "junk"},
    {"score": 70.0, "missing": "junk"},
    {"score": 70.0, "missing": [1, 2]},
])
def test_柱的內層schema壞掉要拒收(tmp_path, 壞內層):
    柱分 = {k: {"score": 70.0} for k in V.REQUIRED_PILLARS}
    柱分["和聲"] = 壞內層
    why = V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=70.0))
    assert why != "" and "和聲" in why, f"沒擋住 {壞內層}:{why!r}"


def test_曲側含柱與八柱不一致要拒收(tmp_path):
    柱分 = {k: {"score": 70.0} for k in V.REQUIRED_PILLARS}
    why = V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=70.0,
                                  曲側含柱=["人聲", "和聲"]))
    assert why != "" and "曲側含柱" in why


def test_裁判的凍結權重要跟評審團同步():
    """⚠️ 裁判故意凍一份自己的權重(不信報告)——但那份必須跟產出端一致,
    否則正常報告會被誤判。這條測試就是兩邊的同步鎖。"""
    J = load("評審團")
    for k, w in V.CANON_PILLAR_W.items():
        assert J.PILLAR_W[k] == w, f"{k} 權重不同步:裁判 {w} vs 評審團 {J.PILLAR_W[k]}"
    assert set(V.CANON_PILLAR_W) == set(J.PILLAR_W) - {"詞"}
