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
# ⛔ 容差不可放到 0.15:兩邊都是「一位小數的柱分 × 同一組固定權重 → round(,1)」,
#    根本沒有 0.1 級的浮點不確定性,0.15 等於放過一整個顯示刻度的錯誤(Codex R15)。
#    0.05 只吸收 round 的最後一位表示誤差。
COMPOSITE_TOL = 0.05

# ⭐ 計分契約版本:權重/曲側柱集合/取整規則的**具名快照**。
# ⛔ 為什麼要版本(Codex R15):現在靠打包測試強迫裁判權重 == 評審團權重,
#    那麼「權重正當改版」與「兩邊一起改錯」在裁判眼裡完全一樣,而且舊報告
#    也無法被明確拒絕。改成:報告自報 scoring_contract,裁判查表;
#    合法改版=新增一個版本,不覆寫舊的。
CONTRACTS = {
    "2026-07-25-v1": {
        "pillars": REQUIRED_PILLARS,
        "weights": CANON_PILLAR_W,
        "composite_round": 1,
        "note": "重構庭 2026-07-25 定版:詞柱 25.3% 不在曲側合成內,曲側八柱自我歸一化",
    },
}
DEFAULT_CONTRACT = "2026-07-25-v1"   # 報告沒自報版本時(舊格式)用這個,但會留痕

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

    # ⭐ 計分契約:報告自報版本 → 裁判查表拿權重與柱集合。
    # ⛔ 不認得的版本一律拒收:那可能是新契約(裁判要跟上)或竄改,
    #    兩種都不該由這支替它背書(Codex R15)。
    cname = d.get("scoring_contract") or pt.get("scoring_contract")
    if cname is None:
        # 舊格式(這個欄位 2026-08-01 才加)→ 用預設契約驗,但要**講出來**:
        # 這份報告沒有版本證據,只是「看起來像」預設契約。
        cname = DEFAULT_CONTRACT
        print(f"⚠ 報告沒有 scoring_contract(舊格式)→ 以預設契約 {cname} 驗證",
              file=sys.stderr)
    else:
        if not isinstance(cname, str) or cname not in CONTRACTS:
            return (f"不認得的計分契約:{cname!r} —— 可能是新版契約(請更新裁判)"
                    f"或報告被竄改;認得的有 {sorted(CONTRACTS)}")
    contract = CONTRACTS[cname]
    pillars, weights = contract["pillars"], contract["weights"]

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
    missing = [p_ for p_ in pillars if p_ not in 柱分]
    if missing:
        return f"柱分缺鍵:{missing}"

    # ⛔ 欄位一律**必填**,不可「有值才驗」:省略 items/missing 時 None 直接放行,
    #    等於獨立裁判替不完整 schema 背書(Codex R15 探針:全部省略照樣 ACCEPT)。
    scores = {}
    for name in pillars:
        det = 柱分.get(name)
        if not isinstance(det, dict):
            return f"柱分[{name}] 不是 dict(拿到 {type(det).__name__})"
        s = det.get("score")
        if isinstance(s, bool) or not isinstance(s, (int, float)):
            return f"柱分[{name}].score 不是數字:{s!r}"
        if not math.isfinite(s) or not (0 <= s <= 100):
            return f"柱分[{name}].score 不是 0-100 的有限數字:{s!r}"
        scores[name] = float(s)
        if "items" not in det:
            return f"柱分[{name}] 少了 items(完整評測必須列出細項,空 dict 也要寫)"
        if not isinstance(det["items"], dict):
            return f"柱分[{name}].items 不是 dict(拿到 {type(det['items']).__name__})"
        if "missing" not in det:
            return f"柱分[{name}] 少了 missing(沒有缺項就寫空陣列)"
        miss = det["missing"]
        if not isinstance(miss, list) or any(not isinstance(x, str) for x in miss):
            return f"柱分[{name}].missing 不是字串陣列:{miss!r}"

    # ⛔ 缺柱權重合計必填(不可 get(...,0) 把「缺鍵」偽造成合法的 0)
    if "缺柱權重合計" not in pt:
        return "少了 缺柱權重合計(完整評測必須明寫 0)"
    lostw = pt["缺柱權重合計"]
    if isinstance(lostw, bool) or not isinstance(lostw, (int, float)) or not math.isfinite(lostw):
        return f"缺柱權重合計不是有限數字:{lostw!r}"
    if abs(float(lostw)) > 1e-9:
        return f"完整評測卻有缺柱權重 {lostw} —— 完整性欄位自相矛盾"

    # ⛔ 曲側合成用**契約裡的權重**重算:八柱 score 全 0 卻宣稱合成 100,
    #    舊裁判照樣 PASS。權重不信報告裡的(那會被一起改壞)。
    wsum = sum(weights.values())
    expect = round(sum(weights[k] * scores[k] for k in pillars) / wsum,
                   contract["composite_round"])
    if abs(expect - float(v)) > COMPOSITE_TOL:
        return (f"曲側合成 {v} 與八柱重算值 {expect} 不符(差 {abs(expect - float(v)):.2f})"
                f" —— 合成算錯或柱分被竄改")

    # ⛔ 曲側含柱必填、必須是 list、內容必須剛好是契約的八柱且不重複。
    #    (舊版 optional 又用 sorted():dict 會被 sorted 成 keys 而矇混過關,
    #     scalar 則直接 TypeError 崩掉而不是回 VERIFY_BAD —— Codex R15。)
    if "曲側含柱" not in pt:
        return "少了 曲側含柱"
    inc = pt["曲側含柱"]
    if not isinstance(inc, list) or any(not isinstance(x, str) for x in inc):
        return f"曲側含柱不是字串陣列:{inc!r}"
    if len(inc) != len(set(inc)):
        return f"曲側含柱有重複:{inc!r}"
    if sorted(inc) != sorted(pillars):
        return f"曲側含柱與契約的八柱不一致:{inc!r}"
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
