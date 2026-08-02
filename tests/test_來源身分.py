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
import shutil
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
    # ⚠️ R24 起這條會更早被「canonical 必須等於該格式的值」抓到(訊息也更明確)
    ("unknown_multichannel_layout", {**_SHAPE_OK, "canonical": ""}, "對不上"),
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


# ── 快照的收尾(Codex R24-P1-1)────────────────────────────────────
def test_唯讀來源的快照也要刪得掉(tmp_path):
    """🔴 R24-P1-1 實測:copy2 會把**唯讀屬性**一起複製過來,rmtree 因此失敗,
    而 ignore_errors=True 把失敗整個吞掉 —— 一整份可能還沒公開的歌就留在 TEMP。
    (Windows 上「來源是唯讀」非常普通:雲端同步、備份還原、防寫的素材夾。)"""
    import os as _os
    import stat as _stat
    J = load("評審團")
    src = tmp_path / "唯讀.wav"
    src.write_bytes(b"RIFF" + b"x" * 300)
    _os.chmod(src, _stat.S_IREAD)
    try:
        with J._immutable_input(src) as snap:
            d = snap.parent
            assert snap.read_bytes() == src.read_bytes()
            # ⛔ 快照不該繼承唯讀:我們只需要 bytes 與檔名
            assert _os.access(snap, _os.W_OK), "🔴 快照是唯讀的 —— 收工時會刪不掉"
        assert not d.exists(), f"🔴 快照目錄沒刪掉:{d}"
    finally:
        _os.chmod(src, _stat.S_IWRITE)


def test_快照刪不掉時要大聲講而且退出碼要不一樣(tmp_path, monkeypatch, capsys):
    """⛔ 刪不掉不可以無聲帶過(那是一整份音訊留在 TEMP),也不可以沿用
    「一切正常」的退出碼 —— 只看退出碼的自動化永遠不會知道。"""
    import types as _types
    J = load("評審團")
    song = tmp_path / "甲.wav"
    song.write_bytes(b"RIFF" + b"y" * 300)
    # ⚠️ 這條會**故意**製造一個刪不掉的快照 → 一定要把暫存目錄導進 tmp_path,
    #    不然殘留就留在真的 TEMP 裡(自己踩到:測試自己變成殘留來源)。
    iso = tmp_path / "temp"
    iso.mkdir()
    # ⛔ 只設環境變數沒有用(自己踩到:真的 TEMP 裡留下 song-jury-src-*):
    #    tempfile 會**快取** gettempdir() 的結果,程序裡第一次用過之後就不再看
    #    環境變數了。要覆蓋現行程序,得直接改 tempfile.tempdir。
    monkeypatch.setattr(J.tempfile, "tempdir", str(iso))
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(iso))
    monkeypatch.setattr(J, "_force_rmtree", lambda d, **kw: str(d))   # 假裝刪不掉
    monkeypatch.setattr(J, "resolve_input", lambda *_a, **_k: song)
    monkeypatch.setattr(J, "_job_lock", lambda *_a, **_k: __import__("contextlib").nullcontext())
    monkeypatch.setattr(J, "_evaluate", lambda *a, **k: (_ for _ in ()).throw(SystemExit(0)))
    monkeypatch.setattr(J.sys, "argv", ["評審團.py", str(song)])
    with pytest.raises(SystemExit) as e:
        J.main()
    out = capsys.readouterr().out
    assert e.value.code == 4, f"🔴 快照沒收乾淨卻回 {e.value.code}(要 4)"
    assert "快照沒清乾淨" in out and "song-jury-src-" in out, out
    # 真的把那個目錄清掉(這條測試自己製造的殘留不可以留下;它在 tmp_path 裡,
    # 就算這裡漏了 pytest 也會收走 —— 兩層保險)
    for d in iso.glob("song-jury-src-*"):
        __import__("shutil").rmtree(d, ignore_errors=True)


