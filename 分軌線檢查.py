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
   3 = **設定錯誤**(SONG_JURY_DEMUCS_PROBE_TIMEOUT 填了非數字/NaN/0/負數)
       🔴 Codex R18-3:舊版這種 typo 會變成未捕捉例外(rc=1),而安裝器把 1
          讀成「缺套件」→ 叫人去重裝 requirements。設定錯誤要有自己的碼。
   4 = **這支自己出事**(internal_error)
       ⛔ 任何預期外的例外收斂成 4,絕不讓裸 traceback 的 rc=1 被誤讀成缺套件 ——
          也不可以跟設定錯誤共用 3(Codex R19-4:兩支安裝器看到 3 一律說
          「設定值有問題」,把工具自己的 bug 導向「請改環境變數」)。

⭐ --status-json <路徑>:把結論寫成 UTF-8 JSON({ok,kind,rc,why,recovered,...})。
   ⛔ 安裝器一律讀這個檔判斷,**不要 grep 人類訊息**(Codex R19-3:PS 5.1 在
      cp950 下會把子程序的 UTF-8 解壞,韓文/emoji 直接變 ?;stderr 還會被
      包成 ErrorRecord)。人看的文字照樣即時印在終端,兩者互不干擾。
"""
import json
import math
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
from 設定讀取 import ConfigError, positive_finite   # noqa: E402

# ⛔ 評審團.py 很重(它自己也會做環境解析)—— import 階段爆掉的話,舊版是
#    裸 traceback rc=1,連 bootstrap 都來不及接(Codex R20-P2-2 實測)。
#    先接住,等 main() 在保護傘裡才丟出來 → 收斂成 rc=4 並寫得出狀態檔。
try:
    from 評審團 import DEMUCS_LINE_MODS, DEMUCS_PY   # noqa: E402
    _IMPORT_ERROR = None
except Exception as _imp_err:      # noqa: BLE001
    DEMUCS_LINE_MODS, DEMUCS_PY, _IMPORT_ERROR = (), "", _imp_err

RETRY_PAUSE = 5.0
BUDGET_ENV = "SONG_JURY_DEMUCS_PROBE_TIMEOUT"
# ⛔ 這是**整段體檢的總預算**,不是每次嘗試各給一份(Codex R17-1:舊版三次各 600s
#    疊成 30 分鐘)。冷啟動第一次 import torch+numba 確實可能要好幾分鐘,
#    所以預設給得寬,但封頂而且可調。
# ⛔ 不可以在 import 時 float(env):設定打錯會讓整支在載入階段就爆 rc=1,
#    被安裝器讀成「缺套件」(Codex R18-3)。改成用時才讀、壞掉有自己的碼。
DEFAULT_BUDGET = 900.0

# 錯誤種類 —— ⛔ 分類要由錯誤本身決定,不可以用「換個 import 再試」反推
OK = "ok"
CONFIG = "config_error"     # 設定值壞掉(不是機器壞掉)
INTERNAL = "internal_error"  # 這支自己出事(也不可以被說成缺套件)
MISSING = "missing_module"      # 指名了哪個模組沒裝 → 補裝
TIMEOUT = "timeout"             # 卡住(下載中?死鎖?)
LAUNCH = "launch_error"         # 連 python 都起不來(權限/檔案被鎖)
IMPORT = "import_error"         # import 得動但炸了(DLL/ABI/損壞快取)

# 只有這兩種值得**再確認一次**:剛裝完幾 GB 剛寫下去、防毒正在掃的時候會出現。
# ⛔ MISSING 是確定性的(重試一百次還是缺);TIMEOUT 已經把預算吃掉了。
# ⚠️ 誠實用詞(Codex R18-7):我們**無法**從 PermissionError / ImportError 本身
#    斷定它是暫時的 —— 永久的權限問題、ABI 不相容長得一模一樣。所以這不是
#    「判定為暫時故障」,而是「再給一次確認機會」,而且只給一次、還要吃預算。
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


def budget_from_env(env=None) -> float:
    """讀總預算 —— 壞掉時丟 ConfigError(呼叫端要翻成 rc=3,不是 rc=1)。"""
    return positive_finite(BUDGET_ENV, DEFAULT_BUDGET, lo=0.0, hi=86400.0, env=env)


def probe(py, mods=DEMUCS_LINE_MODS, attempts=2, budget=None,
          pause=RETRY_PAUSE, log=None) -> LineResult:
    """真的用那支直譯器 import 一次整條線。

    ⛔ attempts < 1 直接 ValueError:回一個「沒有原因的失敗」比拋例外更難查。
    ⛔ 每次嘗試前先 log 一行:這支最壞會等好幾分鐘,沒有進度就等於「卡住」。
    """
    if attempts < 1:
        raise ValueError(f"attempts 至少要 1(拿到 {attempts})—— 不驗就不該叫這支")
    budget = budget_from_env() if budget is None else float(budget)
    # ⛔ API 參數也要驗:NaN/inf/0 會在 subprocess/time 那層才炸,離現場太遠
    if not math.isfinite(budget) or budget <= 0:
        raise ConfigError(f"budget={budget!r} 必須是有限的正數秒數")
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
        # ⛔ 等待也要吃預算(Codex R18-7:budget=0.05 / pause=0.2 實測跑了 0.203s)。
        #    封頂的是**牆上時間**,不是「幾次 import」。
        nap = max(0.0, min(pause, deadline - time.monotonic()))
        log(f"      ↻ 再給一次確認機會({res.kind};無法從錯誤本身判斷是不是暫時的),"
            f"{nap:.0f}s 後重試:{res.why[:120]}")
        time.sleep(nap)
    return res


def _write_status(path, **fields):
    """機器可讀的結論(UTF-8 JSON)—— ⛔ 安裝器判斷一律看這個,不 grep 中文。

    🔴 Codex R19-3:PowerShell 5.1 在 cp950 下會把子程序的 UTF-8 解成 big5,
       捕捉到的字串本身就壞掉(韓文/emoji 直接變 ?);再加上 2>&1 會把 stderr
       變成 ErrorRecord,Out-String 又會灌進一整段 PowerShell 診斷文字。
       靠「解析人類訊息找 RECOVERED / missing_module」本來就不該是契約。"""
    if not path:
        return
    # ⛔ 原子寫入(Codex R20-P2-1):中斷或競速時,半份 JSON 會讓安裝器讀到殘缺狀態。
    # ⛔ 例外一律吃下來,但**要說出來**:狀態檔寫不出來只代表「只能靠退出碼」,
    #    不可以讓它把整支 helper 帶進未分類的失敗(那會被讀成別的意思)。
    try:
        p = Path(path)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:      # noqa: BLE001
        print(f"⚠ 狀態檔寫不出來({type(e).__name__});安裝器只能靠退出碼判斷", flush=True)


def main(argv=None) -> int:
    if _IMPORT_ERROR is not None:
        # 在保護傘裡丟出來 → bootstrap 收斂成 internal_error / rc 4
        raise _IMPORT_ERROR
    args = list(argv) if argv is not None else sys.argv[1:]
    status = None
    if "--status-json" in args:
        i = args.index("--status-json")
        status = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    py = (args or [DEMUCS_PY])[0]
    if not py or not Path(py).exists():
        print(f"DEMUCS_LINE_BAD 找不到可用的 python:{py!r}\n"
              f"           種類:{LAUNCH}")
        _write_status(status, ok=False, kind=LAUNCH, rc=2,
                      why=f"找不到可用的 python:{py!r}", recovered=False)
        return 2
    try:
        res = probe(py)
        return _report_result(res, py, status)
    except ConfigError as e:
        # ⛔ 設定錯誤有自己的碼:被歸進 rc=1 的話,安裝器會叫人重裝 requirements
        print(f"DEMUCS_LINE_BAD 設定值有問題\n"
              f"           種類:{CONFIG}\n"
              f"           實際:{e}")
        _write_status(status, ok=False, kind=CONFIG, rc=3, why=str(e), recovered=False)
        return 3
    except Exception as e:      # noqa: BLE001 —— 這支自己出事也不能被說成缺套件
        print(f"DEMUCS_LINE_BAD 分軌線體檢自己出錯了\n"
              f"           種類:{INTERNAL}\n"
              f"           實際:{type(e).__name__}: {e}")
        # ⛔ 這支自己爆掉 ≠ 你的設定寫錯(Codex R19-4):共用 3 的話,安裝器會
        #    把人導去改一個根本沒問題的環境變數。給它自己的碼。
        _write_status(status, ok=False, kind=INTERNAL, rc=4,
                      why=f"{type(e).__name__}: {e}", recovered=False)
        return 4


def _report_result(res, py, status) -> int:
    """把 LineResult 翻成輸出 + 狀態檔 + 退出碼。

    ⛔ 這段也要在 main 的保護傘裡(Codex R20-P2-2):舊版只包住 probe(),
       之後讀 res.ok / 格式化訊息 / 寫狀態檔若出事,就變成裸 traceback rc=1,
       而 1 在安裝器眼裡是「缺套件」。"""
    if res.ok:
        print(f"DEMUCS_LINE_OK {py}")
        if res.recovered:
            # ⛔ 救回來≠沒事:這台機器的這條線是不穩的,安裝器要把它當警告印出來
            print(f"DEMUCS_LINE_RECOVERED 第 {res.tries} 次才成功;"
                  f"第一次的錯誤:{res.first_error}")
        _write_status(status, ok=True, kind=OK, rc=0, why="",
                      recovered=bool(res.recovered), first_error=res.first_error,
                      tries=res.tries, python=str(py))
        return 0
    print(f"DEMUCS_LINE_BAD {py}\n"
          f"           需要:{', '.join(DEMUCS_LINE_MODS)}\n"
          f"           種類:{res.kind}\n"
          f"           實際:{res.why}")
    # ⛔ 只有「錯誤訊息指名了缺哪個模組」才叫缺套件 —— 其餘一律 2,
    #    不可以把 DLL/權限/損壞快取說成「請重裝 requirements」(Codex R17-1)
    rc = 1 if res.kind == MISSING else 2
    _write_status(status, ok=False, kind=res.kind, rc=rc, why=res.why,
                  module=res.module, recovered=False, python=str(py))
    return rc


def bootstrap(argv=None) -> int:
    """最外層保護傘:**任何**未預期例外都收斂成 4,並盡量把 status 寫出去。

    🔴 Codex R20-P2-2 實測:probe() 回 None 之後讀 res.ok 會 AttributeError → rc=1;
       import-time 例外更是連 main 都進不去 → rc=1 + 裸 traceback。
       1 在安裝器眼裡是「缺套件」,於是工具自己的 bug 會叫使用者去重裝幾 GB。
    ⚠️ KeyboardInterrupt / SystemExit 不在這裡吃掉:那是使用者或呼叫端的決定。"""
    args = list(sys.argv[1:] if argv is None else argv)
    status = None
    if "--status-json" in args:
        i = args.index("--status-json")
        status = args[i + 1] if i + 1 < len(args) else None
    try:
        return main(args)
    except Exception as e:      # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"DEMUCS_LINE_BAD 分軌線體檢自己出錯了\n"
              f"           種類:{INTERNAL}\n"
              f"           實際:{type(e).__name__}: {e}")
        try:
            _write_status(status, ok=False, kind=INTERNAL, rc=4,
                          why=f"{type(e).__name__}: {e}", recovered=False)
        except Exception:       # noqa: BLE001
            pass
        return 4


if __name__ == "__main__":
    sys.exit(bootstrap())
