# -*- coding: utf-8 -*-
"""setup_nrcvad.py — 代使用者自官方源取得 NRC-VAD 情緒詞典(情感弧線功能用)。

NRC-VAD 授權「禁止再散布」→ 不進 repo;由使用者自取。本腳本只是幫忙從**官方原始網址**
下載到使用者自己的機器(等同使用者自己點連結下載,非第三方再散布)。
用法:  python setup_nrcvad.py        (安裝腳本會自動呼叫;也可手動跑)
放置:  lexicon/nrc-vad/NRC-VAD-Lexicon-Aug2018Release/NRC-VAD-Lexicon.txt
"""
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent.resolve()
MARKER = BASE / "lexicon" / "nrc-vad" / "NRC-VAD-Lexicon-Aug2018Release" / "NRC-VAD-Lexicon.txt"
URL = "https://saifmohammad.com/WebDocs/VAD/NRC-VAD-Lexicon-Aug2018Release.zip"


def ensure(retries=3):
    if MARKER.exists():
        print("NRC-VAD 已存在,略過。")
        return True
    import urllib.request
    import zipfile
    import io
    # 官方學術站需完整瀏覽器 User-Agent + https,否則回 406
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0 Safari/537.36", "Accept": "*/*"}
    dest = BASE / "lexicon" / "nrc-vad"
    for attempt in range(1, retries + 1):
        try:
            print(f"下載 NRC-VAD 詞典(約 41MB)… 第 {attempt}/{retries} 次")
            data = urllib.request.urlopen(urllib.request.Request(URL, headers=hdr), timeout=180).read()
            dest.mkdir(parents=True, exist_ok=True)
            zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
            if MARKER.exists():
                print("NRC-VAD 取得完成 → 情感弧線功能可用。")
                return True
            print("解壓後找不到預期檔案,重試…")
        except Exception as e:
            print(f"  取得失敗:{e}")
    print("NRC-VAD 自動取得失敗。可手動至 https://saifmohammad.com/WebPages/nrc-vad.html "
          "下載,解壓到 lexicon/nrc-vad/(不影響物理/美學/詞評三關,只影響情感弧線圖)。")
    return False


if __name__ == "__main__":
    ensure()
