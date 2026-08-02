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
import re
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

from 設定讀取 import ConfigError, finite_number   # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def validate(path: Path, newer_than: float = None, require_contract: bool = False,
             require_identity: bool = False) -> str:
    """回空字串=通過;否則回第一個不合格的原因(講人話)。

    require_contract=True:報告**必須**自報 scoring_contract。
    ⛔ 安裝證據(-VerifyModels)與比較器一律用 strict —— 舊格式相容是給
       「以前產出的報告」用的,不可以套在「本輪剛產生的新產物」上,
       否則產出端一旦迴歸成不寫契約,VerifyModels 照樣印 VERIFY_OK
       (Codex R16-5 探針)。"""
    if not path.exists():
        return f"檔案不存在:{path}"
    if newer_than is not None and path.stat().st_mtime <= newer_than:
        return "檔案不是本輪新產物(mtime 早於驗證開始時間)—— 讀到舊報告了"
    try:
        raw = path.read_bytes()
    except OSError as e:
        return f"讀不到檔案:{type(e).__name__}"
    return validate_data(raw, path.name, require_contract=require_contract,
                         require_identity=require_identity)


# ── 來源身分的 schema(Codex R18-2)──────────────────────────────────
# 🔴 實測:evaluation_id 寫成 list 時裁判照樣說合格,比較器一進 set 就 raw TypeError;
#    把雜湊寫成 "x"、id 寫成 "ev-A" 也會被宣稱成 strong —— 那是對不存在的證據蓋章。
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
# 欄位名 → (正則, 說明)。⚠️ source_audio_sha256 是 R17 的舊名,只讀不寫(檔案雜湊)。
IDENTITY_FIELDS = {
    "evaluation_id": (_HEX32, "32 位小寫 hex"),
    "source_file_sha256": (_HEX64, "64 位小寫 hex"),
    "source_audio_pcm_sha256": (_HEX64, "64 位小寫 hex"),
    "source_audio_sha256": (_HEX64, "64 位小寫 hex(R17 舊名)"),
}
# 認得的解碼身分版本(⛔ 換標準面 = 換一把尺,新舊不可互比 —— Codex R19-1)
# ⛔ v2(一律 s32le)已被證明會把不同的浮點來源撞成同一個身分(Codex R20-P1-1),
#    所以**不列在認得的版本裡** —— 舊 v2 報告會誠實退回 exact-file,不再當同源硬證據。
PCM_CONTRACTS = ("pcm-v5/native-rate/canonical-speakers/native-sample-fmt",)
# ⛔ v4(layout 完全不入雜湊)會把 5.1 與 5.1(side) 撞成同一個身分(Codex R22-P1-1),
#    所以同樣不列在認得的版本裡 —— 舊 v4 報告誠實退回 exact-file。

# 產出端「刻意不發布解碼身分」時的原因白名單(⛔ 與 評審團.PCM_UNAVAILABLE_REASONS
# 同一份定義;這裡是**裁判**端的複製,不 import 產出端 —— 裁判要能單獨驗一份 JSON)
PCM_UNAVAILABLE_REASONS = (
    "no_ffmpeg", "probe_failed", "unsupported_sample_fmt",
    "unknown_multichannel_layout", "decode_failed",
)
# 身分政策(⛔ 不可以用一個布林代表兩種政策 —— Codex R22-P2-1):
#   "decoded"  = 一定要有解碼身分(安裝證據:demo 是 s16,算得出來才叫裝好)
#   "declared" = 解碼身分,**或**一份合法的顯式降級宣告(正式批次:s64 之類的
#                來源產品本來就刻意不發布身分,不該因此連完整九柱都不算數)
IDENTITY_DECODED = "decoded"
IDENTITY_DECLARED = "declared"