def test_快照建不出來時要給人話不是traceback(tmp_path, monkeypatch):
    """⛔ 空間不足/權限/路徑太長是一般使用者會遇到的事;裸 traceback 幫不上忙。"""
    J = load("評審團")
    song = tmp_path / "甲.wav"
    song.write_bytes(b"RIFF")
    # ⚠️ 把暫存目錄導到自己的 tmp_path:去掃**全域** TEMP 會被別的程序(或上一輪
    #    重現)干擾 —— 那種測試會為了別人的垃圾亂紅,也可能因為別人清掉而假綠。
    iso = tmp_path / "temp"
    iso.mkdir()
    # ⛔ 只設環境變數沒有用(自己踩到:真的 TEMP 裡留下 song-jury-src-*):
    #    tempfile 會**快取** gettempdir() 的結果,程序裡第一次用過之後就不再看
    #    環境變數了。要覆蓋現行程序,得直接改 tempfile.tempdir。
    monkeypatch.setattr(J.tempfile, "tempdir", str(iso))
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(iso))

    def _boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(J.shutil, "copyfile", _boom)
    with pytest.raises(SystemExit) as e:
        with J._immutable_input(song):
            pass
    msg = str(e.value.code)
    assert "快照" in msg and "空間" in msg, f"🔴 訊息不是人話:{msg!r}"
    assert "Traceback" not in msg
    # 建立失敗時暫存目錄也要清掉
    left = sorted(x.name for x in iso.glob("song-jury-src-*"))
    assert left == [], f"🔴 建立失敗卻留下暫存目錄:{left}"


# ── 降級 shape 要完整且自洽(Codex R24-P1-2)──────────────────────
_FULL = {"sample_rate": "48000", "channels": "6", "channel_layout": "unknown",
         "sample_fmt": "s32", "canonical": "s32le", "canonical_speakers": ""}


@pytest.mark.parametrize("reason,shape,expect", [
    # 空 dict 不等於「沒有 shape」:探測不到就不可能寫得出來
    ("no_ffmpeg", {}, "不可能同時成立"),
    ("probe_failed", {}, "不可能同時成立"),
    # 只給一半的 shape = 殘缺的證據
    ("unsupported_sample_fmt", {"sample_fmt": "s64"}, "缺欄位"),
    ("decode_failed", {"sample_fmt": "s16", "canonical": "s32le",
                       "canonical_speakers": "FL+FR"}, "缺欄位"),
    # canonical 亂寫:它必須是**那個樣本格式**該有的值
    ("decode_failed", {**_FULL, "channel_layout": "5.1",
                       "canonical": "亂寫", "canonical_speakers": "FL+FR+FC+LFE+BL+BR"},
     "對不上"),
    # 把已知的 5.1 說成「講不出配置」
    ("unknown_multichannel_layout", {**_FULL, "channel_layout": "5.1"}, "對不上"),
    # 數值欄位要是正整數
    ("unknown_multichannel_layout", {**_FULL, "channels": "0"}, "正整數"),
    ("unknown_multichannel_layout", {**_FULL, "sample_rate": "很快"}, "正整數"),
])
def test_降級的shape要完整而且自洽(tmp_path, reason, shape, expect):
    """🔴 Codex R24-P1-2:五組殘缺/不可能的 shape 全被正式批次收下。
    ⛔ 裁判是獨立的 —— 不能假設 JSON 一定來自現在這版產出端。"""
    st = {"status": "unavailable", "reason": reason,
          "generator_contract": _GOOD_CONTRACT}
    if shape or shape == {}:
        st["shape"] = shape
    p = _report(tmp_path, source_audio_pcm_status=st)
    why = V.validate(p, require_contract=True, require_identity="declared")
    assert why and expect in why, f"🔴 {reason} + {shape} 被收下了:{why!r}"


