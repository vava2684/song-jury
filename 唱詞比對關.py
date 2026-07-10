# -*- coding: utf-8 -*-
"""唱詞比對關.py — 第四關A:AI 唱詞準確率(接駁 lyric-checker)

原理:SUNO 等 AI 常吃字/唱錯字/亂改詞——把「應唱歌詞」與「實唱聽寫」逐字比對。
引擎:lyric-checker(Demucs htdemucs_ft 人聲分離 + OpenAI Whisper large-v3 聽寫,本機跑)

用法: python 唱詞比對關.py <音檔> <歌詞txt> [報告輸出資料夾]
- 歌詞自動剝除【】/[] 段落標記行後送比對(標記不是唱出來的字)
- 輸出:整體吻合度 % + HTML 逐句比對報告(存到輸出資料夾)
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent.resolve()
LC = Path(r"C:\Users\VAVA\lyric-checker")
LC_PYTHON = r"C:\Users\VAVA\anaconda3\python.exe"  # lyric-checker 的引擎(whisper+demucs)住在 anaconda
TEMP = BASE / "_第四關暫存"
TAG_LINE = re.compile(r"^\s*[\[【(（].*[\]】)）]\s*$")


def clean_lyrics(txt_path):
    lines = Path(txt_path).read_text(encoding="utf-8").splitlines()
    keep = [ln for ln in lines if ln.strip() and not TAG_LINE.match(ln)]
    return "\n".join(keep) + "\n"


def main():
    if len(sys.argv) < 3:
        sys.exit("用法: python 唱詞比對關.py <音檔> <歌詞txt> [報告輸出資料夾]")
    audio = Path(sys.argv[1]).resolve()
    lyrics = Path(sys.argv[2]).resolve()
    out_dir = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else audio.parent

    if not LC.exists():
        sys.exit(f"找不到 lyric-checker:{LC}")

    TEMP.mkdir(exist_ok=True)
    stem = audio.stem
    a_copy = TEMP / audio.name
    shutil.copy2(audio, a_copy)
    (TEMP / f"{stem}.txt").write_text(clean_lyrics(lyrics), encoding="utf-8")

    print(f"🎤 第四關A|唱詞準確率量測: {audio.name}")
    print("   (Demucs 人聲分離 + Whisper large-v3 聽寫,本機 GPU,幾分鐘)")
    env = {**os.environ, "PYTHONUTF8": "1"}
    p = subprocess.run([LC_PYTHON, str(LC / "check.py"), str(a_copy)],
                       cwd=str(LC), env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")

    m = list(re.finditer(r"整體吻合度\s*(\d+)\s*%", out))
    acc = m[-1].group(1) if m else None

    report_src = LC / "報告_在這裡" / f"{stem}_比對報告.html"
    report_dst = None
    if report_src.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        report_dst = out_dir / f"{stem}_唱詞比對.html"
        shutil.move(str(report_src), report_dst)

    # 清暫存(lyric-checker 的 _work 快取保留,重跑可加速)
    for f in TEMP.glob(f"{stem}.*"):
        f.unlink(missing_ok=True)

    if acc is None:
        print("❌ 比對失敗,lyric-checker 輸出末段:")
        print(out[-800:])
        sys.exit(1)
    print(f"✅ 唱詞吻合度: {acc}%")
    if report_dst:
        print(f"📄 逐句報告: {report_dst}")


if __name__ == "__main__":
    main()
