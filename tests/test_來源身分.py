# -*- coding: utf-8 -*-
"""來源身分的**降級政策**與 CLI 證據標籤(Codex R22-P2-1 / P2-2)。

🔴 R22 實測到的兩件事:
   · 產品對 s64 刻意 fail-closed(不發布會撞號的解碼身分)是對的,但
     `require_identity=True` 一律要求 PCM 雜湊 —— 於是「完成九柱評測的 s64 歌」
     連正式批次都進不去,而比較器還把原因說成「產出端沒有 ffmpeg,裝好重評」,
     叫人去做一件完全沒有用的事。
   · `--newer-than nan` 讓 mtime 比較永遠不成立 → 一天前的舊報告照樣被印上
     「本輪新產物」。那是**證據標籤**被繞過,不是顯示問題。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO, load

V = load("驗證報告")
P8 = ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")
_PT = {"完整評測": True, "缺柱": [], "缺柱權重合計": 0.0, "曲側合成": 70.0,
       "柱分": {k: {"score": 70.0, "items": {"x": 70.0}, "missing": []} for k in P8},
       "曲側含柱": list(P8)}
_GOOD_CONTRACT = V.PCM_CONTRACTS[0]


def _report(tmp_path, name="a", **extra):
    d = {"scoring_contract": "2026-07-25-v1", "pillar_totals": _PT,
         "evaluation_id": "a" * 32, "source_file_sha256": "b" * 64}
    d.update(extra)
    p = tmp_path / f"{name}_評審團.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def _declared(reason="unsupported_sample_fmt", contract=_GOOD_CONTRACT, **more):
    st = {"status": "unavailable", "reason": reason, "generator_contract": contract}
    st.update(more)
    return {"source_audio_pcm_status": st}


# ── 兩種政策要分開(⛔ 不可以用一個布林代表兩件事)─────────────────
def test_安裝證據不接受宣告降級(tmp_path):
    """demo 是 s16,這台裝好就一定算得出解碼身分 —— 降級在這裡就是沒裝好。"""
    p = _report(tmp_path, **_declared())
    why = V.validate(p, require_contract=True, require_identity="decoded")
    assert why and "降級" in why, f"🔴 安裝證據放行了降級報告:{why!r}"


def test_正式批次接受產出端明講的降級(tmp_path):
    """🔴 R22-P2-1:s64 之類的來源產品**刻意**不發布身分,不該因此
    連完整九柱的正式結果都不算數。"""
    p = _report(tmp_path, **_declared())
    why = V.validate(p, require_contract=True, require_identity="declared")
    assert why == "", f"🔴 正式批次拒收了合法的刻意降級:{why}"


def test_受支援格式卻漏寫PCM還是要擋(tmp_path):
    """⛔ 不可以修成 fail-open:「產出端迴歸忘了算」與「明講算不出來」
    是兩件事,前者永遠要擋。"""
    p = _report(tmp_path)                    # 什麼宣告都沒有
    for policy in ("decoded", "declared"):
        why = V.validate(p, require_contract=True, require_identity=policy)
        assert why, f"🔴 {policy} 放行了沒有任何身分證據的報告"


def test_舊版產出端不可以假裝成刻意降級(tmp_path):
    """generator_contract 不是認得的版本 → 那只是「舊的漏寫」,不是宣告。"""
    p = _report(tmp_path, **_declared(contract="pcm-v4/native-rate/channels/native-sample-fmt"))
    why = V.validate(p, require_contract=True, require_identity="declared")
    assert why, "🔴 舊版產出端的假宣告被當成合法降級"


@pytest.mark.parametrize("bad,expect", [
    ({"source_audio_pcm_status": "unavailable"}, "不是物件"),
    ({"source_audio_pcm_status": {"status": "ok"}}, "只能是"),
    (_declared(reason="因為我說了算"), "白名單"),
    (_declared(contract=""), "generator_contract"),
    (_declared(shape=["a"]), "shape"),
])
def test_降級宣告的schema要嚴格(tmp_path, bad, expect):
    """⛔「有欄位但是垃圾」比沒有更危險:下游會把它當成產品刻意降級的證據。"""
    p = _report(tmp_path, **bad)
    why = V.validate(p)          # 連相容模式都要擋(這是 schema,不是政策)
    assert expect in why, f"🔴 畸形宣告沒被擋下:{why!r}"


def test_有雜湊又說算不出來是自相矛盾(tmp_path):
    p = _report(tmp_path, source_audio_pcm_sha256="c" * 64,
                source_audio_pcm_contract=_GOOD_CONTRACT, **_declared())
    why = V.validate(p)
    assert "不可能同時成立" in why, f"🔴 矛盾的兩個欄位同時存在卻通過:{why!r}"


def test_政策參數打錯要當場爆掉(tmp_path):
    """⚠️ 拼錯的政策字串若被當成「不要求」,strict 會靜靜地失效。"""
    p = _report(tmp_path)
    with pytest.raises(ValueError):
        V.validate(p, require_identity="strict")     # 不存在的政策名


# ── CLI 的證據標籤(--newer-than)────────────────────────────────
def _cli(path, *args):
    r = subprocess.run([sys.executable, str(REPO / "驗證報告.py"), str(path), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300, cwd=str(REPO),
                       env={**os.environ, "PYTHONUTF8": "1"})
    return r.returncode, (r.stdout or "").strip()


@pytest.mark.parametrize("val", ["nan", "NaN", "inf", "-inf", "abc", ""])
def test_newer_than不是有限數字一律不可以蓋章(tmp_path, val):
    """🔴 Codex R22-P2-2:任何值與 NaN 比較都是 false → 舊報告照樣被說成
    「本輪新產物」。-inf 同理。⛔ 而且不可以裸 traceback:那是給機器讀的介面。"""
    p = _report(tmp_path, source_audio_pcm_sha256="c" * 64,
                source_audio_pcm_contract=_GOOD_CONTRACT)
    old = time.time() - 86400
    os.utime(p, (old, old))
    rc, out = _cli(p, "--newer-than", val, "--require-contract", "--require-identity")
    assert rc == 1, f"🔴 --newer-than {val!r} 竟然過了:{out}"
    assert out.startswith("VERIFY_BAD"), f"🔴 輸出不是契約化的訊息:{out!r}"
    assert "Traceback" not in out


def test_newer_than缺值也要講人話(tmp_path):
    p = _report(tmp_path)
    rc, out = _cli(p, "--newer-than")
    assert rc == 1 and out.startswith("VERIFY_BAD") and "少了值" in out, out


def test_真的舊報告要被擋下來(tmp_path):
    """⚠️ 對照組:門檻正常時本來就該擋 —— 少了這條,上面那批可能只是
    「反正都會 VERIFY_BAD」而不是真的驗到門檻。"""
    p = _report(tmp_path, source_audio_pcm_sha256="c" * 64,
                source_audio_pcm_contract=_GOOD_CONTRACT)
    old = time.time() - 86400
    os.utime(p, (old, old))
    rc, out = _cli(p, "--newer-than", str(time.time() + 300),
                   "--require-contract", "--require-identity")
    assert rc == 1 and "本輪新產物" in out
    # 而門檻在過去時要通過(證明擋下來的原因是 mtime,不是別的)
    rc2, out2 = _cli(p, "--newer-than", str(old - 10),
                     "--require-contract", "--require-identity")
    assert rc2 == 0 and out2.startswith("VERIFY_OK"), out2


def test_宣告降級的報告用CLI要標出來(tmp_path):
    """⛔ VERIFY_OK 不可以讓人以為身分證據是最強的那一級。"""
    p = _report(tmp_path, **_declared())
    rc, out = _cli(p, "--newer-than", str(time.time() - 60), "--require-contract",
                   "--allow-declared-downgrade")
    assert rc == 0, out
    assert "宣告降級" in out and "unsupported_sample_fmt" in out, out
    # 同一份報告在嚴格模式要被擋(兩個開關不是同義詞)
    rc2, out2 = _cli(p, "--newer-than", str(time.time() - 60), "--require-contract",
                     "--require-identity")
    assert rc2 == 1 and out2.startswith("VERIFY_BAD"), out2


def test_比較器要講真正的降級原因(tmp_path):
    """🔴 R22-P2-1:一律說「產出端沒有 ffmpeg;裝好重評即可升級」——
    但 s64 是產品刻意不發布,重裝 ffmpeg 一點用都沒有。"""
    C = load("比較")
    ps = []
    for i, name in enumerate("ab"):
        p = _report(tmp_path, name=name, **_declared())
        d = json.loads(p.read_text(encoding="utf-8"))
        d["evaluation_id"] = str(i) * 32
        d["source_file_sha256"] = str(i) * 64
        d["語言"] = "zh"
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        ps.append(p)
    note = C.compare_pk(ps, lang="zh")["source_identity"]
    assert note["level"] == "exact-file"
    assert "白名單" in note["note"], f"🔴 沒講出真正的原因:{note['note']}"
    assert "沒有 ffmpeg" not in note["note"], f"🔴 又叫人去裝 ffmpeg:{note['note']}"
