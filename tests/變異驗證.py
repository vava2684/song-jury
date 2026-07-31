# -*- coding: utf-8 -*-
"""變異驗證(mutation check)—— 證明這套測試真的抓得到那些**真實發生過**的 bug。

用法:python tests/變異驗證.py

做法:把每個已修好的缺陷「塞回去」,跑對應的測試,確認它**失敗**;再還原。
⛔ 一條測試若在缺陷被塞回去之後仍然通過,那條測試就是裝飾品,要重寫。

這支不是 pytest 測試(它會改動原始碼再還原),所以刻意不叫 test_*.py,
CI 也另外獨立跑它 —— 讓「測試有沒有效」本身也被自動檢查。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

# ⛔ 這支會印 ✅⛔⏭ 等符號。繁體中文 Windows 的主控台預設是 cp950,
#    不重設編碼的話印第一個符號就 UnicodeEncodeError 當掉(README 教的指令直接崩)。
#    跟其他 CLI 一樣在開頭修好,使用者就不必自己記得設 PYTHONUTF8。
os.environ.setdefault("PYTHONUTF8", "1")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

    ("快取夾名不帶指紋(同名不同曲會共用同一份分軌 → 分數全錯)",
     "分軌快取.py",
     'return f"{audio_path.stem[:40]}__{model_name}__{fingerprint}"',
     'return f"{audio_path.stem[:40]}__{model_name}"',
     "tests/test_stem_cache.py::test_撞名時不會讀到另一首歌的分軌"),

    ("快取夾名只用指紋前 8 碼(32 位元,約 7 萬個檔案就碰撞)",
     "分軌快取.py",
     'return f"{audio_path.stem[:40]}__{model_name}__{fingerprint}"',
     'return f"{audio_path.stem[:40]}__{model_name}__{fingerprint[:8]}"',
     "tests/test_stem_cache.py::test_快取夾名用完整指紋而不是前幾碼"),

    ("命中快取不驗完整身分(只信資料夾名)",
     "分軌快取.py",
     'return rec.get("fingerprint") == fingerprint',
     'return True',
     "tests/test_stem_cache.py::test_命中快取一定要驗完整身分"),

    ("自動採信無身分的舊快取(把別首歌的分軌蓋章成本首的)",
     "分軌快取.py",
     "                if _TRUST_LEGACY:",
     "                if True:",
     "tests/test_stem_cache.py::test_無身分的舊快取預設不採信"),

    # ── Codex 第四輪:這三條原本沒有有效防線(關鍵字測試擋不住)────────────
    ("暫存夾只用 PID(同程序兩執行緒共用同一個暫存夾互相覆寫)",
     "分軌快取.py",
     'uuid.uuid4().hex[:8]}"',
     'fixed"',
     "tests/test_stem_cache.py::test_同程序兩執行緒不會共用暫存夾"),

    ("原子改名吞掉所有錯誤(權限不足/磁碟滿被當成『別人先做好了』)",
     "分軌快取.py",
     "            if not cache.exists():\n                raise",
     "            if False:\n                raise",
     "tests/test_stem_cache.py::test_原子改名不可以吞掉非預期錯誤"),

    ("合法舊快取不被 cache_dir_of 認可(搬不動時指到不存在的位置 → 人聲柱又消失)",
     "分軌快取.py",
     "        if _sidecar_complete(legacy, ident[\"fingerprint\"]):",
     "        if False:",
     "tests/test_stem_cache.py::test_舊快取搬不動時解析路徑仍要對得上"),

    # ── Codex 第六輪 ──────────────────────────────────────────────────
    ("鎖沒有持有者代號(接管後原持有者刪掉別人的鎖 → 三方同檔互踩)",
     "評審團.py",
     '                cur = json.loads(lockf.read_text(encoding="utf-8"))\n                if cur.get("token") == token:\n                    lockf.unlink(missing_ok=True)',
     '                lockf.unlink(missing_ok=True)',
     "tests/test_lock_and_gate.py::test_鎖被接管後原持有者不可以刪掉新鎖"),

    ("陳舊判定只看 mtime(被 kill 的工作卡別人 6 小時)",
     "評審團.py",
     "                holder_alive = _pid_alive(int(rec.get(\"pid\", -1)))",
     "                holder_alive = None",
     "tests/test_lock_and_gate.py::test_持有者死掉的鎖立刻可接管"),

    ("數值閘門退回 is not None(NaN/∞/超範圍/bool 全部進分)",
     "評審團.py",
     "    if isinstance(v, bool) or not isinstance(v, (int, float)):\n        return False\n    return math.isfinite(v) and 0.0 <= v <= 100.0",
     "    return v is not None",
     "tests/test_lock_and_gate.py::test_非法數值不可以進柱分"),

    ("快取完整性退回「有任一 flac」(殘缺夾被當可用)",
     "分軌快取.py",
     "    if isinstance(srcs, list) and srcs:\n        return all((cache / f\"{s}.flac\").exists() for s in srcs)\n    return (cache / \"vocals.flac\").exists()",
     "    return any(cache.glob(\"*.flac\"))",
     "tests/test_stem_cache.py::test_只有部分軌的快取不可以被當成完整"),

    ("Gemini 拿不到鎖照樣無鎖寫入(lost update 從正門回來)",
     "Gemini曲評.py",
     "            if not acquired:\n                return False          # 沒鎖就跳過保存,絕不無鎖寫入",
     "            if False:\n                return False",
     "tests/test_lock_and_gate.py::test_拿不到鎖絕不無鎖寫入"),

    ("Gemini 刪除冷卻退化成 no-op(好金鑰永遠被當冷卻中)",
     "Gemini曲評.py",
     '    _locked_update(lambda cur: cur.pop(fp, None))',
     '    pass',
     "tests/test_lock_and_gate.py::test_成功清除冷卻要真的從磁碟消失"),

    ("階段 JSON 頂層型別不驗(list 讓整份評測 AttributeError)",
     "評審團.py",
     '    if not isinstance(d, dict):\n        return None, f"{label}:JSON 頂層是 {type(d).__name__},不是預期的物件(格式錯誤,視為缺席)"',
     '    if False:\n        pass',
     "tests/test_lock_and_gate.py::test_頂層是list的JSON要當格式錯誤不是炸掉"),

    # ── Codex 第五輪 ──────────────────────────────────────────────────
    ("佔名直接建立正式 mp3(下載失敗留下 0 byte 幽靈檔)",
     "評審團.py",
     'lock = dl / f".{stem}.mp3.reserving"',
     'lock = dl / f"{stem}.mp3"',
     "tests/test_download_and_lock.py::test_佔名不可以建立正式mp3"),

    ("同一個音檔可以同時評兩次(九個中間檔互相覆寫)",
     "評審團.py",
     "            if holder_alive:\n                sys.exit(",
     "            if False:\n                sys.exit(",
     "tests/test_download_and_lock.py::test_同一個音檔不可以同時評兩次"),

    ("Gemini 冷卻狀態不重讀磁碟(lost update → 死金鑰又被呼叫)",
     "Gemini曲評.py",
     "            mutator(cur)",
     "            cur.clear()\n            mutator(cur)",
     "tests/test_download_and_lock.py::test_冷卻狀態不可以lost_update"),

    ("殘缺新快取蓋過合法舊快取(cache_dir_of 指到沒有 flac 的空夾)",
     "分軌快取.py",
     '    if newp.is_dir() and _sidecar_complete(newp, ident["fingerprint"]):',
     '    if newp.is_dir():',
     "tests/test_stem_cache.py::test_殘缺新快取不可以蓋過合法舊快取"),

    ("批次把損壞主檔複製成備份(唯一好備份被毀)",
     "批次評測.py",
     'json.loads(store.read_text(encoding="utf-8"))     # 先確認舊主檔是好的',
     'pass',
     "tests/test_batch_and_windows.py::test_損壞主檔不可以覆蓋好備份"),

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

    ("換行沒鎖(下載 ZIP 的 Linux 使用者會噴 bash: \\r)",
     ".gitattributes",
     "*.sh  text eol=lf",
     "# *.sh 沒鎖",
     "tests/test_packaging.py::test_下載ZIP的人拿到的換行是對的"),

    # ── 以下是 Codex 第二輪抓到的(修完補上變異)──────────────────────
    ("指紋只雜湊頭尾(3MB 檔中段改動測不出來 → 兩首歌共用分軌)",
     "分軌快取.py",
     'for chunk in iter(lambda: f.read(1 << 20), b""):\n            h.update(chunk)',
     'h.update(f.read(1 << 20))',
     "tests/test_stem_cache.py::test_大檔只改中段也要測得出來"),

    ("批次用檔名當結果鍵(不同資料夾的同名歌會漏評)",
     "批次評測.py",
     'key = str(song.resolve()).replace("\\\\", "/")',
     'key = song.name',
     "tests/test_batch_and_windows.py::test_不同路徑的同名歌不可以共用結果鍵"),

    ("批次對缺完整性欄位 fail-open(舊格式/半殘 JSON 反而放行)",
     "批次評測.py",
     'if not isinstance(_pt, dict):\n        return None, "結果缺少 pillar_totals(舊格式或產出不完整),拒收"',
     'if not isinstance(_pt, dict):\n        return d, ""',
     "tests/test_batch_and_windows.py::test_缺少完整性欄位時必須拒收"),
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
    """回 (是否有測試真的 failed, 是否有測試真的跑到)。

    ⛔ 不可以只看 pytest 的退出碼:目標測試若被 **skip**(例如 ZIP 環境沒有 .git,
       打包檢查會誠實跳過),退出碼一樣是 0 → 會被誤判成「變異沒被抓到」。
       skipped 不等於通過,也不等於失敗 —— 它代表這次根本沒驗到,必須另外標示。
    """
    # ⛔ 不可以靠解析主控台文字:pytest.ini 的 addopts 已有 -q,再加一個就變 -qq,
    #    單一被跳過的測試只印 `s [100%]`,正則永遠找不到「1 skipped」→ skip 被誤判成
    #    「沒抓到」。改讀 JUnit XML,結構化資料不受輸出格式影響。
    import tempfile
    import xml.etree.ElementTree as ET
    with tempfile.TemporaryDirectory() as td:
        xml = Path(td) / "r.xml"
        subprocess.run([PY, "-m", "pytest", target, "--no-header",
                        "-p", "no:cacheprovider", f"--junit-xml={xml}"],
                       cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**__import__("os").environ, "PYTHONUTF8": "1"})
        if not xml.exists():
            return False, False        # 連 XML 都沒產出 = 這次沒驗到
        root = ET.parse(xml).getroot()
        cases = root.iter("testcase")
        n_fail = n_skip = n_pass = 0
        for c in cases:
            kinds = {ch.tag for ch in c}
            if kinds & {"failure", "error"}:
                n_fail += 1
            elif "skipped" in kinds:
                n_skip += 1
            else:
                n_pass += 1
    # 有任何一條真的 failed → 抓到了;全部都是 skipped(沒有 pass 也沒有 fail)→ 沒驗到
    return n_fail > 0, (n_fail + n_pass) > 0


def main():
    print("=" * 66)
    print("  變異驗證:把真實 bug 塞回去,確認測試抓得到")
    print("=" * 66)

    # 先確認乾淨狀態全綠,否則後面的結果沒有意義
    _failed, _ = run_pytest("tests")
    if _failed:
        print("\n✗ 乾淨狀態下測試就沒過,先修好再跑變異驗證。")
        return 1

    bad, skipped = [], []
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
            failed, ran = run_pytest(target)
        finally:
            p.write_bytes(raw)                        # 一定要逐位元還原
        if failed:
            print(f"\n[{i}/{len(MUTATIONS)}] ✅ 抓到了:{desc}")
        elif not ran:
            print(f"\n[{i}/{len(MUTATIONS)}] ⏭ 無法驗證:{desc}")
            print(f"        → {target} 在這個環境被 skip,這次沒驗到(不是通過)")
            skipped.append(desc)
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
            # ⛔ 這是「這個環境驗不了」(ZIP 沒有 .git),不是「測試沒抓到」——
            #    算進 bad 會讓 ZIP 版永遠報 4 條缺陷沒抓到,那是假警報。
            print(f"\n[{j}/{n0 + len(GIT_MUTATIONS)}] ⏭ 無法驗證:{desc}")
            print(f"        → 這個環境沒有 git index(ZIP 版),打包類變異只能在 clone 裡驗")
            skipped.append(desc)
            continue
        try:
            failed, ran = run_pytest(target)
        finally:
            subprocess.run(["git", "add", "--", fname], cwd=REPO, capture_output=True)
        if failed:
            print(f"\n[{j}/{n0 + len(GIT_MUTATIONS)}] ✅ 抓到了:{desc}")
        elif not ran:
            print(f"\n[{j}/{n0 + len(GIT_MUTATIONS)}] ⏭ 無法驗證:{desc}(被 skip,不是通過)")
            skipped.append(desc)
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
    total = len(MUTATIONS) + len(GIT_MUTATIONS)
    if skipped:
        # ⛔ 有沒驗到的就不可以宣稱「全部抓到」——那是把 skip 當成通過,正是這支要防的事
        print(f"  ⚠️ {total - len(skipped)}/{total} 條抓到;另有 {len(skipped)} 條在這個環境無法驗證:")
        for s_ in skipped:
            print(f"     ⏭ {s_}")
        print("     (要完整驗證請在 git clone 的目錄跑,ZIP 版沒有 .git)")
    else:
        print(f"  ✅ {total} 條真實缺陷全部會被測試抓到")
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
