# -*- coding: utf-8 -*-
"""九柱組裝:權重、缺項歸一化、缺柱完整性旗標、取值鍵名。

每條測試都對應一個**真的發生過**的事故,不是假想:
  · 取值鍵名寫錯 → Gemini 整關被靜默丟掉,還被重正規化蓋掉看不出來(2026-07)
  · 缺柱分數被印得像正常分數 → 不完整評測被當成可比較的成績(2026-07-31)
  · SongEval 缺席時 sum()/len() 除零 → 整份報告產不出來
"""
import pytest
from conftest import load

J = load("評審團")


# ── 權重 ────────────────────────────────────────────────────────────
def test_九柱權重表未被竄改():
    """⛔ 權重是十三席合議庭定的,改一格要單格重開辯論。這條是防止有人「順手調一下」。"""
    assert J.PILLAR_W == {
        "詞": 25.3, "人聲": 15.2, "和聲": 13.6, "結構編曲": 12.6, "聲學": 12.1,
        "旋律記憶": 6.1, "真實風格": 6.1, "整體": 5.1, "律動": 4.0,
    }


def test_權重加總是100點1且這是刻意的():
    """九柱加總 100.1 是各柱四捨五入的結果,不是 bug。
    這條測試的用途是:哪天有人「修好」成 100,會在這裡被擋下來要求先辯論。"""
    assert round(sum(J.PILLAR_W.values()), 4) == 100.1


def test_柱內細項權重加總為100():
    """柱內是 0-100 的配比;湊不滿或超過都代表柱內配比被改壞了。"""
    items = J.build_pillar_items({}, {}, {}, {}, {}, {}, {}, {})
    for pillar, rows in items.items():
        assert sum(w for _, w, _ in rows) == 100, f"{pillar} 柱內權重加總 != 100"


# ── 缺項與缺柱 ──────────────────────────────────────────────────────
def test_柱內缺項會重新歸一化():
    """兩項各佔 50,缺一項時剩下那項應該獨得整柱,而不是被當成 0 分拉低。"""
    items = {"和聲": [("A", 50, 80.0), ("B", 50, None)]}
    out = J.build_pillar_totals(items)
    assert out["柱分"]["和聲"]["score"] == 80.0
    assert out["柱分"]["和聲"]["missing"] == ["B"]


def test_柱內全缺時不除零且該柱無分():
    """曾經在 SongEval 缺席時 sum()/len() 直接 ZeroDivisionError,整份報告產不出來。"""
    items = {"和聲": [("A", 50, None), ("B", 50, None)]}
    out = J.build_pillar_totals(items)          # 不可以拋例外
    assert out["柱分"]["和聲"]["score"] is None


def test_全部柱都缺時曲側合成為None():
    out = J.build_pillar_totals({"律動": [("X", 100, None)]})
    assert out["曲側合成"] is None


def test_缺柱時完整評測必為False且列出缺柱():
    """⛔ 缺柱 = 換了一把尺。JSON 一定要帶得動這個事實,否則排行榜會照樣吃進去。"""
    items = J.build_pillar_items({}, {}, {}, {}, {}, {}, {}, {})   # 全空 = 全缺
    out = J.build_pillar_totals(items)
    assert out["完整評測"] is False
    assert set(out["缺柱"]) == set(J.PILLAR_W) - {"詞"}
    assert out["缺柱權重合計"] == pytest.approx(74.8, abs=0.05)
    assert "不可" in out["完整性警語"]


def test_九柱齊全時完整評測為True():
    items = {p: [("X", 100, 70.0)] for p in J.PILLAR_W if p != "詞"}
    out = J.build_pillar_totals(items)
    assert out["完整評測"] is True
    assert out["缺柱"] == []
    assert out["曲側合成"] == 70.0


def test_曲側合成是在場柱的加權平均():
    """人聲 15.2 拿 100、律動 4.0 拿 0,其餘缺 → 應為 15.2*100/(15.2+4.0)。"""
    items = {"人聲": [("X", 100, 100.0)], "律動": [("Y", 100, 0.0)]}
    out = J.build_pillar_totals(items)
    assert out["曲側合成"] == pytest.approx(15.2 * 100 / (15.2 + 4.0), abs=0.05)


