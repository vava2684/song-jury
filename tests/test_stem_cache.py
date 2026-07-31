# -*- coding: utf-8 -*-
"""分軌快取的身分驗證。

🔴 最高風險的真實缺陷:快取鍵只有「檔名 + 模型名」,而 _stems 是全域共用的。
   不同資料夾的兩首 song.mp3,第二首會直接讀到第一首的 Demucs 分軌 ——
   人聲、和聲、編曲全部算錯,**而且不會報錯**。
"""
import json
import pytest
from pathlib import Path
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


def test_大檔只改中段也要測得出來(tmp_path):
    """🔴 這條原本是**裝飾品**:名字說驗中段,測資卻只有 200KB ——
    舊版指紋取「大小 + 頭尾各 1MB」,200KB 整份都落在頭部取樣範圍內,
    等於根本沒測到中段。用 3MB 才驗得到真實大音檔的情境(Codex 抓到的)。
    現在指紋是整檔 SHA-256,任何一個位元組變動都測得出來。"""
    a = tmp_path / "song.wav"
    _mk(a, b"ABCD", size=3 * 1024 * 1024)          # 3MB:頭 1MB + 中 1MB + 尾 1MB
    f1 = C._source_ident(a)["fingerprint"]
    data = bytearray(a.read_bytes())
    mid = len(data) // 2                            # 正中央,離頭尾各 1.5MB
    data[mid:mid + 10] = b"\x00" * 10
    a.write_bytes(bytes(data))
    assert C._source_ident(a)["fingerprint"] != f1, \
        "🔴 大檔中段改動測不出來 → 兩首大小相同、頭尾相同的歌會共用分軌,分數全錯"


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


def test_快取夾名用完整指紋而不是前幾碼(tmp_path):
    """🔴 前 8 碼只有 32 位元,生日碰撞期望約 65,536 個檔案 ——
    Codex 實測 70,698 次就撞到,第二首被判 from_cache 讀到第一首的分軌。"""
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    fp = C._source_ident(a)["fingerprint"]
    name = C._cache_name(a, "htdemucs_6s", fp)
    assert name.endswith(fp), "快取夾名必須帶完整 SHA-256,不可以截短"
    assert len(fp) == 64


def test_命中快取一定要驗完整身分(tmp_path):
    """⛔ 只信資料夾名不夠:名字可能碰撞、快取也可能被人手動搬動。"""
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    fp = C._source_ident(a)["fingerprint"]
    srcs = ["drums", "bass", "other", "vocals"]
    d = tmp_path / "_stems" / C._cache_name(a, "htdemucs_6s", fp)
    d.mkdir(parents=True)
    for s in srcs:
        (d / f"{s}.flac").write_bytes(b"x")

    (d / "_source.json").write_text(json.dumps({"fingerprint": "別首歌"}), encoding="utf-8")
    assert not C._cache_is_valid(d, srcs, fp), "🔴 身分不符卻判有效 → 會讀到別首歌的分軌"

    (d / "_source.json").unlink()
    assert not C._cache_is_valid(d, srcs, fp), "沒有身分紀錄也不可以判有效"

    (d / "_source.json").write_text(json.dumps({"fingerprint": fp}), encoding="utf-8")
    assert C._cache_is_valid(d, srcs, fp), "身分相符時應該可以用"


def test_無身分的舊快取預設不採信(monkeypatch, tmp_path):
    """⛔ 沒有身分紀錄的舊快取可能是另一首同名歌的分軌。自動蓋章成本首身分的話,
    錯的分軌會變成「正確快取」,之後所有分數都錯而且再也查不出來。
    預設重新分軌;要沿用必須用環境變數明確授權。"""
    _fake_torch(monkeypatch)
    stems = tmp_path / "_stems"
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    legacy = stems / "song__htdemucs_6s"        # 舊版共用名,沒有 _source.json
    legacy.mkdir(parents=True)
    for s in ["drums", "bass", "other", "vocals"]:
        (legacy / f"{s}.flac").write_bytes(b"x")

    assert C.separate(a, stems, "htdemucs_6s")[3] is False, \
        "🔴 無身分的舊快取被自動採信了"
    assert not (legacy / "_source.json").exists(), \
        "🔴 不可以把本首的身分蓋章到來源不明的舊快取上"


