# -*- coding: utf-8 -*-
"""報告轉PDF.py — 把固定格式的 歌名_評審團報告.md 轉成 PDF
用法: python 報告轉PDF.py 報告.md   → 同資料夾產出同名 .pdf
字型: 微軟正黑(msjh.ttc / msjhbd.ttc)
"""
import re
import sys
import unicodedata
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def _is_hangul(ch):
    """這個字是不是諺文(含 Jamo 字母與相容字母,不只預組合音節)。"""
    return "가" <= ch <= "힣" or "ᄀ" <= ch <= "ᇿ" or "㄰" <= ch <= "㆏"


def _contains_hangul(text) -> bool:
    """報告裡有沒有諺文 —— 決定要不要註冊韓文字型。

    ⛔ 不可以只看預組合音節 AC00-D7A3:NFD 分解式韓文(macOS 檔名、部分編輯器
       貼上的文字)整段都是 Jamo(1100-11FF),舊判定看不到 → 韓文字型不註冊、
       缺字檢查也漏掉(Jamo 被 _is_hangul 從主字型 need 集合剔除了)→
       轉檔 exit 0、零警告,渲染出來整行 □□□□(Codex R10 實測)。"""
    return any(_is_hangul(c) for c in (text or ""))


def _fit_image(iw, ih, maxw, maxh):
    """算圖片在頁面裡的顯示尺寸:寬**高**都限,等比縮放。回 (w, h) 或 None(略過)。

    ⛔ 只限寬不限高:1×10000 的畸形圖(旋轉/損壞的引擎產圖)高度會算出
       頁框的幾百倍,doc.build() 直接 LayoutError,一張圖毀掉整份 PDF
       (Codex R10 實測)。長寬比荒謬(>20:1)的圖不是弧線圖,略過並警告。"""
    try:
        iw, ih = float(iw), float(ih)
    except (TypeError, ValueError):
        return None
    if iw <= 0 or ih <= 0 or max(iw / ih, ih / iw) > 20:
        return None
    scale = min(maxw / iw, maxh / ih)
    return iw * scale, ih * scale


def _missing_glyphs(path: Path, idx, chars) -> set:
    """這個字型檔缺了 chars 裡的哪些字。回傳缺字集合(空集合=全有)。

    ⛔ 不可以只看檔案存在,也不可以只驗幾個樣本字 —— 兩邊都踩過:
       · Windows 微軟正黑(msjh.ttc)**一個韓文字形都沒有** → 韓文整行變 □□□□
       · 改用 Malgun Gothic 之後韓文好了,但它**沒有完整繁體字**
         →「敘事結構」變成「事結構」、「概念論」變成「念論」,一樣是靜默缺字
       所以要拿**報告裡實際出現的每一個字**去驗,而不是幾個代表字。
    """
    try:
        f = TTFont("_probe", str(path), subfontIndex=idx) if idx is not None else TTFont("_probe", str(path))
        cmap = f.face.charToGlyph
        return {c for c in chars if cmap.get(ord(c), 0) == 0}
    except Exception:
        return set(chars)