# ── 取值鍵名(這是 Gemini 被靜默丟掉的那個 bug)──────────────────────
def test_Gemini總分取的是gemini_reported_total而不是total():
    """🔴 真實事故:舊碼取 gemini["total"],但引擎寫出的鍵是 gemini_reported_total
    → 整體柱的 Gemini 那一項永遠是 None,被重正規化蓋掉,報告上看不出來。"""
    gem = {"gemini_reported_total": {"raw_0to10": 7.9},
           "dimensions": {f"M{i}": {"score": 80.0} for i in range(1, 7)}}
    items = J.build_pillar_items({}, {}, {}, gem, {}, {}, {}, {})
    整體 = dict((n, v) for n, w, v in items["整體"])
    assert 整體["Gemini 總分"] == 79.0, "0-10 制要 ×10 換成 0-100"

    # 而放在錯誤的鍵名下,絕不可以被取到(否則等於預設值亂猜)
    items2 = J.build_pillar_items({}, {}, {}, {"total": 7.9}, {}, {}, {}, {})
    assert dict((n, v) for n, w, v in items2["整體"])["Gemini 總分"] is None


def test_退出碼要跟評測完整性一致():
    """🔴 Codex R11:缺柱評測 exit 0,只看退出碼的外部自動化會把無效分數當成功。
    契約:0=完整、2=報告已發布但缺柱、其他=失敗。fail-closed:欄位缺/型別錯=2。"""
    assert J._final_exit_code({"pillar_totals": {"完整評測": True}}) == 0
    assert J._final_exit_code({"pillar_totals": {"完整評測": False}}) == 2
    assert J._final_exit_code({"pillar_totals": {}}) == 2
    assert J._final_exit_code({"pillar_totals": "junk"}) == 2
    assert J._final_exit_code({}) == 2
    assert J._final_exit_code({"pillar_totals": {"完整評測": 1}}) == 2, \
        "1 不是 True —— truthy 放行等於把型別閘門拆掉"


def test_Gemini總分是bool時不可以被洗成10分():
    """🔴 Codex R9:整體柱那行舊寫法 `raw * 10.0` —— True*10 == 10.0,
    bool 在 _ev/_evnum 防線之外的最後一條小路又被洗白一次。
    合法數字才縮放;非法原值原樣進柱,由中央閘門記進 invalid_numeric。"""
    gem = {"gemini_reported_total": {"raw_0to10": True}}
    items = J.build_pillar_items({}, {}, {}, gem, {}, {}, {}, {})
    v = dict((n, x) for n, w, x in items["整體"])["Gemini 總分"]
    assert v is True, f"🔴 True 被轉成 {v!r}(True*10==10 = bool 又洗白一次)"
    out = J.build_pillar_totals(items)
    assert "Gemini 總分" in out["柱分"]["整體"].get("invalid_numeric", {}), \
        "值不合法要留痕 invalid_numeric —— 不可以拿 10 分,也不可以裝成「沒跑到」"


def test_SongEval是1到5制要換算成0到100():
    items = J.build_pillar_items({}, {}, {}, {}, {"Memorability": 4.7}, {}, {}, {})
    assert dict((n, v) for n, w, v in items["旋律記憶"])["記憶點(SongEval)"] == pytest.approx(94.0)


def test_Audiobox是1到10制要換算成0到100():
    items = J.build_pillar_items({}, {}, {}, {}, {}, {"PQ": 8.43}, {}, {})
    assert dict((n, v) for n, w, v in items["聲學"])["製作品質(Audiobox)"] == pytest.approx(84.3)


def test_Audiobox為0時不可被誤判成缺席():
    """`(x or 0)*10` 這種寫法會讓 0 分變成 falsy;0 是有效分數,不是缺席。"""
    items = J.build_pillar_items({}, {}, {}, {}, {}, {"PQ": 0.0}, {}, {})
    assert dict((n, v) for n, w, v in items["聲學"])["製作品質(Audiobox)"] == 0.0


def test_演唱項可吃dict也可吃純數字():
    phys = {"vocal_detail": {"pitch": {"score": 88.0}, "range": 94.2}}
    items = J.build_pillar_items(phys, {}, {}, {}, {}, {}, {}, {})
    人聲 = dict((n, v) for n, w, v in items["人聲"])
    assert 人聲["音準"] == 88.0 and 人聲["音域"] == 94.2


