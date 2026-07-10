# -*- coding: utf-8 -*-
"""情感弧線.py — 歌詞情緒軌跡量測儀(評詞標準 v2 第三支柱的儀器層)

理論依據:
  Russell (1980) 情感環狀模型 — Valence(愉悅度)× Arousal(激動度)
  詞庫: NRC-VAD Lexicon (加拿大國家研究院, Mohammad 2018, 研究用途免費)
       中文對照(簡+繁合併),0–1 分制,0.5 = 中性
用法:
  python 情感弧線.py 歌詞.txt
  歌詞用 SUNO 格式的 [Verse]/[Chorus] 段落標記,無標記則以空行分段
輸出:
  主控台逐段報告 + 歌詞檔旁 _情感弧線.png + _情感弧線.json
"""
import json
import re
import sys
from pathlib import Path
from statistics import mean

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent.resolve()
LEX_DIR = BASE / "lexicon" / "nrc-vad" / "NRC-VAD-Lexicon-Aug2018Release" / "OneFilePerLanguage"
LEX_CACHE = BASE / "lexicon" / "zh_vad.tsv"
EN_LEX_SRC = BASE / "lexicon" / "nrc-vad" / "NRC-VAD-Lexicon-Aug2018Release" / "NRC-VAD-Lexicon.txt"
EN_LEX_CACHE = BASE / "lexicon" / "en_vad.tsv"
HAN = re.compile(r"[一-鿿]")
TEXTY = re.compile(r"[一-鿿A-Za-z]")


def build_lexicon():
    """合併簡繁 NRC-VAD 中文對照成 word→(V,A);重複翻譯取平均。"""
    pool = {}
    for fname in ("Chinese (Simplified)-zh-CN-NRC-VAD-Lexicon.txt",
                  "Chinese (Traditional)-zh-TW-NRC-VAD-Lexicon.txt"):
        fp = LEX_DIR / fname
        for line in fp.read_text(encoding="utf-8").splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) < 5:
                continue
            zh, v, a = cols[1].strip(), cols[2], cols[3]
            if not zh or zh == "NO TRANSLATION" or not HAN.search(zh):
                continue
            pool.setdefault(zh, []).append((float(v), float(a)))
    rows = [f"{w}\t{mean(v for v, _ in vs):.4f}\t{mean(a for _, a in vs):.4f}"
            for w, vs in pool.items()]
    LEX_CACHE.write_text("\n".join(rows), encoding="utf-8")
    return load_lexicon()


def load_lexicon():
    if not LEX_CACHE.exists():
        return build_lexicon()
    lex = {}
    for line in LEX_CACHE.read_text(encoding="utf-8").splitlines():
        w, v, a = line.split("\t")
        lex[w] = (float(v), float(a))
    return lex


def load_en_lexicon():
    """英文詞庫:NRC-VAD 原生英文詞(word→V,A)。"""
    if EN_LEX_CACHE.exists():
        lex = {}
        for line in EN_LEX_CACHE.read_text(encoding="utf-8").splitlines():
            w, v, a = line.split("\t")
            lex[w] = (float(v), float(a))
        return lex
    lex = {}
    for line in EN_LEX_SRC.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) >= 3:
            lex[cols[0].strip().lower()] = (float(cols[1]), float(cols[2]))
    EN_LEX_CACHE.write_text(
        "\n".join(f"{w}\t{v:.4f}\t{a:.4f}" for w, (v, a) in lex.items()), encoding="utf-8")
    return lex


def split_sections(text):
    """依 [標記] 分段(標記可含演唱描述,取「-」或「:」前的短名);無標記則空行分段。"""
    tags = list(re.finditer(r"^\s*[\[(（【]([^\]\)）】]{1,60})[\])）】]\s*$", text, re.M))
    sections = []
    if tags:
        for i, m in enumerate(tags):
            start = m.end()
            end = tags[i + 1].start() if i + 1 < len(tags) else len(text)
            body = text[start:end].strip()
            label = re.split(r"\s*[-–—:,]\s*", m.group(1).strip())[0].strip()[:20]
            if TEXTY.search(body or ""):
                sections.append((label, body))
    else:
        for i, blk in enumerate([b for b in re.split(r"\n\s*\n", text) if TEXTY.search(b)], 1):
            sections.append((f"段{i}", blk.strip()))
    return sections


def measure_section_en(body, en_lex):
    """英文段落:斷詞後查英文詞庫。"""
    words = re.findall(r"[A-Za-z']+", body.lower())
    hits = [(w, *en_lex[w]) for w in words if w in en_lex]
    coverage = len(hits) / len(words) if words else 0.0
    return hits, coverage