def _identity_policy(require_identity):
    """把呼叫端給的值正規化成 None / "decoded" / "declared"。

    ⚠️ True 沿用舊語意(= decoded,安裝證據),但**新的呼叫端請直接寫字串**:
       一個布林同時代表兩種政策正是 Codex R22-P2-1 指出的問題。"""
    if require_identity in (None, False, ""):
        return None
    if require_identity is True:
        return IDENTITY_DECODED
    if require_identity in (IDENTITY_DECODED, IDENTITY_DECLARED):
        return require_identity
    raise ValueError(f"不認得的身分政策:{require_identity!r}"
                     f"(要 None / {IDENTITY_DECODED!r} / {IDENTITY_DECLARED!r})")


def identity_problem(d: dict) -> str:
    """回一句話說明身分欄位哪裡不合法;完全沒有身分欄位不算錯(舊報告)。

    ⚠️ 空字串 == 缺席(Codex R19-2):產出端算不到 PCM 雜湊時,舊版寫 ""
       而 schema 判它畸形 —— 沒有 ffmpeg 的機器產出的報告會整份不合法。
       現在產出端改成不寫該欄位;過渡期留下的 "" 一律當成「這台算不出來」,
       降級處理,而不是當成偽造。⛔ 但 strict 模式(安裝證據)照樣要求它存在。"""
    for name, (rx, how) in IDENTITY_FIELDS.items():
        if name not in d:
            continue
        v = d[name]
        if isinstance(v, str) and v == "":
            continue                      # 缺席,不是畸形
        if isinstance(v, bool) or not isinstance(v, str) or not rx.match(v):
            return f"{name} 不是合法的身分值(要 {how},拿到 {type(v).__name__} {v!r:.40})"
    c = d.get("source_audio_pcm_contract")
    if c is not None and (not isinstance(c, str) or not c.strip()):
        return f"source_audio_pcm_contract 不是合法的版本字串(拿到 {c!r:.40})"
    # ⛔ 有版本卻沒雜湊是無意義的組合(Codex R20-P1-2):版本是用來描述那個雜湊的
    if c and not d.get("source_audio_pcm_sha256"):
        return "有 source_audio_pcm_contract 卻沒有對應的 source_audio_pcm_sha256"
    return status_problem(d)


def status_problem(d: dict) -> str:
    """驗 source_audio_pcm_status(顯式降級宣告)的 schema —— Codex R22-P2-1。

    ⛔ 這個欄位會被下游當成「產品刻意降級」的證據,所以「有欄位但是垃圾」
       比沒有更危險:原因要在白名單裡、產出端版本要認得、而且不可以與
       「其實有解碼雜湊」矛盾(兩個都寫等於在講兩件相反的事)。"""
    st = d.get("source_audio_pcm_status")
    if st is None:
        return ""
    if not isinstance(st, dict):
        return f"source_audio_pcm_status 不是物件({type(st).__name__})"
    if st.get("status") != "unavailable":
        return f"source_audio_pcm_status.status 只能是 'unavailable'(拿到 {st.get('status')!r:.40})"
    if d.get("source_audio_pcm_sha256"):
        return "同時寫了解碼雜湊與『算不出解碼身分』的宣告 —— 這兩件事不可能同時成立"
    reason = st.get("reason")
    if reason not in PCM_UNAVAILABLE_REASONS:
        return (f"source_audio_pcm_status.reason 不在白名單:{reason!r:.40}"
                f"(認得的是 {list(PCM_UNAVAILABLE_REASONS)})")
    gc = st.get("generator_contract")
    if not isinstance(gc, str) or not gc.strip():
        return f"source_audio_pcm_status 缺 generator_contract(拿到 {gc!r:.40})"
    shape = st.get("shape")
    if shape is not None and not isinstance(shape, dict):
        return f"source_audio_pcm_status.shape 不是物件({type(shape).__name__})"
    return ""


