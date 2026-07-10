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

pdfmetrics.registerFont(TTFont("JhengHei", "C:/Windows/Fonts/msjh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont("JhengHeiBd", "C:/Windows/Fonts/msjhbd.ttc", subfontIndex=0))

S_TITLE = ParagraphStyle("t", fontName="JhengHeiBd", fontSize=16, leading=22, spaceAfter=4)
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
    s = EMOJI_RE.sub("", s).strip()
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return s


def main():
    src = Path(sys.argv[1]).resolve()
    lines = src.read_text(encoding="utf-8").splitlines()
    out = src.with_suffix(".pdf")

    story = []
    table_rows = []
    footers = []

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
            story.append(Paragraph(md_inline(line[3:]), S_HEAD))
        elif line.startswith("# "):
            story.append(Paragraph(md_inline(line[2:]), S_TITLE))
        elif line.startswith("- "):
            story.append(Paragraph(md_inline(line[2:]), S_META))
        elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            footers.append(Paragraph(md_inline(line.strip("*")), S_FOOT))
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
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image as RLImage
        iw, ih = ImageReader(str(arc)).getSize()
        w = (A4[0] - 30 * mm) * (0.72 if heading == "多維雷達圖" else 1.0)
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"<b>{heading}</b>", S_HEAD))
        story.append(RLImage(str(arc), width=w, height=w * ih / iw))

    if footers:
        story.append(Spacer(1, 10))
        story.extend(footers)

    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=src.stem)
    doc.build(story)
    print(f"PDF: {out}")


if __name__ == "__main__":
    main()
