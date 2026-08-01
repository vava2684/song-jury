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
     '_gt = _raw(gemini, "gemini_reported_total", "raw_0to10")',
     '_gt = _raw(gemini, "total")',
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

    # ── Codex 第七輪:OS 鎖、bool 洗白、清洗共用、原子報告 ───────────────
    # ⚠ R9 起 busy 與 error 都會 sys.exit(fail-closed),只把 if 換成 False 會
    #   落到 error 分支照樣退出、測試照樣過 → 變異要把**兩個出口都拔掉**才算 fail-open。
    ("拿不到 OS 鎖卻照樣進入評測(互斥失效)",
     "評審團.py",
     "            if e.errno in _BUSY:\n"
     "                sys.exit(f\"⛔ 這個檔正在被另一個評測工作處理中:{song.name}\\n\"\n"
     "                         f\"   (中間檔會互相覆寫,所以同一個檔不允許同時評兩次)\\n\"\n"
     "                         f\"   → 等它跑完再試。持有工作若被強制終止,OS 會自動釋放這把鎖,不必手動清。\")\n"
     "            sys.exit(f\"⛔ 工作鎖在此檔案系統不可用(errno={e.errno})。\\n\"\n"
     "                     f\"   鎖檔位置:{lockf}\\n\"\n"
     "                     f\"   → 請把本工具移到支援檔案鎖的本機磁碟再跑。\"\n"
     "                     f\"(不放行:沒有互斥就評,兩個工作的中間檔會互相覆寫,分數會錯得無聲無息)\")",
     "            pass  # 變異:兩個 sys.exit 都拔掉,拿不到鎖照樣進入評測",
     "tests/test_download_and_lock.py::test_同一個音檔不可以同時評兩次"),

    ("bool 在取值層被 float() 洗成 1.0(True 混進正式柱分)",
     "評審團.py",
     "    if isinstance(v, bool) or not isinstance(v, (int, float)):\n        return None",
     "    if not isinstance(v, (int, float)):\n        return None",
     "tests/test_lock_and_gate.py::test_bool不可以被洗成浮點數且要留下證據"),

    # ── Codex 第八輪 ──────────────────────────────────────────────────
    ("拿到租約後沿用舊冷卻快照(對剛被限流的 key 再打一發)",
     "Gemini曲評.py",
     "            state.clear()\n            state.update(load_state())",
     "            pass",
     "tests/test_lock_and_gate.py::test_拿到租約後要重讀冷卻不可沿用舊快照"),

    ("取值層把非法值抹成 None(invalid_numeric 證據消失)",
     "評審團.py",
     "        n = _num_or_none(d)\n        return n if n is not None else d",
     "        return _num_or_none(d)",
     "tests/test_lock_and_gate.py::test_bool不可以被洗成浮點數且要留下證據"),

    ("清洗器不驗來源量尺(SongEval 99 讓主控台與柱分互相矛盾)",
     "評審團.py",
     "    if lo is not None and hi is not None:",
     "    if False:",
     "tests/test_lock_and_gate.py::test_clean_scores驗來源量尺範圍"),

    ("深層欄位直接格式化(N/A 讓摘要在報告寫完後炸掉)",
     "評審團.py",
     '    n = _num_or_none(v)\n    return None if n is None else f"{n:.{nd}f}"',
     '    return f"{v:.{nd}f}"',
     "tests/test_lock_and_gate.py::test_深層欄位格式化不可以炸掉"),

    ("報告直接覆寫正式檔(發布失敗留下半截報告)",
     "評審團.py",
     '    try:\n        tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2, allow_nan=False),',
     '    try:\n        out_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2, allow_nan=False),',
     "tests/test_lock_and_gate.py::test_報告發布失敗要保住舊報告且不留暫存"),

    ("鎖壞掉被當成有人持有(所有 key 被跳過/使用者被幽靈持有者擋住)",
     "Gemini曲評.py",
     '                if e.errno in _BUSY:\n                    status = "busy"',
     '                if True:\n                    status = "busy"',
     "tests/test_lock_and_gate.py::test_鎖壞掉不可以被當成有人持有"),

    ("引擎輸出不清洗(摘要層 sum 到字串 → 報告寫完才 TypeError)",
     "評審團.py",
     "    out = {k: float(v) for k, v in d.items() if k not in bad}",
     "    out = dict(d)",
     "tests/test_lock_and_gate.py::test_clean_scores把非數值欄位清掉並留痕"),

    ("報告不清洗非有限值(寫出 NaN/Infinity 的非標準 JSON)",
     "評審團.py",
     "    cleaned = _scrub_nonfinite(merged)",
     "    cleaned = merged",
     "tests/test_lock_and_gate.py::test_報告寫出是原子的且不含NaN"),

    ("狀態鎖不互斥(兩個持有者同時進鎖 → lost update)",
     "Gemini曲評.py",
     "        # 狀態鎖:busy 與 error 都回 False(不寫比亂寫安全;冷卻只是最佳化)\n        yield status == \"ok\"",
     "        # 狀態鎖:busy 與 error 都回 False(不寫比亂寫安全;冷卻只是最佳化)\n        yield True",
     "tests/test_lock_and_gate.py::test_狀態鎖真的互斥"),

    ("金鑰租約形同虛設(同一把 key 同時被兩個工作轟)",
     "Gemini曲評.py",
     "        yield status          # \"ok\" / \"busy\" / \"error\"",
     "        yield \"ok\"          # 變異:busy/error 全部放行",
     "tests/test_lock_and_gate.py::test_同一把金鑰同時只准一個工作在打"),

    # ── Codex 第十五輪:schema 必填、契約版本、政策唯一來源、比較器規則 ────
    ("裁判又讓 items/missing 可省略(不完整 schema 被蓋章)",
     "驗證報告.py",
     '        if "items" not in det:',
     '        if False:',
     "tests/test_keyprobe_and_verify.py::test_柱的內層schema壞掉要拒收"),

    ("缺柱權重合計用 default 0(缺鍵被偽造成合法)",
     "驗證報告.py",
     '    if "缺柱權重合計" not in pt:',
     '    if False:',
     "tests/test_keyprobe_and_verify.py::test_缺欄位一律要拒收"),

    ("曲側含柱退回 optional(缺鍵/dict 都矇混過關)",
     "驗證報告.py",
     '    if "曲側含柱" not in pt:',
     '    if False:',
     "tests/test_keyprobe_and_verify.py::test_缺欄位一律要拒收"),

    ("合成容差放回 0.15(放過一整個顯示刻度的錯)",
     "驗證報告.py",
     'COMPOSITE_TOL = 0.05',
     'COMPOSITE_TOL = 0.15',
     "tests/test_keyprobe_and_verify.py::test_合成差一個刻度也要抓到"),

    ("不認得的計分契約照樣放行(舊報告/竄改版被蓋章)",
     "驗證報告.py",
     '        if not isinstance(cname, str) or cname not in CONTRACTS:',
     '        if False:',
     "tests/test_keyprobe_and_verify.py::test_不認得的計分契約要拒收"),

    ("秘密檔只驗 leaf(父目錄 junction 借用別條產線)",
     "金鑰政策.py",
     '        if parent.is_symlink() or (os.name == "nt" and _is_reparse(pst)):',
     '        if False:',
     "tests/test_key_policy.py::test_父目錄是連結要拒絕"),

    ("政策錯誤被洗成「沒有金鑰」(自動化分不出安全問題與沒填)",
     "金鑰驗證.py",
     '    if policy_error:',
     '    if False:',
     "tests/test_keyprobe_and_verify.py::test_政策錯誤要用獨立退出碼"),

    ("PK 不要求指定語言(跨語言尺被硬比)",
     "比較.py",
     '    if not lang:',
     '    if False:',
     "tests/test_compare.py::test_PK要指定語言"),

    ("比較器不檢查計分契約(換了尺照樣比)",
     "比較.py",
     '    if len(names) > 1:',
     '    if False:',
     "tests/test_compare.py::test_不同計分契約不可比"),

    ("比較器不過獨立裁判(不完整報告混進排名)",
     "比較.py",
     '    why = validate(path)',
     '    why = ""',
     "tests/test_compare.py::test_不完整的報告不可以進比較"),

    ("並列門檻拆掉(0.3 分的差距被當成真的高下)",
     "比較.py",
     '        tie = i > 0 and (ordered[i - 1]["composite"] - it["composite"]) < TIE_THRESHOLD',
     '        tie = False',
     "tests/test_compare.py::test_差距很小要標並列不是硬排名次"),

    ("批次退回「只收完整九柱」(預設模式每首都被拒收)",
     "批次評測.py",
     '    extra = lost - GEMINI_ONLY_PILLARS',
     '    extra = lost',
     "tests/test_batch_and_windows.py::test_預設批次收得到結果而不是每首都拒收"),

    ("VerifyModels 拿掉外層 timeout(模型 deadlock 就永遠掛著)",
     "install.ps1",
     # ⚠ 要**整行**拿掉:只改前半的話,同一行後段與訊息裡的變數名還在,
     #   grep 型的測試照樣命中(裝飾品)。
     '            $vTimeout = if ($env:SONG_JURY_VERIFY_TIMEOUT) { $env:SONG_JURY_VERIFY_TIMEOUT } else { "7200" }',
     '            $vTimeout = "7200"   # 變異:拿掉可設定的逾時',
     "tests/test_packaging.py::test_VerifyModels要有外層timeout",
     "win32"),

    # ── Codex 第十四輪:驗證順序、失敗路徑殺樹、裁判自洽、政策 fail-closed ──
    # ⚠ 要注入的是**順序錯誤**(清理跑在裁判之前),不是「不叫裁判」——
    #   後者是另一種缺陷,描述與注入不一致就等於沒驗到那個 bug(Codex R15)。
    ("VerifyModels 先清理才叫裁判(成功路徑必定假陰性)",
     "install.ps1",
     '            if ($vrc -eq 124) {',
     '            Get-ChildItem -File -Filter "$vid*" -EA SilentlyContinue | Remove-Item -Force -EA SilentlyContinue\n'
     '            if ($vrc -eq 124) {',
     "tests/test_installer_order.py::test_ps1的驗證順序_裁判先看到報告清理在最後",
     "win32"),

    # ⚠ 殺樹在 Windows 上有兩道:主動 kill_tree + 最外層 finally 的 job.close()
    #   (KILL_ON_JOB_CLOSE)。只拔一道會被另一道救回 → 兩道一起拔才是真 fail-open。
    ("非逾時失敗不殺樹(呼叫端已失敗,子程序還在吃 GPU)",
     "子程序.py",
     '        except BaseException:\n'
     '            # ⛔ 任何其他失敗(含 KeyboardInterrupt)也要殺樹再往外拋 ——\n'
     '            #    「呼叫端失敗了但子程序還在跑」是這個模組存在的理由要防的事。\n'
     '            kill_tree(p, job)\n'
     '            try:\n'
     '                p.communicate(timeout=10)\n'
     '            except Exception:\n'
     '                pass\n'
     '            raise\n'
     '        return subprocess.CompletedProcess(cmd, p.returncode, stdout=out, stderr=err)\n'
     '    finally:\n'
     '        if job is not None:\n'
     '            job.close()      # KILL_ON_JOB_CLOSE:即使前面漏殺,關 handle 也會收乾淨',
     '        except _SJ_NEVER:\n'
     '            pass\n'
     '        return subprocess.CompletedProcess(cmd, p.returncode, stdout=out, stderr=err)\n'
     '    finally:\n'
     '        pass  # 變異:既不殺樹也不關 job',
     "tests/test_run_tree.py::test_非逾時例外也要殺樹"),

    ("Popen 失敗洩漏 Job handle(長跑服務一路漏核心 handle)",
     "子程序.py",
     '    finally:\n        if job is not None:\n            job.close()      # KILL_ON_JOB_CLOSE',
     '    finally:\n        pass  # 變異:不關 Job handle',
     "tests/test_run_tree.py::test_Popen失敗不可洩漏Job_handle",
     "win32"),

    ("裁判不重算曲側合成(八柱全 0 卻宣稱 100 照樣蓋章)",
     "驗證報告.py",
     '    if abs(expect - float(v)) > COMPOSITE_TOL:',
     '    if False:',
     "tests/test_keyprobe_and_verify.py::test_八柱全0卻宣稱合成100要拒收"),

    ("裁判不驗缺柱權重(完整=true 卻缺柱權重 99.9)",
     "驗證報告.py",
     '    if abs(float(lostw)) > 1e-9:',
     '    if False:',
     "tests/test_keyprobe_and_verify.py::test_完整評測卻有缺柱權重要拒收"),

    ("裁判不驗柱的內層 schema(items=[]、missing='junk' 照過)",
     "驗證報告.py",
     '        if not isinstance(det["items"], dict):',
     '        if False:',
     "tests/test_keyprobe_and_verify.py::test_柱的內層schema壞掉要拒收"),

    ("拒絕名單不驗格式(打錯一碼就靜默放行,以為擋住其實沒擋)",
     "金鑰政策.py",
     '            if not _HEX64.match(tok):',
     '            if False:',
     "tests/test_key_policy.py::test_拒絕名單格式錯要fail_closed"),

    (".env 是硬連結照樣採用(借到別條產線的秘密檔)",
     "金鑰政策.py",
     '    if getattr(st, "st_nlink", 1) > 1:',
     '    if False:',
     "tests/test_key_policy.py::test_env是硬連結要拒絕"),

    ("拒絕名單退回 last-one-wins(後面一行空值清掉前面的 hard deny)",
     "金鑰政策.py",
     "        if k == DENY_ENV:",
     "        if False:",
     "tests/test_key_policy.py::test_env裡重複的拒絕名單要聯集不可被空值蓋掉"),

    # ── Codex 第十三輪:和聲柱假陽性、產線隔離、柱值裁判、脫離程序 ────────
    ("分軌線只驗 demucs 不驗 librosa(缺 librosa 時和聲柱整根降級卻報九柱齊全)",
     "評審團.py",
     'DEMUCS_LINE_MODS = ("demucs", "librosa", "numpy", "soundfile")',
     'DEMUCS_LINE_MODS = ("demucs",)',
     "tests/test_demucs_resolve.py::test_整條線的模組清單要含librosa"),

    ("requirements-demucs 又漏 librosa(全新安裝的和聲柱直接死)",
     "requirements-demucs.txt",
     'librosa==0.11.0',
     '# 這一行被拿掉了(變異)',
     "tests/test_demucs_resolve.py::test_安裝腳本自檢要驗整條線而不是只驗demucs"),

    # ⚠ 這個 bug 的根因已被架構消掉:預篩看錯 layout 時,第二輪「所有候選都真 import」
    #   會把答案救回來(只是慢一點)。所以變異要**兩道一起拔**,才是 R13 當時的行為:
    #   預篩用錯 layout + 沒有救援 → Windows 上專案 venv 永遠選不到,改用全域 conda。
    ("venv 預篩只看一種 layout 且無救援(專案 venv 永遠選不到,改用全域 conda)",
     "評審團.py",
     '        for root in (py.parent.parent, py.parent):\n'
     '            if any(next(root.glob(p), None) is not None for p in pats):\n'
     '                return True\n'
     '        return False',
     '        for root in (py.parent,):        # 變異:只看一種 layout\n'
     '            if any(next(root.glob(p), None) is not None for p in pats):\n'
     '                return True\n'
     '        return False\n'
     '    globals()["_SJ_NO_RESCUE"] = True    # 變異:同時拔掉第二輪救援',
     "tests/test_demucs_resolve.py::test_專案venv要贏過全域conda",
     "win32"),   # ⚠ Windows 專屬:POSIX 的 py.parent.parent 本來就對,這個 bug 不存在

    ("process env 的一般金鑰又被借走(拿別條產線的付費額度)",
     "金鑰政策.py",
     '    for name in GENERIC_ENVS:\n        if os.environ.get(name) and not os.environ.get(PRIMARY_ENV):',
     '    for name in GENERIC_ENVS:\n        if os.environ.get(name):\n            raw = raw or [(os.environ[name], "環境變數")]\n        if False:',
     "tests/test_key_policy.py::test_process環境的一般金鑰不被借用"),

    ("拒絕名單失效(明知不可用的金鑰照樣拿去打)",
     "金鑰政策.py",
     '        if key_fingerprint(k) in denied:',
     '        if False:',
     "tests/test_key_policy.py::test_拒絕名單用完整SHA256硬擋"),

    (".env 鍵名不 strip(`KEYS = A` 驗證器讀不到、執行期讀得到)",
     "金鑰政策.py",
     '        k = k.strip()\n        v = v.strip()',
     '        v = v.strip()',
     "tests/test_key_policy.py::test_等號兩邊有空白也讀得到"),

    ("多把與單把相加(沒被驗過的金鑰偷渡進真正的呼叫)",
     "金鑰政策.py",
     '                raw = [(k.strip(), f".env {name}") for k in val.split(",")]\n                break',
     '                raw = raw + [(k.strip(), f".env {name}") for k in val.split(",")]',
     "tests/test_key_policy.py::test_多把存在時不可把單把也追加進來"),

    ("驗證報告只驗柱名存在(柱值 None/{}/NaN/true/999 全部 PASS)",
     "驗證報告.py",
     '        s = det.get("score")\n        if isinstance(s, bool) or not isinstance(s, (int, float)):',
     '        s = det.get("score")\n        if False:',
     "tests/test_keyprobe_and_verify.py::test_柱值畸形也要被打回"),

    ("報告解析吃 NaN/Infinity(非標準 JSON 混進來)",
     "驗證報告.py",
     '        d = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_const)',
     '        d = json.loads(path.read_text(encoding="utf-8"))',
     "tests/test_keyprobe_and_verify.py::test_非標準JSON常數要被拒收"),

    ("Windows 退回 taskkill(自行 DETACHED 的孫程序逃掉繼續吃 GPU)",
     "子程序.py",
     '            job = _WinJob()',
     '            raise RuntimeError("變異:不建 Job Object")',
     "tests/test_run_tree.py::test_Windows下脫離又被孤兒化的後代也要被殺掉"),

    # ── Codex 第十二輪:程序樹、逐把驗金鑰、獨立 JSON 裁判、目錄防線 ──────
    # ⚠ 殺樹有兩道:主動 kill_tree + job.close() 的 KILL_ON_JOB_CLOSE 兜底。
    #   只拔一道會被另一道救回(跟 R12 的 symlink 同型),要兩道一起拔才是真 fail-open。
    ("逾時只殺直屬子程序(Demucs/torch 孫程序活著繼續吃 GPU)",
     "子程序.py",
     # ⚠ 同上:逾時路徑的 kill_tree 與最外層 finally 的 job.close() 是兩道,
     #   兩道一起拔才是真的「只殺直屬」。
     '            kill_tree(p, job)\n'
     '            try:\n'
     '                out, err = p.communicate(timeout=10)   # 回收,不留殭屍\n'
     '            except Exception:\n'
     '                out, err = "", ""\n'
     '            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)',
     '            p.kill()   # 變異:只殺直屬\n'
     '            try:\n'
     '                out, err = p.communicate(timeout=10)\n'
     '            except Exception:\n'
     '                out, err = "", ""\n'
     '            if job is not None:\n'
     '                job.handle = None      # 變異:讓 finally 的 job.close() 失效\n'
     '            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)',
     "tests/test_run_tree.py::test_逾時要殺整棵樹_孫程序不可存活寫檔"),

    ("金鑰驗證退回只驗第一把(第一好第二壞=假陽性)",
     "金鑰驗證.py",
     '    for i, k in enumerate(keys, 1):',
     '    for i, k in enumerate(keys[:1], 1):',
     "tests/test_keyprobe_and_verify.py::test_第一把好第二把壞_要逐把驗且誠實列出"),

    ("429 被洗成 verified(全部限流照樣綠燈)",
     "金鑰驗證.py",
     '        if e.code == 429:\n            return "cooling", e.code',
     '        if e.code == 429:\n            return "verified", e.code',
     "tests/test_keyprobe_and_verify.py::test_真網路分類器_HTTPError對照"),

    ("網路/TLS 錯誤被洗成 verified(斷網也給綠燈)",
     "金鑰驗證.py",
     '    except Exception:\n        return "unknown", None       # DNS/TLS/逾時 —— 不是金鑰的錯,但也沒驗成',
     '    except Exception:\n        return "verified", None',
     "tests/test_keyprobe_and_verify.py::test_真網路分類器_HTTPError對照"),

    ("驗證報告裁判放水:不驗完整評測(stub 寫 {} 也算完整)",
     "驗證報告.py",
     '    if pt.get("完整評測") is not True:',
     '    if False:',
     "tests/test_keyprobe_and_verify.py::test_各種殘缺都要被打回"),

    ("驗證報告不驗新舊(舊報告冒充本輪 VerifyModels 證據)",
     "驗證報告.py",
     '    if newer_than is not None and path.stat().st_mtime <= newer_than:',
     '    if False:',
     "tests/test_keyprobe_and_verify.py::test_舊檔不可冒充本輪新產物"),

    ("SKILL 又不看退出碼(exit 2 的報告被丟掉或當完整交付)",
     "SKILL.md",
     '$juryRc = $LASTEXITCODE   # ⛔ 立刻保存 —— 退出碼是完整性契約,不看等於裝瞎',
     '# 變異:不看退出碼',
     "tests/test_packaging.py::test_SKILL有實作退出碼契約"),

    ("鎖檔 hardlink 防線拆掉(鎖寫入可覆寫任意可硬連結檔案)",
     "狀態目錄.py",
     '        if st.st_nlink != 1:',
     '        if False:',
     "tests/test_state_dir.py::test_鎖檔hardlink要拒絕且不碰目標"),

    # ⚠️ 下面三條的測試是 POSIX 專屬(Windows 本機跑會誠實顯示「無法驗證」,CI ubuntu 會抓)
    # ⚠ symlink 檢查有前後兩道(mkdir 前+mkdir 後),只拔一道會被另一道救回 →
    #   變異必須「兩道一起拔」才是真的 fail-open(CI ubuntu 變異工作抓出來的)
    ("狀態/鎖目錄的 symlink 防線拆掉(鎖被導向外部目錄)",
     "狀態目錄.py",
     '    if d.is_symlink():\n'
     '        raise StateDirError(f"{what} 是符號連結,拒絕使用:{d} —— 鎖/狀態可被導向外部目錄")\n'
     '    try:\n'
     '        d.mkdir(parents=True, exist_ok=True, mode=0o700)   # mode 只作用在最後一層\n'
     '    except FileExistsError:\n'
     '        raise StateDirError(f"{what} 的位置被一個普通檔案佔住:{d} —— 請移走那個檔案")\n'
     '    except OSError as e:\n'
     '        raise StateDirError(f"{what} 建不起來:{d}({type(e).__name__}: {e})")\n'
     '    if d.is_symlink() or not d.is_dir():',
     '    try:\n'
     '        d.mkdir(parents=True, exist_ok=True, mode=0o700)   # mode 只作用在最後一層\n'
     '    except FileExistsError:\n'
     '        raise StateDirError(f"{what} 的位置被一個普通檔案佔住:{d} —— 請移走那個檔案")\n'
     '    except OSError as e:\n'
     '        raise StateDirError(f"{what} 建不起來:{d}({type(e).__name__}: {e})")\n'
     '    if not d.is_dir():',
     "tests/test_state_dir.py::test_locks目錄本身是symlink要拒絕"),

    ("目錄建立退回預設權限(mkdir→chmod 之間出現 0777 窗口)",
     "狀態目錄.py",
     '        d.mkdir(parents=True, exist_ok=True, mode=0o700)   # mode 只作用在最後一層',
     '        d.mkdir(parents=True, exist_ok=True)',
     "tests/test_state_dir.py::test_umask全開時建立當下就是0700沒有窗口"),

    # ── Codex 第十一輪:隔離競態、狀態上限、修復寫入、退出碼契約、安裝驗證 ──
    ("隔離退回無鎖 rename(寫入者剛發布的新狀態被搬去 .corrupt)",
     "Gemini曲評.py",
     '    try:\n        with _state_lock(timeout=5.0) as acquired:\n            if acquired:\n                _read_state_locked()   # 內含「重讀→還是壞的才隔離」;好檔原樣保留\n    except Exception:\n        pass',
     '    try:\n        bad = STATE_FILE.with_name(f"{STATE_FILE.name}.corrupt.{uuid.uuid4().hex[:8]}")\n        STATE_FILE.rename(bad)\n    except Exception:\n        pass',
     "tests/test_state_and_cooldown.py::test_隔離前要鎖內重讀_新狀態不可被搬走"),

    ("狀態檔大小上限拆掉(16MiB 垃圾檔整份讀進記憶體)",
     "Gemini曲評.py",
     '    if size > MAX_STATE_BYTES:\n        _quarantine_locked(f"檔案 {size} bytes 超過上限 {MAX_STATE_BYTES}")\n        return {}',
     '    if size > MAX_STATE_BYTES and False:\n        _quarantine_locked(f"檔案 {size} bytes 超過上限 {MAX_STATE_BYTES}")\n        return {}',
     "tests/test_state_and_cooldown.py::test_狀態檔超過大小上限要隔離不吃記憶體"),

    ("merge 對畸形舊 record 直接 float(寫入端永遠修不好壞資料)",
     "Gemini曲評.py",
     '            _o = old.get("cooldown_until")\n            _n = float(record.get("cooldown_until", 0) or 0)\n            if (isinstance(_o, (int, float)) and not isinstance(_o, bool)\n                    and math.isfinite(_o) and _o > _n):\n                return                 # 磁碟上那筆比較晚到期 → 保留它',
     '            _n = float(record.get("cooldown_until", 0) or 0)\n            _o = float(old.get("cooldown_until", 0) or 0)\n            if _o > _n:\n                return',
     "tests/test_state_and_cooldown.py::test_merge對畸形舊record要直接取代不可炸"),

    ("相對的 SONG_JURY_STATE_DIR 被放行(互斥域隨 cwd 分裂)",
     "狀態目錄.py",
     '        if not d.is_absolute():',
     '        if False:',
     "tests/test_state_dir.py::test_相對override要被拒絕"),

    ("狀態目錄錯誤退回原始 traceback(FileExistsError 噴在保護層外)",
     "狀態目錄.py",
     '    except FileExistsError:\n        raise StateDirError(f"{what} 的位置被一個普通檔案佔住:{d} —— 請移走那個檔案")',
     '    except FileExistsError:\n        raise',
     "tests/test_state_dir.py::test_override指到普通檔案要講人話不可原始traceback"),

    ("不完整評測照樣 exit 0(外部自動化把無效分數當成功)",
     "評審團.py",
     '    pt = merged.get("pillar_totals")\n    if isinstance(pt, dict) and pt.get("完整評測") is True:\n        return 0\n    return 2',
     '    pt = merged.get("pillar_totals")\n    return 0            # 變異:一律成功',
     "tests/test_pillars.py::test_退出碼要跟評測完整性一致"),

    ("批次把 exit 2 當一般失敗(昂貴的不完整報告被丟掉)",
     "批次評測.py",
     '    if r.returncode not in (0, 2):',
     '    if r.returncode != 0:',
     "tests/test_batch_and_windows.py::test_退出碼2的缺柱報告要讀進來不可當成程式炸掉"),

    ("安裝器又內嵌金鑰探針(繞過 金鑰驗證.py 的逐把/三態契約)",
     "install.ps1",
     '$script:KeyUnverified = $false',
     '$script:KeyUnverified = $false   # generativelanguage 內嵌探針(變異)',
     "tests/test_packaging.py::test_安裝腳本真的驗金鑰有效性且有完整驗證開關"),

    ("安裝步數又少算(完整安裝印 [10/9])",
     "install.ps1",
     '$TOTAL = if ($CheckOnly) { 1 } elseif ($SkipML) { 5 } else { 10 }',
     '$TOTAL = if ($CheckOnly) { 1 } elseif ($SkipML) { 4 } else { 9 }',
     "tests/test_packaging.py::test_安裝步數要跟實際步驟一致"),

    # ── Codex 第十輪:鎖跨副本、狀態 schema、冷卻持久化、顯示層防炸 ──────
    ("鎖位置退回 BASE(兩份 ZIP 副本各鎖各的,互斥只在單一副本內成立)",
     "評審團.py",
     '    d = locks_dir()',
     '    d = BASE / "_locks"; d.mkdir(exist_ok=True)',
     "tests/test_lock_and_gate.py::test_鎖的位置跟工具副本無關"),

    ("狀態檔頂層不驗型別(合法 JSON 的 [] 讓 Gemini 整關炸掉)",
     "Gemini曲評.py",
     '    if not isinstance(raw, dict):\n        _quarantine_locked(f"頂層是 {type(raw).__name__},應為 dict")\n        return {}',
     '    if not isinstance(raw, dict):\n        return raw             # 變異:合法 JSON 就直接交出去',
     "tests/test_state_and_cooldown.py::test_狀態檔頂層不是dict要隔離成corrupt不可炸"),

    ("狀態檔單筆 record 不驗(cooldown_until 是字串時 is_cooling 炸)",
     "Gemini曲評.py",
     '        cu = rec.get("cooldown_until", 0)\n        if isinstance(cu, bool) or not isinstance(cu, (int, float)) or not math.isfinite(cu):\n            continue',
     '        cu = rec.get("cooldown_until", 0)',
     "tests/test_state_and_cooldown.py::test_狀態檔單筆壞只丟單筆不整檔陪葬"),

    ("冷卻寫入失敗仍宣稱成功(其他工作立刻再轟已限流的 key)",
     "Gemini曲評.py",
     '    persisted = merge_cooldown(fp, rec)',
     '    persisted = merge_cooldown(fp, rec) or True',
     "tests/test_state_and_cooldown.py::test_冷卻寫入失敗不可宣稱已冷卻"),

    ("429 現場吞掉持久化失敗(JSON 裡乾乾淨淨,像已冷卻)",
     "Gemini曲評.py",
     '                    if not cool_down(state, key, max(delay, COOLDOWN_RATE_SEC), "429 額度/頻率上限"):',
     '                    if cool_down(state, key, max(delay, COOLDOWN_RATE_SEC), "429 額度/頻率上限") and False:',
     "tests/test_state_and_cooldown.py::test_429冷卻寫入失敗要留cooldown_persist_error"),

    ("諺文只認預組合音節(NFD 分解式韓文零警告變 □)",
     "報告轉PDF.py",
     '    return any(_is_hangul(c) for c in (text or ""))',
     '    return any("\\uac00" <= c <= "\\ud7a3" for c in (text or ""))',
     "tests/test_pdf_render.py::test_NFD分解式韓文也要觸發韓文字型"),

    ("圖片只限寬不限高(1×10000 畸形圖毀掉整份 PDF)",
     "報告轉PDF.py",
     '    scale = min(maxw / iw, maxh / ih)',
     '    scale = maxw / iw',
     "tests/test_pdf_render.py::test_圖片要同時限寬限高等比縮放"),

    ("網頁版成績表假定巢狀都是 dict(scores: [] → TypeError 拒收評測)",
     "app.py",
     '    def _d(v):\n        return v if isinstance(v, dict) else {}',
     '    def _d(v):\n        return v',
     "tests/test_rubric_pick.py::test_成績表對畸形巢狀容器不可炸"),

    ("PS5.1 沒有的有限性方法又被用回去(完整安裝永遠 exit 1)",
     "install.ps1",
     '-not [double]::IsNaN([double]$tot) -and -not [double]::IsInfinity([double]$tot)',
     '[double]::IsFinite([double]$tot)',
     "tests/test_packaging.py::test_安裝腳本不可用PS51沒有的API或會寫BOM的寫法"),

    ("安裝 log 退回固定共用檔(並行安裝互相 truncate)",
     "install.sh",
     'SJ_STEP_LOG="$(mktemp "${TMPDIR:-/tmp}/sj_step.XXXXXX" 2>/dev/null)" || SJ_STEP_LOG="${TMPDIR:-/tmp}/sj_step_$$.log"',
     'SJ_STEP_LOG="/tmp/_sj_step.log"',
     "tests/test_packaging.py::test_install_sh不可用固定tmp檔且要容忍BOM"),

    ("讀 .env 不吃 BOM(PS5.1 寫的 .env 被判成沒金鑰)",
     "金鑰政策.py",
     '        text = p.read_text(encoding="utf-8-sig")',
     '        text = p.read_text(encoding="utf-8")',
     "tests/test_keyprobe_and_verify.py::test_BOM開頭的env也讀得到金鑰"),

    ("ffmpeg 從退出碼移除(缺它仍 exit 0=假完整)",
     "install.ps1",
     '$failed = ($script:Problems.Count -gt 0) -or ($lost -gt 0) -or (-not $script:SmokeOk) -or (-not $hasFfmpeg)',
     '$failed = ($script:Problems.Count -gt 0) -or ($lost -gt 0) -or (-not $script:SmokeOk)',
     "tests/test_packaging.py::test_安裝腳本把ffmpeg當完整安裝必要件"),

    # ── Codex 第九輪:鎖 fail-closed、bool 最後一條小路、酬載順序 ────────
    ("工作鎖壞掉照樣放行(fail-open 取消互斥保證)",
     "評審團.py",
     "            sys.exit(f\"⛔ 工作鎖在此檔案系統不可用(errno={e.errno})。\\n\"\n"
     "                     f\"   鎖檔位置:{lockf}\\n\"\n"
     "                     f\"   → 請把本工具移到支援檔案鎖的本機磁碟再跑。\"\n"
     "                     f\"(不放行:沒有互斥就評,兩個工作的中間檔會互相覆寫,分數會錯得無聲無息)\")",
     "            pass  # 變異:鎖壞掉警告都不給,照樣放行",
     "tests/test_lock_and_gate.py::test_工作鎖壞掉要硬擋不可裝沒事"),

    ("租約壞掉呼叫端照樣打 API(同 key 在途回到 2)",
     "Gemini曲評.py",
     "            if lease_status != \"ok\":",
     "            if lease_status == \"busy\":",
     "tests/test_lock_and_gate.py::test_鎖壞掉整條鏈fail_closed一次都不打"),

    ("0 呼叫的原因一律推給冷卻(租約鎖問題被說成額度問題)",
     "Gemini曲評.py",
     "            if results and results <= {\"busy_inflight\", \"lease_error\"}:",
     "            if False:",
     "tests/test_lock_and_gate.py::test_鎖壞掉整條鏈fail_closed一次都不打"),

    ("Gemini 總分 bool 在縮放層被洗成 10 分(True*10==10)",
     "評審團.py",
     "        n = _num_or_none(v)\n        return n * k if n is not None else v",
     "        return v * k  # 變異:原值直接乘",
     "tests/test_pillars.py::test_Gemini總分是bool時不可以被洗成10分"),

    ("留言欄位非字串直接透傳(dims 摘要 .replace 炸掉)",
     "評審團.py",
     "    return v if isinstance(v, str) else \"\"",
     "    return v",
     "tests/test_lock_and_gate.py::test_留言欄位不是字串時要當空字串"),

    ("超限判斷回到 base64 之後(500MB 檔先吃 1.2GB 記憶體才轉檔)",
     "Gemini曲評.py",
     "        if est_b64_mb > MAX_INLINE_B64_MB:",
     "        if False:",
     "tests/test_gemini_payload.py::test_超大檔要先轉檔再讀不可先整檔base64"),

    # ── Codex 第六輪 ──────────────────────────────────────────────────
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
     '    return _locked_update(lambda cur: cur.pop(fp, None))',
     '    return True            # 變異:什麼都不刪',
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
     'if r.returncode not in (0, 2):\n        return None, f"評審團 結束碼 {r.returncode}:" + (r.stderr or r.stdout or "")[-260:]',
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
    ("四語範例歌曲其中一首沒進 repo(開源門面缺一角)",
     "examples/中文範例-貓步友情進行式.mp3",
     "tests/test_packaging.py::test_四語範例歌曲成對且語言對得上"),
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
    for i, item in enumerate(MUTATIONS, 1):
        # 第 6 個元素(可選)= 這條變異只在哪個平台成立。
        # ⛔ 有些 bug 是平台專屬的(例:Windows venv 的 site-packages layout),
        #    在別的平台上「沒抓到」不是測試爛,是那個 bug 在那裡根本不存在。
        #    硬算進 bad 會逼人去修一個假問題(CI ubuntu 實際踩到)。
        desc, fname, old, new, target = item[:5]
        only = item[5] if len(item) > 5 else None
        if only and sys.platform != only:
            print(f"\n[{i}/{len(MUTATIONS)}] ⏭ 無法驗證:{desc}")
            print(f"        → 這條是 {only} 專屬的缺陷,本平台({sys.platform})不成立")
            skipped.append(("platform", desc))
            continue
        p = REPO / fname
        # ⛔ 一定要用二進位讀寫:read_text/write_text 在 Windows 會做換行轉換,
        #    「還原」時會把 LF 檔案寫成 CRLF,把原始碼弄髒(自己踩過)。
        raw = p.read_bytes()
        src = raw.decode("utf-8")
        # ⛔ 比對前要把換行正規化(Codex R15 抓到的假驗證):
        #    .gitattributes 規定 *.ps1 / *.bat 是 CRLF,所以**任何 clone 拿到的
        #    工作區都是 CRLF**;而變異 pattern 寫在 .py 裡一律是 LF。
        #    我本機因為都用編輯器寫檔、還沒被 git 轉過,工作區是 LF —— 於是
        #    「我這台抓到、別人那台找不到字串」,我宣稱的通過率對別人不成立。
        #    → 比對/替換都在正規化後的文字上做,寫回時換回原檔的換行,
        #      還原一律用原始 bytes(逐位元)。
        crlf = "\r\n" in src
        norm = src.replace("\r\n", "\n") if crlf else src
        if old not in norm:
            print(f"\n[{i}/{len(MUTATIONS)}] ⚠ 跳過:在 {fname} 找不到要變異的字串")
            print(f"        ({desc})  ← 程式改過了?請更新這條變異")
            bad.append(desc)
            continue
        mutated = norm.replace(old, new, 1)
        if crlf:
            mutated = mutated.replace("\n", "\r\n")
        p.write_bytes(mutated.encode("utf-8"))
        try:
            failed, ran = run_pytest(target)
        finally:
            p.write_bytes(raw)                        # 一定要逐位元還原
        if failed:
            print(f"\n[{i}/{len(MUTATIONS)}] ✅ 抓到了:{desc}")
        elif not ran:
            print(f"\n[{i}/{len(MUTATIONS)}] ⏭ 無法驗證:{desc}")
            print(f"        → {target} 在這個平台被 skip,這次沒驗到(不是通過)")
            skipped.append(("platform", desc))
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
            skipped.append(("zip", desc))
            continue
        try:
            failed, ran = run_pytest(target)
        finally:
            subprocess.run(["git", "add", "--", fname], cwd=REPO, capture_output=True)
        if failed:
            print(f"\n[{j}/{n0 + len(GIT_MUTATIONS)}] ✅ 抓到了:{desc}")
        elif not ran:
            print(f"\n[{j}/{n0 + len(GIT_MUTATIONS)}] ⏭ 無法驗證:{desc}(被 skip,不是通過)")
            skipped.append(("platform", desc))
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
        # ⛔ 跳過的原因不同,能做的事也不同 —— 混成一句「請在 git clone 跑」會誤導:
        #    在精確 clone 的 Windows 上跳過的是**平台**限制,重跑一百次也一樣(Codex R13)。
        print(f"  ⚠️ {total - len(skipped)}/{total} 條抓到;另有 {len(skipped)} 條在這個環境無法驗證:")
        for why, s_ in skipped:
            print(f"     ⏭ [{why}] {s_}")
        by = {}
        for why, s_ in skipped:
            by.setdefault(why, []).append(s_)
        if "zip" in by:
            print(f"     · zip({len(by['zip'])} 條):這個目錄沒有 .git(ZIP 版)——"
                  f"要驗打包自足性請改用 git clone。")
        if "platform" in by:
            print(f"     · platform({len(by['platform'])} 條):這幾條要 POSIX 語意"
                  f"(symlink/權限位元),Windows 上驗不了 —— 到 WSL 跑,"
                  f"或看 CI 的 ubuntu 變異工作(那裡每次都會驗)。")
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
