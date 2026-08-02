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


def _report(tmp_path, name, scores, contract="2026-07-25-v1",
            audio_sha=None, eval_id=None, pcm_sha=None):
    """造一份會通過獨立裁判的完整報告(scores: 柱名→分數,或單一數字)。

    ⭐ 預設每首歌都有自己的來源身分(evaluation_id / source_audio_sha256)——
       那是新版產出端會寫的東西,比較器靠它擋複製改名(R17-3)。"""
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
    # ⚠️ 身分要跟**完整路徑**綁,不是檔名:x/same 與 y/same 是兩首不同的歌,
    #    用檔名當種子會讓它們共用身分,同名那道防線就永遠測不到(自己踩到)。
    seed = str(p.resolve())
    doc = {"scoring_contract": contract, "pillar_totals": pt,
           "source_file_sha256": audio_sha or (f"{abs(hash(seed)):064x}"[:64]),
           "source_audio_pcm_sha256": pcm_sha or (f"{abs(hash((seed, 'p'))):064x}"[:64]),
           "evaluation_id": eval_id or f"{abs(hash((seed, 'e'))):032x}"[:32]}
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
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
    # ⚠️ 身分要換掉:沿用甲的 evaluation_id 會被「複製改名」那道先攔下來,
    #    測到的就不是裁判(變異驗證抓到我這個錯)
    d["evaluation_id"] = "b" * 32
    d["source_file_sha256"], d["source_audio_pcm_sha256"] = "b" * 64, "d" * 64
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
    assert ei.value.code == "duplicate_label", f"要是同名那道攔的:{ei.value.code}"
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
    被變異驗證證明抓不到這條迴歸(兩道防線互相掩護)。
    ⭐ 但根因不可以綁在中文文案上(Codex R17-7):改寫或翻譯訊息會變成
       沒有行為迴歸的紅燈,久了就會有人為了讓測試過而不敢改字。用穩定的 code。"""
    a = _report(tmp_path, "甲", 70)
    for call in (lambda: C.compare_pk([a, a], "zh"),
                 lambda: C.compare_takes([a, a], "g")):
        with pytest.raises(C.CompareError) as e:
            call()
        assert e.value.code == "duplicate_input", \
            f"🔴 攔下來的是別道防線({e.value.code}),訊息會把人帶錯方向:{e.value}"


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


# ── 來源身分(Codex R17-3)────────────────────────────────────────────
def test_同一份報告複製改名不可以當成兩首(tmp_path):
    """🔴 Codex R17-3 實測:`copy takeA_評審團.json takeB_評審團.json` 之後
    compare_pk 收下 n=2 —— 同一次評測變兩票,PK 冠軍與抽卡結論建立在同一份資料上。
    inode 那道擋不到(複製出來是不同 inode),檔名又不是身分。
    ⚠️ 這多半不是攻擊,是整理檔案時的順手複製 —— 更需要程式擋。"""
    import shutil
    a = _report(tmp_path, "takeA", 70)
    b = tmp_path / "takeB_評審團.json"
    shutil.copyfile(a, b)
    for call in (lambda: C.compare_pk([a, b], "zh"),
                 lambda: C.compare_takes([a, b], "g")):
        with pytest.raises(C.CompareError) as e:
            call()
        assert e.value.code == "duplicate_source", f"🔴 被別道防線攔的:{e.value.code}"


def test_同一個音源的兩份報告不可以同場比(tmp_path):
    """重跑同一首歌會得到兩份**內容不同**(時間戳/隨機性)的報告 ——
    bytes 那層擋不住,但音檔 sha256 一樣就是同一個音源,不該互比。"""
    a = _report(tmp_path, "甲", 70, audio_sha="f" * 64, eval_id="1" * 32, pcm_sha="9" * 64)
    b = _report(tmp_path, "乙", 80, audio_sha="f" * 64, eval_id="2" * 32, pcm_sha="8" * 64)
    with pytest.raises(C.CompareError) as e:
        C.compare_pk([a, b], "zh")
    assert e.value.code == "duplicate_source"
    assert e.value.detail["field"] == "source_file_sha256"


def test_舊格式報告要誠實標示身分防線比較弱(tmp_path):
    """⛔ 不可以讓人以為舊報告也擋得住:沒有 evaluation_id / 音檔雜湊時,
    只剩「內容完全相同」那一層,重跑再改名就繞過去了。輸出要講明。"""
    a = _report(tmp_path, "舊甲", 70)
    b = _report(tmp_path, "舊乙", 80)
    for f in (a, b):
        d = json.loads(f.read_text(encoding="utf-8"))
        for k in ("source_file_sha256", "source_audio_pcm_sha256", "evaluation_id"):
            d.pop(k, None)
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    out = C.compare_pk([a, b], "zh")
    assert out["source_identity"]["level"] == "weak"
    assert "擋不住" in out["source_identity"]["note"]
    # 新版報告(含解碼後雜湊)才是最強的那一級
    c, d_ = _report(tmp_path, "新甲", 70), _report(tmp_path, "新乙", 80)
    assert C.compare_pk([c, d_], "zh")["source_identity"]["level"] == "decoded-audio"


def test_產出端真的會寫來源身分():
    """比較器的防線建立在產出端寫了身分 —— 那一段不可以只存在於比較器的想像裡。"""
    from conftest import REPO
    src = (REPO / "評審團.py").read_text(encoding="utf-8")
    # ⚠️ 值算不出來時**不寫欄位**(R19-2),所以是迴圈寫入 —— 驗的是
    #    「兩個雜湊都被算、而且只有非空才寫」這件事,不是某一行長什麼樣。
    assert '("source_file_sha256", _file_sha256(song))' in src
    assert '("source_audio_pcm_sha256", _pcm_sha256(song))' in src
    assert "        if val:" in src, "🔴 又變成無條件寫入了(空字串會被 schema 判畸形)"
    assert '"evaluation_id": uuid.uuid4().hex' in src


# ── 錯誤碼是對外契約(Codex R17-7)──────────────────────────────────
def test_每一種拒絕都有自己的機器碼(tmp_path):
    """⛔ 測試要驗「哪一道防線攔的」,但不可以綁在可編輯的中文文案上。
    ⚠️ 碼是契約:改字可以,改碼要當成破壞性變更。"""
    a = _report(tmp_path, "甲", 70)
    b = _report(tmp_path, "乙", 80)
    cases = [
        ("bad_language", lambda: C.compare_pk([a, b], None)),
        ("bad_language", lambda: C.compare_pk([a, b], "de")),
        ("too_few_reports", lambda: C.compare_pk([a], "zh")),
        ("missing_group", lambda: C.compare_takes([a, b], "")),
        ("duplicate_input", lambda: C.compare_pk([a, a], "zh")),
        ("unreadable_report", lambda: C.compare_pk([a, tmp_path / "沒有這份_評審團.json"], "zh")),
    ]
    for code, call in cases:
        with pytest.raises(C.CompareError) as e:
            call()
        assert e.value.code == code, f"🔴 期望 {code},拿到 {e.value.code}({e.value})"


def test_契約不同與不認得的契約要分得開(tmp_path):
    a = _report(tmp_path, "甲", 70)
    b = _report(tmp_path, "乙", 80, contract="2099-01-01-vX")
    with pytest.raises(C.CompareError) as e:
        C.compare_pk([a, b], "zh")
    assert e.value.code in ("contract_mismatch", "invalid_report")


# ── 身分欄位的 schema 與證據等級(Codex R18-2 / R18-4)────────────────
def test_畸形的身分值不可以讓比較器噴traceback(tmp_path, monkeypatch):
    """🔴 Codex R18-2 實測:evaluation_id 寫成 list 時裁判說合格,比較器進 set
    直接 `TypeError: unhashable type: 'list'` —— CLI 的 except CompareError 接不到,
    使用者拿到 traceback、自動化拿到不是契約的錯。

    ⚠️ 這裡**故意把裁判停用**:兩道防線都在時,裁判會先擋下來,
    比較器自己那道拿掉了也測不出來(互相掩護 = 裝飾品)。"""
    a = _report(tmp_path, "甲", 70)
    d = json.loads(a.read_text(encoding="utf-8"))
    d["evaluation_id"] = ["not", "hashable"]
    a.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    b = _report(tmp_path, "乙", 80)
    monkeypatch.setattr(C, "validate_data", lambda *a_, **k: "")   # 假裝裁判迴歸了
    with pytest.raises(C.CompareError) as e:
        C.compare_pk([a, b], "zh")
    assert e.value.code == "invalid_source_identity", e.value.code


def test_裁判自己就要擋掉畸形身分(tmp_path):
    """裁判是第一道:有身分欄位就必須合法,「有欄位但是垃圾」比沒有更危險 ——
    因為下游會把它當成證據。"""
    a = _report(tmp_path, "甲", 70)
    base = json.loads(a.read_text(encoding="utf-8"))
    for bad in ({"evaluation_id": ["x"]}, {"evaluation_id": "ev-A"},
                {"source_file_sha256": "x"}, {"source_file_sha256": "A" * 64},
                {"source_audio_pcm_sha256": 123}, {"evaluation_id": True}):
        d = {**base, **bad}
        raw = json.dumps(d, ensure_ascii=False).encode("utf-8")
        why = V.validate_data(raw, "t.json", require_contract=True)
        assert why, f"🔴 裁判放行了畸形身分:{bad}"
        assert "身分值" in why


def test_換個容器的同一段聲音不可以當兩首(tmp_path):
    """🔴 Codex R18-4 實測:同一個 wav 尾端加幾個 byte,檔案 sha256 就不同,
    但 ffmpeg 解碼後的 PCM 完全相同 —— 只靠檔案雜湊,重新封裝就能繞過。
    現在報告另外帶解碼後雜湊,這一層要擋得住。"""
    same_pcm = "c" * 64
    a = _report(tmp_path, "原檔", 70, audio_sha="a" * 64, pcm_sha=same_pcm)
    b = _report(tmp_path, "改殼", 80, audio_sha="b" * 64, pcm_sha=same_pcm)
    with pytest.raises(C.CompareError) as e:
        C.compare_pk([a, b], "zh")
    assert e.value.code == "duplicate_source"
    assert e.value.detail["field"] == "source_audio_pcm_sha256"


def test_證據等級要說清楚強到哪裡(tmp_path):
    """⛔ 「有欄位」不等於「同一首歌一定認得出來」:沒有解碼後雜湊時,
    換容器就會被當成兩個來源 —— 輸出必須誠實標成 exact-file 而不是最強等級。"""
    a, b = _report(tmp_path, "甲", 70), _report(tmp_path, "乙", 80)
    assert C.compare_pk([a, b], "zh")["source_identity"]["level"] == "decoded-audio"

    for f in (a, b):                      # 拿掉解碼後雜湊 → 降級
        d = json.loads(f.read_text(encoding="utf-8"))
        d.pop("source_audio_pcm_sha256")
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    out = C.compare_pk([a, b], "zh")
    assert out["source_identity"]["level"] == "exact-file"
    assert "換個容器" in out["source_identity"]["note"] or "metadata" in out["source_identity"]["note"]


def test_產出端要寫出解碼後的聲音身分():
    """比較器最強的那一層建立在產出端真的算了 PCM 雜湊。"""
    from conftest import REPO as R
    src = (R / "評審團.py").read_text(encoding="utf-8")
    assert '("source_audio_pcm_sha256", _pcm_sha256(song))' in src
    assert '("source_file_sha256", _file_sha256(song))' in src
    # 版本欄位也要跟著寫,否則日後換標準面時新舊雜湊會被硬比(R19-1)
    assert 'out["source_audio_pcm_contract"] = PCM_IDENTITY_CONTRACT' in src


# ── R19:身分欄位缺席/版本/strict ────────────────────────────────────
def test_算不出PCM時不可以寫空字串(tmp_path, monkeypatch):
    """🔴 Codex R19-2 實測:沒有 ffmpeg 時產出端寫 "",而身分 schema 規定
    「欄位存在就要是 64 hex」→ **整份報告變成不合法**,連 README 說的
    「降級成 exact-file」都走不到。缺席要用「不寫這個欄位」表示。"""
    J = load("評審團")
    monkeypatch.setattr(J.shutil, "which", lambda *a, **k: None)   # 假裝沒有 ffmpeg
    # ⚠️ 要呼叫**產品自己的組裝函式**,不可以在測試裡複製一份邏輯 ——
    #    那樣改壞產品碼測試照樣綠(變異驗證抓到我這個錯)
    fields = J._identity_fields(tmp_path / "沒這檔.wav")
    assert "source_audio_pcm_sha256" not in fields, f"🔴 算不出來卻還是寫了欄位:{fields}"
    assert "source_file_sha256" not in fields, f"🔴 讀不到檔也寫了欄位:{fields}"
    assert fields.get("evaluation_id"), "evaluation_id 一定要有"
    # 而「有欄位但空字串」的舊產物要被當成缺席(相容),不是畸形
    assert V.identity_problem({"source_audio_pcm_sha256": ""}) == ""


def test_安裝證據要求三個身分欄位都在(tmp_path):
    """⛔ 安裝本來就強制 ffmpeg;產出端若迴歸成不算 PCM,九柱照樣 VERIFY_OK,
    下游卻只剩最弱的證據 —— 所以 strict 模式三個都要(Codex R19-2)。"""
    full = {"scoring_contract": "2026-07-25-v1",
            "evaluation_id": "a" * 32, "source_file_sha256": "b" * 64,
            "source_audio_pcm_sha256": "c" * 64}
    base = json.loads(_report(tmp_path, "甲", 70).read_text(encoding="utf-8"))
    for drop in ("evaluation_id", "source_file_sha256", "source_audio_pcm_sha256"):
        d = {**base, **full}
        d.pop(drop)
        raw = json.dumps(d, ensure_ascii=False).encode("utf-8")
        why = V.validate_data(raw, "t.json", require_contract=True, require_identity=True)
        assert why and drop in why, f"🔴 strict 沒要求 {drop}:{why!r}"
    # 三個都在就要過
    raw = json.dumps({**base, **full}, ensure_ascii=False).encode("utf-8")
    assert V.validate_data(raw, "t.json", require_contract=True, require_identity=True) == ""


def test_不同PCM版本的雜湊不可以互比(tmp_path):
    """🔴 Codex R19-1:標準面換了就是換一把尺 —— 沒有版本欄位的話,
    日後改演算法時新舊報告會被當成同一種 identity 硬比。"""
    a = _report(tmp_path, "甲", 70, pcm_sha="e" * 64)
    b = _report(tmp_path, "乙", 80, pcm_sha="e" * 64)   # 同雜湊、不同版本
    for f, ver in ((a, "pcm-v2/native-rate/native-layout/s32le"), (b, "pcm-v9/未來版")):
        d = json.loads(f.read_text(encoding="utf-8"))
        d["source_audio_pcm_contract"] = ver
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    out = C.compare_pk([a, b], "zh")      # ⛔ 不可以被當成同源硬擋
    assert out["n"] == 2
    assert out["source_identity"]["level"] == "exact-file", out["source_identity"]
    assert "版本" in out["source_identity"]["note"]