def test_未來版本的宣告不可以被這一版的規則整份擋掉(tmp_path):
    """⭐ 與解碼雜湊的政策一致(Codex R24-P1-2):未知版本**不當證據**,
    但也不該讓整份報告變成不合法 —— 那會讓「升級產出端」變成破壞性事件。"""
    st = {"status": "unavailable", "reason": "decode_failed",
          "generator_contract": "pcm-v6/未來的標準面",
          "shape": {"sample_rate": "48000", "channels": "1", "channel_layout": "mono",
                    "sample_fmt": "s64", "canonical": "s64le",
                    "canonical_speakers": "FC"}}
    p = _report(tmp_path, source_audio_pcm_status=st)
    assert V.validate(p) == "", "🔴 未來版本的宣告在相容模式被整份擋掉"
    # 但它**不是**證據:正式批次仍要擋
    why = V.validate(p, require_contract=True, require_identity="declared")
    assert why, "🔴 未知版本的宣告被當成合法降級"


def test_裁判與產出端的兩張表不可以漂移():
    """⛔ 裁判**故意**維護自己的一份(要能單獨驗外來 JSON),所以更要有人盯著
    兩邊一致 —— 漂移了就是「產品算得出來、裁判說不合法」或反過來。"""
    J = load("評審團")
    assert V.CANONICAL_BY_FMT == J._CANONICAL_BY_FMT, "🔴 樣本格式表漂移了"
    assert V.SPEAKERS_BY_LAYOUT == J._SPEAKERS_BY_LAYOUT, "🔴 喇叭表漂移了"
    assert V.DEFAULT_SPEAKERS == J._DEFAULT_SPEAKERS, "🔴 1/2 聲道的產品規則漂移了"
    assert tuple(V.PCM_UNAVAILABLE_REASONS) == tuple(J.PCM_UNAVAILABLE_REASONS), \
        "🔴 降級原因白名單漂移了"
    assert V.PCM_CONTRACTS[0] == J.PCM_IDENTITY_CONTRACT, "🔴 契約字串漂移了"


# ── CLI 參數(Codex R24-P2-2)─────────────────────────────────────
@pytest.mark.parametrize("args", [
    ("--require-contract", "--require-identit"),      # 少打一個 y
    ("--require-contract", "--unknown-flag"),
    ("--require-contract", "--require-contract"),     # 重複
    ("--newer-than", "1", "--newer-than", "2"),       # 重複帶值
])
def test_裁判不可以靜靜忽略打錯的參數(tmp_path, args):
    """🔴 R24-P2-2 實測:`--require-identit` 會退成 rc=0 的相容驗證 ——
    只看退出碼的自動化把「參數打錯」當成功,以為自己要求了最強證據。"""
    p = _report(tmp_path, source_audio_pcm_sha256="c" * 64,
                source_audio_pcm_contract=_GOOD_CONTRACT)
    rc, out = _cli(p, *args)
    assert rc == 1 and out.startswith("VERIFY_BAD"), f"{args} → rc={rc} {out!r}"


# ── 退出碼 4 要貫穿下游(Codex R25-P1-1)──────────────────────────
def _rc4_stub(tmp_path, complete=True, broken=False):
    """一支假的 評審團.py:寫出報告(可控完整性)然後回 4。"""
    body = [
        "import json, pathlib, sys",
        "p = pathlib.Path(sys.argv[1])",
        f"P8 = {list(P8)!r}",
        ("pt = {'完整評測': %s, '缺柱': %s, '缺柱權重合計': 0.0, '曲側合成': 70.0,"
         # ⚠️ 缺柱要挑**非 Gemini** 的(和聲):local 契約本來就允許 Gemini 造成的
         #    缺柱(那是設計,不是 bug)——用律動當樣本會驗不到「缺柱要被擋」。
         % (complete, "[]" if complete else "['和聲']")),
        "      '柱分': {k: {'score': 70.0, 'items': {'x': 70.0}, 'missing': []} for k in P8},",
        "      '曲側含柱': P8}",
        "d = {'scoring_contract': '2026-07-25-v1', 'pillar_totals': pt,",
        "     'evaluation_id': 'a'*32, 'source_file_sha256': 'b'*64,",
        "     'source_audio_pcm_sha256': 'c'*64,",
        f"     'source_audio_pcm_contract': {_GOOD_CONTRACT!r}}}",
        "out = p.with_name(p.stem + '_評審團.json')",
        ("out.write_text('這不是 JSON', encoding='utf-8')" if broken
         else "out.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')"),
        "print(f'完整報告:{out}')",
        "print('⛔ 來源快照沒清乾淨:C:/Temp/song-jury-src-xxxx')",
        "sys.exit(4)",
    ]
    f = tmp_path / "評審團.py"
    f.write_text("\n".join(body) + "\n", encoding="utf-8")
    return f