def measure_section(body, lex, max_len):
    """最長匹配掃描:回傳配到的 (詞, V, A) 列表與覆蓋率。"""
    hits, i, n = [], 0, len(body)
    matched_chars = 0
    while i < n:
        if not HAN.match(body[i]):
            i += 1
            continue
        hit = None
        for L in range(min(max_len, n - i), 0, -1):
            w = body[i:i + L]
            if w in lex:
                hit = (w, *lex[w])
                break
        if hit:
            hits.append(hit)
            matched_chars += len(hit[0])
            i += len(hit[0])
        else:
            i += 1
    total_han = len(HAN.findall(body))
    coverage = matched_chars / total_han if total_han else 0.0
    return hits, coverage


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python 情感弧線.py 歌詞.txt")
    src = Path(sys.argv[1]).resolve()
    text = src.read_text(encoding="utf-8")

    sections = split_sections(text)
    if not sections:
        sys.exit("找不到可分析的段落")

    is_zh = bool(HAN.search(text))
    if is_zh:
        lex = load_lexicon()
        max_len = max(len(w) for w in lex)
        lang_note = f"中文詞庫 {len(lex):,} 詞"
    else:
        en_lex = load_en_lexicon()
        lang_note = f"英文詞庫 {len(en_lex):,} 詞"

    results = []
    print(f"🎭 情感弧線量測: {src.name}({lang_note},NRC-VAD)\n")
    for label, body in sections:
        if is_zh:
            hits, cov = measure_section(body, lex, max_len)
        else:
            hits, cov = measure_section_en(body, en_lex)
        if not hits:
            print(f"[{label}] 無配詞,略過")
            continue
        v = mean(h[1] for h in hits)
        a = mean(h[2] for h in hits)
        neg = sorted(hits, key=lambda h: h[1])[:3]
        hot = sorted(hits, key=lambda h: -h[2])[:3]
        results.append({"section": label, "valence": round(v, 3), "arousal": round(a, 3),
                        "n_words": len(hits), "coverage": round(cov, 2),
                        "most_negative": [h[0] for h in neg],
                        "most_arousing": [h[0] for h in hot]})
        print(f"[{label}]  V={v:.3f}  A={a:.3f}  (配詞 {len(hits)},覆蓋 {cov:.0%})")
        print(f"    最負面: {'、'.join(h[0] for h in neg)} | 最激動: {'、'.join(h[0] for h in hot)}")

    if len(results) >= 2:
        dv = results[-1]["valence"] - results[0]["valence"]
        da = results[-1]["arousal"] - results[0]["arousal"]
        vs = [r["valence"] for r in results]
        swing = max(vs) - min(vs)
        print()
        print(f"弧線判定: Valence 首尾位移 {dv:+.3f} | Arousal 首尾位移 {da:+.3f} | 最大擺幅 {swing:.3f}")
        print("→ " + ("情緒有明顯移動(擺幅 ≥ 0.05)" if swing >= 0.05 else "⚠ 情緒弧線平坦(擺幅 < 0.05),整首停在同一情緒"))

    # 圖
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.family"] = "Microsoft JhengHei"
        plt.rcParams["axes.unicode_minus"] = False
        labels = [r["section"] for r in results]
        vs = [r["valence"] for r in results]
        as_ = [r["arousal"] for r in results]
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
        x = range(len(results))
        ax1.plot(x, vs, "o-", label="Valence 愉悅度", linewidth=2)
        ax1.plot(x, as_, "s--", label="Arousal 激動度", linewidth=2)
        ax1.axhline(0.5, color="gray", linewidth=0.8, alpha=0.6)
        ax1.set_xticks(list(x))
        ax1.set_xticklabels(labels, rotation=30, ha="right")
        ax1.set_ylim(0, 1)
        ax1.set_title("逐段情緒軌跡(0.5=中性)")
        ax1.legend()
        ax2.plot(vs, as_, "o-", alpha=0.7)
        for i, lb in enumerate(labels):
            ax2.annotate(f"{i+1}.{lb}", (vs[i], as_[i]), fontsize=8)
        ax2.axhline(0.5, color="gray", linewidth=0.8, alpha=0.6)
        ax2.axvline(0.5, color="gray", linewidth=0.8, alpha=0.6)
        ax2.set_xlim(0, 1)
        ax2.set_ylim(0, 1)
        ax2.set_xlabel("Valence 愉悅度")
        ax2.set_ylabel("Arousal 激動度")
        ax2.set_title("Russell 情感環狀平面路徑")
        fig.suptitle(src.stem)
        fig.tight_layout()
        png = src.with_name(src.stem + "_情感弧線.png")
        fig.savefig(png, dpi=120)
        print(f"\n弧線圖: {png}")
    except Exception as e:
        print(f"\n(畫圖失敗,不影響量測: {e})")

    out = src.with_name(src.stem + "_情感弧線.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON: {out}")


if __name__ == "__main__":
    main()
