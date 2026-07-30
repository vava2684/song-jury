# -*- coding: utf-8 -*-
"""報告轉PDF.py — 把固定格式的 歌名_評審團報告.md 轉成 PDF
用法: python 報告轉PDF.py 報告.md   → 同資料夾產出同名 .pdf
字型: 微軟正黑(msjh.ttc / msjhbd.ttc)
"""
import re
import sys
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

def _register_cjk_fonts():
    """跨平台註冊中文字型(取代寫死的 Windows 微軟正黑)。
    順序:repo assets/ 內的 Noto Sans TC → 系統既有 CJK 字型(Win 微軟正黑 / mac 蘋方 / Linux Noto)。
    .ttc 需 subfontIndex,.otf/.ttf 不用。找不到粗體→用一般體頂替;全找不到→給安裝指示。"""
    here = Path(__file__).parent
    reg_cands = [
        (here / "assets" / "NotoSansTC-Regular.otf", None), (here / "assets" / "NotoSansTC-Regular.ttf", None),
        (here / "assets" / "NotoSansCJKtc-Regular.otf", None),
        (Path("C:/Windows/Fonts/msjh.ttc"), 0),                                # Windows 微軟正黑
        (Path("/System/Library/Fonts/PingFang.ttc"), 0),                      # macOS 蘋方
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0),  # Linux Noto
        (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"), 0),
        (Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"), 0),
    ]
    bold_cands = [
        (here / "assets" / "NotoSansTC-Bold.otf", None), (here / "assets" / "NotoSansTC-Bold.ttf", None),
        (Path("C:/Windows/Fonts/msjhbd.ttc"), 0),
        (Path("/System/Library/Fonts/PingFang.ttc"), 0),
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"), 0),
        (Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"), 0),
        (Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"), 0),
    ]
    def _first(cands):
        for p, idx in cands:
            try:
                if p.exists():
                    return p, idx
            except Exception:
                continue
        return None, None
    def _reg(name, p, idx):
        pdfmetrics.registerFont(TTFont(name, str(p), subfontIndex=idx) if idx is not None else TTFont(name, str(p)))
    rp, ri = _first(reg_cands)
    if not rp:
        raise SystemExit("找不到中文字型。Windows/macOS 通常內建;Linux 請 `apt install fonts-noto-cjk`,"
                         "或把 NotoSansTC-Regular.otf(可加 -Bold)放進 assets/。")
    _reg("JhengHei", rp, ri)
    bp, bi = _first(bold_cands)
    if not bp:
        bp, bi = rp, ri   # 沒粗體 → 用一般體頂替
    _reg("JhengHeiBd", bp, bi)


_register_cjk_fonts()

S_TITLE = ParagraphStyle("t", fontName="JhengHeiBd", fontSize=16, leading=22, spaceAfter=4, alignment=1)
S_SUB = ParagraphStyle("sub", fontName="JhengHeiBd", fontSize=10, leading=15, spaceBefore=2, spaceAfter=8, alignment=1)
S_META = ParagraphStyle("m", fontName="JhengHei", fontSize=8.5, leading=13, textColor=colors.HexColor("#555555"))
S_HEAD = ParagraphStyle("h", fontName="JhengHeiBd", fontSize=10, leading=15, spaceBefore=8, spaceAfter=2)
S_BODY = ParagraphStyle("b", fontName="JhengHei", fontSize=9.5, leading=14.5)
S_CELL = ParagraphStyle("c", fontName="JhengHei", fontSize=8.8, leading=12.5)
S_CELL_B = ParagraphStyle("cb", fontName="JhengHeiBd", fontSize=8.8, leading=12.5)
S_FOOT = ParagraphStyle("f", fontName="JhengHei", fontSize=7.5, leading=11, textColor=colors.HexColor("#888888"))


EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-⛿✀-➿⬀-⯿️‍]")


def md_inline(s):
    s = s.replace("✅", "(通過)").replace("❌", "(未過)")
    s = s.replace("🥇", "冠 ").replace("🥈", "亞 ").replace("🥉", "季 ")
    s = s.replace("≥", ">=").replace("≤", "<=")  # 數學符號字型無此字，避免變豆腐
    s = EMOJI_RE.sub("", s).strip()  # ⚠️ 等裝飾 emoji 一律清掉(檢查表的「部分」verdict 已在 md 寫成文字)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
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
    lines = src.read_text(encoding="utf-8").splitlines()
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
        if line.startswith("## "):
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
            w = (A4[0] - 30 * mm) * (0.72 if heading == "多維雷達圖" else 1.0)
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>{heading}</b>", S_HEAD))
            story.append(RLImage(str(arc), width=w, height=w * ih / iw))
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