def test_凍結項不在計分細項裡():
    """⛔ 演唱.rhythm 與 和聲.non_diatonic 是凍結項:照列不計分。
    它們若出現在 PILLAR_ITEMS,就是被偷偷復權了。"""
    items = J.build_pillar_items({}, {}, {}, {}, {}, {}, {}, {})
    names = [n for rows in items.values() for n, _, _ in rows]
    assert not any("節奏準度" in n or "rhythm" in n.lower() for n in names)
    assert not any("離調" in n or "non_diatonic" in n.lower() for n in names)


# ── 來源身分:檔案 bytes vs 解碼後聲音(Codex R18-4)────────────────────
def test_換容器的同一段聲音_解碼後雜湊要相同(tmp_path):
    """🔴 實測過的繞過方式:wav 尾端追加 18 bytes → 檔案 sha256 變了,
    ffmpeg 解碼出來的 PCM 一模一樣。所以「檔案雜湊」不能當成聲音身分。"""
    import shutil as _sh
    import sys as _sys
    from conftest import REPO as R, load as _load
    if not _sh.which("ffmpeg"):
        pytest.skip("這台沒有 ffmpeg —— 解碼後雜湊是 best effort")
    J = _load("評審團")
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    _sh.copyfile(R / "demo_mix.wav", a)
    _sh.copyfile(R / "demo_mix.wav", b)
    with b.open("ab") as f:
        f.write(b"JUNK_METADATA_ONLY")
    assert J._file_sha256(a) != J._file_sha256(b), "這個 fixture 本來就該讓檔案雜湊不同"
    pa, pb = J._pcm_sha256(a), J._pcm_sha256(b)
    assert pa and pa == pb, f"🔴 解碼後雜湊沒認出是同一段聲音:{pa} vs {pb}"


def test_沒有ffmpeg時解碼雜湊回空字串而不是炸掉(tmp_path, monkeypatch):
    """⚠️ best effort:算不出來就留空,報告照樣發布,比較器會標較弱的等級。"""
    from conftest import REPO as R, load as _load
    J = _load("評審團")
    monkeypatch.setattr(J.shutil, "which", lambda *a, **k: None)
    assert J._pcm_sha256(R / "demo_mix.wav") == ""


def test_不同取樣率與聲道的版本不可以撞成同一個身分(tmp_path):
    """🔴 Codex R19-1 實測:舊版強制 -ac 2 -ar 44100 是**多對一**正規化 ——
    「48k 單聲道」與「由它轉出的 44.1k 雙單聲道」canonical PCM 雜湊完全相同,
    兩個結構不同的來源被硬判成同源(比較器會直接拒絕)。
    現在保留原始取樣率/聲道並把結構餵進雜湊,兩者必須不同。"""
    import shutil as _sh
    import subprocess as _sp
    from conftest import REPO as R, load as _load
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("這台沒有 ffmpeg/ffprobe")
    J = _load("評審團")
    mono, dual = tmp_path / "m.wav", tmp_path / "d.wav"
    _sp.run(["ffmpeg", "-v", "error", "-y", "-i", str(R / "demo_mix.wav"),
             "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(mono)],
            check=True, timeout=300)
    _sp.run(["ffmpeg", "-v", "error", "-y", "-i", str(mono),
             "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(dual)],
            check=True, timeout=300)
    hm, hd = J._pcm_sha256(mono), J._pcm_sha256(dual)
    assert hm and hd, "兩個都要算得出來"
    assert hm != hd, "🔴 不同取樣率/聲道的版本撞成同一個身分 —— 會被硬判同源"


def _f32(path, expr, dur=0.2):
    import subprocess as _sp
    _sp.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"aevalsrc={expr}:s=48000:d={dur}",
             "-c:a", "pcm_f32le", "-ac", "1", str(path)], check=True, timeout=300)


@pytest.mark.parametrize("a_expr,b_expr,why", [
    ("0", "0.000000000001*sin(t)", "低於 s32 最小刻度的浮點訊號會被量化成 0"),
    ("1.1", "1.2", "超出滿刻度的浮點會被 clip 成同一個值"),
])
def test_浮點來源不可以在正規化時撞成同一個身分(tmp_path, a_expr, b_expr, why):
    """🔴 Codex R20-P1-1 實測兩組真實碰撞:一律轉 s32le **不是**無損 ——
    {why}。浮點來源要留在浮點面上(f32→f64 精確),否則兩個不同的音訊
    會拿到同一個解碼身分,然後被比較器硬判成同源。"""
    import shutil as _sh
    from conftest import load as _load
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("這台沒有 ffmpeg/ffprobe")
    J = _load("評審團")
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    _f32(a, a_expr)
    _f32(b, b_expr)
    assert J._file_sha256(a) != J._file_sha256(b), "fixture 本身要是兩個不同的檔"
    ha, hb = J._pcm_sha256(a), J._pcm_sha256(b)
    assert ha and hb, "兩邊都要算得出身分"
    assert ha != hb, f"🔴 {why} —— 兩個不同的音訊撞成同一個解碼身分"


