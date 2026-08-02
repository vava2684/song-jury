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


# 產出端對一首 s64 單聲道歌真的會寫出來的 shape(⚠️ reason 與 shape 要互相成立,
# 見 test_降級原因要跟shape互相成立 —— 這個 fixture 就是「合法」的那一組)
_S64_SHAPE = {"sample_rate": "48000", "channels": "1", "channel_layout": "mono",
              "sample_fmt": "s64", "canonical": "", "canonical_speakers": "FC"}


def _declared(reason="unsupported_sample_fmt", contract=_GOOD_CONTRACT,
              shape=None, **more):
    st = {"status": "unavailable", "reason": reason, "generator_contract": contract}
    if shape is None:
        shape = dict(_S64_SHAPE)
    if shape:
        st["shape"] = shape
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


# ── 不可變快照(Codex R23-P1-1)────────────────────────────────────
def test_評分階段與來源身分只讀同一份不可變快照(tmp_path, monkeypatch):
    """🔴 R23-P1-1 實測:各階段各自開使用者給的那個路徑、身分又在最後才另算一次 ——
    評測進行中把檔案換掉,就會發布一份「分數來自 A、身分宣告 B」的報告,
    rc=0、strict 裁判也過。⛔ 那份報告的每個數字都可能是別首歌的。

    這條把兩個 stage runner 換成 stub(只記錄「這一刻讀到的內容」),
    在第一個階段結束後把原路徑換掉,再看所有階段與身分讀到的是不是同一份。"""
    import hashlib
    import os as _os
    import types as _types
    J = load("評審團")
    song = tmp_path / "甲.wav"
    song.write_bytes(b"RIFF-AAAA" * 64)
    other = tmp_path / "_乙.wav"
    other.write_bytes(b"RIFF-BBBB" * 64)
    first = hashlib.sha256(song.read_bytes()).hexdigest()
    seen, swapped = [], []

    def _rec(cmd):
        for a in cmd:
            s = str(a)
            if s.lower().endswith((".wav", ".mp3")) and Path(s).exists():
                seen.append(hashlib.sha256(Path(s).read_bytes()).hexdigest())
        if not swapped:                      # 第一個階段跑完 → 原路徑被換成另一首
            swapped.append(True)
            _os.replace(other, song)

    def _opt(cmd, label, **kw):
        _rec(cmd)
        return None, f"{label}:stub"

    def _run(cmd, cwd, label, env=None):
        _rec(cmd)
        if "--json" in cmd:
            Path(cmd[cmd.index("--json") + 1]).write_text("{}", encoding="utf-8")
        return _types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(J, "_optional_stage", _opt)
    monkeypatch.setattr(J, "_run_stage", _run)
    monkeypatch.setenv("SONG_JURY_SKIP_GEMINI", "1")
    with J._immutable_input(song) as snap:
        snap_dir = snap.parent
        # ⭐ 快照要**保留原檔名**:分軌快取的目錄名帶檔名前綴,改名會讓每次評測
        #    都重跑 Demucs(白燒 GPU)——那是很容易在重構時失手的地方。
        assert snap.name == "甲.wav", f"🔴 快照改了檔名:{snap.name}"
        assert snap != song and snap.read_bytes() == b"RIFF-AAAA" * 64
        try:
            J._evaluate(song, snap)
        except SystemExit:
            pass                              # 缺柱 → rc=2,報告照樣發布
    assert not snap_dir.exists(), "🔴 快照目錄沒清掉"

    assert swapped, "🔴 這次沒換到檔,等於沒驗到"
    assert seen, "🔴 沒有任何階段被記錄到,這條沒驗到東西"
    assert set(seen) == {first}, \
        f"🔴 有階段讀到換過的檔(讀到 {len(set(seen))} 種內容):{sorted(set(seen))}"
    d = json.loads((song.with_name("甲_評審團.json")).read_text(encoding="utf-8"))
    assert d["source_file_sha256"] == first, "🔴 身分算的是換過的檔,不是評測用的那份"


# ── 降級宣告的 reason↔shape 關聯(Codex R23-P1-2)──────────────────
_SHAPE_OK = {"sample_rate": "48000", "channels": "6", "channel_layout": "unknown",
             "sample_fmt": "s32", "canonical": "s32le", "canonical_speakers": ""}


