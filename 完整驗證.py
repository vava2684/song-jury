# -*- coding: utf-8 -*-
"""完整驗證.py — -VerifyModels 的整條流程:實跑九柱 → 獨立裁判 → 清理。

⛔ 為什麼要把整段從 shell 收斂進 python(Codex R16-9/R16-10):
   · Windows:真的按 Ctrl+C 時 PowerShell **不可靠地**進入 finally ——
     實測送 CTRL_C_EVENT 後安裝器掛住 15 秒、內外層 finally 都沒跑、
     verify_* 殘留。環境還原與清理全部落空。
   · POSIX:`trap ... INT` 會把中斷吞掉繼續執行,自動化分不出
     「安裝失敗」與「使用者取消」。
   python 對 KeyboardInterrupt 的 try/finally 是語言保證,而且殺樹、
   清理、退出碼全部在同一個地方 —— shell 只要看退出碼就好。

⭐ 隔離設計(沿用 R11 起的規則):
   · 唯一檔名 verify_<uuid>.wav → 分軌快取鍵含檔名,強迫每個模型真的重跑;
   · 子環境清掉 SONG_JURY_SKIP_GEMINI / SONG_JURY_TRUST_LEGACY_STEMS,
     ⛔ 用 subprocess 的 env 參數傳,不動自己的 os.environ(不留給呼叫者);
   · 不信 exit 0:用 驗證報告.validate(require_contract=True) 獨立拆 JSON。

退出碼(安裝器據此決定;⛔ 每一個都要能傳到最外層):
   0   = 九柱實跑 + 獨立裁判都過
   1   = 沒過(jury 失敗、JSON 驗不過…原因印在 stdout)
   2   = 評測跑完但缺柱(jury 的專用碼)
   124 = 逾時(已中止整棵程序樹)
   130 = 使用者中斷(Ctrl+C / SIGINT)—— 跟「失敗」明確分開
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))
from 子程序 import run_tree              # noqa: E402
from 驗證報告 import validate            # noqa: E402

# 這些環境變數會讓驗證跳關或信任舊快取 → 子程序一律拿掉
STRIP_ENV = ("SONG_JURY_SKIP_GEMINI", "SONG_JURY_TRUST_LEGACY_STEMS")

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _cleanup(vid: str, retries: int = 3, pause: float = 1.0):
    """清掉這次驗證的所有產物:音檔、各階段中間 JSON、報告、分軌快取。

    ⛔ 不是只清 wav 與最終報告 —— 中途炸掉時 _評分.json / _編曲層次.json /
       _和聲分析.json / _伴奏節奏軌.wav… 都會留下(Codex R13)。
    🔴 Codex R17-6:舊版 `except Exception: pass` + `rmtree(ignore_errors=True)`,
       刪不掉時**完全沒有人知道** —— helper 照樣回 0/130 並印「已中止並清理」。
       防毒、索引器、還沒放手的 child handle 都會讓刪除失敗,結果是音檔與分軌快取
       默默留在磁碟上,而輸出還在跟你保證清乾淨了。
       → 現在:重試幾次(Windows 的 sharing violation 多半是短暫的)、**重新掃一次**
         當判準,並把仍然存在的路徑回傳給呼叫者。清理是可觀測的事實,不是宣稱。

    回傳:清完之後仍然存在的路徑清單(空清單=真的乾淨)。"""
    def _targets():
        out = list(BASE.glob(f"{vid}*"))
        stems = BASE / "_stems"
        if stems.is_dir():
            out += list(stems.glob(f"{vid}*"))
        return out

    for i in range(max(1, retries)):
        if i:
            time.sleep(pause)
        left = _targets()
        if not left:
            return []
        for p in left:
            try:
                if p.is_file():
                    p.unlink()
                else:
                    shutil.rmtree(p)      # ⛔ 不用 ignore_errors:失敗要看得見
            except Exception:
                pass                       # 這一輪刪不掉沒關係,下面重新掃才是判準
    return [str(p) for p in _targets()]


def run(audio: Path, timeout: float, py: str = None) -> int:
    vid = "verify_" + uuid.uuid4().hex[:8]
    wav = BASE / f"{vid}{audio.suffix}"
    report = BASE / f"{vid}_評審團.json"
    py = py or sys.executable
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env["PYTHONUTF8"] = "1"
    started = time.time()
    # ⚠️ 用單一 rc 變數走到底(不在 try 裡 return):清理結果可能把 0 降級成 1,
    #    而 `finally` 改不動「已經 return 出去的值」—— 那樣寫等於沒改(自己踩過)。
    rc = 1
    # ⛔ 成功標記**只能在最後發布一次**(Codex R18-5):舊版在裁判過關當下就印
    #    VERIFY_OK,清理失敗時同一份輸出會同時有 OK 與 BAD —— 退出碼雖然對,
    #    但任何 grep 成功字串的日誌工具/腳本都會假綠,人讀起來也自相矛盾。
    verified = False
    try:
        shutil.copy(audio, wav)
        print(f"      實跑 評審團.py {wav.name}(唯一檔名,強迫全模型路徑;"
              f"首次會下載數 GB;逾時 {timeout:.0f}s)", flush=True)
        try:
            r = run_tree([py, str(BASE / "評審團.py"), str(wav)],
                         cwd=BASE, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"⛔ 評審團逾時({timeout:.0f}s,已中止整棵程序樹)", file=sys.stderr)
            rc = 124
        else:
            sys.stdout.write(r.stdout or "")
            sys.stderr.write(r.stderr or "")
            if r.returncode == 2:
                print("⛔ 評測跑完但缺柱(退出碼 2)—— 缺柱清單見上面評審團的輸出")
                rc = 2
            elif r.returncode != 0:
                print(f"⛔ 評審團失敗(退出碼 {r.returncode})")
                rc = 1
            else:
                # ⛔ 不信 exit 0:獨立裁判拆 JSON,而且**必須**自報 scoring_contract ——
                #    這是「本輪新產物」的安裝證據,不可套用舊格式相容(Codex R16-5)。
                why = validate(report, newer_than=started, require_contract=True,
                                       require_identity=True)
                if why:
                    print(f"VERIFY_BAD {why}")
                    rc = 1
                else:
                    verified = True      # ⛔ 先記著,清理確認乾淨後才發布
                    rc = 0
    except KeyboardInterrupt:
        # ⭐ 這正是收進 python 的理由:中斷有明確語意(130)且清理一定會跑。
        print("\n⛔ 使用者中斷(Ctrl+C)—— 已中止", file=sys.stderr)
        rc = 130
    finally:
        # ⛔ 清理結果要誠實回報(Codex R17-6):清不掉就把還在的檔案列出來,
        #    不可以一律印「已清理」。成功路徑清不乾淨 → 降級成失敗(1),
        #    因為「零殘留」本來就是這條驗證對外宣稱的一部分。
        left = _cleanup(vid)
        if left:
            print("⛔ 清理沒完全成功,這些還在(請自行刪除):", file=sys.stderr)
            for p_ in left:
                print(f"     · {p_}", file=sys.stderr)
            if rc == 0:
                print("VERIFY_BAD 九柱與格式都過,但驗證產物沒清乾淨(見上面清單)")
                rc = 1
                verified = False
        elif rc == 130:
            print("   (已清理乾淨)", file=sys.stderr)
    # ⭐ 唯一一次成功標記:九柱過 + 格式過 + 本輪新產物 + **零殘留**都成立才印
    if verified and rc == 0:
        print("VERIFY_OK 九柱完整、格式合格、本輪新產物、零殘留")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="song-jury 完整驗證(實跑九柱+獨立裁判)")
    ap.add_argument("--audio", type=Path, default=BASE / "demo_mix.wav")
    ap.add_argument("--timeout", type=float,
                    default=float(os.environ.get("SONG_JURY_VERIFY_TIMEOUT", "7200")))
    ap.add_argument("--python", default=None, help="用哪支直譯器跑評審團(預設:本程序)")
    a = ap.parse_args(argv)
    if not a.audio.exists():
        print(f"⛔ 找不到驗證用音檔:{a.audio}")
        return 1
    return run(a.audio, a.timeout, a.python)


if __name__ == "__main__":
    sys.exit(main())