def test_canonical格式是白名單而且分得出寬度(tmp_path):
    """canonical 由**原始樣本格式**決定,而且是白名單。

    🔴 Codex R21-P1-1:上一版寫成「浮點→f64,其餘→s32」——
    s64/s64p 被壓進 s32,實測兩個只差 1 個 s64 LSB 的音訊撞成同一個身分。
    ⛔ 而且不認得的格式不可以有 fallback:ffmpeg 之後多一種格式,
       fallback 會靜靜製造新的碰撞。回空字串 = 不發布解碼身分(fail closed)。"""
    from conftest import load as _load
    J = _load("評審團")
    assert J._canonical_fmt("flt") == "f64le"
    assert J._canonical_fmt("fltp") == "f64le"
    assert J._canonical_fmt("dbl") == "f64le"
    assert J._canonical_fmt("dblp") == "f64le"
    assert J._canonical_fmt("s16") == "s32le"
    assert J._canonical_fmt("s32p") == "s32le"
    # ⚠️ s64/s64p 沒有無損容器可用(ffmpeg 沒有 s64le raw muxer,f64 尾數又不夠)
    #    → 故意不給 canonical:寧可不發布身分,也不要一個會撞號的身分
    assert J._canonical_fmt("s64") == "", "🔴 s64 壓進 s32 會撞號"
    assert J._canonical_fmt("s64p") == ""
    assert J._canonical_fmt("") == "", "不認得就不要給 canonical(fail closed)"
    assert J._canonical_fmt("未來的新格式") == ""


def test_ffprobe要讀keyvalue不可以靠欄位順序(tmp_path):
    """🔴 自己踩到:ffprobe 是照**它自己的順序**印的(sample_fmt 排在 sample_rate
    前面),用 `nk=1` 靠位置對應會把 channel_layout 當成 sample_fmt ——
    浮點來源全被當成整數處理,R20-P1-1 的修法整個失效。"""
    import shutil as _sh
    from conftest import REPO as R, load as _load
    if not _sh.which("ffprobe"):
        pytest.skip("這台沒有 ffprobe")
    J = _load("評審團")
    shape = J._audio_shape(_sh.which("ffprobe"), R / "demo_mix.wav")
    assert isinstance(shape, dict), "要回 dict(key=value),不是靠位置的 list"
    assert shape.get("sample_rate") and shape.get("channels")
    assert "sample_fmt" in shape


def test_s64來源寧可不發布身分也不要撞號(tmp_path):
    """🔴 Codex R21-P1-1:s64 被壓進 s32le 時,只差 1 個 s64 LSB 的兩段聲音
    會拿到同一個身分。

    ⚠️ 實測發現 ffmpeg **沒有** s64le raw muxer(只有 f64le/f64be),而 f64 的
    53 位尾數也裝不下 64 位整數 —— 沒有無損容器可用。
    ⛔ 那就**不要發布解碼身分**(回空字串),讓它退到檔案雜湊那層,
       絕不可以給一個會撞號的身分。"""
    import shutil as _sh
    import subprocess as _sp
    from conftest import load as _load
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("這台沒有 ffmpeg/ffprobe")
    J = _load("評審團")
    assert J._canonical_fmt("s64") == "", "s64 不可以有 canonical(會撞號)"
    src = tmp_path / "s64.nut"
    _sp.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "aevalsrc=0.25*sin(1000*t):s=48000:d=0.2",
             "-c:a", "pcm_s64le", "-ac", "1", str(src)], check=True, timeout=300)
    shape = J._audio_shape(_sh.which("ffprobe"), src)
    assert shape.get("sample_fmt") == "s64", f"fixture 要真的是 s64:{shape}"
    assert J._pcm_sha256(src) == "", "🔴 對 s64 發布了身分 —— 那個身分會撞號"


