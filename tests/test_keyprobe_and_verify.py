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

PILLARS = ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")


def _det(score=70.0):
    """一柱的完整明細 —— items/missing 都是**必填**(R15 起裁判不接受省略)。"""
    return {"score": score, "items": {"某細項": score}, "missing": []}


def _full_report(tmp_path, **overrides):
    pt = {
        "完整評測": True, "缺柱": [], "缺柱權重合計": 0.0,
        # ⚠️ 預設值要自洽:八柱全 70 → 加權合成就是 70.0。
        "曲側合成": 70.0,
        "柱分": {k: _det() for k in PILLARS},
        "曲側含柱": list(PILLARS),
    }
    top = {"scoring_contract": overrides.pop("_contract", "2026-07-25-v1")}
    pt.update(overrides)
    p = tmp_path / "x_評審團.json"
    top["pillar_totals"] = pt
    p.write_text(json.dumps(top, ensure_ascii=False), encoding="utf-8")
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
    pt_柱分 = {k: _det() for k in PILLARS}
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
    柱分 = {k: _det(0.0) for k in V.REQUIRED_PILLARS}
    why = V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=100.0))
    assert why != "" and "重算" in why, f"🔴 合成與柱分矛盾卻通過:{why!r}"


def test_合成正確就過(tmp_path):
    柱分 = {k: _det() for k in V.REQUIRED_PILLARS}
    assert V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=70.0)) == ""


def test_完整評測卻有缺柱權重要拒收(tmp_path):
    """🔴 完整=true、缺柱=[] 卻寫 缺柱權重合計=99.9 —— 完整性欄位自相矛盾。"""
    why = V.validate(_full_report(tmp_path, 缺柱權重合計=99.9))
    assert why != "" and "缺柱權重" in why, f"沒擋住:{why!r}"


@pytest.mark.parametrize("壞內層", [
    {"score": 70.0, "items": [], "missing": []},
    {"score": 70.0, "items": "junk", "missing": []},
    {"score": 70.0, "items": {}, "missing": "junk"},
    {"score": 70.0, "items": {}, "missing": [1, 2]},
    {"score": 70.0, "missing": []},              # 少 items(R15:必填)
    {"score": 70.0, "items": {}},                # 少 missing(R15:必填)
])
def test_柱的內層schema壞掉要拒收(tmp_path, 壞內層):
    柱分 = {k: _det() for k in V.REQUIRED_PILLARS}
    柱分["和聲"] = 壞內層
    why = V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=70.0))
    assert why != "" and "和聲" in why, f"沒擋住 {壞內層}:{why!r}"


def test_曲側含柱與八柱不一致要拒收(tmp_path):
    柱分 = {k: _det() for k in V.REQUIRED_PILLARS}
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


# ── Codex R15:必填欄位、契約版本、政策錯誤要有自己的退出碼 ─────────────

@pytest.mark.parametrize("拿掉", ["缺柱權重合計", "曲側含柱"])
def test_缺欄位一律要拒收(tmp_path, 拿掉):
    """🔴 Codex R15:`get(..., 0)` 把缺鍵偽造成合法 0;曲側含柱是 optional
    又用 sorted() → dict 會被 sorted 成 keys 而矇混、scalar 直接 TypeError。
    完整評測的欄位一律必填、強型別。"""
    p = _full_report(tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    d["pillar_totals"].pop(拿掉)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    why = V.validate(p)
    assert why != "" and 拿掉 in why, f"少了 {拿掉} 卻通過:{why!r}"


@pytest.mark.parametrize("壞值", [{"a": 1}, 7, "junk", ["人聲"], ["人聲"] * 8])
def test_曲側含柱型別與內容都要驗(tmp_path, 壞值):
    """dict 會被 sorted 成 keys、scalar 會 TypeError 崩掉 —— 都要變成穩定的拒收。"""
    why = V.validate(_full_report(tmp_path, 曲側含柱=壞值))
    assert why != "" and "曲側含柱" in why, f"沒擋住 {壞值!r}:{why!r}"


def test_合成差一個刻度也要抓到(tmp_path):
    """🔴 兩邊都是「一位小數柱分 × 固定權重 → round(,1)」,沒有 0.1 級的浮點
    不確定性 —— 0.15 容差等於放過一整個顯示刻度的錯(Codex R15)。"""
    柱分 = {k: _det() for k in V.REQUIRED_PILLARS}
    why = V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=70.1))
    assert why != "" and "重算" in why, f"差 0.1 沒抓到:{why!r}"
    assert V.validate(_full_report(tmp_path, 柱分=柱分, 曲側合成=70.0)) == ""


def test_不認得的計分契約要拒收(tmp_path):
    """⛔ 契約版本是「這份報告用哪把尺」的證據:不認得就不能替它背書
    (可能是新版契約→裁判要更新,也可能是竄改)。"""
    why = V.validate(_full_report(tmp_path, _contract="未來-v9"))
    assert why != "" and "契約" in why, f"沒擋住未知契約:{why!r}"


def test_舊格式沒有契約欄位仍可驗但要出聲(tmp_path, capsys):
    """舊報告(這個欄位 2026-08-01 才加)用預設契約驗 —— 但要在 stderr 講清楚
    它沒有版本證據,不能默默當成「就是預設契約」。"""
    p = _full_report(tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    d.pop("scoring_contract")
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    assert V.validate(p) == ""
    assert "scoring_contract" in capsys.readouterr().err


def test_政策錯誤要用獨立退出碼(tmp_path, monkeypatch):
    """🔴 Codex R15:PolicyError 回零 key 後被洗成 exit 4「沒有金鑰」,
    安裝器接著說「只有佔位字串?」—— 自動化分不出「安全設定壞了」與「沒填」。"""
    env = _env(tmp_path, f"GEMINI_API_KEYS={GOOD}")
    monkeypatch.setenv("SONG_JURY_DENY_KEY_SHA256", "壞掉的名單")
    rc = K.main(["金鑰驗證.py", str(env)])
    assert rc == 5, f"政策錯誤要回 5(拿到 {rc})"


def test_沒填金鑰仍然是4(tmp_path):
    env = _env(tmp_path, "GEMINI_API_KEYS=你的第一把金鑰")
    assert K.main(["金鑰驗證.py", str(env)]) == 4
