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
from 驗證報告 import (CONTRACTS, PCM_CONTRACTS, REQUIRED_PILLARS,   # noqa: E402
                      declared_downgrade,
                     identity_problem, validate_data)

COMPARE_CONTRACT = "compare-v1"
TIE_THRESHOLD = 1.0     # 曲側合成差距 < 1.0 分 → 顯示為並列(保守顯示規則,非統計檢定)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class CompareError(RuntimeError):
    """輸入不符合比較的前提。⛔ 一律不出結果,不做「盡量比一比」。

    ⭐ 每個拒絕都帶一個**穩定的機器碼**(Codex R17-7):測試要驗的是
       「哪一道防線攔的」,不是那句中文長什麼樣。把根因綁在文案上,
       改寫、翻譯、把訊息寫得更好都會變成沒有行為迴歸的紅燈,
       久了就會有人為了讓測試過而不敢改文字。
       ⛔ code 是對外契約的一部分,改字可以,改碼要當成破壞性變更。
    """

    def __init__(self, message, code="compare_error", detail=None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


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
        raise CompareError(f"讀不到 {path.name}:{type(e).__name__}",
                           "unreadable_report", {"path": str(path)})
    why = validate_data(raw, path.name, require_contract=True)
    if why:
        raise CompareError(f"{path.name} 不是可比較的完整報告:{why}",
                           "invalid_report", {"path": str(path), "why": why})
    d = json.loads(raw.decode("utf-8"))
    pt = d["pillar_totals"]
    import hashlib
    # ⛔ 再驗一次身分格式(Codex R18-2):畸形值(list/短字串)以前會一路帶到
    #    set 裡才 raw TypeError,CLI 的 except CompareError 接不到 —— 使用者拿到
    #    traceback,自動化拿到不是契約的錯。這裡轉成有 code 的拒絕。
    why_id = identity_problem(d)
    if why_id:
        raise CompareError(f"{path.name} 的來源身分不合法:{why_id}",
                           "invalid_source_identity", {"path": str(path)})
    return {
        # ⭐ 三層來源身分(R17-3 起,R18-4 補上「解碼後」那層):
        #    file  = 檔案 bytes(換容器/改 metadata 就不同,擋不到重新封裝)
        #    pcm   = 解碼後聲音(這才是「同一段聲音」)
        "evaluation_id": d.get("evaluation_id") or "",
        "source_file_sha256": (d.get("source_file_sha256")
                               or d.get("source_audio_sha256") or ""),
        # ⚠️ 解碼身分要連**版本**一起帶(Codex R19-1):標準面換了就是換一把尺。
        # ⛔ 沒有版本(或版本不認得)的雜湊**一律不採用**(Codex R20-P1-2):
        #    舊版會自己補一個 "pcm-v1" 當成有效證據,於是「全都缺版本」的一批
        #    反而被標成最高等級 decoded-audio —— 對不存在的證據蓋章。
        "source_audio_pcm_sha256": (
            f'{d["source_audio_pcm_contract"]}#{d["source_audio_pcm_sha256"]}'
            if (d.get("source_audio_pcm_sha256")
                and d.get("source_audio_pcm_contract") in PCM_CONTRACTS) else ""),
        "pcm_contract": (d.get("source_audio_pcm_contract")
                         if d.get("source_audio_pcm_contract") in PCM_CONTRACTS else ""),
        # 報告裡「有寫」解碼雜湊(不管版本認不認得)—— 用來分辨兩種降級原因
        "pcm_raw_present": bool(d.get("source_audio_pcm_sha256")),
        # ⭐ 產出端**明講**的降級原因(Codex R22-P2-1):沒有這個就只能猜,
        #    實測會把「格式不支援」說成「沒裝 ffmpeg,裝好重評即可升級」——
        #    叫使用者去做一件完全沒有用的事。
        "pcm_downgrade": declared_downgrade(d),
        "report_bytes_sha256": hashlib.sha256(raw).hexdigest(),
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
    """同一個檔案不可重複上場(A 對 A 會被包裝成合法 PK —— Codex R16-2)。
    這一層擋的是**同一個 inode**:同路徑、相對/絕對別名、symlink、hardlink。
    ⚠️ 擋不到「複製後改名」—— 那是不同 inode,交給 _reject_same_source()。"""
    seen = []
    for p in paths:
        rp = Path(p).resolve()
        for q in seen:
            if rp == q or (rp.exists() and q.exists() and rp.samefile(q)):
                raise CompareError(
                    f"同一份報告被放進來兩次:{rp.name} —— 不能自己跟自己比",
                    "duplicate_input", {"path": str(rp)})
        seen.append(rp)


def _reject_same_source(items):
    """🔴 Codex R17-3:把一份合法報告 **複製 + 改名**,就能當兩首歌/兩個 take 上場。

    `_reject_duplicates` 用 inode 判定,byte-for-byte copy 是不同 inode → 通過;
    報告裡當時只有顯示用的檔名,沒有任何可驗證的來源身分。於是同一次評測可以
    重複投票、灌出假 PK 或假抽卡結論 —— 而且這多半不是攻擊,是整理檔案時的順手複製。

    三層身分,由強到弱:
      ① evaluation_id —— 同一次評測的唯一識別(複製出來的一定相同)
      ② source_audio_sha256 —— 同一個音源不可以在同一場比兩次
      ③ 報告 bytes —— 舊報告沒有前兩者時的退路
    ⚠️ 誠實邊界:舊版報告(沒有 ①②)只剩第 ③ 層,兩次重跑同一首歌的 JSON 會因
       時間戳而不同 → 擋不住。輸出的 note 會講明這件事,不假裝擋得住。"""
    for field, why in (("evaluation_id", "同一次評測的結果被放進來兩次"),
                       ("source_audio_pcm_sha256",
                        "同一段聲音(解碼後在同一個格式面上完全相同)被放進來兩次"),
                       ("source_file_sha256", "同一個音檔的報告被放進來兩次"),
                       ("report_bytes_sha256", "內容完全相同的報告被放進來兩次")):
        seen = {}
        for it in items:
            v = it.get(field)
            if not v:
                continue
            if v in seen:
                raise CompareError(
                    f"{why}:{seen[v]} 與 {it['file']}({field} 相同)"
                    f" —— 改檔名不會讓它變成另一首,請確認是不是複製出來的",
                    "duplicate_source",
                    {"field": field, "files": [seen[v], it["file"]], "value": v})
            seen[v] = it["file"]


def _reject_dup_labels(items):
    """顯示名撞名時直接拒絕:輸出裡的排名/逐柱/冠軍必須能對回真實檔案。"""
    from collections import Counter
    dup = [n for n, c in Counter(i["song"] for i in items).items() if c > 1]
    if dup:
        raise CompareError(
            f"有同名的報告:{dup} —— 排名與逐柱表會對不回來源檔案。"
            f"請把檔案改成不同名字再比(來源:"
            f"{[i['report_id'] for i in items if i['song'] in dup]})",
            "duplicate_label", {"labels": dup})


def _same_contract(items):
    names = {it["contract"] for it in items}
    if len(names) > 1:
        # ⛔ 訊息裡不可以 sorted() 混型別(None 與 str 會 TypeError,
        #    連錯誤都噴不出來 —— Codex R16-5)。
        raise CompareError(f"這幾份報告的計分契約不同:"
                           f"{sorted(map(repr, names))} —— 尺不一樣不能比",
                           "contract_mismatch", {"contracts": sorted(map(repr, names))})
    name = names.pop()
    if name not in CONTRACTS:
        raise CompareError(f"不認得的計分契約:{name!r}",
                           "unknown_contract", {"contract": name})
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


# 降級原因 → 給人看的說法(⛔ 不可以一律說成「沒裝 ffmpeg」)
_REASON_TEXT = {
    "no_ffmpeg": "產出端沒有 ffmpeg/ffprobe;裝好重評即可升級",
    "probe_failed": "探測不到音訊結構",
    "unsupported_sample_fmt": "樣本格式不在白名單(例:s64),產品刻意不發布會撞號的身分",
    "unknown_multichannel_layout": "多聲道但講不出喇叭配置,產品刻意不發布",
    "decode_failed": "解碼失敗",
}


def _identity_note(items):
    """輸出裡明說這一批的身分證據**強到哪裡為止** ——
    ⛔ 不可以讓人以為「有欄位」就等於「同一首歌一定認得出來」(Codex R18-4)。"""
    n = len(items)
    with_eval = sum(1 for i in items if i.get("evaluation_id"))
    with_file = sum(1 for i in items if i.get("source_file_sha256"))
    with_pcm = sum(1 for i in items if i.get("source_audio_pcm_sha256"))
    contracts = {i.get("pcm_contract") for i in items if i.get("pcm_contract")}
    if with_eval == n and with_pcm == n and len(contracts) <= 1:
        level = "decoded-audio"
        why = ("每份都帶 evaluation_id 與**解碼後**音訊雜湊(保留原始取樣率與聲道結構):"
               "複製改名、換容器、改 metadata 之後再上場都擋得住。"
               "⚠️ 擋不到 lossy 重壓、也擋不到重新取樣/改聲道數之後的版本 —— "
               "那需要 acoustic fingerprint,本系統不做。")
    elif with_eval == n and with_pcm == n and len(contracts) > 1:
        # ⛔ 兩份報告用不同版本的解碼身分算出來的雜湊**不可互比**(Codex R19-1)
        level = "exact-file"
        why = (f"這批的解碼身分版本不一致({sorted(contracts)})—— 不同標準面算出來的"
               f"雜湊不可互比,這一層自動退回檔案雜湊。請用同一版重評再比。")
    elif with_eval == n and with_file == n and any(
            i.get("pcm_raw_present") and not i.get("source_audio_pcm_sha256")
            for i in items):
        # ⛔ 有報告**寫了**解碼雜湊卻沒有(或不認得)版本 → 那一層不算數,誠實退回
        level = "exact-file"
        why = ("有報告的解碼身分缺版本或版本不認得 —— 那個雜湊不能當同源證據,"
               "這一層退回檔案雜湊(請用新版重評以取得可比較的解碼身分)。")
    elif with_eval == n and with_file == n:
        level = "exact-file"
        # ⛔ 原因要講**真的**那個(Codex R22-P2-1):以前一律說「沒有 ffmpeg」,
        #    但 s64/未知配置是產品**刻意**不發布身分,重裝 ffmpeg 一點用都沒有。
        reasons = sorted({i["pcm_downgrade"] for i in items if i.get("pcm_downgrade")})
        if reasons:
            why = ("每份都帶 evaluation_id 與**檔案** sha256,但產出端明講算不出解碼身分"
                   f"({'、'.join(_REASON_TEXT.get(r, r) for r in reasons)})——"
                   "同一段聲音換個容器或改 metadata 就會被當成兩個來源。")
        else:
            why = ("每份都帶 evaluation_id 與**檔案** sha256,但缺解碼後雜湊 ——"
                   "同一段聲音只要換個容器或改 metadata 就會被當成兩個來源。"
                   "(產出端沒有 ffmpeg 時會這樣;裝好 ffmpeg 重評即可升級。)")
    elif with_eval or with_file or with_pcm:
        level = "mixed"
        why = (f"只有部分報告帶身分(id {with_eval}/{n}、檔案 {with_file}/{n}、"
               f"解碼 {with_pcm}/{n})—— 沒帶的那幾份只剩「內容完全相同」這一層,"
               f"同一首歌重跑產生的兩份報告擋不住")
    else:
        level = "weak"
        why = ("這批都是舊格式報告(沒有任何來源身分)—— 只擋得掉內容完全相同的複製;"
               "重跑一次再改名擋不住,請用新版重評再比")
    return {"level": level, "with_evaluation_id": with_eval,
            "with_source_file_sha256": with_file,
            "with_source_audio_pcm_sha256": with_pcm, "n": n, "note": why}


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
            f"語言不能用猜的:四把尺不可共量",
            "bad_language", {"lang": lang})
    _reject_duplicates(paths)
    items = [load_report(p) for p in paths]
    if len(items) < 2:
        raise CompareError("PK 至少要兩首", "too_few_reports", {"n": len(items)})
    _reject_same_source(items)      # ⛔ 複製改名不算另一首(R17-3)
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
        # ⚠️ 誠實邊界:身分防線的強度取決於報告有沒有帶 evaluation_id / 音檔雜湊
        "source_identity": _identity_note(items),
    }


def compare_takes(paths, group: str):
    if not group:
        raise CompareError("抽卡比較必須指定 --group(同一份詞+prompt 的那組)",
                           "missing_group")
    _reject_duplicates(paths)
    items = [load_report(p) for p in paths]
    if len(items) < 2:
        raise CompareError("抽卡比較至少要兩個 take", "too_few_reports",
                           {"n": len(items)})
    # ⛔ 抽卡更需要這道:同一份詞+prompt 的多個 take 本來就長得像,
    #    複製一份改個名字看起來完全合理,結論卻建立在同一次評測上(R17-3)
    _reject_same_source(items)
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
        "source_identity": _identity_note(items),
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
