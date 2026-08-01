# -*- coding: utf-8 -*-
"""分軌線(Demucs 分軌 + 和聲辨識)到底能不能用 —— 安裝器與測試共用的唯一實作。

⚠️ 這條線斷掉 = 結構編曲柱(12.6%)+ 和聲柱(13.6%)一起出事,合計 26.2% 權重。

🔴 2026-08-02 自己實跑踩到的事故(這支誕生的原因):
   安裝跑完 `[10/10] 自我檢查` 印「和聲 13.6% 缺項 → 這台機器評不出有效分數」exit 1;
   **同一次執行**接下來的 `-VerifyModels` 真跑九柱卻一路順利(Demucs 六軌走 GPU、
   和聲柱 57.9),獨立裁判也給 VERIFY_OK —— 自我檢查的結論是錯的,而且一句原因都沒印。

🔴 Codex R17-1 又抓到第一版的四個病(都已在下面修掉):
   ① fail→success 直接回 (True, "") —— **第一次的錯誤證據被抹掉**,
      間歇性不穩被洗成完整綠燈,下次正式評分照樣掉柱;
   ② 三次 600s 疊起來最壞 1805 秒(30 分)完全沒有輸出,使用者只看到安裝器「卡住」;
   ③ 用「單獨 import demucs 成功」反推「缺 librosa/numpy/soundfile」——
      DLL/ABI 壞掉、權限、快取損壞全被錯誤歸因成缺套件,把人導去重裝 requirements;
   ④ attempts=0 回 (False, "") —— 違反這支自己寫的「失敗一定要講原因」。

   → 現在的規矩:**總預算封頂**(不是每次各給一份)、**每次嘗試都先印進度**、
     **照錯誤種類決定要不要重試**(缺套件是確定性的,重試一百次也一樣)、
     **救回來的要留證據**(RECOVERED),而且分類由錯誤本身決定,不靠反推。

退出碼(安裝器據此判,⛔ 別自己再猜一次):
   0 = 整條線可用(若是重試才成功,會多印一行 DEMUCS_LINE_RECOVERED)
   1 = **明確缺套件**(錯誤訊息指名了哪個模組沒裝)→ 補裝就好
   2 = 其他不可用(逾時 / 啟動失敗 / DLL 或 ABI 壞掉 / 找不到 python)→ 看原因,不是重裝套件
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))

# ⛔ 模組清單與「用哪支 python」都跟評審團.py 拿,不可以在這裡另抄一份 ——
#    抄了就會有「安裝器說可以、實際跑分說不行」的兩套真理(Codex R13 的老 bug)。
from 評審團 import DEMUCS_LINE_MODS, DEMUCS_PY   # noqa: E402

RETRY_PAUSE = 5.0
# ⛔ 這是**整段體檢的總預算**,不是每次嘗試各給一份(Codex R17-1:舊版三次各 600s
#    疊成 30 分鐘)。冷啟動第一次 import torch+numba 確實可能要好幾分鐘,
#    所以預設給得寬,但封頂而且可調。
TOTAL_BUDGET = float(os.environ.get("SONG_JURY_DEMUCS_PROBE_TIMEOUT", "900"))

# 錯誤種類 —— ⛔ 分類要由錯誤本身決定,不可以用「換個 import 再試」反推
OK = "ok"
MISSING = "missing_module"      # 指名了哪個模組沒裝 → 補裝
TIMEOUT = "timeout"             # 卡住(下載中?死鎖?)
LAUNCH = "launch_error"         # 連 python 都起不來(權限/檔案被鎖)
IMPORT = "import_error"         # import 得動但炸了(DLL/ABI/損壞快取)

# 只有這兩種值得再給一次機會:剛裝完幾 GB 剛寫下去、防毒正在掃的時候會出現。
# ⛔ MISSING 是確定性的(重試一百次還是缺);TIMEOUT 已經把預算吃掉了。
RETRIABLE = (LAUNCH, IMPORT)

_MISSING_RE = re.compile(r"No module named ['\"]([\w.]+)['\"]")


class LineResult:
    """一次體檢的完整結論 —— ⛔ 不要只回 bool,救回來的證據要留著。"""

    def __init__(self, ok, kind, why="", module=None, first_error="", tries=1):
        self.ok, self.kind, self.why = ok, kind, why
        self.module, self.first_error, self.tries = module, first_error, tries

    @property
    def recovered(self):
        """第一次失敗、後來成功 —— 綠燈可以給,但這件事必須說出來。"""
        return self.ok and bool(self.first_error)

    def __repr__(self):
        return (f"LineResult(ok={self.ok}, kind={self.kind!r}, why={self.why!r}, "
                f"module={self.module!r}, tries={self.tries})")


def _classify(rc, out):
    m = _MISSING_RE.search(out or "")
    if m:
        return MISSING, m.group(1), f"缺模組 {m.group(1)}(退出碼 {rc})"
    tail = ((out or "").strip().splitlines() or ["(沒有輸出)"])[-1]
    return IMPORT, None, f"退出碼 {rc}:{tail[:300]}"


def probe(py, mods=DEMUCS_LINE_MODS, attempts=2, budget=None,
          pause=RETRY_PAUSE, log=None) -> LineResult:
    """真的用那支直譯器 import 一次整條線。

    ⛔ attempts < 1 直接 ValueError:回一個「沒有原因的失敗」比拋例外更難查。
    ⛔ 每次嘗試前先 log 一行:這支最壞會等好幾分鐘,沒有進度就等於「卡住」。
    """
    if attempts < 1:
        raise ValueError(f"attempts 至少要 1(拿到 {attempts})—— 不驗就不該叫這支")
    budget = TOTAL_BUDGET if budget is None else budget
    log = log if log is not None else (lambda s: print(s, flush=True))
    deadline = time.monotonic() + budget
    first_error = ""
    res = None
    for i in range(1, attempts + 1):
        left = deadline - time.monotonic()
        if left <= 0:
            why = f"總預算 {budget:.0f}s 用完(做了 {i - 1} 次嘗試)"
            return LineResult(False, TIMEOUT, why, None, first_error or why, i - 1)
        log(f"      分軌線體檢 {i}/{attempts}(整段上限 {budget:.0f}s,剩 {left:.0f}s)…")
        try:
            r = subprocess.run([str(py), "-c", "import " + ", ".join(mods)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=left)
        except subprocess.TimeoutExpired:
            # ⛔ 逾時不重試:預算是共用的,再試一次只會把剩下的時間也吃光
            why = f"import 逾時(這次等了 {left:.0f}s)"
            return LineResult(False, TIMEOUT, why, None, first_error or why, i)
        except OSError as e:
            res = LineResult(False, LAUNCH, f"啟動 {py} 失敗:{type(e).__name__}: {e}",
                             None, "", i)
        else:
            if r.returncode == 0:
                return LineResult(True, OK, "", None, first_error, i)
            kind, mod, why = _classify(r.returncode, (r.stderr or "") + (r.stdout or ""))
            res = LineResult(False, kind, why, mod, "", i)
        first_error = first_error or res.why
        res.first_error = first_error
        if res.kind not in RETRIABLE or i == attempts:
            return res
        log(f"      ↻ 可能是暫時的({res.kind}),{pause:.0f}s 後再試一次:{res.why[:120]}")
        time.sleep(pause)
    return res


def main(argv=None) -> int:
    py = (argv or sys.argv[1:] or [DEMUCS_PY])[0]
    if not py or not Path(py).exists():
        print(f"DEMUCS_LINE_BAD 找不到可用的 python:{py!r}")
        return 2
    res = probe(py)
    if res.ok:
        print(f"DEMUCS_LINE_OK {py}")
        if res.recovered:
            # ⛔ 救回來≠沒事:這台機器的這條線是不穩的,安裝器要把它當警告印出來
            print(f"DEMUCS_LINE_RECOVERED 第 {res.tries} 次才成功;"
                  f"第一次的錯誤:{res.first_error}")
        return 0
    print(f"DEMUCS_LINE_BAD {py}\n"
          f"           需要:{', '.join(DEMUCS_LINE_MODS)}\n"
          f"           種類:{res.kind}\n"
          f"           實際:{res.why}")
    # ⛔ 只有「錯誤訊息指名了缺哪個模組」才叫缺套件 —— 其餘一律 2,
    #    不可以把 DLL/權限/損壞快取說成「請重裝 requirements」(Codex R17-1)
    return 1 if res.kind == MISSING else 2


if __name__ == "__main__":
    sys.exit(main())