def test_同一段聲音換容器身分不可以變(tmp_path):
    """🔴 Codex R21-P2-1:ffprobe 的 channel_layout 字面值是**容器/探測器**的描述 ——
    同一段 PCM 裝 WAV 被寫成 unknown、裝 MOV/CAF 寫成 mono,身分就變了。
    那正是「換容器也認得出」這個宣稱要擋的情況。"""
    import shutil as _sh
    import subprocess as _sp
    from conftest import load as _load
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("這台沒有 ffmpeg/ffprobe")
    J = _load("評審團")
    wav = tmp_path / "a.wav"
    _sp.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "aevalsrc=0.25*sin(1000*t):s=48000:d=0.2",
             "-c:a", "pcm_f64le", "-ac", "1", str(wav)], check=True, timeout=300)
    outs = []
    for ext in ("mov", "caf"):
        o = tmp_path / f"a.{ext}"
        _sp.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-c:a", "copy", str(o)],
                check=True, timeout=300)
        outs.append(o)
    base = J._pcm_sha256(wav)
    assert base
    # ⚠️ 這條的前提是「這台 ffprobe 對不同容器講出不同的 layout」——
    #    那是**該版 ffmpeg 的行為**,不是我們的契約(CI 的 ubuntu 版本就一致,
    #    於是這條在那裡驗不到東西)。前提不成立要明講跳過,不可以靜靜算通過。
    layouts = {J._audio_shape(_sh.which("ffprobe"), f).get("channel_layout", "")
               for f in [wav] + outs}
    if len(layouts) < 2:
        pytest.skip(f"這台 ffmpeg 對各容器的 layout 講法一致({layouts}),"
                    "重現不了容器漂移;不靠版本行為的那條是 "
                    "test_layout字面值不可以進身分雜湊")
    for o in outs:
        assert J._pcm_sha256(o) == base, f"🔴 換成 {o.suffix} 之後身分就變了"


def test_layout字面值不可以進身分雜湊(tmp_path, monkeypatch):
    """🔴 Codex R21-P2-1 的**契約版**:同一段聲音、只有 channel_layout 這個
    字串不同 → 身分必須一模一樣。

    ⛔ 為什麼不靠「裝 WAV vs 裝 MOV」:那是 ffmpeg 版本行為 —— 某些版本兩邊
       都寫 mono,於是缺陷塞回去也測不出來(CI ubuntu 實測,變異存活)。
       這裡直接餵兩個只差 layout 的 shape,任何有 ffmpeg 的機器都成立。"""
    import shutil as _sh
    import subprocess as _sp
    from conftest import load as _load
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("這台沒有 ffmpeg/ffprobe")
    J = _load("評審團")
    wav = tmp_path / "a.wav"
    _sp.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "aevalsrc=0.25*sin(1000*t):s=48000:d=0.2",
             "-c:a", "pcm_f64le", "-ac", "1", str(wav)], check=True, timeout=300)
    real = J._audio_shape(_sh.which("ffprobe"), wav)
    assert real, "fixture 探測不到結構"

    seen = []
    for layout in ("mono", "unknown"):
        monkeypatch.setattr(J, "_audio_shape",
                            lambda _probe, _p, _l=layout: {**real, "channel_layout": _l})
        seen.append(J._pcm_sha256(wav))
    assert seen[0] and seen[0] == seen[1], \
        f"🔴 layout 字面值進了身分雜湊:{seen}"


# ── 喇叭語意(Codex R22-P1-1)─────────────────────────────────────
def _six_ch_wav(tmp_path, mask, name):
    """做一支 6 聲道 WAV,再**只改** WAVEFORMATEXTENSIBLE 的 dwChannelMask。

    ⭐ 這樣兩個檔的 data chunk 逐 byte 相同,差別只有「每個聲道送去哪個喇叭」——
       正是 Codex 用來證明 pcm-v4 會撞號的那組樣本。"""
    import struct
    import subprocess as _sp
    src = tmp_path / "src6.wav"
    if not src.exists():
        _sp.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                 "-i", "aevalsrc=0.2*sin(400*t)|0.2*sin(500*t)|0.2*sin(600*t)|"
                       "0.2*sin(700*t)|0.2*sin(800*t)|0.2*sin(900*t):s=48000:d=0.2:c=5.1",
                 "-c:a", "pcm_s32le", str(src)], check=True, timeout=300)
    raw = bytearray(src.read_bytes())
    at = raw.index(b"fmt ") + 8 + 20          # fmt data + 20 = dwChannelMask
    raw[at:at + 4] = struct.pack("<I", mask)
    out = tmp_path / name
    out.write_bytes(bytes(raw))
    return out


