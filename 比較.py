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
from 驗證報告 import CONTRACTS, REQUIRED_PILLARS, validate_data   # noqa: E402

COMPARE_CONTRACT = "compare-v1"
TIE_THRESHOLD = 1.0     # 曲側合成差距 < 1.0 分 → 顯示為並列(保守顯示規則,非統計檢定)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class CompareError(RuntimeError):
    """輸入不符合比較的前提。⛔ 一律不出結果,不做「盡量比一比」。"""


def load_report(path: Path) -> dict:
    """讀一份 _評審團.json,先過獨立裁判再用。

    ⛔ 只讀一次 bytes(Codex R16-6 TOCTOU):舊版先 validate(path) 再
       read_text() 第二次 —— 兩次之間檔案被原子換掉的話,排名用的是**沒被驗過**
       的內容(探針把人聲改成 999 照樣進榜)。
    ⛔ 契約一律 strict:比較是「同一把尺才能比」,沒有版本證據就不能上場
       (舊格式相容只給單檔檢視用)。"""
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise CompareError(f"讀不到 {path.name}:{type(e).__name__}")
    why = validate_data(raw, path.name, require_contract=True)
    if why:
        raise CompareError(f"{path.name} 不是可比較的完整報告:{why}")
    d = json.loads(raw.decode("utf-8"))
    pt = d["pillar_totals"]
    return {
        # ⛔ report_id 必須不可碰撞:不同資料夾的同名報告會在 per_pillar 互相覆蓋,
        #    高分那份被低分那份用同一個 key 蓋掉(Codex R16-1 實測 n=2 但表只剩一筆)。
        "report_id": str(path.resolve()),
        "file": path.name,
        "song": path.stem.replace("_評審團", ""),
        "contract": d.get("scoring_contract") or pt.get("scoring_contract"),
        "composite": float(pt["曲側合成"]),
        "pillars": {k: float(pt["柱分"][k]["score"]) for k in REQUIRED_PILLARS},
    }


def _reject_duplicates(paths):
    """同一份輸入不可重複上場(A 對 A 會被包裝成合法 PK —— Codex R16-2)。"""
    seen = []
    for p in paths:
        rp = Path(p).resolve()
        for q in seen:
            if rp == q or (rp.exists() and q.exists() and rp.samefile(q)):
                raise CompareError(f"同一份報告被放進來兩次:{rp.name} —— 不能自己跟自己比")
        seen.append(rp)


def _reject_dup_labels(items):
    """顯示名撞名時直接拒絕:輸出裡的排名/逐柱/冠軍必須能對回真實檔案。"""
    from collections import Counter
    dup = [n for n, c in Counter(i["song"] for i in items).items() if c > 1]
    if dup:
        raise CompareError(
            f"有同名的報告:{dup} —— 排名與逐柱表會對不回來源檔案。"
            f"請把檔案改成不同名字再比(來源:"
            f"{[i['report_id'] for i in items if i['song'] in dup]})")


def _same_contract(items):
    names = {it["contract"] for it in items}
    if len(names) > 1:
        # ⛔ 訊息裡不可以 sorted() 混型別(None 與 str 會 TypeError,
        #    連錯誤都噴不出來 —— Codex R16-5)。
        raise CompareError(f"這幾份報告的計分契約不同:"
                           f"{sorted(map(repr, names))} —— 尺不一樣不能比")
    name = names.pop()
    if name not in CONTRACTS:
        raise CompareError(f"不認得的計分契約:{name!r}")
    return name


def _rank(items):
    """依曲側合成排名;與**該並列組的最高分**差距 < TIE_THRESHOLD 才算並列。

    ⛔ 不可以用「跟前一名比」(Codex R16-3):並列關係不具傳遞性 ——
       100 / 99.2 / 98.4 在相鄰比較下會鏈式擴張成全部 rank 1,
       但頭尾差 1.6 已超過門檻。改用 complete-link(與組首比),
       上例正確結果是前兩首並列第 1、第三首第 3。"""
    ordered = sorted(items, key=lambda x: (-x["composite"], x["report_id"]))
    out, rank, head = [], 0, None
    for i, it in enumerate(ordered):
        tie = head is not None and (head - it["composite"]) < TIE_THRESHOLD
        if not tie:
            rank = i + 1
            head = it["composite"]
        out.append({**it, "rank": rank, "tied_with_previous": tie})
    return out