def test_合法舊快取與解析路徑必須一致(monkeypatch, tmp_path):
    """🔴 真實迴歸(第二次):separate() 接受了身分相符的舊快取,
    但 cache_dir_of() 卻回傳不存在的新 SHA 路徑 → 編曲層次拿不到 vocals.flac,
    **人聲柱又一次靜靜消失**。兩邊的採用判斷必須一致。"""
    _fake_torch(monkeypatch)
    stems = tmp_path / "_stems"
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    fp = C._source_ident(a)["fingerprint"]
    srcs = ["drums", "bass", "other", "vocals"]

    legacy = stems / "song__htdemucs_6s"          # 舊名,但身分正確
    legacy.mkdir(parents=True)
    for s in srcs:
        (legacy / f"{s}.flac").write_bytes(b"x")
    (legacy / "_source.json").write_text(json.dumps({"fingerprint": fp}), encoding="utf-8")

    # ① 正常情況:合法舊快取會被**搬到**新路徑,之後只有一個位置
    _, _, _, cached = C.separate(a, stems, "htdemucs_6s")
    assert cached is True, "身分相符的舊快取應該被採用,不該重跑 Demucs"
    d = C.cache_dir_of(a, stems, "htdemucs_6s")
    assert d.exists(), f"🔴 cache_dir_of 指到不存在的路徑 {d.name} → vocal_stem 會變 None"
    assert (d / "vocals.flac").exists(), "下游要的 vocals.flac 不在解析出來的位置"
    assert not legacy.exists(), "搬過去之後舊路徑不該還在(留著就是兩個位置,規則又會漂移)"


def test_舊快取搬不動時解析路徑仍要對得上(monkeypatch, tmp_path):
    """🔴 搬家可能失敗(權限、跨磁碟)。那時 separate() 會原地沿用舊路徑 ——
    cache_dir_of() 必須跟著回傳同一個位置,否則又是「兩份規則漂移」那個老問題。

    ⚠️ 這條是 fallback 路徑:上一條測試蓋不到它(正常情況搬家會成功),
       所以少了這條,把 cache_dir_of 的 legacy 判斷拿掉也不會被抓到(變異驗證證明過)。"""
    _fake_torch(monkeypatch)
    import os as _os
    stems = tmp_path / "_stems"
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    fp = C._source_ident(a)["fingerprint"]
    srcs = ["drums", "bass", "other", "vocals"]

    legacy = stems / "song__htdemucs_6s"
    legacy.mkdir(parents=True)
    for s in srcs:
        (legacy / f"{s}.flac").write_bytes(b"x")
    (legacy / "_source.json").write_text(json.dumps({"fingerprint": fp}), encoding="utf-8")

    monkeypatch.setattr(_os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("模擬搬不動")))

    _, _, _, cached = C.separate(a, stems, "htdemucs_6s")
    assert cached is True, "搬不動也應該原地沿用,不該白白重跑 Demucs"
    d = C.cache_dir_of(a, stems, "htdemucs_6s")
    assert d == legacy, f"🔴 搬不動時 cache_dir_of 指到 {d.name},與 separate 用的 {legacy.name} 不一致"
    assert (d / "vocals.flac").exists()


def test_只有部分軌的快取不可以被當成完整(monkeypatch, tmp_path):
    """🔴 Codex 第六輪:「有任一 flac」不等於「完整」——
    sidecar 正確 + 只有 drums.flac 的殘缺新夾,仍被 cache_dir_of 選中,
    下游拿不到 vocals.flac。sidecar 要記軌清單,驗證要求**全部**存在。"""
    _fake_torch(monkeypatch)
    stems = tmp_path / "_stems"
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    fp = C._source_ident(a)["fingerprint"]
    srcs = ["drums", "bass", "other", "vocals"]

    # 新夾:sidecar 正確且記了軌清單,但只有 drums.flac(殘缺)
    newp = stems / C._cache_name(a, "htdemucs_6s", fp)
    newp.mkdir(parents=True)
    (newp / "drums.flac").write_bytes(b"x")
    (newp / "_source.json").write_text(
        json.dumps({"fingerprint": fp, "sources": srcs}), encoding="utf-8")
    # 舊夾:完整
    legacy = stems / "song__htdemucs_6s"
    legacy.mkdir(parents=True)
    for s in srcs:
        (legacy / f"{s}.flac").write_bytes(b"x")
    (legacy / "_source.json").write_text(
        json.dumps({"fingerprint": fp, "sources": srcs}), encoding="utf-8")

    d = C.cache_dir_of(a, stems, "htdemucs_6s")
    assert (d / "vocals.flac").exists(), \
        f"🔴 選中了只有 drums.flac 的殘缺夾 {d.name} → 人聲柱會消失"


def test_殘缺新快取不可以蓋過合法舊快取(monkeypatch, tmp_path):
    """🔴 交叉狀態(Codex 第五輪):新 SHA 夾**存在但殘缺**、舊快取完整且身分正確。
    separate() 會退去用舊的,cache_dir_of() 卻因為「新夾存在」就回傳那個空夾
    → 下游拿不到 vocals.flac,人聲柱第三次靜默消失。
    判斷必須看「有沒有內容」,不是只看 is_dir()。"""
    _fake_torch(monkeypatch)
    stems = tmp_path / "_stems"
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    fp = C._source_ident(a)["fingerprint"]
    srcs = ["drums", "bass", "other", "vocals"]

    # 新夾:存在但殘缺(只有 sidecar,沒有 flac)
    newp = stems / C._cache_name(a, "htdemucs_6s", fp)
    newp.mkdir(parents=True)
    (newp / "_source.json").write_text(json.dumps({"fingerprint": fp}), encoding="utf-8")
    # 舊夾:完整且身分正確
    legacy = stems / "song__htdemucs_6s"
    legacy.mkdir(parents=True)
    for s in srcs:
        (legacy / f"{s}.flac").write_bytes(b"x")
    (legacy / "_source.json").write_text(json.dumps({"fingerprint": fp}), encoding="utf-8")

    d = C.cache_dir_of(a, stems, "htdemucs_6s")
    assert (d / "vocals.flac").exists(), \
        f"🔴 cache_dir_of 指到殘缺的 {d.name}(沒有 vocals.flac)→ 人聲柱會消失"


