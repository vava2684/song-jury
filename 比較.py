# -*- coding: utf-8 -*-
"""比較.py — PK 與「重複抽卡比較」的**可執行**比較器(只用標準庫)。

⛔ 為什麼要有這支(Codex R15):README 把 PK 與抽卡列為三種模式之二,
   但 repo 裡沒有任何比較程式、schema 或公式,只有「由 AI 編排」一句話。
   於是同一批資料在不同對話裡可以合法地得出不同冠軍、不同落差 ——
   那不是評測系統,是即興發揮。這支把規則寫死成程式與固定輸出。

用法:
    python 比較.py pk    --lang zh a_評審團.json b_評審團.json [...]
    python 比較.py takes --group 抽卡A  t1_評審團.json t2_評審團.json [...]
    (加 --json 出檔;預設印人可讀摘要 + 機器可讀 JSON 到 stdout)

硬規則(全部 fail-closed,違反就非零退出、不出結果):
  · 每份輸入都要通過 驗證報告.validate(完整九柱、schema、合成自洽)。
  · 所有輸入的 scoring_contract 必須**相同**;不同版就拒絕(尺不一樣不能比)。
  · PK:必須明確指定 --lang,而且只比同一語言 —— 四把語言尺維度數與軸不可共量。
       ⛔ 語言不是猜的:報告裡沒有語言欄位,所以由呼叫者宣告(或 manifest 提供)。
  · 抽卡:必須指定 --group;比較的是**曲側全部八柱**,不是只有三個模型分。
       ⚠️ 評詞標準舊版寫「只有物理/SongEval/Audiobox 會隨 take 變」是錯的:
          不同 take 的人聲、和聲、編曲、旋律、律動、曲風當然都會變。

排名與並列(版本化,寫死在 RANKING 裡):
  · 主排序鍵 = 曲側合成(契約權重算出來的那個數字)。
  · **統計並列**:差距 < TIE_THRESHOLD 視為並列,報告會明寫「並列」而不是硬排名次。
    這個門檻是保守的顯示規則,不是統計檢定 —— 系統沒有重複量測的變異數,
    不可能給真的信賴區間,所以誠實用固定門檻並講清楚它是什麼。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from 驗證報告 import CONTRACTS, REQUIRED_PILLARS, validate   # noqa: E402

COMPARE_CONTRACT = "compare-v1"
TIE_THRESHOLD = 1.0     # 曲側合成差距 < 1.0 分 → 顯示為並列(保守顯示規則,非統計檢定)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class CompareError(RuntimeError):
    """輸入不符合比較的前提。⛔ 一律不出結果,不做「盡量比一比」。"""


def load_report(path: Path) -> dict:
    """讀一份 _評審團.json,先過獨立裁判再用。"""
    why = validate(path)
    if why:
        raise CompareError(f"{path.name} 不是可比較的完整報告:{why}")
    d = json.loads(path.read_text(encoding="utf-8"))
    pt = d["pillar_totals"]
    return {
        "file": path.name,
        "song": path.stem.replace("_評審團", ""),
        "contract": d.get("scoring_contract") or pt.get("scoring_contract"),
        "composite": float(pt["曲側合成"]),
        "pillars": {k: float(pt["柱分"][k]["score"]) for k in REQUIRED_PILLARS},
    }


def _same_contract(items):
    names = {it["contract"] for it in items}
    if len(names) > 1:
        raise CompareError(f"這幾份報告的計分契約不同:{sorted(names)} —— 尺不一樣不能比")
    name = names.pop()
    if name not in CONTRACTS:
        raise CompareError(f"不認得的計分契約:{name!r}")
    return name


def _rank(items):
    """依曲側合成排名;差距 < TIE_THRESHOLD 的相鄰者標成並列。"""
    ordered = sorted(items, key=lambda x: -x["composite"])
    out, rank = [], 0
    for i, it in enumerate(ordered):
        tie = i > 0 and (ordered[i - 1]["composite"] - it["composite"]) < TIE_THRESHOLD
        if not tie:
            rank = i + 1
        out.append({**it, "rank": rank, "tied_with_previous": tie})
    return out


def compare_pk(paths, lang: str):
    if not lang:
        raise CompareError("PK 必須指定 --lang(四把語言尺不可共量,語言不能用猜的)")
    items = [load_report(p) for p in paths]
    if len(items) < 2:
        raise CompareError("PK 至少要兩首")
    contract = _same_contract(items)
    ranked = _rank(items)
    return {
        "compare_contract": COMPARE_CONTRACT,
        "mode": "pk",
        "language": lang,
        "scoring_contract": contract,
        "tie_threshold": TIE_THRESHOLD,
        "n": len(items),
        "ranking": [{"rank": r["rank"], "song": r["song"], "composite": r["composite"],
                     "tied_with_previous": r["tied_with_previous"]} for r in ranked],
        "per_pillar": {k: {r["song"]: r["pillars"][k] for r in ranked}
                       for k in REQUIRED_PILLARS},
        "pillar_winners": {k: max(ranked, key=lambda r: r["pillars"][k])["song"]
                           for k in REQUIRED_PILLARS},
        "note": ("⛔ 只在同語言、同計分契約、都是完整九柱評測時成立;"
                 "並列門檻是保守顯示規則,不是統計檢定。詞柱不在曲側合成內。"),
    }


def compare_takes(paths, group: str):
    if not group:
        raise CompareError("抽卡比較必須指定 --group(同一份詞+prompt 的那組)")
    items = [load_report(p) for p in paths]
    if len(items) < 2:
        raise CompareError("抽卡比較至少要兩個 take")
    contract = _same_contract(items)
    ranked = _rank(items)
    spread = {k: round(max(i["pillars"][k] for i in items)
                       - min(i["pillars"][k] for i in items), 1)
              for k in REQUIRED_PILLARS}
    comp = [i["composite"] for i in items]
    return {
        "compare_contract": COMPARE_CONTRACT,
        "mode": "takes",
        "group": group,
        "scoring_contract": contract,
        "n": len(items),
        # ⭐ 「該留哪一個」= 曲側合成最高的那個(明確定義,不是含糊的「綜合分」)
        "best_take": ranked[0]["song"],
        "best_composite": ranked[0]["composite"],
        "composite_spread": round(max(comp) - min(comp), 1),
        "ranking": [{"rank": r["rank"], "take": r["song"], "composite": r["composite"],
                     "tied_with_previous": r["tied_with_previous"]} for r in ranked],
        # ⛔ 八柱**全部**都要看落差:舊規格只比物理/SongEval/Audiobox,
        #    宣稱「只有這些會隨 take 變」是錯的(人聲/和聲/編曲/律動都會變)。
        "pillar_spread": spread,
        "most_volatile_pillar": max(spread, key=spread.get),
        "note": ("同一份詞+prompt 的多個 take;詞柱共用不重複評。"
                 "落差大的柱=這個 prompt 在那個面向不穩定。"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="song-jury 比較器(PK / 抽卡)")
    ap.add_argument("mode", choices=["pk", "takes"])
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--lang", help="PK 專用:這批歌的語言(zh/en/ja/ko)")
    ap.add_argument("--group", help="抽卡專用:這組 take 的識別名")
    ap.add_argument("--json", type=Path, help="把結果寫成 JSON 檔")
    a = ap.parse_args(argv)
    try:
        out = (compare_pk(a.reports, a.lang) if a.mode == "pk"
               else compare_takes(a.reports, a.group))
    except CompareError as e:
        print(f"⛔ 不能比較:{e}", file=sys.stderr)
        return 2
    text = json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)
    if a.json:
        a.json.write_text(text, encoding="utf-8")
        print(f"已寫出:{a.json}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
