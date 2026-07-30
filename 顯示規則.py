#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顯示規則 —— 決定成績單上哪些指標該印、哪些該閉嘴。

她的要求(2026-07-20):
    「掛了這麼多模型,我只要有用的。其他沒用的直接屏蔽不用顯示。
      一直顯示那些沒意義的,就跟你放 100 首歌進去都是一樣的數字,
      那顯示它幹嘛?讓人嘲笑的嗎」

⭐ 這裡的每一條規則都有實測出處(A 層受控破壞 + B 層 29 首鑑別力),
   不是我的判斷。要推翻請拿新數據,不要拿意見。

三種處置:
    show       正常顯示
    exception  **體檢型**:健康時閉嘴,只有真的驗出問題才印
               —— 這類指標不是壞掉,是「沒事就沒話講」。
                  直接刪掉會拿掉安全網(真的爆音時就沒人講話了)。
    hide       常數或已證實失效,永遠不印

⛔ 不要把 exception 改成 hide:A 層證實它們對真實破壞會反應。
⛔ 不要把 hide 改回 show,除非你有新數據證明它會動。
"""

# ── 規則表 ─────────────────────────────────────────────────────────────
# healthy = 「沒事」時的值;實測分數偏離它才顯示(exception 模式用)
RULES = {
    # ── 體檢型:A 層證實有效,但在健康輸入上恆為滿分 ──
    "物理.混音.clipping": dict(
        mode="exception", healthy=100.0, tol=0.5,
        why="B層 29 首正規化全距 0.003(幾乎恆 100);"
            "但 A 層推爆 +12dB 時 100→88.4,證實會對真削波反應。"
            "→ 沒削波就別佔一行,真削波時要大聲講。"),
    "物理.混音.loudness": dict(
        mode="exception", healthy=100.0, tol=0.5,
        why="B層 29 首正規化全距 0.000(SUNO 全落在 -16~-9 滿分平台內);"
            "但 A 層壓低 12dB 時 100→52.2,證實有效。"
            "→ 響度沒問題就別印,掉出平台才印。"),

    # ⭐ dynamic_range 已於 2026-07-24 G 庭復權(13:0)——
    #    改 LRA 後 A 層過(20:1 壓縮 100→32)+ SUNO 鑑別力 0.278 + 真實得獎歌 0.618,
    #    正常顯示、正常計分(混音權重 15)。舊 crest 版的判死紀錄見 全案總結.md。

    # ── 廢項(她 2026-07-25 裁定:「廢掉的就如字面意思,真的不要顯示出來了」)──
    "編曲.n_sections": dict(
        mode="hide", healthy=None, tol=0.0,
        why="⛔ 已廢(V3 案 11:2):它=總幀數÷固定窗長,量的是歌曲長度不是段落。零顯示。"),
    "編曲.spectral_coverage": dict(
        mode="hide", healthy=None, tol=0.0,
        why="⛔ 已廢(G4 案 9:4):SUNO 與真實得獎歌鑑別力皆 0.033=常數。零顯示。"),

    # ── 常數:所有歌都給同一個數字 ──
    "Flamingo.structure_completeness": dict(
        mode="hide", healthy=None, tol=0.0,
        why="⛔ 19 首實測全部剛好 100.00,一個變化都沒有。這不是評分,是常數。"),
    "Flamingo.genre_idiom_fit": dict(
        mode="hide", healthy=None, tol=0.0,
        why="⛔ 19 首實測全部剛好 60.00,一個變化都沒有。這不是評分,是常數。"),
}

# 整個來源層級的處置
SOURCE_RULES = {
    "Flamingo": dict(
        in_total=False, hide_all_scores=True,
        why="⛔ **全部分數隱藏,只留質性文字**。四個獨立問題:\n"
            "  ①29 首只有 19 首出得了分(**失敗率 34%**)—— 同一首跑兩次可能得到不同總分;\n"
            "  ②八項裡兩項是常數(structure_completeness 恆 100、genre_idiom_fit 恆 60);\n"
            "  ③三個主觀項在 19 首上只吐得出 2-3 個不同值(development_payoff 只有 60/70);\n"
            "  ④**與確定性量測全面對不起來**(Spearman,n=19):\n"
            "     樂器豐富度↔實際同時樂器數 r=+0.17、演唱表現↔嗓音品質 r=+0.00、\n"
            "     段落變化↔實際編曲變化量 r=-0.39、製作質感↔Audiobox 製作品質 **r=-0.61**。\n"
            "     十組對照沒有一組超過 +0.24 —— 它給的不是同一件事的重複答案,是矛盾答案。\n"
            "  ⚠️ 但書:n=19 且值只有 2-3 檔,單看 ④ 不能定論;是 ①②③ 都成立才一起判。\n"
            "  ⭐ **留質性文字**:它指認具體缺點與段落地圖的能力才是價值,那不跟任何數字打架。"),
}

# 這幾項雖然會動,但已知是「數數」不是模型判斷 —— 印可以,但別當成 AI 的音樂鑑賞力
COUNTING_NOT_JUDGEMENT = {
    "Flamingo.timbral_family_coverage":
        "實測值只有 50/62.5/75/87.5/100 = 4/8、5/8、6/8、7/8、8/8 —— "
        "這是「偵測到幾種樂器家族 ÷ 8」的算術,不是模型的音樂判斷。",
}


def verdict(metric_name: str, value):
    """回 (要不要顯示, 模式, 原因)。metric_name 用 批次評測.flatten 的命名。"""
    # 先看整個來源有沒有被整批停用(Flamingo 分數全隱藏)
    src = metric_name.split(".")[0]
    sr = SOURCE_RULES.get(src)
    if sr and sr.get("hide_all_scores"):
        return False, "hide", sr["why"]
    r = RULES.get(metric_name)
    if not r:
        return True, "show", ""
    if r["mode"] == "hide":
        return False, "hide", r["why"]
    if r["mode"] == "exception":
        if value is None:
            return False, "exception", r["why"]
        # 偏離「健康值」才顯示
        if abs(float(value) - float(r["healthy"])) > float(r["tol"]):
            return True, "exception-fired", r["why"]
        return False, "exception", r["why"]
    return True, "show", ""


def annotate(flat: dict):
    """吃 {指標名: 值} → 回 {shown: {...}, hidden: [(名, 原因)], fired: [(名, 值)]}。"""
    shown, hidden, fired = {}, [], []
    for k, v in flat.items():
        ok, mode, why = verdict(k, v)
        if ok:
            shown[k] = v
            if mode == "exception-fired":
                fired.append((k, v))
        else:
            hidden.append((k, why))
    return {"shown": shown, "hidden": hidden, "fired": fired}


def summary_lines():
    """給 SKILL.md / 報告用的人話摘要。"""
    L = ["## 成績單顯示規則(由實測數據決定,非判斷)", ""]
    for k, r in RULES.items():
        tag = "🔇 永久隱藏" if r["mode"] == "hide" else "🩺 只在異常時顯示"
        L.append(f"- **{k}** — {tag}")
        L.append(f"  - {r['why']}")
    for k, r in SOURCE_RULES.items():
        L.append(f"- **{k}(整個來源)** — 分數不進總分")
        L.append(f"  - {r['why']}")
    for k, w in COUNTING_NOT_JUDGEMENT.items():
        L.append(f"- **{k}** — ⚠️ 是計數不是判斷")
        L.append(f"  - {w}")
    return L


if __name__ == "__main__":
    import sys
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(summary_lines()))
