#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""演唱聽感(SingMOS Pro)—— 人聲柱的聽感層第二意見(重構庭定版:人聲柱內 12%)。

用法:.venv-audition\\Scripts\\python.exe 演唱聽感.py <vocals音檔> --json 出檔
輸入:Demucs 人聲軌(_stems 快取);16kHz、20 秒窗、RMS<0.01 靜音窗跳過、最多 12 窗均值。
輸出:{"mos": 原始 1-5, "score": 0-100(83 首基準百分位,夾 5..95), "n_win": N}
分數映射與試音基準同源(_錨參照/基準.json);任何失敗 → degraded JSON,不炸產線。
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.resolve()))
from 評審團 import iter_windows  # noqa: E402  (評審團.py 頂層只用標準庫,跨 venv import 安全)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vocal")
    ap.add_argument("--json", dest="json_out", required=True)
    a = ap.parse_args()
    out = {"component": "演唱聽感(SingMOS)", "degraded": False}
    try:
        import numpy as np
        import torch
        import librosa
        base = json.load(open(BASE / "_錨參照/基準.json", encoding="utf-8"))["base_mos"]
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        pred = torch.hub.load("South-Twilight/SingMOS:v1.1.2", "singmos_pro",
                              trust_repo=True).to(dev).eval()
        y, _ = librosa.load(a.vocal, sr=16000, mono=True)
        WIN = 16000 * 20
        scores = []
        # 切窗用共用函式(見 評審團.py 的 iter_windows):曾經兩支各寫一次
        # range(0, len-WIN, WIN),同一個 off-by-one 犯了兩遍,漏掉最後一個完整窗。
        for s in iter_windows(len(y), WIN):
            w = y[s:s + WIN]
            if float(np.sqrt(np.mean(w ** 2))) < 0.01:
                continue
            t = torch.from_numpy(w).float().unsqueeze(0).to(dev)
            with torch.no_grad():
                sc = pred(t, torch.tensor([t.shape[1]]).to(dev))
            scores.append(float(torch.as_tensor(sc).squeeze().float().cpu()))
            if len(scores) >= 12:
                break
        if not scores:
            raise RuntimeError("全曲人聲窗皆低於 RMS 門檻(可能純器樂)")
        mos = float(np.mean(scores))
        below = sum(1 for x in base if x < mos) + 0.5 * sum(1 for x in base if x == mos)
        out.update({"mos": round(mos, 3), "n_win": len(scores),
                    "score": round(float(np.clip(below / len(base) * 100.0, 5.0, 95.0)), 1)})
    except Exception as e:
        out.update({"degraded": True, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    Path(a.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(("✓ MOS %.2f → %s/100" % (out.get("mos", 0), out.get("score"))) if not out["degraded"]
          else f"✗ {out['error']}")


if __name__ == "__main__":
    main()
