# -*- coding: utf-8 -*-
"""轉PNG.py — 把 PDF 轉成 PNG 圖片
用法: python 轉PNG.py 檔案.pdf
輸出: 單頁 → 檔名.png;多頁 → 檔名_全長.png(直向拼接成一張,適合社群分享)
      ⚠️ 多頁**不會**產生 _p1/_p2 分頁檔 —— 刻意的,報告是一份連續文件,拆頁反而不好轉貼。
         (文件原本寫成會出分頁檔,與實作不符,2026-08-01 對齊成實際行為。)
"""
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCALE = 2.0  # 144 dpi,社群分享夠清晰


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python 轉PNG.py 檔案.pdf")
    src = Path(sys.argv[1]).resolve()
    pdf = pdfium.PdfDocument(str(src))
    pages = [p.render(scale=SCALE).to_pil() for p in pdf]

    if len(pages) == 1:
        out = src.with_suffix(".png")
        pages[0].save(out)
        print(f"PNG: {out}")
        return

    # 多頁只輸出直向拼接的長圖,不留分頁檔
    w = max(im.width for im in pages)
    total_h = sum(im.height for im in pages)
    canvas = Image.new("RGB", (w, total_h), "white")
    y = 0
    for im in pages:
        canvas.paste(im, ((w - im.width) // 2, y))
        y += im.height
    long_out = src.with_name(f"{src.stem}_全長.png")
    canvas.save(long_out)
    print(f"PNG(直向長圖): {long_out}")


if __name__ == "__main__":
    main()
