# -*- coding: utf-8 -*-
"""變異驗證(mutation check)—— 證明這套測試真的抓得到那些**真實發生過**的 bug。

用法:python tests/變異驗證.py

做法:把每個已修好的缺陷「塞回去」,跑對應的測試,確認它**失敗**;再還原。
⛔ 一條測試若在缺陷被塞回去之後仍然通過,那條測試就是裝飾品,要重寫。

這支不是 pytest 測試(它會改動原始碼再還原),所以刻意不叫 test_*.py,
CI 也另外獨立跑它 —— 讓「測試有沒有效」本身也被自動檢查。
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable

# (說明, 檔案, 原字串, 換成的「壞掉版本」, 應該要失敗的測試)
MUTATIONS = [
    ("切窗漏掉最後一個完整窗(40s 只分析 1 個窗)",
     "評審團.py",
     "return range(0, max(1, n_samples - win + 1), win)",
     "return range(0, max(1, n_samples - win), win)",
     "tests/test_batch_and_windows.py::test_切窗不漏最後一個完整窗"),

    ("Gemini 分數不夾範圍(M1:99 → 990/100)",
     "Gemini曲評.py",
     "return v if 0.0 <= v <= 10.0 else None",
     "return v",
     "tests/test_gemini_parse.py"),

    ("Gemini 總分取錯鍵名(整關被靜默丟掉)",
     "評審團.py",
     '_gt = _g(gemini, "gemini_reported_total", "raw_0to10")',
     '_gt = _g(gemini, "total")',
     "tests/test_pillars.py::test_Gemini總分取的是gemini_reported_total而不是total"),

    ("快取不驗身分(同名不同曲會讀到別首歌的分軌)",
     "分軌快取.py",
     'if rec.get("fingerprint") != ident["fingerprint"]:',
     'if False:',
     "tests/test_stem_cache.py::test_撞名時不會讀到另一首歌的分軌"),

    ("批次不看 returncode(程式炸掉但檔案已寫出 → 誤判成功)",
     "批次評測.py",
     'if r.returncode != 0:\n        return None, f"評審團 結束碼 {r.returncode}:" + (r.stderr or r.stdout or "")[-260:]',
     'if False:\n        pass',
     "tests/test_batch_and_windows.py::test_子程序失敗但已寫出檔案時仍要判失敗"),

    ("批次不先刪舊產物(失敗時偷用上一輪的舊報告)",
     "批次評測.py",
     "if out_json.exists():\n        out_json.unlink()",
     "if False:\n        pass",
     "tests/test_batch_and_windows.py::test_這輪沒產出新檔時不可以讀到上一輪的舊JSON"),

    ("缺柱不標記(不完整評測偽裝成正常分數)",
     "評審團.py",
     '"完整評測": not lost,',
     '"完整評測": True,',
     "tests/test_pillars.py::test_缺柱時完整評測必為False且列出缺柱"),

    ("第三方相依沒宣告(作者本機間接裝了所以沒事,別人一裝就炸)",
     "requirements.txt",
     "requests            # Gemini曲評.py 呼叫 API 用",
     "# requests 忘了寫",
     "tests/test_packaging.py::test_每個環境的第三方相依都由該環境的requirements宣告"),
]

# 打包類的變異不能靠改字串 —— 檔案一旦已被 git 追蹤,改 .gitignore 是不會讓它消失的
#(這也正是當初「白名單漏放行」沒被 git status 抓到的原因)。
# 要真的模擬「這個檔沒進 repo」,得把它從 index 拿掉。
GIT_MUTATIONS = [
    ("白名單漏放行 分軌快取.py(頂層 import 的共用底層沒進 repo → 別人 clone 必炸)",
     "分軌快取.py",
     "tests/test_packaging.py::test_每個被引用的本地模組都在repo裡"),
    ("白名單漏放行 伴奏混音.py(評審團會 subprocess 呼叫它)",
     "伴奏混音.py",
     "tests/test_packaging.py::test_每個被subprocess呼叫的腳本都在repo裡"),
    ("四把尺其中一把沒進 repo(詞柱評不出來)",
     "rubrics/JA_lyric_rubric_v3.md",
     "tests/test_packaging.py::test_規則與尺都隨包"),
]


def run_pytest(target):
    r = subprocess.run([PY, "-m", "pytest", target, "-q", "--no-header", "-x"],
                       cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**__import__("os").environ, "PYTHONUTF8": "1"})
    return r.returncode


def main():
    print("=" * 66)
    print("  變異驗證:把真實 bug 塞回去,確認測試抓得到")
    print("=" * 66)

    # 先確認乾淨狀態全綠,否則後面的結果沒有意義
    if run_pytest("tests") != 0:
        print("\n✗ 乾淨狀態下測試就沒過,先修好再跑變異驗證。")
        return 1

    bad = []
    for i, (desc, fname, old, new, target) in enumerate(MUTATIONS, 1):
        p = REPO / fname
        # ⛔ 一定要用二進位讀寫:read_text/write_text 在 Windows 會做換行轉換,
        #    「還原」時會把 LF 檔案寫成 CRLF,把原始碼弄髒(自己踩過)。
        raw = p.read_bytes()
        src = raw.decode("utf-8")
        if old not in src:
            print(f"\n[{i}/{len(MUTATIONS)}] ⚠ 跳過:在 {fname} 找不到要變異的字串")
            print(f"        ({desc})  ← 程式改過了?請更新這條變異")
            bad.append(desc)
            continue
        p.write_bytes(src.replace(old, new, 1).encode("utf-8"))
        try:
            rc = run_pytest(target)
        finally:
            p.write_bytes(raw)                        # 一定要逐位元還原
        if rc != 0:
            print(f"\n[{i}/{len(MUTATIONS)}] ✅ 抓到了:{desc}")
        else:
            print(f"\n[{i}/{len(MUTATIONS)}] ❌ 沒抓到:{desc}")
            print(f"        → {target} 在缺陷存在時仍然通過,這條測試是裝飾品")
            bad.append(desc)

    # ── 打包類:用 git rm --cached 模擬「這個檔沒進 repo」 ──────────────
    n0 = len(MUTATIONS)
    for j, (desc, fname, target) in enumerate(GIT_MUTATIONS, n0 + 1):
        rm = subprocess.run(["git", "rm", "--cached", "-q", "--", fname],
                            cwd=REPO, capture_output=True, text=True)
        if rm.returncode != 0:
            print(f"\n[{j}/{n0 + len(GIT_MUTATIONS)}] ⚠ 跳過:{fname} 本來就不在 index 裡")
            bad.append(desc)
            continue
        try:
            rc = run_pytest(target)
        finally:
            subprocess.run(["git", "add", "--", fname], cwd=REPO, capture_output=True)
        if rc != 0:
            print(f"\n[{j}/{n0 + len(GIT_MUTATIONS)}] ✅ 抓到了:{desc}")
        else:
            print(f"\n[{j}/{n0 + len(GIT_MUTATIONS)}] ❌ 沒抓到:{desc}")
            print(f"        → {target} 在缺陷存在時仍然通過,這條測試是裝飾品")
            bad.append(desc)

    print("\n" + "=" * 66)
    if bad:
        print(f"  ❌ 有 {len(bad)} 條缺陷不會被測試抓到:")
        for b in bad:
            print(f"     · {b}")
        return 1
    print(f"  ✅ {len(MUTATIONS) + len(GIT_MUTATIONS)} 條真實缺陷全部會被測試抓到")
    # 最後再確認一次:所有檔案都還原乾淨了。
    # ⚠️ 要用 `git diff --name-only`(工作區 vs index),不是 `git status --porcelain` ——
    #    後者會把「跑之前就已經 stage 的正常修改」也一起列出來,變成誤報(自己踩過)。
    r = subprocess.run(["git", "-c", "core.quotepath=false", "diff", "--name-only"],
                       cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    touched = {m[1] for m in MUTATIONS}
    dirty = [ln.strip() for ln in r.stdout.splitlines() if ln.strip() in touched]
    if dirty:
        print(f"  ⚠️ 變異後沒還原乾淨:{dirty}")
        return 1
    print("  ✅ 原始碼已全部還原")
    return 0


if __name__ == "__main__":
    sys.exit(main())
