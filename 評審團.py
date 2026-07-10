# -*- coding: utf-8 -*-
"""評審團.py — 三層歌曲評審整合器
用法: python 評審團.py 歌曲檔或SUNO連結
  支援: 本機音檔 / https://suno.com/song/xxxx 連結(公開歌曲) / 直接 mp3 連結
第一層 物理技術 = song_scorer(.venv)
第二層 美學情感 = SongEval 五維(.venv-ml)+ Audiobox 四軸(.venv-ml)
第三層 詞曲文本 = Claude 在對話裡評(本程式不做)
輸出: 歌名_評審團.json + 主控台摘要
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent.resolve()
ENV = {**os.environ, "PYTHONUTF8": "1"}

SONGEVAL_LABELS = {
    "Coherence": "整體連貫性", "Musicality": "整體音樂性",
    "Memorability": "記憶點", "Clarity": "結構清晰度", "Naturalness": "人聲自然度",
}
AUDIOBOX_LABELS = {
    "PQ": "製作品質", "PC": "製作複雜度", "CE": "內容感染力", "CU": "內容實用性",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_UUID_RE = r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"


def _follow_short_link(url):
    """SUNO 短連結(/s/xxxx)只轉址一次就會露出帶 UUID 的正式網址。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=30)
        return resp.geturl()
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if loc:
            return urllib.parse.urljoin(url, loc)
        return url


