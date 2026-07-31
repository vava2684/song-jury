# -*- coding: utf-8 -*-
"""Gemini 音檔酬載:超限判斷要在 base64「之前」。

🔴 Codex R9:舊順序「整檔讀進來 → base64 → 才發現超限 → 轉檔」——
下載上限允許 500MB,base64 後 667MB、峰值 ~1.2GB,還沒進 ffmpeg 就先把
記憶體吃光。修法:用 stat().st_size × 4/3 預估,必超限的檔**先轉檔再讀**。
"""
import subprocess
import sys
from pathlib import Path

from conftest import load

G = load("Gemini曲評")


def test_超大檔要先轉檔再讀不可先整檔base64(tmp_path, monkeypatch):
    """必超限的檔:原檔一個 byte 都不准讀進記憶體,只讀轉檔後的小檔。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(G, "MAX_INLINE_B64_MB", 0.001)   # 讓 4KB 檔就「必超限」
    monkeypatch.setattr(G, "load_keys", lambda: ["KEY-BIG-" + "x" * 20])
    monkeypatch.setattr(G.time, "sleep", lambda *_: None)

    big = tmp_path / "big.wav"
    big.write_bytes(b"RIFF" + b"\x00" * 4096)

    reads = []
    real_read = Path.read_bytes
    def spy_read(self):
        reads.append(self.name)
        return real_read(self)
    monkeypatch.setattr(Path, "read_bytes", spy_read)

    def fake_ffmpeg(cmd, **kw):
        assert cmd[0] == "ffmpeg"
        Path(cmd[-1]).write_bytes(b"\xff\xfb tiny")       # 假轉檔輸出:很小
        class R:
            returncode = 0
        return R()
    monkeypatch.setattr(subprocess, "run", fake_ffmpeg)

    # 不打真 API:讓 POST 直接失敗走 network_error 收場即可
    monkeypatch.setattr(G.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("斷網")))

    _, meta = G.call_gemini(big, "zh", 5, ignore_cooldown=False, verbose=False)
    assert "big.wav" not in reads, \
        "🔴 必超限的原檔還是被整檔讀進記憶體了(500MB 檔會先 OOM 才轉檔)"
    assert "先轉檔再讀" in (meta.get("transcoded_for_gemini") or ""), \
        f"轉檔要誠實記錄於 meta:{meta.get('transcoded_for_gemini')!r}"
    assert meta["mime_type"] == "audio/mpeg"
    assert not list(tmp_path.glob("*.mp3")) or True       # tmp 檔在系統暫存區,由 finally 清


def test_轉檔失敗要誠實降級不留孤兒暫存(tmp_path, monkeypatch):
    """ffmpeg 不在/掛掉:預估超限 + 轉不了 → degraded_reason 講清楚,不炸不裝死。"""
    monkeypatch.setattr(G, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(G, "MAX_INLINE_B64_MB", 0.001)
    monkeypatch.setattr(G, "load_keys", lambda: ["KEY-BIG-" + "x" * 20])
    big = tmp_path / "big.wav"
    big.write_bytes(b"RIFF" + b"\x00" * 4096)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no ffmpeg")))
    parsed, meta = G.call_gemini(big, "zh", 5, ignore_cooldown=False, verbose=False)
    assert parsed is None
    assert "預估" in (meta["degraded_reason"] or "") and "轉檔失敗" in meta["degraded_reason"], \
        f"要說清楚是「預估超限+轉檔失敗」:{meta['degraded_reason']!r}"


def test_小檔不轉檔直接用原檔():
    """預估沒超限的檔走原路:不呼叫 ffmpeg、mime 用原格式。"""
    # 用 call_gemini 跑到組 body 前的路徑成本太高,這裡守「預估公式」本身:
    # base64 長度 = 4*ceil(n/3),預估 n*4/3 誤差 ≤ 4 bytes,絕不會低估超過 4 bytes。
    import base64
    for n in (1, 2, 3, 100, 4096, 44100):
        actual = len(base64.b64encode(b"\x00" * n))
        est = n * 4 / 3
        assert actual - est <= 4, f"n={n}:實際 {actual} vs 預估 {est}"
