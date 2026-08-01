# -*- coding: utf-8 -*-
"""金鑰驗證.py — 逐把驗證 .env 裡的 Gemini 金鑰(安裝器共用,只用標準庫)。

⛔ 為什麼是獨立小工具而不是安裝腳本內嵌(Codex R12 兩條):
   · 內嵌版只驗第一把(Split(',')[0]):第一把好、第二把壞 → 假陽性;
     第一把壞、第二把好 → 假陰性。
   · 內嵌版把 429/網路/TLS 全洗成「先當有」→ 九柱齊全綠燈。
   放進 python 模組才有辦法寫行為測試+變異驗證,PS5.1 的 TLS 問題也一併消失
   (python 的 ssl 自己會談 TLS1.2+)。

打的是 models 清單端點(不耗生成配額)。每把金鑰分類四態:
   verified(HTTP 200)/ invalid(400/401/403)/ cooling(429)/ unknown(網路/TLS/逾時)

輸出(機器可讀,絕不印完整金鑰):
   KEY <序號> <指紋=末4碼> <狀態> [HTTP]
   KEYPROBE verified=V invalid=I cooling=C unknown=U total=T

退出碼(安裝器據此決定綠燈/擋下/「未能驗證」):
   0 = 至少一把 verified(部分壞的照樣列出來警告)
   1 = 全部 invalid(格式像金鑰但 Google 全不認)→ 視同沒金鑰
   3 = 沒有任何 verified,且有 cooling/unknown → 「未能驗證」,不可宣稱九柱齊全
   4 = .env 裡沒有(非佔位的)金鑰
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1&key="
TIMEOUT_SEC = 15

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_keys(env_path: Path) -> list:
    """讀 .env 撈出所有金鑰。規則與 Gemini曲評.load_keys 對齊(佔位字串不算);
    這裡刻意不 import 它 —— 本工具要在「連 .venv 都還沒建」的機器上用任何 python3 跑。"""
    if not env_path.exists():
        return []
    text = env_path.read_text(encoding="utf-8-sig")   # utf-8-sig:剝 PS5.1 寫的 BOM
    keys = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        for name in ("GEMINI_API_KEYS", "GEMINI_API_KEY"):
            if line.startswith(name + "="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                keys.extend(k.strip() for k in v.split(","))
    def _placeholder(k):
        return (any(ord(c) > 127 for c in k) or len(k) < 20
                or k.lower().startswith(("your", "xxx", "todo", "<")))
    seen, out = set(), []
    for k in keys:
        if k and k not in seen and not _placeholder(k):
            seen.add(k)
            out.append(k)
    return out


def probe_key(key: str):
    """驗一把,回 ("verified"|"invalid"|"cooling"|"unknown", http碼或None)。"""
    try:
        req = urllib.request.Request(ENDPOINT + key, headers={"User-Agent": "song-jury-keyprobe"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC):
            return "verified", 200
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            return "invalid", e.code
        if e.code == 429:
            return "cooling", e.code
        return "unknown", e.code
    except Exception:
        return "unknown", None       # DNS/TLS/逾時 —— 不是金鑰的錯,但也沒驗成


def main(argv) -> int:
    env_path = Path(argv[1]) if len(argv) > 1 else Path(".env")
    keys = parse_keys(env_path)
    if not keys:
        print("KEYPROBE verified=0 invalid=0 cooling=0 unknown=0 total=0")
        return 4
    counts = {"verified": 0, "invalid": 0, "cooling": 0, "unknown": 0}
    for i, k in enumerate(keys, 1):
        status, code = probe_key(k)
        counts[status] += 1
        fp = k[-4:]                                   # 只印末 4 碼,絕不印整把
        print(f"KEY {i} …{fp} {status}" + (f" HTTP{code}" if code else ""))
    print("KEYPROBE " + " ".join(f"{s}={counts[s]}" for s in
                                 ("verified", "invalid", "cooling", "unknown"))
          + f" total={len(keys)}")
    if counts["verified"] > 0:
        return 0
    if counts["invalid"] == len(keys):
        return 1
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