def _register_cjk_fonts(sample_text: str = ""):
    """跨平台註冊 CJK 字型,並**依報告實際內容挑得動那些字的字型**。

    順序:repo assets/ 的 Noto → 系統字型。
    ⚠️ 韓文特別處理:繁中系統的預設 CJK 字型(微軟正黑/蘋方繁中)多半沒有諺文字形,
       所以報告若含韓文,要優先選 Malgun Gothic / Noto Sans KR 這類涵蓋諺文的字型。
    .ttc 需 subfontIndex,.otf/.ttf 不用。找不到粗體→用一般體頂替;全找不到→給安裝指示。"""
    here = Path(__file__).parent
    # ⭐ 拿**報告裡實際出現的每一個 CJK/諺文字**去挑字型,而不是幾個代表字。
    #    只驗樣本會挑到「樣本有、正文缺」的字型,結果是靜默缺字(最糟的失敗方式)。
    #    ⚠️ 只收「真的要用 CJK 字型畫」的字:漢字、假名、諺文、全形。
    #       emoji(🎤✍️…)不能算 —— 沒有任何 CJK 字型有它們,算進去會每份報告都誤報缺字。
    def _needs_cjk_font(ch):
        o = ord(ch)
        return (0x3040 <= o <= 0x30FF        # 平假名/片假名
                or 0x3400 <= o <= 0x4DBF     # CJK 擴充 A
                or 0x4E00 <= o <= 0x9FFF     # CJK 統一漢字
                or 0xAC00 <= o <= 0xD7A3     # 諺文音節
                or 0x1100 <= o <= 0x11FF     # 諺文字母
                or 0x3130 <= o <= 0x318F     # 諺文相容字母
                or 0xF900 <= o <= 0xFAFF)    # CJK 相容漢字
    # ⭐ 主字型只用「非諺文」的字去挑 —— 諺文交給另外註冊的韓文字型,段落內用 <font> 切換。
    #    ⛔ 不可以硬要一個字型全包:實測掃過 Windows 全部字型,**沒有任何一個同時涵蓋繁中與
    #       諺文**;硬選 Malgun 的話韓文好了但繁體字反過來缺(敘/概/獎/真…)。
    #       (Noto Sans CJK 有全涵蓋,但它是 CFF 輪廓,reportlab 明確不支援。)
    need = {c for c in (sample_text or "評分") if _needs_cjk_font(c) and not _is_hangul(c)}
    if not need:
        need = set("評分")
    # \u26d4 \u8981\u7528 _contains_hangul(\u542b Jamo \u5b57\u6bcd),\u4e0d\u53ef\u53ea\u6383\u9810\u7d44\u5408\u97f3\u7bc0 AC00-D7A3 \u2014\u2014
    #    NFD \u5206\u89e3\u5f0f\u97d3\u6587\u6703\u300cexit 0\u3001\u96f6\u8b66\u544a\u300d\u5730\u6574\u884c\u8b8a\u65b9\u584a(\u898b _contains_hangul \u8aaa\u660e)\u3002
    has_hangul = _contains_hangul(sample_text)

    kr_reg = [(Path("C:/Windows/Fonts/malgun.ttf"), None),                     # Windows Malgun Gothic
              (Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"), 0),         # macOS
              (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0)]
    kr_bold = [(Path("C:/Windows/Fonts/malgunbd.ttf"), None),
               (Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"), 0),
               (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"), 0)]

    reg_cands = [
        # 安裝腳本抓的全 CJK 字型(中日韓全涵蓋)—— .ttc 內含多個子字型,逐一試
        *[(here / "assets" / "NotoSansCJK-Regular.ttc", i) for i in range(6)],
        (here / "assets" / "NotoSansTC-Regular.otf", None), (here / "assets" / "NotoSansTC-Regular.ttf", None),
        (here / "assets" / "NotoSansCJKtc-Regular.otf", None),
        (Path("C:/Windows/Fonts/msjh.ttc"), 0),                                # Windows 微軟正黑
        (Path("/System/Library/Fonts/PingFang.ttc"), 0),                      # macOS 蘋方
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0),  # Linux Noto
        (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"), 0),
        (Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"), 0),
    ]
    bold_cands = [
        *[(here / "assets" / "NotoSansCJK-Bold.ttc", i) for i in range(6)],
        (here / "assets" / "NotoSansTC-Bold.otf", None), (here / "assets" / "NotoSansTC-Bold.ttf", None),
        (Path("C:/Windows/Fonts/msjhbd.ttc"), 0),
        (Path("/System/Library/Fonts/PingFang.ttc"), 0),
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"), 0),
        (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"), 0),
        (Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"), 0),
    ]

    def _first(cands, chars):
        """挑「缺字最少」的字型。全涵蓋就直接用;沒有全涵蓋的就挑最好的並回報缺字。
        ⛔ 不可以「第一個能載入的就用」—— 那正是韓文變方塊、以及換成 Malgun 之後
           繁體字反過來缺掉的原因。"""
        best = (None, None, None)          # (path, idx, missing)
        for p, idx in cands:
            try:
                if not p.exists():
                    continue
                miss = _missing_glyphs(p, idx, chars)
                if not miss:
                    return p, idx, set()
                if best[2] is None or len(miss) < len(best[2]):
                    best = (p, idx, miss)
            except Exception:
                continue
        return best

    def _reg(name, p, idx):
        # ⚠️ reportlab 對「已註冊過的同名字型」會直接忽略,不會覆蓋(實測)。
        #    所以韓文那次要註冊成**不同的名字**,再把樣式的 fontName 換過去。
        pdfmetrics.registerFont(TTFont(name, str(p), subfontIndex=idx) if idx is not None else TTFont(name, str(p)))

    reg_name, bold_name = "JhengHei", "JhengHeiBd"

    # 韓文字型另外註冊一個名字,段落內用 <font name="Hangul"> 切換過去。
    # ⚠️ reportlab 沒有段落內自動字型 fallback,所以要自己在標記層切。
    if has_hangul and "Hangul" not in pdfmetrics.getRegisteredFontNames():
        kp, ki, kmiss = _first(kr_reg, {c for c in sample_text if _is_hangul(c)})
        if kp:
            pdfmetrics.registerFont(TTFont("Hangul", str(kp), subfontIndex=ki)
                                    if ki is not None else TTFont("Hangul", str(kp)))
            if kmiss:
                print(f"⚠ 韓文字型缺 {len(kmiss)} 個諺文字形", file=sys.stderr)
        else:
            print("⚠ 找不到含諺文字形的字型,韓文會顯示成方塊。"
                  "Windows 需要 Malgun Gothic;Linux 請 `apt install fonts-noto-cjk`。",
                  file=sys.stderr)

    rp, ri, miss = _first(reg_cands, need)
    if not rp:
        raise SystemExit("找不到可用的 CJK 字型。Windows/macOS 通常內建;"
                         "Linux 請 `apt install fonts-noto-cjk`,"
                         "或把 NotoSansTC-Regular.otf(可加 -Bold)放進 assets/。")
    if miss:
        # ⛔ 缺字一定要講出來 —— 靜默缺字是最糟的失敗方式:PDF 看起來正常,
        #    但「敘事結構」少一個字變「事結構」,讀報告的人根本不會發現。
        show = "".join(sorted(miss))[:40]
        print(f"⚠ 目前選到的字型缺 {len(miss)} 個字形,PDF 會缺字:{show}"
              f"{'…' if len(miss) > 40 else ''}\n"
              f"  → 多語(尤其中文+韓文同頁)請裝涵蓋 CJK 全區的字型:\n"
              f"    Linux: apt install fonts-noto-cjk / "
              f"其他:把 NotoSansCJK-Regular.ttc 放進 assets/", file=sys.stderr)
    if reg_name not in pdfmetrics.getRegisteredFontNames():
        _reg(reg_name, rp, ri)
    bp, bi, _ = _first(bold_cands, need)
    if not bp:
        bp, bi = rp, ri   # 沒粗體 → 用一般體頂替
    if bold_name not in pdfmetrics.getRegisteredFontNames():
        _reg(bold_name, bp, bi)
    return reg_name, bold_name


# 先用預設(中文)註冊一次,讓下面的 ParagraphStyle 建得起來;
# main() 讀到報告內容後會**再註冊一次**,依實際文字挑對的字型(例如韓文報告換成 Malgun)。
# reportlab 允許同名重註冊,後者覆蓋前者。
# ⚠️ 匯入時找不到字型**不擋 import**(測試/工具鏈要能載入這個模組;
#    ParagraphStyle 只存字型名字串,不驗存在)——真正轉檔時 main() 會再註冊,
#    那裡照樣 SystemExit fail-loud,該炸的地方一個都沒少。
try:
    _register_cjk_fonts()
except SystemExit as _e:
    print(f"⚠ 匯入時找不到 CJK 字型(只影響現在 import,轉檔時會再檢查並擋下):{_e}",
          file=sys.stderr)

S_TITLE = ParagraphStyle("t", fontName="JhengHeiBd", fontSize=16, leading=22, spaceAfter=4, alignment=1)
S_SUB = ParagraphStyle("sub", fontName="JhengHeiBd", fontSize=10, leading=15, spaceBefore=2, spaceAfter=8, alignment=1)
S_META = ParagraphStyle("m", fontName="JhengHei", fontSize=8.5, leading=13, textColor=colors.HexColor("#555555"))
S_HEAD = ParagraphStyle("h", fontName="JhengHeiBd", fontSize=11.5, leading=17, spaceBefore=10, spaceAfter=3,
                        textColor=colors.HexColor("#1b3a63"))
# 三級標題(九柱制報告的逐柱小節靠這個;沒有它 ### 會原字印出來)
S_HEAD3 = ParagraphStyle("h3", fontName="JhengHeiBd", fontSize=10, leading=15, spaceBefore=8, spaceAfter=2,
                         textColor=colors.HexColor("#33506e"))
# 引言(> 開頭的前言列;沒有它 > 會原字印出來)
S_QUOTE = ParagraphStyle("q", fontName="JhengHei", fontSize=8.5, leading=13, leftIndent=6,
                         textColor=colors.HexColor("#555555"))
S_BODY = ParagraphStyle("b", fontName="JhengHei", fontSize=9.5, leading=14.5)
S_CELL = ParagraphStyle("c", fontName="JhengHei", fontSize=8.8, leading=12.5)
S_CELL_B = ParagraphStyle("cb", fontName="JhengHeiBd", fontSize=8.8, leading=12.5)
S_FOOT = ParagraphStyle("f", fontName="JhengHei", fontSize=7.5, leading=11, textColor=colors.HexColor("#888888"))


EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-⛿✀-➿⬀-⯿️‍]")

# 連續的諺文(含中間的空白)——整段換成韓文字型,免得一個字包一次標記
HANGUL_RUN_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]"
                           r"[가-힣ᄀ-ᇿ㄰-㆏\s]*"
                           r"[가-힣ᄀ-ᇿ㄰-㆏]"
                           r"|[가-힣ᄀ-ᇿ㄰-㆏]")


def md_inline(s):
    s = s.replace("✅", "(通過)").replace("❌", "(未過)")
    s = s.replace("🥇", "冠 ").replace("🥈", "亞 ").replace("🥉", "季 ")
    s = s.replace("≥", ">=").replace("≤", "<=")  # 數學符號字型無此字，避免變豆腐
    s = EMOJI_RE.sub("", s).strip()  # ⚠️ 等裝飾 emoji 一律清掉(檢查表的「部分」verdict 已在 md 寫成文字)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    # ⭐ 諺文段落換成韓文字型 —— reportlab 沒有段落內自動 fallback,要自己在標記層切。
    #    不切的話:主字型(微軟正黑/蘋方繁中)一個諺文字形都沒有 → 韓文整行變 □□□□;
    #    但把主字型換成 Malgun 又會害繁體字缺(敘/概/獎/真…),所以只能逐段切換。
    if "Hangul" in pdfmetrics.getRegisteredFontNames():
        s = HANGUL_RUN_RE.sub(r'<font name="Hangul">\g<0></font>', s)
    return s


# 固定落款(兩行)——由轉檔器保證「永遠都在、且正確」,不靠 AI 每次手寫記得(曾漏第二行、曾寫成三模型)。
# md 裡若有這兩行會被忽略、統一用這裡的正版覆蓋。
#
# ⚠️ 2026-07-20 改版(她授權:「那是因為你一直漏掉,現在不同,要整個重做」)。
#    舊版列的權威,大半是四把新尺【實際上沒有引用】的 —— 實查:Berklee 0 次、黃霑博士論文 0 次、
#    林夕 0 次;王國維境界說更被 ZH v5 明文禁用(「⛔ 別用王國維境界說當評分框架(修辭習慣非方法論)」);
#    方文山只出現 2 次而且是【反面案例】(防呆閘)。掛著不用的權威就是灌水,
#    故一律換成四把尺真正引用的出處,並補上這次新增的評審元件。
# ⚖️ 2026-07-25 重構庭同步:移除 Music Flamingo(2026-07-20 已除名,此處漏掃——她抓到的)、
#    補新柱儀器(SingMOS/MuQ 真實距離)與九柱定版出處。詞評出處策展名單不動(見上方考據)。
CANON_FOOTER_1 = ("評審體系｜量測:EBU R128 國際響度/動態 + Demucs 六軌編曲層次 + 模板/Viterbi 和弦辨識"
                  " + parselmouth 演唱量測｜模型:SongEval(16 位職業音樂人×2,399 首標註訓練)"
                  "+ Meta Audiobox Aesthetics + Gemini 六維證據型曲評(聽音檔·引時間碼)"
                  " + SingMOS 演唱聽感 + MuQ 真實距離(得獎 54 首分佈錨)｜"
                  "情感:Russell 情感環狀模型 × NRC-VAD 心理學詞庫(加拿大國家研究院)｜"
                  "詞評:中/英/日/韓四語尺——理論基礎:T.S. Eliot 客觀對應物・Pattison 中心概念論・"
                  "Ladd & Kirby 聲調-旋律三分術語(The Oxford Handbook of Language Prosody, OUP 2020)・"
                  "Wong & Diehl 序數尺度・陸正蘭《歌詞學》・十三轍傳統(吳頌今/尤靜波教學體系)・"
                  "金曲獎評審精神,四把尺各經八家模型五輪對抗審查定版(Claude・GPT/Codex・Gemini・Grok・DeepSeek・Qwen・Nemotron・ERNIE)｜"
                  "權重:十三席模型評審團九柱定版——Claude Fable・GPT-5.5・Gemini・Grok 4.3・DeepSeek V4・Qwen3・Kimi K3・MiniMax M3・Mistral Small 4・Nemotron 3・Llama 4・ERNIE 5.1・騰訊混元 HY3(2026-07-25 定版)")
CANON_FOOTER_2 = "本報告為診斷性評審,供創作與製作決策參考;最終效度以聽眾市場回饋為檢驗"


def main():
    src = Path(sys.argv[1]).resolve()
    # ⛔ 先 NFC 正規化:NFD 分解式韓文(macOS 檔名/部分編輯器)整段是 Jamo,
    #    不組合回音節的話,即使字型對了,渲染仍可能逐字母拆開。統一成 NFC 一勞永逸。
    text = unicodedata.normalize("NFC", src.read_text(encoding="utf-8"))
    # ⚠️ 字型要在**看過報告內容之後**才決定 —— 韓文報告需要有諺文字形的字型,
    #    在 import 時就鎖死會讓韓文整行變成方塊(繁中系統的預設字型一個諺文字形都沒有)。
    #    ⚠️ 落款是**程式自己生成的**,不在 .md 裡 —— 不一起算進去的話,
    #       它那一整段中文會靜靜缺字(實測:「概念論」變「念論」)。
    _reg_name, _bold_name = _register_cjk_fonts(text + CANON_FOOTER_1 + CANON_FOOTER_2)
    if _reg_name != "JhengHei":          # 韓文 → 把所有樣式改綁到有諺文的那套
        for _st in (S_TITLE, S_SUB, S_META, S_HEAD, S_HEAD3, S_QUOTE,
                    S_BODY, S_CELL, S_CELL_B, S_FOOT):
            _st.fontName = _bold_name if _st.fontName.endswith("Bd") else _reg_name
    lines = text.splitlines()
    out = src.with_suffix(".pdf")

    story = []
    table_rows = []
    footers = []
    # 標準報告(單評/抽卡,依檔名)→ 落款必補;PK 對決報告靠內文的「評審體系」行觸發(見迴圈)
    has_canon_footer = ("評審團報告" in src.stem or "抽卡比較報告" in src.stem)
    seen_sub = False

    # 品牌頁首 logo(Meow House),置中約 1/3 頁寬。base64 烤進碼(brand_logo.py),
    # 不依賴鬆散 PNG → 強制品牌、跨機可攜(丟張圖蓋不掉,要換得改碼)。
    try:
        import base64 as _b64, io as _io
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image as RLImage
        from brand_logo import LOGO_B64
        _raw = _b64.b64decode(LOGO_B64)
        liw, lih = ImageReader(_io.BytesIO(_raw)).getSize()
        lw = 26 * mm
        limg = RLImage(_io.BytesIO(_raw), width=lw, height=lw * lih / liw)
        limg.hAlign = "CENTER"
        story.append(limg)
        story.append(Spacer(1, 2))
    except Exception as e:
        print(f"⚠ logo 內嵌略過(不影響報告):{e}")

    def flush_table():
        nonlocal table_rows
        if not table_rows:
            return
        data = []
        for cells in table_rows:
            row = []
            for j, c in enumerate(cells):
                is_sub = c.startswith("　・") or c.startswith("・")
                style = S_CELL_B if (len(data) == 0 or (j == 0 and not is_sub)) else S_CELL
                row.append(Paragraph(md_inline(c), style))
            data.append(row)
        w = A4[0] - 30 * mm
        n_cols = max(len(r) for r in table_rows)
        if n_cols == 3:
            col_widths = [w * 0.20, w * 0.22, w * 0.58]
        else:
            # 依各欄平均字數比例配寬,設下限避免擠死
            avg = [max(2.0, sum(len(r[j]) if j < len(r) else 0 for r in table_rows) / len(table_rows)) + 2
                   for j in range(n_cols)]
            total = sum(avg)
            min_w = w * 0.06
            col_widths = [max(min_w, w * a / total) for a in avg]
            scale = w / sum(col_widths)
            col_widths = [cw * scale for cw in col_widths]
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
        ]))
        story.append(Spacer(1, 4))
        story.append(t)
        story.append(Spacer(1, 4))
        table_rows = []

    for raw in lines:
        line = raw.rstrip()
        if re.match(r"^\|[\s:\-|]+\|$", line):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            table_rows.append(cells)
            continue
        flush_table()
        if not line.strip() or line.strip() == "---":
            continue
        if line.startswith("### "):
            story.append(Paragraph(md_inline(line[4:]), S_HEAD3))
        elif line.startswith("> "):
            story.append(Paragraph(md_inline(line[2:]), S_QUOTE))
        elif line.startswith("## "):
            # 第一個 ## = 副標(置中,跟標題湊招牌);其後 ## = section 標題(靠左)
            if not seen_sub:
                story.append(Paragraph(md_inline(line[3:]), S_SUB))
                seen_sub = True
            else:
                story.append(Paragraph(md_inline(line[3:]), S_HEAD))
        elif line.startswith("# "):
            story.append(Paragraph(md_inline(line[2:]), S_TITLE))
        elif line.startswith("- "):
            story.append(Paragraph(md_inline(line[2:]), S_META))
        elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            inner = line.strip("*")
            if inner.startswith("評審體系") or inner.startswith("本報告為診斷性"):
                has_canon_footer = True  # 標準落款:忽略 md 這行,結尾由轉檔器統一補正版
                continue
            footers.append(Paragraph(md_inline(inner), S_FOOT))
        elif re.match(r"^\*\*.+?\*\*[::]", line):
            story.append(Paragraph(md_inline(line), S_HEAD))
        elif re.match(r"^\d+\.\s", line):
            story.append(Paragraph(md_inline(line), S_BODY))
        else:
            story.append(Paragraph(md_inline(line), S_BODY))
    flush_table()

    # 自動內嵌情感弧線圖(找 歌名_歌詞_情感弧線.png 或同資料夾含「情感弧線」的 png)
    song = src.stem.replace("_評審團報告", "")
    arc = src.parent / f"{song}_歌詞_情感弧線.png"
    heading = "情感弧線圖"
    if not arc.exists():
        cands = sorted(src.parent.glob(f"{song}*情感弧線*.png"))
        arc = cands[0] if cands else None
    if not arc:  # PK 等報告:找雷達圖
        cands = sorted(src.parent.glob("*雷達*.png"))
        if cands:
            arc, heading = cands[0], "多維雷達圖"
    if arc:
        try:
            from reportlab.lib.utils import ImageReader
            from reportlab.platypus import Image as RLImage
            iw, ih = ImageReader(str(arc)).getSize()
            maxw = (A4[0] - 30 * mm) * (0.72 if heading == "多維雷達圖" else 1.0)
            # ⛔ 高度也要限(頁框內高再留標題/落款空間):只限寬的話,1×10000 的
            #    畸形圖高度會爆出頁框,doc.build() LayoutError,一張圖毀掉整份 PDF。
            fitted = _fit_image(iw, ih, maxw, A4[1] - 70 * mm)
            if fitted is None:
                print(f"⚠ {heading}內嵌略過(圖片尺寸異常 {iw}x{ih},長寬比不合理,不影響報告)")
            else:
                story.append(Spacer(1, 6))
                story.append(Paragraph(f"<b>{heading}</b>", S_HEAD))
                story.append(RLImage(str(arc), width=fitted[0], height=fitted[1]))
        except Exception as e:
            print(f"⚠ {heading}內嵌略過(圖檔異常，不影響報告):{e}")

    if footers or has_canon_footer:
        story.append(Spacer(1, 10))
        story.extend(footers)
        if has_canon_footer:  # 標準報告:轉檔器保證兩行落款都在、四模型正確(不管 AI 有沒有寫)
            story.append(Paragraph(md_inline(CANON_FOOTER_1), S_FOOT))
            story.append(Paragraph(md_inline(CANON_FOOTER_2), S_FOOT))

    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=src.stem)
    doc.build(story)
    print(f"PDF: {out}")


if __name__ == "__main__":
    main()