def fetch_suno_meta(uuid):
    """抓 SUNO 歌曲頁,取正式歌名與歌詞(埋在頁面 prompt 欄位)。失敗回傳 (None, None)。"""
    try:
        req = urllib.request.Request(
            f"https://suno.com/song/{uuid}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception:
        return None, None
    title = None
    mt = re.search(r"<title>(.*?)\s+by\s", html)
    if mt:
        title = mt.group(1).strip().strip("《》〈〉\"' ")

    def decode_js(s):
        try:
            return json.loads('"' + s + '"')
        except Exception:
            return None

    def lyric_score(t):
        """0=不是歌詞;2=有段落標記(最可信);1=多行文字。先擋網頁程式碼雜訊。"""
        if not t or len(t) < 60:
            return 0
        if '"$"' in t or "_next/static" in t or '"src":' in t or '{"children"' in t:
            return 0
        if re.search(r"\[(intro|verse|chorus|bridge|hook|pre[- ]?chorus|outro)", t, re.I) or "【" in t:
            return 2
        return 1 if t.count("\n") >= 6 else 0

    candidates = []
    # 策略一:prompt 欄位(雙層 JSON 逸出,自訂歌詞模式)
    idx = html.find('\\"prompt\\":\\"')
    if idx >= 0:
        peeled = re.sub(r"\\(.)", r"\1", html[idx:idx + 60000])
        m = re.search(r'"prompt":"((?:[^"\\]|\\.)*)"', peeled, re.S)
        if m:
            d = decode_js(m.group(1))
            if d:
                candidates.append(d.strip())
    # 策略二:Next.js flight 推送字串(單層逸出,新版頁面)
    for m in re.finditer(r'\.push\(\[\d+,"((?:[^"\\]|\\.)*)"', html, re.S):
        d = decode_js(m.group(1))
        if d:
            candidates.append(d.strip())

    best = max(((lyric_score(c), len(c), c) for c in candidates), default=(0, 0, None))
    lyrics = best[2] if best[0] > 0 else None
    return title, lyrics


def _safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', "_", s)[:60]


def resolve_input(arg):
    """本機路徑直接用;SUNO 連結/直連 mp3 先下載到 下載\\ 再評。"""
    if not re.match(r"^https?://", arg, re.I):
        p = Path(arg).resolve()
        if not p.exists():
            sys.exit(f"找不到檔案: {p}")
        return p
    uuid = re.search(_UUID_RE, arg)
    if not uuid and "suno.com" in arg.lower():
        arg = _follow_short_link(arg)
        uuid = re.search(_UUID_RE, arg)
    if arg.lower().split("?")[0].endswith(".mp3"):
        url = arg
        name = Path(urllib.parse.urlparse(arg).path).name or "download.mp3"
    elif uuid:
        url = f"https://cdn1.suno.ai/{uuid.group(1)}.mp3"
        title, lyrics = fetch_suno_meta(uuid.group(1))
        name = f"{_safe_name(title)}.mp3" if title else f"suno_{uuid.group(1)[:8]}.mp3"
        # 同名歌已存在(重 roll 版本)→ 自動加版號,避免覆蓋
        base_stem = Path(name).stem
        stem, k = base_stem, 2
        while (BASE / "下載" / f"{stem}.mp3").exists():
            stem = f"{base_stem} v{k}"
            k += 1
        name = f"{stem}.mp3"
        if lyrics:
            res_dir = BASE / "下載" / f"{Path(name).stem}_評分結果"
            res_dir.mkdir(parents=True, exist_ok=True)
            lyr_path = res_dir / f"{Path(name).stem}_歌詞.txt"
            lyr_path.write_text(lyrics + "\n", encoding="utf-8")
            print(f"📝 歌詞已自動抓取: {lyr_path}")
        else:
            print("📝 頁面抓不到歌詞(可能純音樂或頁面改版),請手動提供")
    else:
        sys.exit("看不懂的連結。請給 SUNO 歌曲頁連結(https://suno.com/song/...)或直接的 mp3 連結")
    dl_dir = BASE / "下載"
    dl_dir.mkdir(exist_ok=True)
    dest = dl_dir / name
    print(f"⬇ 從 SUNO 下載中: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        sys.exit(f"下載失敗(HTTP {e.code})。歌曲可能不是「公開」狀態——"
                 f"私人歌曲請先在 SUNO 網站下載,再把檔案拖進來評。")
    print(f"已存: {dest}\n")
    return dest


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python 評審團.py 歌曲檔或SUNO連結")
    song = resolve_input(sys.argv[1])

    print(f"🎵 評審對象: {song.name}\n")

    # ── 第一層: 物理技術 ──
    print("[1/3] 物理技術評分(song_scorer)...")
    phys_json = song.with_name(song.stem + "_評分.json")
    subprocess.run(
        [str(BASE / ".venv/Scripts/python.exe"), str(BASE / "song_scorer.py"),
         str(song), "--json", str(phys_json)],
        cwd=str(BASE), env=ENV, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    physical = json.loads(phys_json.read_text(encoding="utf-8"))
    phys_json.unlink()  # 內容已併入 _評審團.json,不留中間檔

    # ── 第二層 A: SongEval 五維美學 ──
    print("[2/3] SongEval 美學評分(音樂人訓練模型)...")
    tmp_out = BASE / "_tmp_songeval"
    if tmp_out.exists():
        shutil.rmtree(tmp_out)
    subprocess.run(
        [str(BASE / ".venv-ml/Scripts/python.exe"), "eval.py",
         "-i", str(song), "-o", str(tmp_out)],
        cwd=str(BASE / "SongEval"), env=ENV, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    se_raw = json.loads((tmp_out / "result.json").read_text(encoding="utf-8"))
    songeval = list(se_raw.values())[0]
    shutil.rmtree(tmp_out)

    # ── 第二層 B: Audiobox 四軸 ──
    print("[3/3] Audiobox 美學評分(Meta 模型)...")
    tmp_lst = BASE / "_tmp_audiobox.jsonl"
    tmp_lst.write_text(json.dumps({"path": str(song)}) + "\n", encoding="utf-8")
    p = subprocess.run(
        [str(BASE / ".venv-ml/Scripts/audio-aes.exe"), str(tmp_lst), "--batch-size", "1"],
        capture_output=True, text=True, encoding="utf-8", env=ENV, check=True)
    audiobox = json.loads(p.stdout.strip().splitlines()[-1])
    tmp_lst.unlink()

    # ── 整合輸出 ──
    merged = {
        "file": song.name,
        "layer1_physical": physical,
        "layer2_songeval_1to5": songeval,
        "layer2_audiobox_1to10": audiobox,
        "layer3_lyrics": "由 Claude 在對話中評(把歌詞丟給 Claude 說「評詞」)",
    }
    out_path = song.with_name(song.stem + "_評審團.json")
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    se_avg = sum(songeval.values()) / len(songeval)
    print()
    print("=" * 54)
    print("  評審團總表")
    print("=" * 54)
    print(f"【物理技術】 {physical['scores']['total']} / 100(等級 {physical['scores']['grade']})")
    print(f"【美學-SongEval】 平均 {se_avg:.2f} / 5")
    for k, v in songeval.items():
        print(f"  ・{SONGEVAL_LABELS.get(k, k)}:{v:.2f}")
    print("【美學-Audiobox】(1–10)")
    for k in ("PQ", "CE", "CU", "PC"):
        if k in audiobox:
            print(f"  ・{AUDIOBOX_LABELS[k]}:{audiobox[k]:.2f}")
    print(f"【詞曲文本】 把歌詞貼給 Claude 說「評詞」即可")
    print("-" * 54)
    print(f"完整報告:{out_path}")


if __name__ == "__main__":
    main()