def test_喇叭配置不同不可以撞成同一個身分(tmp_path):
    """🔴 Codex R22-P1-1:pcm-v4 把 channel_layout 整個踢出雜湊,於是
    5.1(BL/BR 後置)與 5.1(side)(SL/SR 側置)——同一串數字送去不同喇叭 ——
    得到同一個解碼身分,比較器直接喊 duplicate_source 拒絕兩者同場。
    ⛔ 那是**假陽性**:播放空間與語意不同,本來就是兩個作品。"""
    import shutil as _sh
    from conftest import load as _load
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("這台沒有 ffmpeg/ffprobe")
    J = _load("評審團")
    back = _six_ch_wav(tmp_path, 0x003F, "back.wav")      # FL FR FC LFE BL BR
    side = _six_ch_wav(tmp_path, 0x060F, "side.wav")      # FL FR FC LFE SL SR
    probe = _sh.which("ffprobe")
    got = {f.name: J._audio_shape(probe, f).get("channel_layout") for f in (back, side)}
    assert len(set(got.values())) == 2, f"fixture 沒做出兩種配置:{got}"
    hb, hs = J._pcm_sha256(back), J._pcm_sha256(side)
    assert hb and hs, "兩個都該發布得出身分"
    assert hb != hs, f"🔴 5.1 與 5.1(side) 撞成同一個身分:{hb}"


def test_多聲道講不出喇叭配置就不發布身分(tmp_path):
    """⛔ fail closed:mask=0 的 6 聲道檔,ffprobe 只說得出 unknown ——
    這時候硬補一個預設配置等於**製造**碰撞(不同配置的檔會撞在一起)。"""
    import shutil as _sh
    from conftest import load as _load
    if not (_sh.which("ffmpeg") and _sh.which("ffprobe")):
        pytest.skip("這台沒有 ffmpeg/ffprobe")
    J = _load("評審團")
    un = _six_ch_wav(tmp_path, 0x0000, "unknown6.wav")
    pcm, reason, shape = J._pcm_identity(un)
    assert pcm == "", f"🔴 對講不出配置的多聲道發布了身分:{pcm}"
    assert reason == "unknown_multichannel_layout", reason
    # 而且要**寫進報告**讓人稽核得到原因(不是靜靜地少一個欄位)
    ident = J._identity_fields(un)
    assert ident["source_audio_pcm_status"]["reason"] == "unknown_multichannel_layout"
    assert ident["source_audio_pcm_status"]["shape"]["channels"] == "6"


def test_單聲道與立體聲用產品規則補預設(tmp_path):
    """⚠️ 1/2 聲道沒有「送去哪個喇叭」的歧義,所以探測器講不出來時補預設 ——
    這正是換容器不漂移的來源。⛔ 3 聲道以上不適用(見上一條)。"""
    from conftest import load as _load
    J = _load("評審團")
    assert J._canonical_speakers("unknown", 1) == "FC"
    assert J._canonical_speakers("", 2) == "FL+FR"
    assert J._canonical_speakers("mono", 1) == J._canonical_speakers("unknown", 1)
    assert J._canonical_speakers("unknown", 6) == ""       # fail closed
    assert J._canonical_speakers("5.1", 6) != J._canonical_speakers("5.1(side)", 6)