def _winners(items, key):
    """回 (贏家清單, 是否同分)。⛔ 不可以用單一 max ——
    同分時它會依無關的排序或輸入順序任選一個,把真正的平手偽裝成唯一冠軍
    (Codex R16-4)。"""
    best = max(key(i) for i in items)
    names = [i["song"] for i in items if key(i) == best]
    return names, len(names) > 1


LANGS = ("zh", "en", "ja", "ko")


def compare_pk(paths, lang: str):
    # ⛔ 一個檢查涵蓋「沒給」與「給錯」:分成兩層寫的話,前一層拿掉了後一層照樣擋,
    #    測試變成擋不住迴歸的裝飾品(變異驗證抓到過)。
    if lang not in LANGS:
        raise CompareError(
            f"PK 必須指定有效的 --lang(拿到 {lang!r})—— 只有四把尺 {LANGS},"
            f"語言不能用猜的:四把尺不可共量")
    _reject_duplicates(paths)
    items = [load_report(p) for p in paths]
    if len(items) < 2:
        raise CompareError("PK 至少要兩首")
    _reject_dup_labels(items)
    contract = _same_contract(items)
    ranked = _rank(items)
    winners = {k: _winners(ranked, lambda r, kk=k: r["pillars"][kk]) for k in REQUIRED_PILLARS}
    return {
        "compare_contract": COMPARE_CONTRACT,
        "mode": "pk",
        "language": lang,
        "scoring_contract": contract,
        "tie_threshold": TIE_THRESHOLD,
        "n": len(items),
        "ranking": [{"rank": r["rank"], "song": r["song"], "report_id": r["report_id"],
                     "composite": r["composite"],
                     "tied_with_previous": r["tied_with_previous"]} for r in ranked],
        # ⛔ 以 report_id 為鍵:同名報告會互相覆蓋,排名與逐柱表對不回來源(R16-1)
        "per_pillar": {k: {r["report_id"]: r["pillars"][k] for r in ranked}
                       for k in REQUIRED_PILLARS},
        # ⛔ 冠軍是**清單**:同分時挑一個等於把平手偽裝成唯一冠軍(R16-4)
        "pillar_winners": {k: {"songs": w[0], "tie": w[1]} for k, w in winners.items()},
        "note": ("⛔ 只在同語言、同計分契約、都是完整九柱評測時成立;"
                 "並列門檻是保守顯示規則(與該並列組最高分比,可傳遞),不是統計檢定。"
                 "⚠️ 語言由呼叫者宣告 —— 報告本身沒有語言欄位,程式無法代為證明。"
                 "詞柱不在曲側合成內。"),
    }


def compare_takes(paths, group: str):
    if not group:
        raise CompareError("抽卡比較必須指定 --group(同一份詞+prompt 的那組)")
    _reject_duplicates(paths)
    items = [load_report(p) for p in paths]
    if len(items) < 2:
        raise CompareError("抽卡比較至少要兩個 take")
    _reject_dup_labels(items)
    contract = _same_contract(items)
    ranked = _rank(items)
    best_names, best_tie = _winners(ranked, lambda r: r["composite"])
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
        # ⭐ 「該留哪一個」= 曲側合成最高的(明確定義,不是含糊的「綜合分」)
        #    ⛔ 回清單 + tie 旗標:同分時挑輸入順序第一個等於騙人(R16-4)
        "best_takes": best_names,
        "best_take_tie": best_tie,
        "best_composite": ranked[0]["composite"],
        "composite_spread": round(max(comp) - min(comp), 1),
        "ranking": [{"rank": r["rank"], "take": r["song"], "composite": r["composite"],
                     "tied_with_previous": r["tied_with_previous"]} for r in ranked],
        # ⛔ 八柱**全部**都要看落差:舊規格只比物理/SongEval/Audiobox,
        #    宣稱「只有這些會隨 take 變」是錯的(人聲/和聲/編曲/律動都會變)。
        "pillar_spread": spread,
        "most_volatile_pillar": max(spread, key=spread.get),
        "note": ("同一份詞+prompt 的多個 take;詞柱共用不重複評。"
                 "落差大的柱=這個 prompt 在那個面向不穩定。"
                 "⚠️ 『同一份詞+prompt』由呼叫者用 --group 宣告 —— 報告裡沒有"
                 "lyrics/prompt 指紋,程式無法代為證明(要硬保證需產出端寫入)。"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="song-jury 比較器(PK / 抽卡)")
    ap.add_argument("mode", choices=["pk", "takes"])
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--lang", choices=LANGS, help="PK 專用:這批歌的語言")
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
