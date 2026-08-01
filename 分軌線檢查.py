# -*- coding: utf-8 -*-
"""分軌線(Demucs 分軌 + 和聲辨識)到底能不能用 —— 安裝器與測試共用的唯一實作。

⚠️ 這條線斷掉 = 結構編曲柱(12.6%)+ 和聲柱(13.6%)一起出事,合計 26.2% 權重。

🔴 2026-08-02 自己實跑踩到的事故:
   安裝跑完 `[10/10] 自我檢查` 印「和聲 13.6% 缺項 → 這台機器評不出有效分數」、exit 1;
   **同一次執行**接下來的 `-VerifyModels` 真跑九柱卻一路順利(Demucs 六軌分軌走 GPU、
   和聲柱 57.9),獨立裁判也給 VERIFY_OK。也就是自我檢查的結論是**錯的**。
   兩個要命的地方:
     ① 那次探測失敗**一句原因都沒印** —— 使用者只看到「你缺一根柱子」,無從查起;
     ② 剛裝完的 venv 是最容易出現暫時性失敗的時刻(幾 GB 剛寫下去、防毒正在掃、
        第一次 import torch/numba 要建快取)。一次失敗就定生死,等於拿最不穩的那一秒
        當永久結論。

   → 這支的兩條規矩:**失敗一定要講出真正的錯誤**、**暫時性失敗要再給一次機會**。

退出碼(安裝器據此判,⛔ 別自己再猜一次):
   0 = 整條線可用
   1 = 有 demucs,但少了 librosa/numpy/soundfile 其一 → 分軌會跑、和聲柱整根降級
   2 = 整條線不可用(連 demucs 都 import 不起來,或找不到任何 python)
"""
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))

# ⛔ 模組清單與「用哪支 python」都跟評審團.py 拿,不可以在這裡另抄一份 ——
#    抄了就會有「安裝器說可以、實際跑分說不行」的兩套真理(Codex R13 的老 bug)。
from 評審團 import DEMUCS_LINE_MODS, DEMUCS_PY   # noqa: E402

RETRY_PAUSE = 5.0        # 秒;給剛寫完的 venv / 防毒掃描一點時間
PROBE_TIMEOUT = 600.0    # 秒;冷啟動第一次 import torch+numba 可能很久,寧可等


def probe(py, mods=DEMUCS_LINE_MODS, attempts=2, timeout=PROBE_TIMEOUT,
          pause=RETRY_PAUSE):
    """真的用那支直譯器 import 一次。回 (成功?, 失敗原因)。

    ⛔ attempts 預設 2:剛裝完那一刻的失敗多半是暫時的(見檔頭事故)。
    ⛔ 失敗原因一定要帶回去 —— 沒有原因的「缺柱」是最難修的那種訊息。"""
    why = ""
    for i in range(attempts):
        if i:
            time.sleep(pause)
        try:
            r = subprocess.run([str(py), "-c", "import " + ", ".join(mods)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            why = f"import 逾時({timeout:.0f}s)"
            continue
        except OSError as e:
            why = f"啟動 {py} 失敗:{type(e).__name__}: {e}"
            continue
        if r.returncode == 0:
            return True, ""
        tail = ((r.stderr or r.stdout or "").strip().splitlines() or ["(沒有輸出)"])[-1]
        why = f"退出碼 {r.returncode}:{tail[:300]}"
    return False, why


def main(argv=None) -> int:
    py = (argv or sys.argv[1:] or [DEMUCS_PY])[0]
    if not py or not Path(py).exists():
        print(f"DEMUCS_LINE_BAD 找不到可用的 python:{py!r}")
        return 2
    ok, why = probe(py)
    if ok:
        print(f"DEMUCS_LINE_OK {py}")
        return 0
    # 分兩種:只缺依賴(分軌還能跑、和聲柱死)vs 整條線不可用
    has_demucs, _ = probe(py, ("demucs",), attempts=1)
    print(f"DEMUCS_LINE_BAD {py}\n"
          f"           需要:{', '.join(DEMUCS_LINE_MODS)}\n"
          f"           實際:{why}")
    return 1 if has_demucs else 2


if __name__ == "__main__":
    sys.exit(main())
