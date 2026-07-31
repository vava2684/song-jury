# -*- coding: utf-8 -*-
"""分軌快取的身分驗證。

🔴 最高風險的真實缺陷:快取鍵只有「檔名 + 模型名」,而 _stems 是全域共用的。
   不同資料夾的兩首 song.mp3,第二首會直接讀到第一首的 Demucs 分軌 ——
   人聲、和聲、編曲全部算錯,**而且不會報錯**。
"""
import json
import sys
import types
from conftest import load, REPO

C = load("分軌快取")


def _mk(p, seed: bytes, size=200_000):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(seed * (size // len(seed) + 1))


def test_同名不同曲指紋不同(tmp_path):
    a, b = tmp_path / "a" / "song.wav", tmp_path / "b" / "song.wav"
    _mk(a, b"AAAA"); _mk(b, b"BBBB")
    assert C._source_ident(a)["fingerprint"] != C._source_ident(b)["fingerprint"]


def test_同曲換路徑指紋不變(tmp_path):
    """⛔ 刻意不用 mtime 當指紋:複製或重新下載會讓它改變,
    會無謂地讓正確的快取失效(她的 _stems 有 7.4GB,重跑要好幾小時)。"""
    a = tmp_path / "x" / "song.wav"
    _mk(a, b"SAME")
    b = tmp_path / "y" / "song.wav"
    b.parent.mkdir(parents=True)
    b.write_bytes(a.read_bytes())
    import os, time
    os.utime(b, (time.time() - 99999, time.time() - 99999))   # 故意改 mtime
    assert C._source_ident(a)["fingerprint"] == C._source_ident(b)["fingerprint"]


def test_只改中段內容也要測得出來(tmp_path):
    """指紋取頭尾各 1MB + 大小;小檔案(<2MB)會整份讀入,所以中段改動也抓得到。"""
    a = tmp_path / "song.wav"
    _mk(a, b"ABCD")
    f1 = C._source_ident(a)["fingerprint"]
    data = bytearray(a.read_bytes()); data[1000:1010] = b"\x00" * 10
    a.write_bytes(bytes(data))
    assert C._source_ident(a)["fingerprint"] != f1


class _T:
    """假張量:支援 separate() 用到的 mean/std/切片/[None]/.to()/.cpu()/.numpy()。
    ⚠️ 這裡刻意不用真的 torch —— CI 不該為了測快取邏輯去裝 2.5GB 的 torch。"""
    def __init__(self, arr): self.a = arr
    def mean(self, *a, **k): return _T(self.a.mean(*a, **k))
    def std(self, *a, **k): return float(self.a.std())
    def __sub__(self, o): return _T(self.a - (o.a if isinstance(o, _T) else o))
    def __truediv__(self, o): return _T(self.a / (o.a if isinstance(o, _T) else o))
    def __mul__(self, o): return _T(self.a * (o.a if isinstance(o, _T) else o))
    def __add__(self, o): return _T(self.a + (o.a if isinstance(o, _T) else o))
    def __getitem__(self, i): return _T(self.a[i]) if i is not None else _T(self.a[None])
    def to(self, *a, **k): return self
    def cpu(self): return self
    def numpy(self): return self.a


def _fake_torch(monkeypatch):
    """把 separate() 需要的重相依全部換成假的,讓快取邏輯能在 CI 上單獨測。"""
    import contextlib
    import numpy as np
    from pathlib import Path as _P

    class _FakeModel:
        sources = ["drums", "bass", "other", "vocals"]
        samplerate = 44100
        audio_channels = 2
        def to(self, *_): return self
        def eval(self): return self

    def _save(path, arr, sr):
        _P(path).parent.mkdir(parents=True, exist_ok=True)
        _P(path).write_bytes(b"flac")

    def _loadf(path):
        return _T(np.zeros((2, 10), dtype="float32")), 44100

    torch = types.ModuleType("torch")
    torch.no_grad = contextlib.nullcontext
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    ta = types.ModuleType("torchaudio"); ta.save, ta.load = _save, _loadf
    pre = types.ModuleType("demucs.pretrained"); pre.get_model = lambda n: _FakeModel()
    ap = types.ModuleType("demucs.apply")
    ap.apply_model = lambda m, w, **k: [_T(np.zeros((4, 2, 10), dtype="float32"))]
    aud = types.ModuleType("demucs.audio")

    class _AF:
        def __init__(self, p): pass
        def read(self, **k): return _T(np.zeros((2, 10), dtype="float32"))
    aud.AudioFile = _AF
    dem = types.ModuleType("demucs")
    for name, mod in [("torch", torch), ("torchaudio", ta), ("demucs", dem),
                      ("demucs.pretrained", pre), ("demucs.apply", ap), ("demucs.audio", aud)]:
        monkeypatch.setitem(sys.modules, name, mod)


def test_撞名時不會讀到另一首歌的分軌(monkeypatch, tmp_path):
    """🔴 核心迴歸:兩首同名不同曲,共用同一個 _stems,第二首必須另建快取。"""
    _fake_torch(monkeypatch)
    stems = tmp_path / "_stems"
    a, b = tmp_path / "a" / "song.wav", tmp_path / "b" / "song.wav"
    _mk(a, b"AAAA"); _mk(b, b"BBBB")

    _, _, _, cached_a = C.separate(a, stems, "htdemucs_6s")
    assert cached_a is False, "第一次應該是現場分軌"

    _, _, _, cached_b = C.separate(b, stems, "htdemucs_6s")
    assert cached_b is False, "🔴 不同曲卻讀到快取 = 分數會全錯"

    dirs = sorted(p.name for p in stems.iterdir() if p.is_dir())
    assert len(dirs) == 2, f"兩首同名不同曲應各有一份快取,實際:{dirs}"


def test_同一首歌第二次會命中快取(monkeypatch, tmp_path):
    """身分驗證不可以矯枉過正 —— 同一首歌重跑必須讀到快取,否則 GPU 成本翻倍。"""
    _fake_torch(monkeypatch)
    stems = tmp_path / "_stems"
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    assert C.separate(a, stems, "htdemucs_6s")[3] is False
    assert C.separate(a, stems, "htdemucs_6s")[3] is True


def test_快取夾會寫下來源身分(monkeypatch, tmp_path):
    _fake_torch(monkeypatch)
    stems = tmp_path / "_stems"
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    C.separate(a, stems, "htdemucs_6s")
    side = next(stems.glob("*/_source.json"))
    rec = json.loads(side.read_text(encoding="utf-8"))
    assert rec["fingerprint"] == C._source_ident(a)["fingerprint"]
    assert rec["name"] == "song.wav"