@pytest.mark.parametrize("complete,broken,expect_ok", [
    (True, False, True),      # 合格完整報告 → 要收下(只是加警告)
    (False, False, False),    # 缺柱 → 照原本的完整性規則擋
    (True, True, False),      # 報告損壞 → 擋
])
def test_批次遇到退出碼4要繼續讀報告(tmp_path, monkeypatch, complete, broken, expect_ok):
    """🔴 Codex R25-P1-1 實測:4 =「報告已產出,但來源快照沒收乾淨」,
    舊版連 JSON 都不讀就整份丟掉 —— 一份跑了幾十分鐘的**有效**評測就這樣沒了。
    ⛔ 但 4 會蓋掉原本的 2,所以完整性一定要**讀報告內容**,不能因為
       「碼是 4 就當完整」(那會讓缺柱的結果混進批次表)。"""
    import types as _types
    B = load("批次評測")
    song = tmp_path / "甲.wav"
    song.write_bytes(b"RIFF")
    stub = _rc4_stub(tmp_path, complete=complete, broken=broken)
    real_run = B.run_tree

    def fake_run_tree(cmd, **kw):
        out = subprocess.run([sys.executable, str(stub), str(song)], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=300)
        return _types.SimpleNamespace(returncode=out.returncode, stdout=out.stdout,
                                      stderr=out.stderr)

    monkeypatch.setattr(B, "run_tree", fake_run_tree)
    monkeypatch.setattr(B, "VENV_PY", sys.executable)
    data, err = B.run_one(song)
    if expect_ok:
        assert data is not None and not err, f"🔴 rc=4 的**有效**報告被丟掉了:{err}"
    else:
        # ⚠️ 契約是「err 非空」= 這一首不進表(caller 據此跳過),
        #    不是「data 一定是 None」—— 別把測試綁在實作細節上。
        assert err, "🔴 缺柱/損壞的報告不該因為 rc=4 就被靜靜收下"
    _ = real_run


def test_完整驗證遇到退出碼4要驗報告但不可以說VERIFY_OK(tmp_path):
    """⛔ 舊版把 4 壓成 1 且完全不跑裁判 —— 於是「評測有效、只是清理失敗」
    這個新分類在安裝器眼裡跟「裝壞了」一模一樣(Codex R25-P1-1)。"""
    work = tmp_path / "w"
    work.mkdir()
    for f in ("完整驗證.py", "驗證報告.py", "子程序.py", "設定讀取.py"):
        shutil.copy(REPO / f, work / f)
    (work / "demo_mix.wav").write_bytes(b"RIFF0000")
    shutil.copy(_rc4_stub(tmp_path), work / "評審團.py")
    r = subprocess.run([sys.executable, "完整驗證.py"], cwd=str(work), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600,
                       env={**os.environ, "PYTHONUTF8": "1",
                            "SONG_JURY_VERIFY_TIMEOUT": "120"})
    assert r.returncode == 4, f"🔴 4 被壓成 {r.returncode}:\n{r.stdout[-500:]}"
    assert "VERIFY_OK" not in r.stdout, "🔴 快照沒清乾淨卻印了 VERIFY_OK"
    assert "VERIFY_DIRTY" in r.stdout, f"🔴 沒有講出「報告可用但殘留未清」:{r.stdout[-300:]}"