def declared_downgrade(d: dict) -> str:
    """回「這份報告宣告的降級原因」;不是合法宣告就回 ""。"""
    st = d.get("source_audio_pcm_status")
    if not isinstance(st, dict) or st.get("status") != "unavailable":
        return ""
    if st.get("reason") not in PCM_UNAVAILABLE_REASONS:
        return ""
    # ⛔ 產出端版本要認得:不然「舊產出端漏寫」可以偽裝成「新產出端刻意降級」
    return st.get("reason", "") if st.get("generator_contract") in PCM_CONTRACTS else ""


def validate_data(raw: bytes, name: str = "<memory>", require_contract: bool = False,
                  require_identity: bool = False) -> str:
    """驗**已經讀進記憶體的那一份 bytes**(給比較器用)。

    ⛔ 為什麼要拆出來(Codex R16-6):比較器舊版先 validate(path) 再自己
       read_text() 第二次 —— 兩次之間檔案被換掉的話,排名用的是沒被驗過的內容。
       只讀一次 bytes、在記憶體裡驗同一份,TOCTOU 窗口就不存在。"""
    def _reject_const(x):
        # ⛔ json.loads 預設吃 NaN/Infinity —— 那不是合法 JSON,別人的解析器會炸,
        #    而且 NaN 混進柱分還會一路無聲汙染(Codex R13)。這裡直接拒收。
        raise ValueError(f"非標準 JSON 常數:{x}")

    try:
        d = json.loads(raw.decode("utf-8"), parse_constant=_reject_const)
    except ValueError as e:
        return f"JSON 不合格:{e}"
    except Exception as e:
        return f"JSON 解析失敗:{type(e).__name__}"
    if not isinstance(d, dict):
        return f"頂層是 {type(d).__name__},應為 dict"
    pt = d.get("pillar_totals")
    if not isinstance(pt, dict):
        return "缺 pillar_totals(舊格式或產出不完整)"

    # ⭐ 來源身分(Codex R18-2):有寫就必須合法 —— 「有欄位但是垃圾」比沒有更危險,
    #    因為下游會把它當成證據(實測:寫 "x" 也會被宣稱成 strong)。
    why_id = identity_problem(d)
    if why_id:
        return why_id
    policy = _identity_policy(require_identity)
    if policy:
        # 本輪新產物(安裝證據)必須帶得出**完整**身分,不可以退回舊格式相容。
        # ⛔ 一定要含解碼後雜湊(Codex R19-2):安裝本來就強制 ffmpeg,
        #    產出端若迴歸成不算 PCM,九柱照樣 VERIFY_OK,下游卻只剩最弱的證據。
        base = [k for k in ("evaluation_id", "source_file_sha256") if not d.get(k)]
        if base:
            return (f"報告缺少來源身分欄位 {base} —— 這個模式要求新版產出端的完整證據"
                    f"(舊格式相容只給既有報告用)")
        why_declared = declared_downgrade(d)
        if not d.get("source_audio_pcm_sha256"):
            # ⭐ 兩種政策要分開(Codex R22-P2-1):
            #    「產出端迴歸、忘了算 PCM」與「新版產出端明確說算不出來(s64)」
            #    不是同一件事 —— 前者永遠要擋,後者在正式批次要能過。
            if policy == IDENTITY_DECLARED and why_declared:
                return ""
            if why_declared:
                return (f"這個模式要求解碼身分,但報告宣告降級({why_declared})"
                        f" —— 安裝證據不接受降級(demo 是 s16,算得出來才叫裝好)")
            return ("報告缺少來源身分欄位 ['source_audio_pcm_sha256', "
                    "'source_audio_pcm_contract'] —— 這個模式要求新版產出端的完整證據"
                    "(算不出來時要寫 source_audio_pcm_status 明確宣告原因)")
        # ⛔ 雜湊與版本一定要**成對**,而且版本必須是認得的(Codex R20-P1-2):
        #    「有雜湊、沒版本」以前照樣過 strict,下游還會把它當成最高等級的證據。
        pc = d.get("source_audio_pcm_contract")
        if pc is None or (isinstance(pc, str) and not pc.strip()):
            # ⛔ 訊息要講得出**缺哪個欄位**:呼叫端(與測試)是照欄位名判斷的
            return ("報告缺少來源身分欄位 ['source_audio_pcm_contract'] ——"
                    " 有解碼雜湊就一定要有版本,否則新舊標準面會被硬比")
        if pc not in PCM_CONTRACTS:
            return (f"不認得的解碼身分版本:{pc!r} —— 認得的是 {list(PCM_CONTRACTS)}"
                    f"(換過標準面的舊報告請用新版重評)")

    # ⭐ 計分契約:報告自報版本 → 裁判查表拿權重與柱集合。
    # ⛔ 不認得的版本一律拒收:那可能是新契約(裁判要跟上)或竄改,
    #    兩種都不該由這支替它背書(Codex R15)。
    cname = d.get("scoring_contract") or pt.get("scoring_contract")
    if cname is None:
        if require_contract:
            return ("報告沒有 scoring_contract —— 這個模式要求版本證據"
                    "(舊格式相容只給既有報告用,不給本輪新產物/比較用)")
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
    """⛔ 成功訊息要說**實際上驗了什麼**(Codex R21-P2-4)。

    舊版不管怎麼呼叫都印「本輪新產物」—— 連 pcm-v2 的舊報告、
    完全沒有身分欄位的舊格式,都被說成本輪新產物。相容可讀 ≠ 新產物證據。"""
    if len(argv) < 2:
        print("用法:python 驗證報告.py <報告.json> [--newer-than <epoch>] "
              "[--require-contract] [--require-identity | --allow-declared-downgrade]")
        return 1
    newer = None
    if "--newer-than" in argv:
        i = argv.index("--newer-than") + 1
        try:
            # ⛔ 一定要驗有限值(Codex R22-P2-2):`nan` 會讓 mtime 比較永遠不成立,
            #    一份一天前的舊報告照樣被蓋上「本輪新產物」。
            newer = finite_number("newer-than", argv[i] if i < len(argv) else None)
        except ConfigError as e:
            print(f"VERIFY_BAD --newer-than 的值不合法:{e}")
            return 1
    strict_contract = "--require-contract" in argv
    strict_identity = ("--require-identity" in argv
                       or "--allow-declared-downgrade" in argv)
    policy = (IDENTITY_DECLARED if "--allow-declared-downgrade" in argv
              else IDENTITY_DECODED if "--require-identity" in argv else None)
    path = Path(argv[1])
    why = validate(path, newer, require_contract=strict_contract,
                   require_identity=policy)
    if why:
        print(f"VERIFY_BAD {why}")
        return 1
    # 「本輪新產物」只有在**三個條件都要求過**時才能講
    if newer is not None and strict_contract and strict_identity:
        try:
            dd = declared_downgrade(json.loads(path.read_bytes().decode("utf-8")))
        except Exception:       # noqa: BLE001 —— 到這裡一定解析得開,保險而已
            dd = ""
        extra = f";來源身分=宣告降級({dd})" if dd else ""
        print(f"VERIFY_OK 九柱完整、格式合格、本輪新產物{extra}")
        return 0
    # 相容模式:講清楚驗了什麼、身分證據到哪一級
    try:
        d = json.loads(path.read_bytes().decode("utf-8"))
    except Exception:       # noqa: BLE001 —— 到這裡一定解析得開,保險而已
        d = {}
    lv = ("decoded-audio" if d.get("source_audio_pcm_contract") in PCM_CONTRACTS
          and d.get("source_audio_pcm_sha256") and d.get("evaluation_id")
          else "exact-file" if d.get("source_file_sha256") and d.get("evaluation_id")
          else "weak")
    checked = ["九柱完整", "格式合格"]
    checked.append("有計分契約" if strict_contract else "計分契約:相容模式")
    print(f"VERIFY_OK_LEGACY {'、'.join(checked)};來源身分證據={lv}"
          f"(要當『本輪新產物』的證據請加 --newer-than/--require-contract/--require-identity)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