# pcm-v5 的喇叭表 golden contract —— ⛔ 這是**身分契約的一部分**,
# 少一列 = 那種配置從此不發布身分(靜靜降級),多一列/改一列 = 身分定義變了。
# 兩者都必須是「明確改版」而不是「順手編輯」,所以整份鎖在這裡。
_GOLDEN_SPEAKERS = {
    "mono": "FC", "stereo": "FL+FR", "downmix": "DL+DR",
    "2.1": "FL+FR+LFE", "3.0": "FL+FR+FC", "3.0(back)": "FL+FR+BC",
    "4.0": "FL+FR+FC+BC", "quad": "FL+FR+BL+BR", "quad(side)": "FL+FR+SL+SR",
    "3.1": "FL+FR+FC+LFE", "5.0": "FL+FR+FC+BL+BR", "5.0(side)": "FL+FR+FC+SL+SR",
    "4.1": "FL+FR+FC+LFE+BC",
    "5.1": "FL+FR+FC+LFE+BL+BR", "5.1(side)": "FL+FR+FC+LFE+SL+SR",
    "6.0": "FL+FR+FC+BC+SL+SR", "6.0(front)": "FL+FR+FLC+FRC+SL+SR",
    "3.1.2": "FL+FR+FC+LFE+TFL+TFR", "hexagonal": "FL+FR+FC+BL+BR+BC",
    "6.1": "FL+FR+FC+LFE+BC+SL+SR", "6.1(back)": "FL+FR+FC+LFE+BL+BR+BC",
    "6.1(front)": "FL+FR+LFE+FLC+FRC+SL+SR",
    "7.0": "FL+FR+FC+BL+BR+SL+SR", "7.0(front)": "FL+FR+FC+FLC+FRC+SL+SR",
    "7.1": "FL+FR+FC+LFE+BL+BR+SL+SR", "7.1(wide)": "FL+FR+FC+LFE+BL+BR+FLC+FRC",
    "7.1(wide-side)": "FL+FR+FC+LFE+FLC+FRC+SL+SR",
    "5.1.2": "FL+FR+FC+LFE+BL+BR+TFL+TFR",
    "octagonal": "FL+FR+FC+BL+BR+BC+SL+SR",
    "cube": "FL+FR+BL+BR+TFL+TFR+TBL+TBR",
    "5.1.4": "FL+FR+FC+LFE+BL+BR+TFL+TFR+TBL+TBR",
    "7.1.2": "FL+FR+FC+LFE+BL+BR+SL+SR+TFL+TFR",
    "7.1.4": "FL+FR+FC+LFE+BL+BR+SL+SR+TFL+TFR+TBL+TBR",
    "7.2.3": "FL+FR+FC+LFE+BL+BR+SL+SR+TFL+TFR+TBC+LFE2",
    "9.1.4": "FL+FR+FC+LFE+BL+BR+FLC+FRC+SL+SR+TFL+TFR+TBL+TBR",
    "hexadecagonal": "FL+FR+FC+BL+BR+BC+SL+SR+TFL+TFC+TFR+TBL+TBC+TBR+WL+WR",
    "22.2": ("FL+FR+FC+LFE+BL+BR+FLC+FRC+BC+SL+SR+TC+TFL+TFC+TFR+TBL+TBC+TBR"
             "+LFE2+TSL+TSR+BFC+BFL+BFR"),
}


# 這台 ffmpeg 有、而我們**刻意**不收的配置(今天是空的:37 筆全收)
_MISSING_OK = frozenset()
# 允許共用同一組喇叭的「同義別名」(今天沒有;要加請一併更新 README)
_ALIAS_OK = frozenset()


def test_喇叭表是鎖住的契約_整份都要對():
    """🔴 Codex R23-P2-3:舊測試只比「產品表 ∩ 這台 ffmpeg」的交集 ——
    把整列 `22.2` 刪掉,四條相關測試照樣全綠(實測)。那正是最稀有、最沒人會
    手動發現的一列:刪掉之後 22.2 的來源會靜靜降級成 exact-file。
    ⛔ 契約要整份鎖:少一列、多一列、改一列都得是明確的改版。"""
    from conftest import load as _load
    J = _load("評審團")
    got = J._SPEAKERS_BY_LAYOUT
    missing = {k: v for k, v in _GOLDEN_SPEAKERS.items() if k not in got}
    extra = {k: v for k, v in got.items() if k not in _GOLDEN_SPEAKERS}
    changed = {k: (got[k], v) for k, v in _GOLDEN_SPEAKERS.items()
               if k in got and got[k] != v}
    assert not missing, f"🔴 喇叭表少了幾列(那些配置會靜靜降級):{missing}"
    assert not extra, f"🔴 喇叭表多了幾列(身分定義變了,要升 contract):{extra}"
    assert not changed, f"🔴 有列被改過(同一個名字換了喇叭集合):{changed}"
    # 分解目前不可以重複 —— ⚠️ 但這不是永恆定律(Codex R24 觀察):真正的**同義
    # 別名**(同一組喇叭、兩個名字)映到同一組 canonical speakers 正是正規化的目的,
    # 不是碰撞。今天表裡沒有這種別名,所以先鎖住;要加別名時,把它列進 _ALIAS_OK
    # 並在 README 說明,不要默默放寬。
    rev = {}
    for k, v in got.items():
        rev.setdefault(v, []).append(k)
    dup = {v: ks for v, ks in rev.items()
           if len(ks) > 1 and tuple(sorted(ks)) not in _ALIAS_OK}
    assert not dup, f"🔴 有兩個配置名對到同一組喇叭(不在別名清單裡):{dup}"