@pytest.mark.parametrize("reason,shape,expect", [
    # 格式其實有支援 → 這是「漏寫 PCM」偽裝成刻意降級
    ("unsupported_sample_fmt",
     {**_SHAPE_OK, "channels": "2", "sample_fmt": "s16", "canonical": "s32le",
      "canonical_speakers": "FL+FR"}, "是支援的"),
    # 2 聲道講不出配置?1/2 聲道有產品規則,不可能講不出來
    ("unknown_multichannel_layout",
     {**_SHAPE_OK, "channels": "2", "sample_fmt": "s16", "canonical": "s32le",
      "canonical_speakers": "FL+FR"}, "channels"),
    # 沒有 ffprobe 哪來的 shape
    ("no_ffmpeg", {**_SHAPE_OK}, "不可能同時成立"),
    ("probe_failed", {**_SHAPE_OK}, "不可能同時成立"),
    # 說不出是哪個格式/配置算不出來 = 沒有證據
    ("unsupported_sample_fmt", {}, "一定要附 shape"),
    ("unknown_multichannel_layout", {**_SHAPE_OK, "canonical": ""}, "缺 canonical"),
    # decode_failed 的前提是前面每一關都過了
    ("decode_failed", {**_SHAPE_OK, "canonical_speakers": ""}, "配置問題"),
])
def test_降級原因要跟shape互相成立(tmp_path, reason, shape, expect):
    """🔴 Codex R23-P1-2 實測:只驗型別的話,四組自相矛盾的宣告都被正式批次收下 ——
    等於「受支援格式卻沒算 PCM」換個殼就過關。⛔ 裁判是獨立的,不能假設
    JSON 一定來自現在這版產出端。"""
    st = {"status": "unavailable", "reason": reason,
          "generator_contract": _GOOD_CONTRACT}
    if shape:
        st["shape"] = shape
    p = _report(tmp_path, source_audio_pcm_status=st)
    why = V.validate(p, require_contract=True, require_identity="declared")
    assert why and expect in why, f"🔴 矛盾的宣告被收下了({reason}):{why!r}"


@pytest.mark.parametrize("reason,shape", [
    ("no_ffmpeg", None),
    ("probe_failed", None),
    ("unsupported_sample_fmt",
     {"sample_rate": "48000", "channels": "1", "channel_layout": "mono",
      "sample_fmt": "s64", "canonical": "", "canonical_speakers": "FC"}),
    ("unknown_multichannel_layout", _SHAPE_OK),
    ("decode_failed", {**_SHAPE_OK, "channel_layout": "5.1",
                       "canonical_speakers": "FL+FR+FC+LFE+BL+BR"}),
])
def test_五種合法的降級宣告都要收(tmp_path, reason, shape):
    """⚠️ 對照組:關聯 schema 不可以嚴到把**產出端真的會寫出來的**組合擋掉 ——
    少了這幾條,上面那批可能只是「反正都會被擋」。"""
    st = {"status": "unavailable", "reason": reason,
          "generator_contract": _GOOD_CONTRACT}
    if shape:
        st["shape"] = shape
    p = _report(tmp_path, source_audio_pcm_status=st)
    assert V.validate(p, require_contract=True, require_identity="declared") == "", reason


def test_產出端寫出來的降級一定過得了自己的裁判(tmp_path):
    """⭐ 端對端:真的用產出端算一份 s64 的身分,直接餵給裁判 ——
    ⛔ 產出端與裁判各自維護一份規則,漂移了就是這條會紅。"""
    import shutil as _sh
    import subprocess as _sp
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("這台沒有 ffmpeg/ffprobe")
    J = load("評審團")
    s64 = tmp_path / "s.mka"
    _sp.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "aevalsrc=0.25*sin(1000*t):s=48000:d=0.2", "-c:a", "pcm_s64le",
             "-ac", "1", str(s64)], check=True, timeout=300)
    fields = J._identity_fields(s64)
    p = _report(tmp_path, **{k: v for k, v in fields.items() if k != "evaluation_id"})
    assert V.validate(p, require_contract=True, require_identity="declared") == "", \
        "🔴 產出端寫出來的降級宣告被自己的裁判擋下 —— 兩邊的規則漂移了"


# ── 互斥旗標(Codex R23-P2-1)──────────────────────────────────────
@pytest.mark.parametrize("order", [
    ("--require-identity", "--allow-declared-downgrade"),
    ("--allow-declared-downgrade", "--require-identity"),
])
def test_兩個互斥的身分旗標不可以同時給(tmp_path, order):
    """🔴 R23-P2-1:舊版只看有沒有 --allow-declared-downgrade,兩個一起給時
    **比較鬆的那個無聲勝出** —— 使用者以為要求了最強證據,其實收下了降級報告。"""
    p = _report(tmp_path, **_declared())
    rc, out = _cli(p, "--newer-than", str(time.time() - 60), "--require-contract", *order)
    assert rc == 1 and out.startswith("VERIFY_BAD") and "互斥" in out, out
