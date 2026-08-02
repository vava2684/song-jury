# -*- coding: utf-8 -*-
"""狀態檔(--status-json)的**唯一**驗證實作 —— 安裝器與測試共用。

⛔ 為什麼獨立成一支(不是塞在 分軌線檢查.py 裡):
   驗證器不可以是「被驗證的那支程式」。helper 自己出事(rc=4)時,
   再叫同一支去驗它自己寫的狀態檔,只會拿到第二個未知狀態。
   而且測試會用 stub 換掉 helper —— 驗證邏輯得留在原地。

⛔ 為什麼要嚴格 schema 而不只是「rc 對得上」(Codex R21-P2-2 實測):
   · 狀態檔寫 rc=4 卻寫 kind=config_error → 三個 shell 都採信,顯示
     「設定值有問題」,把人導去改一個根本沒問題的環境變數;
   · `"recovered": "false"`(字串)在三個 shell 都是 truthy → 假的重試警告。
   狀態檔是**機器契約**,不是一段自由文字:型別、枚舉、成套關係都要驗。

⚠️ 威脅模型講清楚:同一個 OS 使用者本來就能改腳本,這裡不做密碼學真實性。
   這支能可靠處理的是**殘留、競速、半份檔案、結構性矛盾**。
   ⛔ 而且無論狀態檔說什麼,**成功與否永遠只看實際退出碼**。

用法:python 狀態驗證.py <狀態檔> <實際退出碼>
   → 印 `<kind>\t<1|空>`(可採信;第二欄是 recovered)
   → 或 `MISMATCH\t<原因>`(不可採信,呼叫端只依退出碼判斷)
"""
import json
import sys
from pathlib import Path

# 錯誤種類(⛔ 與 分軌線檢查.py 共用同一份定義,不可以各抄一份)
OK = "ok"
MISSING = "missing_module"
TIMEOUT = "timeout"
LAUNCH = "launch_error"
IMPORT = "import_error"
CONFIG = "config_error"
INTERNAL = "internal_error"

# 每個退出碼**只允許**這些 kind —— 對不上就是矛盾,整份不採信
KIND_BY_RC = {
    0: {OK},
    1: {MISSING},
    2: {TIMEOUT, LAUNCH, IMPORT},
    3: {CONFIG},
    4: {INTERNAL},
}


def status_problem(data, actual_rc: int) -> str:
    """回空字串=可以採信;否則回不採信的原因(要印給使用者看)。"""
    if not isinstance(data, dict):
        return f"狀態檔不是物件({type(data).__name__})"
    rc = data.get("rc")
    if isinstance(rc, bool) or not isinstance(rc, int):
        return f"rc 不是整數:{rc!r:.40}"
    if rc != actual_rc:
        return f"狀態檔說 rc={rc},實際是 {actual_rc}"
    ok = data.get("ok")
    if not isinstance(ok, bool):
        return f"ok 不是布林:{ok!r:.40}"
    if ok != (actual_rc == 0):
        return f"ok={ok} 與實際退出碼 {actual_rc} 不符"
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in KIND_BY_RC.get(actual_rc, set()):
        return f"kind={kind!r:.40} 不屬於退出碼 {actual_rc} 的合法種類"
    rec = data.get("recovered", False)
    if not isinstance(rec, bool):
        return f"recovered 不是布林:{rec!r:.40}"
    if rec:
        # 「重試才成功」是有前提的:成功、而且真的重試過、而且留得出第一次的錯
        if actual_rc != 0 or kind != OK:
            return "recovered=true 只可能出現在成功的結果上"
        tries = data.get("tries")
        if isinstance(tries, bool) or not isinstance(tries, int) or tries < 2:
            return f"recovered=true 但 tries={tries!r:.20}(重試過才會 >= 2)"
        first = data.get("first_error")
        if not isinstance(first, str) or not first.strip():
            return "recovered=true 但沒有第一次的錯誤訊息"
    return ""


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("MISMATCH\t用法:狀態驗證.py <狀態檔> <實際退出碼>")
        return 0
    try:
        rc = int(args[1])
    except (TypeError, ValueError):
        print(f"MISMATCH\t實際退出碼不是整數:{args[1]!r}")
        return 0
    try:
        data = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    except Exception as e:      # noqa: BLE001 —— 讀不了/半份 JSON 都算不可採信
        print(f"MISMATCH\t狀態檔讀不了:{type(e).__name__}")
        return 0
    why = status_problem(data, rc)
    if why:
        print(f"MISMATCH\t{why}")
    else:
        print(f"{data['kind']}\t{'1' if data.get('recovered') else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