def test_喇叭表要對得上這台ffmpeg的分解():
    """⭐ 這張表是**我們自己的**契約(所以身分不隨 ffmpeg 版本漂移),
    但它描述的是 ffmpeg 的配置 —— 對不上就是表寫錯或 ffmpeg 改了定義,
    兩種都要當場知道,而不是等到身分算錯。"""
    import shutil as _sh
    import subprocess as _sp
    from conftest import load as _load
    if not _sh.which("ffmpeg"):
        pytest.skip("這台沒有 ffmpeg")
    J = _load("評審團")
    r = _sp.run(["ffmpeg", "-hide_banner", "-layouts"], capture_output=True,
                text=True, timeout=300)
    real, seen = {}, False
    for line in (r.stdout or "").splitlines():
        if line.startswith("Standard channel layouts"):
            seen = True
            continue
        if seen:
            parts = line.split()
            if len(parts) == 2 and "+" in parts[1] or (len(parts) == 2 and parts[1].isupper()):
                real[parts[0]] = parts[1]
    assert real, "沒解析到 ffmpeg 的配置表"
    got = J._SPEAKERS_BY_LAYOUT
    changed = {k: (v, real[k]) for k, v in got.items() if k in real and real[k] != v}
    missing = sorted(k for k in real if k not in got and k != "NAME")
    assert not changed, f"🔴 喇叭表與這台 ffmpeg 的分解對不上:{changed}"
    # ⛔ missing 要**紅**,不可以只 print(Codex R24-P2-3):CI 跑的是
    #    `pytest -q` 再加 pytest.ini 的 -q(等於 -qq)且開著 capture ——
    #    通過的測試印什麼都看不到,「覆蓋面悄悄縮小」就沒人會知道。
    #    新版 ffmpeg 多出配置時,產品會誠實降級(安全方向),但那是**要有人決定**
    #    的事:補進表裡、或寫進下面這個明列的例外清單。
    assert not [k for k in missing if k not in _MISSING_OK], (
        f"🔴 這台 ffmpeg 有、而喇叭表沒有的配置:{missing}\n"
        "   → 那些來源會被降級成 exact-file(不發布解碼身分)。請把它們補進\n"
        "     評審團._SPEAKERS_BY_LAYOUT、tests 的 golden、驗證報告.SPEAKERS_BY_LAYOUT,\n"
        "     或明確加進 _MISSING_OK 並寫清楚為什麼。")


def test_探測音訊結構不可以用系統code_page解輸出(monkeypatch, tmp_path):
    """⚠️ 這條是 R23 順手抓到的(跑別條測試時看到 UnicodeDecodeError 警告):

    `subprocess.run(..., text=True)` 不給 encoding 就會用**父程序的 locale** ——
    繁中 Windows 是 cp950,工具只要吐一個非 ASCII 位元組就 UnicodeDecodeError。
    🔴 後果不是崩潰而是**靜靜降級**:_audio_shape 的 except 會把它變成
       「探測不到」→ 那台機器從此發不出解碼身分,使用者完全看不出原因。
    ⛔ 這裡用一個「只有指定 encoding 才解得開」的假 subprocess 模擬那個環境。"""
    from conftest import load as _load
    J = _load("評審團")
    real_run = J.subprocess.run

    def fake_run(cmd, **kw):
        if kw.get("encoding") is None:
            raise UnicodeDecodeError("cp950", b"\xe7", 0, 1, "illegal multibyte sequence")
        return _Result()

    class _Result:
        returncode = 0
        stdout = ("sample_fmt=s16\nsample_rate=44100\nchannels=2\n"
                  "channel_layout=stereo\n")
        stderr = ""

    monkeypatch.setattr(J.subprocess, "run", fake_run)
    shape = J._audio_shape("ffprobe", tmp_path / "x.wav")
    assert shape and shape["sample_fmt"] == "s16", \
        f"🔴 沒有指定 encoding —— 這台機器會靜靜地失去解碼身分:{shape}"
    J.subprocess.run = real_run
