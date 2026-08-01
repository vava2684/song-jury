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
   4 = 找不到可用金鑰(沒填/只有佔位字串)
   5 = **金鑰政策無效**(拒絕名單格式錯、秘密檔來源可疑)——
       ⛔ 跟 4 分開:那是安全設定壞了,叫使用者去申請新 key 沒有用(Codex R15)
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1&key="
TIMEOUT_SEC = 15

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from 金鑰政策 import effective_keys


def parse_keys(env_path: Path):
    """讀出**執行期真正會用的那組金鑰**(順序即嘗試順序)。回 (keys, policy_error)。

    ⛔ 這裡絕不可以自己再解析一次 .env:驗證器與執行期各解析各的,就會
       「驗 A、跑 B」——`.env=A` 但 process env 有 B/C 時驗證形同虛設,
       `KEYS = A`(等號旁有空白)更是一邊讀得到一邊讀不到(Codex R13)。
       唯一真理來源是 金鑰政策.effective_keys,兩邊共用。"""
    keys, notes = effective_keys(env_path)
    for n in notes:
        print(n)
    # ⛔ 「政策壞掉」與「沒填金鑰」是兩件事,不可以混成同一個退出碼:
    #    GUI/自動化只能靠解析人類文字猜(Codex R15)。政策問題回 5。
    policy_error = any("政策無效" in n for n in notes)
    return keys, policy_error


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
    keys, policy_error = parse_keys(env_path)
    if policy_error:
        print("KEYPROBE verified=0 invalid=0 cooling=0 unknown=0 total=0 policy_error=1")
        return 5
    if not keys:
        print("KEYPROBE verified=0 invalid=0 cooling=0 unknown=0 total=0 policy_error=0")
        return 4
    counts = {"verified": 0, "invalid": 0, "cooling": 0, "unknown": 0}
    for i, k in enumerate(keys, 1):
        status, code = probe_key(k)
        counts[status] += 1
        fp = k[-4:]                                   # 只印末 4 碼,絕不印整把
        print(f"KEY {i} …{fp} {status}" + (f" HTTP{code}" if code else ""))
    print("KEYPROBE " + " ".join(f"{s}={counts[s]}" for s in
                                 ("verified", "invalid", "cooling", "unknown"))
          + f" total={len(keys)} policy_error=0")
    if counts["verified"] > 0:
        return 0
    if counts["invalid"] == len(keys):
        return 1
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
