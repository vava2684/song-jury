#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真實距離(MuQ 馬氏)+ AI 感(SONICS,顯示軸)—— 真實性與風格柱(重構庭定版:柱內 60%)。

用法:.venv-audition\\Scripts\\python.exe 真實距離.py <音檔> --json 出檔
- 馬氏距離:MuQ-large 24k 10s窗≤24 平均嵌入,對「得獎 54 分佈」(mu/var)開根均值;
  score = 83 首基準上 (-maha) 的百分位,夾 5..95(越像真實音樂分佈越高)。
- SONICS P(AI):⛔ 不入分(重構庭 19:7「像 AI 不是罪」),顯示軸;
  隨值標註:新版 SUNO 漏抓率 ~31%(概念漂移,訓練語料為舊版)。
任何失敗 → degraded,不炸產線。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--json", dest="json_out", required=True)
    a = ap.parse_args()
    out = {"component": "真實距離+AI感", "degraded": False}
    try:
        import numpy as np
        import torch
        import librosa
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        ref = np.load(BASE / "_錨參照/muq參照.npz")
        mu, var, base = ref["mu"], ref["var"], sorted(ref["base_maha"].tolist())

        from muq import MuQ
        enc = MuQ.from_pretrained("OpenMuQ/MuQ-large-msd-iter").to(dev).eval()
        y, _ = librosa.load(a.audio, sr=24000, mono=True)
        WIN = 240000
        es = []
        for s in range(0, max(1, len(y) - WIN), WIN):
            w = y[s:s + WIN]
            if len(w) < 24000:
                continue
            t = torch.from_numpy(w).float().unsqueeze(0).to(dev)
            with torch.no_grad():
                h = enc(t, output_hidden_states=False)
            es.append(h.last_hidden_state.mean(dim=1).squeeze().float().cpu().numpy())
            if len(es) >= 24:
                break
        emb = np.mean(es, axis=0)
        maha = float(np.sqrt(np.mean((emb - mu) ** 2 / var)))
        below = sum(1 for x in base if x > maha) + 0.5 * sum(1 for x in base if x == maha)
        out["maha"] = round(maha, 4)
        out["score"] = round(float(np.clip(below / len(base) * 100.0, 5.0, 95.0)), 1)
        del enc
        torch.cuda.empty_cache()

        # SONICS(AI 感,顯示軸不入分)—— checkpoint 要自己下載,本專案不隨包。
        #   預設找 ckpt/sonics-alpha-120s/,也可用環境變數 SONG_JURY_SONICS_CKPT 指到別處;
        #   兩者都沒有就直接寫一行說明,不去 HuggingFace 撞一次沒必要的網路錯誤。
        #   取得方式見 README「要自行取得」那節。缺它完全不影響任何一柱的分數。
        _ck = os.environ.get("SONG_JURY_SONICS_CKPT") or str(BASE / "ckpt/sonics-alpha-120s")
        try:
            if not Path(_ck).is_dir():
                raise FileNotFoundError(f"SONICS checkpoint 不在 {_ck}(顯示軸,不影響計分)")
            from sonics import HFAudioClassifier
            m = HFAudioClassifier.from_pretrained(_ck).to(dev).eval()
            y16, _ = librosa.load(a.audio, sr=16000, mono=True)
            MAXLEN = 1920000
            if len(y16) > MAXLEN:
                s0 = (len(y16) - MAXLEN) // 2
                y16 = y16[s0:s0 + MAXLEN]
            y16 = (y16 - y16.mean()) / (y16.std() + 1e-9)
            with torch.no_grad():
                logit = m(torch.from_numpy(y16).float().unsqueeze(0).to(dev))
            out["sonics_p_ai"] = round(float(torch.sigmoid(logit).squeeze().cpu()), 4)
            out["sonics_note"] = "顯示軸不入分(重構庭19:7);新版SUNO漏抓~31%,判「真」不保證非AI"
        except Exception as e:
            out["sonics_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    except Exception as e:
        out.update({"degraded": True, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    Path(a.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(("✓ maha %.3f → %s/100;P(AI)=%s" % (out.get("maha", 0), out.get("score"),
          out.get("sonics_p_ai"))) if not out["degraded"] else f"✗ {out['error']}")


if __name__ == "__main__":
    main()