def test_同一個程序連跑兩次不可以繼承上一輪的快照殘留(tmp_path, monkeypatch):
    """🔴 Codex R25-P2-1:殘留清單是模組全域、又不會在 main() 開頭清空 ——
    被嵌入/測試/長跑服務重用時,上一輪的殘留會讓下一輪(其實乾淨)也回 4。"""
    import contextlib as _ctx
    J = load("評審團")
    song = tmp_path / "甲.wav"
    song.write_bytes(b"RIFF")
    iso = tmp_path / "temp"
    iso.mkdir()
    # ⛔ 只設環境變數沒有用(自己踩到:真的 TEMP 裡留下 song-jury-src-*):
    #    tempfile 會**快取** gettempdir() 的結果,程序裡第一次用過之後就不再看
    #    環境變數了。要覆蓋現行程序,得直接改 tempfile.tempdir。
    monkeypatch.setattr(J.tempfile, "tempdir", str(iso))
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(iso))
    monkeypatch.setattr(J, "resolve_input", lambda *_a, **_k: song)
    monkeypatch.setattr(J, "_job_lock", lambda *_a, **_k: _ctx.nullcontext())
    monkeypatch.setattr(J, "_evaluate", lambda *a, **k: (_ for _ in ()).throw(SystemExit(0)))
    monkeypatch.setattr(J.sys, "argv", ["評審團.py", str(song)])
    codes = []
    for attempt in range(2):
        # 第一輪假裝刪不掉,第二輪正常
        monkeypatch.setattr(J, "_force_rmtree",
                            (lambda d, **kw: str(d)) if attempt == 0 else J._force_rmtree.__wrapped__
                            if hasattr(J._force_rmtree, "__wrapped__") else _real_rmtree(J))
        with pytest.raises(SystemExit) as e:
            J.main()
        codes.append(e.value.code)
    assert codes == [4, 0], f"🔴 第二輪繼承了上一輪的殘留:{codes}"
    for d in iso.glob("song-jury-src-*"):
        shutil.rmtree(d, ignore_errors=True)


def _real_rmtree(J):
    """拿回沒被 monkeypatch 前的實作(給上面那條第二輪用)。"""
    import importlib.util as _u
    spec = _u.spec_from_file_location("評審團_原", REPO / "評審團.py")
    m = _u.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m._force_rmtree


# ── 樣本格式表也要有獨立 golden(Codex R25-P2-2)──────────────────
# ⛔ 「產出端 == 裁判」只擋得住單邊漂移:兩份一起改壞(或一起刪一列)照樣全綠。
#    這份 golden 是第三個獨立來源,少一列/多一列/改一列都要紅。
_GOLDEN_FMT = {
    "u8": "s32le", "u8p": "s32le", "s16": "s32le", "s16p": "s32le",
    "s32": "s32le", "s32p": "s32le",
    "s64": "", "s64p": "",
    "flt": "f64le", "fltp": "f64le", "dbl": "f64le", "dblp": "f64le",
}


def test_樣本格式表是鎖住的契約_整份都要對():
    """🔴 Codex R25-P2-2:我在隔離副本同時從產出端與裁判刪掉合法的 `u8 -> s32le`,
    118 條相關測試照樣全過 —— 典型的「兩份真理一起漂移」。"""
    J = load("評審團")
    for name, table in (("產出端", J._CANONICAL_BY_FMT), ("裁判", V.CANONICAL_BY_FMT)):
        missing = {k: v for k, v in _GOLDEN_FMT.items() if k not in table}
        extra = {k: v for k, v in table.items() if k not in _GOLDEN_FMT}
        changed = {k: (table[k], v) for k, v in _GOLDEN_FMT.items()
                   if k in table and table[k] != v}
        assert not missing, f"🔴 {name}的樣本格式表少了:{missing}"
        assert not extra, f"🔴 {name}的樣本格式表多了(身分定義變了,要升 contract):{extra}"
        assert not changed, f"🔴 {name}的樣本格式表被改過:{changed}"
    # s64 一定要在表裡而且對到空字串(那是「刻意不發布身分」的明確宣告,
    # 不是「忘了列」——後者會走 fallback,那正是 R21 修掉的碰撞來源)
    assert J._CANONICAL_BY_FMT["s64"] == "" and V.CANONICAL_BY_FMT["s64"] == ""
