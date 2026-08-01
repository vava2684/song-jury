# -*- coding: utf-8 -*-
"""驗證報告.py — 驗一份 評審團 JSON 是不是「本輪產出的完整評測」(只用標準庫)。

⛔ 為什麼要有它(Codex R12):-VerifyModels 只看「exit 0 + 檔案存在」——
   stub 寫個 `{}` 也被宣稱「完整評測=True」。成功訊息宣稱了沒驗過的事,
   是最高等級的假陽性。這支獨立把 JSON 拆開驗,退出碼契約再迴歸也擋得住。

用法:python 驗證報告.py <報告.json> [--newer-than <unix epoch>]
驗:頂層 dict、pillar_totals dict、完整評測 is True、缺柱==[]、
    曲側合成是 0-100 有限數字、八根曲側柱的鍵都在柱分裡、
    (--newer-than)檔案 mtime 晚於基準 —— 確認是本輪新產物不是舊檔。
退出碼:0=完整;1=不完整/格式壞/舊檔(原因印在 stdout)。
"""
import json
import math
import sys
from pathlib import Path

REQUIRED_PILLARS = ("人聲", "和聲", "結構編曲", "聲學", "旋律記憶", "真實風格", "整體", "律動")

# ⭐ 裁判**自己凍結**一份曲側八柱權重(重構庭 2026-07-25 定版),用來重算曲側合成。
# ⛔ 不可以信報告裡的「柱權重」:產出端算錯合成時,權重多半也被一起改壞
#    (Codex R14:八柱 score 全 0、曲側合成 100,舊裁判照樣 PASS)。
# ⚠️ 這份要跟 評審團.PILLAR_W 的曲側部分一致;test_packaging 有測試釘住兩邊同步。
CANON_PILLAR_W = {"人聲": 15.2, "和聲": 13.6, "結構編曲": 12.6, "聲學": 12.1,
                  "旋律記憶": 6.1, "真實風格": 6.1, "整體": 5.1, "律動": 4.0}
COMPOSITE_TOL = 0.15      # 產出端 round(...,1) → 容差給到 0.15 足夠,再大就是真的算錯

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def validate(path: Path, newer_than: float = None) -> str:
    """回空字串=通過;否則回第一個不合格的原因(講人話)。"""
    if not path.exists():
        return f"檔案不存在:{path}"
    if newer_than is not None and path.stat().st_mtime <= newer_than:
        return "檔案不是本輪新產物(mtime 早於驗證開始時間)—— 讀到舊報告了"
    def _reject_const(x):
        # ⛔ json.loads 預設吃 NaN/Infinity —— 那不是合法 JSON,別人的解析器會炸,
        #    而且 NaN 混進柱分還會一路無聲汙染(Codex R13)。這裡直接拒收。
        raise ValueError(f"非標準 JSON 常數:{x}")

    try:
        d = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_const)
    except ValueError as e:
        return f"JSON 不合格:{e}"
    except Exception as e:
        return f"JSON 解析失敗:{type(e).__name__}"
    if not isinstance(d, dict):
        return f"頂層是 {type(d).__name__},應為 dict"
    pt = d.get("pillar_totals")
    if not isinstance(pt, dict):
        return "缺 pillar_totals(舊格式或產出不完整)"
    if pt.get("完整評測") is not True:
        return f"完整評測={pt.get('完整評測')!r},不是 True(缺柱:{pt.get('缺柱')})"
    if pt.get("缺柱") != []:
        return f"缺柱不是空的:{pt.get('缺柱')}"
    v = pt.get("曲側合成")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not (0 <= v <= 100):
        return f"曲側合成不是 0-100 的有限數字:{v!r}"
    柱分 = pt.get("柱分")
    if not isinstance(柱分, dict):
        return "柱分不是 dict"
    missing = [p for p in REQUIRED_PILLARS if p not in 柱分]
    if missing:
        return f"柱分缺鍵:{missing}"
    # ⛔ 只驗「柱名在不在」是裝飾:柱值換成 None / {} / {"score": NaN} / true / 999
    #    以前全部 PASS(Codex R13 五連探針)。每一柱的 score 都要是
    #    非 bool、有限、0-100 的數字 —— 這才是「九柱真的算出來了」。
    scores = {}
    for name in REQUIRED_PILLARS:
        det = 柱分.get(name)
        if not isinstance(det, dict):
            return f"柱分[{name}] 不是 dict(拿到 {type(det).__name__})"
        s = det.get("score")
        if isinstance(s, bool) or not isinstance(s, (int, float)):
            return f"柱分[{name}].score 不是數字:{s!r}"
        if not math.isfinite(s) or not (0 <= s <= 100):
            return f"柱分[{name}].score 不是 0-100 的有限數字:{s!r}"
        scores[name] = float(s)
        # ⛔ 內層 schema 也要驗:items 必須是 dict、missing 必須是字串 list ——
        #    不驗的話 items=[]、missing="junk" 這種破結構照樣被蓋章
        #    (Codex R14:裁判只驗「八個 score 各自像數字」)。
        items = det.get("items")
        if items is not None and not isinstance(items, dict):
            return f"柱分[{name}].items 不是 dict(拿到 {type(items).__name__})"
        miss = det.get("missing")
        if miss is not None and (not isinstance(miss, list)
                                 or any(not isinstance(x, str) for x in miss)):
            return f"柱分[{name}].missing 不是字串陣列:{miss!r}"

    # ⛔ 完整評測時「缺柱權重合計」必須是 0 —— 完整=true、缺柱=[] 卻寫 99.9
    #    是內部自相矛盾,代表產出端的完整性計算壞了(Codex R14 探針)。
    lostw = pt.get("缺柱權重合計", 0)
    if isinstance(lostw, bool) or not isinstance(lostw, (int, float)) or not math.isfinite(lostw):
        return f"缺柱權重合計不是有限數字:{lostw!r}"
    if abs(float(lostw)) > 1e-9:
        return f"完整評測卻有缺柱權重 {lostw} —— 完整性欄位自相矛盾"

    # ⛔ 曲側合成用**裁判自己的權重**重算一次:八柱 score 全 0 卻宣稱合成 100,
    #    舊裁判照樣 PASS。權重不信報告裡的(那會被一起改壞)。
    wsum = sum(CANON_PILLAR_W.values())
    expect = round(sum(CANON_PILLAR_W[k] * scores[k] for k in REQUIRED_PILLARS) / wsum, 1)
    if abs(expect - float(v)) > COMPOSITE_TOL:
        return (f"曲側合成 {v} 與八柱重算值 {expect} 不符(差 {abs(expect - float(v)):.2f})"
                f" —— 合成算錯或柱分被竄改")

    # 曲側含柱(有的話)要正好是八柱
    inc = pt.get("曲側含柱")
    if inc is not None and sorted(inc) != sorted(REQUIRED_PILLARS):
        return f"曲側含柱與必要八柱不一致:{inc!r}"
    return ""


def main(argv) -> int:
    if len(argv) < 2:
        print("用法:python 驗證報告.py <報告.json> [--newer-than <epoch>]")
        return 1
    newer = None
    if "--newer-than" in argv:
        newer = float(argv[argv.index("--newer-than") + 1])
    why = validate(Path(argv[1]), newer)
    if why:
        print(f"VERIFY_BAD {why}")
        return 1
    print("VERIFY_OK 九柱完整、格式合格、本輪新產物")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