def test_同程序兩執行緒不會共用暫存夾(monkeypatch, tmp_path):
    """🔴 只用 PID 命名的話,同一個程序裡的兩個執行緒會共用同一個暫存夾互相覆寫。

    ⚠️ 這條原本是**關鍵字裝飾品**(只 grep 原始碼有沒有 'uuid'),Codex 實測
       「把 uuid 改回固定 PID 值」測試照樣通過 → 等於沒守到。改成行為測試:
       同一個程序裡真的開兩條執行緒跑,看暫存夾名是不是兩個不同的。"""
    _fake_torch(monkeypatch)
    import threading
    seen, lock = set(), threading.Lock()
    real_mkdir = Path.mkdir

    def spy(self, *a, **k):        # mkdir(parents=True) 每一層都會呼叫 → 用集合去重
        if self.name.startswith(".tmp_"):
            with lock:
                seen.add(self.name)
        return real_mkdir(self, *a, **k)
    monkeypatch.setattr(Path, "mkdir", spy)

    stems = tmp_path / "_stems"
    songs = []
    for i in (1, 2):
        p = tmp_path / f"s{i}" / "song.wav"
        _mk(p, bytes([65 + i]) * 4)
        songs.append(p)
    ts = [threading.Thread(target=C.separate, args=(s, stems, "htdemucs_6s")) for s in songs]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(seen) == 2, f"🔴 兩條執行緒共用同一個暫存夾(只出現 {len(seen)} 個):{seen}"


def test_原子改名不可以吞掉非預期錯誤(monkeypatch, tmp_path):
    """🔴 權限不足、磁碟滿、路徑太長都必須**立刻**拋出來 —— 全吞掉的話使用者會以為
    快取寫好了,下一輪又整首重跑,而且永遠查不出原因。

    ⚠️ 這條原本是關鍵字裝飾品(grep 'raise'),改成行為測試之後**還是抓不到** ——
       因為錯誤路徑與正確路徑「最後都會拋 PermissionError」,看例外型別分不出來。
       真正的差別是:正確版本看到「目標不存在」就直接重拋(os.replace 只被呼叫 1 次);
       吞掉的版本會往下走到修復分支、再呼叫一次 os.replace(共 2 次)。
       所以要**數呼叫次數**,不是看例外型別。(這一課是 Codex 逼出來的。)"""
    _fake_torch(monkeypatch)
    import os as _os
    calls = []

    def boom(src, dst):
        calls.append((str(src), str(dst)))
        raise PermissionError("模擬權限不足")
    monkeypatch.setattr(_os, "replace", boom)

    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    with pytest.raises(OSError):
        C.separate(a, tmp_path / "_stems", "htdemucs_6s")
    assert len(calls) == 1, \
        f"🔴 目標不存在時應該立刻重拋,而不是走去修復分支再試一次(os.replace 被呼叫 {len(calls)} 次)"
    leftovers = list((tmp_path / "_stems").glob(".tmp_*"))
    assert not leftovers, f"炸掉後留了半成品:{leftovers}"


def test_呼叫端不可以自己拼快取路徑():
    """🔴 真實迴歸:編曲層次.py 自己寫死 `{stem}__{model}` 拼快取路徑,
    我把快取命名加上來源指紋之後,它那份沒跟著改 → vocal_stem 變 None,
    **人聲柱整根靜靜消失**(15.2% 權重),而且不會報錯。

    規則只能有一份:路徑一律跟 分軌快取.cache_dir_of() 拿。"""
    import re as _re
    for f in ("編曲層次.py", "和聲分析.py", "伴奏混音.py"):
        src = (REPO / f).read_text(encoding="utf-8")
        # 找「自己用 stem + model 拼資料夾名」的字樣
        bad = _re.findall(r'stems_dir\s*/\s*f?"[^"]*__\{[^"]*\}', src)
        assert not bad, f"{f} 自己拼了快取路徑 {bad} —— 請改用 cache_dir_of()"


def test_cache_dir_of與separate指到同一個位置(monkeypatch, tmp_path):
    """交接契約:下游拿 vocal_stem 要拿得到真的檔案。"""
    _fake_torch(monkeypatch)
    stems = tmp_path / "_stems"
    a = tmp_path / "song.wav"
    _mk(a, b"AAAA")
    C.separate(a, stems, "htdemucs_6s")
    d = C.cache_dir_of(a, stems, "htdemucs_6s")
    assert d.is_dir(), "cache_dir_of 指到的資料夾不存在"
    assert (d / "vocals.flac").exists(), "下游要的 vocals.flac 不在 cache_dir_of 指的地方"


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
