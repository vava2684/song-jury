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

    # ── Codex 第十六輪:比較器身分、並列傳遞性、契約 strict、批次污染、入口退出碼 ──
    ("比較器用顯示名當鍵(不同資料夾的同名報告互相覆蓋)",
     "比較.py",
     '        "report_id": str(path.resolve()),',
     '        "report_id": path.stem,   # 變異:用會撞名的顯示名',
     "tests/test_compare.py::test_不同資料夾的同名報告不可互相覆蓋"),

    ("同一份報告可以重複上場(A 對 A 被當成合法 PK)",
     "比較.py",
     '    _reject_duplicates(paths)\n    items = [load_report(p) for p in paths]',
     '    items = [load_report(p) for p in paths]',
     "tests/test_compare.py::test_同一份報告不可以重複上場"),

    ("並列退回相鄰比較(三首以上鏈式擴張成全部第一名)",
     "比較.py",
     '        tie = head is not None and (head - it["composite"]) < TIE_THRESHOLD',
     '        tie = i > 0 and (ordered[i - 1]["composite"] - it["composite"]) < TIE_THRESHOLD',
     "tests/test_compare.py::test_三首以上的並列不可以鏈式擴張"),

    ("冠軍退回單一 max(同分被偽裝成唯一冠軍)",
     "比較.py",
     '    best = max(key(i) for i in items)\n    names = [i["song"] for i in items if key(i) == best]',
     '    best = max(key(i) for i in items)\n    names = [max(items, key=key)["song"]]',
     "tests/test_compare.py::test_柱冠軍與最佳take同分時要全部列出"),

    ("比較器驗完再讀第二次(TOCTOU:排名用到沒驗過的內容)",
     "比較.py",
     '    d = json.loads(raw.decode("utf-8"))',
     '    d = json.loads(path.read_text(encoding="utf-8"))   # 變異:第二次讀檔',
     "tests/test_compare.py::test_驗過的內容就是排名用的內容_TOCTOU"),

    ("比較器不要求契約(舊格式混進來,尺是用猜的)",
     "比較.py",
     '    why = validate_data(raw, path.name, require_contract=True)',
     '    why = validate_data(raw, path.name)',
     "tests/test_compare.py::test_舊格式報告不可以進比較"),

    ("安裝證據接受缺契約的報告(產出端迴歸也照樣 VERIFY_OK)",
     "驗證報告.py",
     '        if require_contract:',
     '        if False:',
     "tests/test_installer_order.py::test_jury回0但報告缺契約要被裁判擋下"),

    ("批次 full 不過獨立裁判(半殘 JSON 冒充九柱完整)",
     "批次評測.py",
     "validate(out_json, require_contract=True, require_identity=\"declared\")",
     'None if False else ""',
     "tests/test_batch_and_windows.py::test_full模式要過獨立裁判"),

    ("進度檔不驗批次契約(--skip-existing 跨契約偷用舊結果)",
     "批次評測.py",
     '    got = d["batch_contract"]\n    if got != want:',
     '    got = d["batch_contract"]\n    if False:',
     "tests/test_batch_and_windows.py::test_不同批次契約的進度檔不可續跑"),

    ("鑑別力分析吃舊格式 store(兩把尺混一張表)",
     "曲評測清單.py",
     '    if not isinstance(d, dict) or "batch_contract" not in d or "results" not in d:',
     '    if False:',
     "tests/test_batch_and_windows.py::test_下游分析拒絕沒有契約的store"),

    ("一鍵安裝.bat 又被 pause 洗掉退出碼",
     "一鍵安裝.bat",
     'set "rc=%errorlevel%"',
     'set "rc=0"   REM 變異:不保存 child 的退出碼',
     "tests/test_packaging.py::test_一鍵安裝bat要保住子程序退出碼"),

    ("install.ps1 又靜默忽略未知參數(拼錯 -VerifyModels 拿到假綠燈)",
     "install.ps1",
     'if ($Unknown) {',
     'if ($false) {',
     "tests/test_packaging.py::test_安裝器要擋下未知參數_真的跑一次"),

    ("金鑰政策錯誤被洗成一般失敗(自動化分不出安全問題)",
     "install.ps1",
     'if ($script:PolicyError) {',
     'if ($false) {',
     "tests/test_packaging.py::test_安裝器要原樣傳出政策錯誤碼5"),

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
     '    if lang not in LANGS:',
     '    if False:',
     "tests/test_compare.py::test_PK要指定語言"),

    ("比較器不檢查計分契約(換了尺照樣比)",
     "比較.py",
     '    if len(names) > 1:',
     '    if False:',
     "tests/test_compare.py::test_不同計分契約不可比"),

    ("比較器不過獨立裁判(不完整報告混進排名)",
     "比較.py",
     '    why = validate_data(raw, path.name, require_contract=True)',
     '    why = ""',
     "tests/test_compare.py::test_不完整的報告不可以進比較"),

    ("並列門檻拆掉(0.3 分的差距被當成真的高下)",
     "比較.py",
     '        tie = head is not None and (head - it["composite"]) < TIE_THRESHOLD',
     '        tie = False',
     "tests/test_compare.py::test_差距很小要標並列不是硬排名次"),

    ("批次退回「只收完整九柱」(預設模式每首都被拒收)",
     "批次評測.py",
     '    extra = lost - GEMINI_ONLY_PILLARS',
     '    extra = lost',
     "tests/test_batch_and_windows.py::test_預設批次收得到結果而不是每首都拒收"),

    # ⭐ R16 起這兩條的家從 install.ps1 搬進 完整驗證.py(shell 只看退出碼),
    #    也因此不再需要 win32 標記 —— 三個平台每次都驗得到。
    ("VerifyModels 拿掉外層 timeout(模型 deadlock 就永遠掛著)",
     "完整驗證.py",
     '        default_timeout = positive_finite("SONG_JURY_VERIFY_TIMEOUT", 7200.0,\n'
     "                                          lo=0.0, hi=86400.0)",
     "        default_timeout = 7200.0   # 變異:逾時寫死、外部關不掉",
     "tests/test_installer_order.py::test_逾時要回124且殺乾淨"),

    # ── Codex 第十四輪:驗證順序、失敗路徑殺樹、裁判自洽、政策 fail-closed ──
    # ⚠ 要注入的是**順序錯誤**(清理跑在裁判之前),不是「不叫裁判」——
    #   後者是另一種缺陷,描述與注入不一致就等於沒驗到那個 bug(Codex R15)。
    ("VerifyModels 先清理才叫裁判(成功路徑必定假陰性)",
     "完整驗證.py",
     '                #    (正式批次才接受「產出端明講的降級」—— 見 批次評測.py)',
     '                _cleanup(vid)   # 變異:清理跑到裁判前面',
     "tests/test_installer_order.py::test_成功路徑_裁判看得到報告且收工後全清乾淨"),

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
     '        d = json.loads(raw.decode("utf-8"), parse_constant=_reject_const)',
     '        d = json.loads(raw.decode("utf-8"))',
     "tests/test_keyprobe_and_verify.py::test_非標準JSON常數要被拒收"),

    ("Windows 退回 taskkill(自行 DETACHED 的孫程序逃掉繼續吃 GPU)",
     "子程序.py",
     '            job = _WinJob()',
     '            raise RuntimeError("變異:不建 Job Object")',
     "tests/test_run_tree.py::test_Windows下脫離又被孤兒化的後代也要被殺掉",
     "win32"),   # ⚠ 標平台是為了讓 Windows CI 的 --only-platform 抓得到它

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
     "    if r.returncode not in (0, 2, 4):",
     "    if r.returncode != 0:",
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
     'if r.returncode not in (0, 2, 4):\n'
     '        return None, f"評審團 結束碼 {r.returncode}:" + (r.stderr or r.stdout or "")[-260:]',
     "if False:\n        pass",
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
    # ── 2026-08-02 自己實跑 -VerifyModels 抓到的:自我檢查說「和聲缺項」exit 1,
    #    同一次執行的九柱實跑卻用同一條線拿到 VERIFY_OK ──────────────────
    ("分軌線體檢不重試(剛裝完的暫時性失敗被當成永久結論)",
     "分軌線檢查.py",
     "def probe(py, mods=DEMUCS_LINE_MODS, attempts=2, budget=None,",
     "def probe(py, mods=DEMUCS_LINE_MODS, attempts=1, budget=None,",
     "tests/test_demucs_resolve.py::test_暫時性失敗要再給一次機會"),

    ("分軌線缺套件時不說是缺哪一個(只留一句『不能用』)",
     "分軌線檢查.py",
     '        return MISSING, m.group(1), f"缺模組 {m.group(1)}(退出碼 {rc})"',
     '        return MISSING, m.group(1), ""   # 變異:原因被吞掉',
     "tests/test_demucs_resolve.py::test_失敗一定要講出真正的原因"),

    ("分軌線其他壞法不說原因(DLL/權限的錯誤訊息被吞掉)",
     "分軌線檢查.py",
     '    tail = ((out or "").strip().splitlines() or ["(沒有輸出)"])[-1]\n'
     '    return IMPORT, None, f"退出碼 {rc}:{tail[:300]}"',
     '    return IMPORT, None, ""   # 變異:原因被吞掉',
     "tests/test_demucs_resolve.py::test_非缺套件的錯不可以被說成缺套件"),

    ("分軌線壞掉時不分「缺套件」與「其他壞法」(安裝器給不出對的建議)",
     "分軌線檢查.py",
     "    rc = 1 if res.kind == MISSING else 2",
     "    rc = 1",
     "tests/test_demucs_resolve.py::test_非缺套件的錯不可以被說成缺套件"),

    # ── Codex 第十七輪:重試假綠、退出碼分裂、複製灌票、git 假 skip、清理謊報 ──
    ("救回來的分軌線不留證據(間歇性不穩被洗成完整綠燈)",
     "分軌線檢查.py",
     "                return LineResult(True, OK, \"\", None, first_error, i)",
     "                return LineResult(True, OK, \"\", None, \"\", i)   # 變異:證據抹掉",
     "tests/test_demucs_resolve.py::test_救回來的要留下證據不可以當沒事發生"),

    ("缺套件也盲目重試(白等一次冷啟動,錯誤還是一樣)",
     "分軌線檢查.py",
     "RETRIABLE = (LAUNCH, IMPORT)",
     "RETRIABLE = (LAUNCH, IMPORT, MISSING)",
     "tests/test_demucs_resolve.py::test_缺套件是確定性的不可以重試"),

    ("逾時每次都重新給整份預算(最壞 30 分鐘沒有輸出)",
     "分軌線檢查.py",
     "                               errors=\"replace\", timeout=left)",
     "                               errors=\"replace\", timeout=budget)",
     "tests/test_demucs_resolve.py::test_逾時不可以乘成好幾份預算"),

    ("分軌線體檢不報進度(等好幾分鐘跟當機看起來一樣)",
     "分軌線檢查.py",
     '        log(f"      分軌線體檢 {i}/{attempts}(整段上限 {budget:.0f}s,剩 {left:.0f}s)…")',
     '        pass   # 變異:不報進度',
     "tests/test_demucs_resolve.py::test_每次嘗試都要先報進度"),

    ("PowerShell 把使用者中斷(130)洗成一般失敗(自動化分不出取消與裝壞)",
     "install.ps1",
     '            130 { Bad "完整驗證被使用者中斷(Ctrl+C)" "已中止並清理;要驗請重跑"\n'
     '                  Write-Host "  (退出碼 130:使用者中斷)" -ForegroundColor DarkGray\n'
     '                  exit 130 }',
     '            130 { Bad "完整驗證被使用者中斷(Ctrl+C)" "已中止並清理;要驗請重跑"\n'
     '                  $script:VerifyOk = $false }',
     "tests/test_installer_order.py::test_ps1把helper的退出碼照契約傳出",
     "win32"),

    ("比較器不擋「複製改名」(同一次評測變兩票)",
     "比較.py",
     "    _reject_same_source(items)      # ⛔ 複製改名不算另一首(R17-3)",
     "    pass",
     "tests/test_compare.py::test_同一份報告複製改名不可以當成兩首"),

    ("產出端不寫來源身分(比較器的防線失去依據)",
     "評審團.py",
     '    fh = _file_sha256(song)',
     '    fh = ""   # 變異:不寫檔案身分',
     "tests/test_compare.py::test_產出端真的會寫來源身分"),

    ("拒絕理由退回沒有機器碼(測試只能綁中文文案)",
     "比較.py",
     '                    "duplicate_input", {"path": str(rp)})',
     '                    )',
     "tests/test_compare.py::test_同一份報告不可以重複上場"),

    ("清理失敗被吞掉(仍然宣稱已清理)",
     "完整驗證.py",
     "    return [str(p) for p in _targets()]",
     "    return []   # 變異:一律宣稱乾淨",
     "tests/test_installer_order.py::test_清不掉的檔案要被回報而不是靜靜留著"),

    ("九柱都過但清不乾淨仍給綠燈(零殘留的宣稱做不到卻不說)",
     "完整驗證.py",
     '            if rc == 0:\n'
     '                print("VERIFY_BAD 九柱與格式都過,但驗證產物沒清乾淨(見上面清單)")\n'
     '                rc = 1',
     '            pass   # 變異:殘留不影響結論',
     "tests/test_installer_order.py::test_九柱都過但清不乾淨要降級成失敗"),

    ("git 故障被冒充成 ZIP skip(打包變異在 clone 裡靜靜關掉)",
     "tests/變異驗證.py",
     '        except GitFailure as e:\n'
     '            print(f"\\n[{j}/{n0 + len(git_items)}] ❌ Git 故障,這條驗不了也不能當沒事:{desc}")\n'
     '            print(f"        → {e}")\n'
     '            bad.append(desc + "(git 故障,打包變異沒驗到)")\n'
     '            continue',
     '        except GitFailure:\n'
     '            skipped.append(("zip", desc))\n'
     '            continue',
     "tests/test_mutation_harness.py::test_index_lock故障時整支不可以還是綠的"),

    # ── Codex 第十八輪:進度被緩衝、身分沒 schema、設定 typo 被說成缺套件、
    #    整檔雜湊不等於聲音、成功標記太早發布、pause 不受預算限制 ────────────
    ("安裝器把分軌體檢的輸出整段緩衝(執行中看不到進度,像當機)",
     "install.ps1",
     "        & .venv\\Scripts\\python.exe 分軌線檢查.py --status-json $statusFile\n"
     "        $lineRc = $LASTEXITCODE",
     "        $lineOut = (& .venv\\Scripts\\python.exe 分軌線檢查.py --status-json $statusFile 2>&1 | Out-String)\n"
     "        $lineRc = $LASTEXITCODE",
     "tests/test_installer_order.py::test_ps1要即時顯示分軌體檢的進度",
     "win32"),

    ("install.sh 把分軌體檢的輸出整段緩衝(同上,POSIX 版)",
     "install.sh",
     '  PYTHONUTF8=1 .venv/bin/python 分軌線檢查.py --status-json "$_line_status" &',
     '  LINE_OUT=$(PYTHONUTF8=1 .venv/bin/python 分軌線檢查.py --status-json "$_line_status" 2>&1) &',
     "tests/test_installer_order.py::test_sh要即時顯示分軌體檢的進度"),

    ("裁判不驗來源身分的格式(list / 'x' 都被當成有效證據)",
     "驗證報告.py",
     "    why_id = identity_problem(d)\n"
     "    if why_id:\n"
     "        return why_id",
     "    pass   # 變異:身分欄位不驗格式",
     "tests/test_compare.py::test_裁判自己就要擋掉畸形身分"),

    ("比較器不再自己防守畸形身分(裁判一迴歸就變 raw TypeError)",
     "比較.py",
     "    why_id = identity_problem(d)\n"
     "    if why_id:\n"
     "        raise CompareError(f\"{path.name} 的來源身分不合法:{why_id}\",\n"
     "                           \"invalid_source_identity\", {\"path\": str(path)})",
     "    pass   # 變異:不防守",
     "tests/test_compare.py::test_畸形的身分值不可以讓比較器噴traceback"),

    ("安裝證據不要求來源身分(產出端迴歸也照樣 VERIFY_OK)",
     "完整驗證.py",
     "                why = validate(report, newer_than=started, require_contract=True,\n"
     "                                       require_identity=\"decoded\")",
     "                why = validate(report, newer_than=started, require_contract=True)",
     "tests/test_installer_order.py::test_本輪新產物沒有來源身分要被擋下"),

    ("設定值不驗有限正數(abc/nan/inf 變成未捕捉例外 → 被說成缺套件)",
     "設定讀取.py",
     "    if not math.isfinite(val):\n"
     "        raise ConfigError(f\"{name}={txt!r} 不是有限的數字(NaN/Infinity 不能當秒數)\")",
     "    pass   # 變異:NaN/Infinity 放行",
     "tests/test_設定讀取.py::test_NaN與Infinity不是秒數"),

    ("設定錯誤退回一般失敗碼(安裝器又會叫人重裝 requirements)",
     "分軌線檢查.py",
     "        return 3\n"
     "    except Exception as e:      # noqa: BLE001 —— 這支自己出事也不能被說成缺套件",
     "        return 1\n"
     "    except Exception as e:      # noqa: BLE001 —— 這支自己出事也不能被說成缺套件",
     "tests/test_installer_order.py::test_設定打錯不可以被說成缺套件"),

    ("產出端不算解碼後的聲音雜湊(換個容器就變成另一首歌)",
     "評審團.py",
     '    pcm, reason, shape = _pcm_identity(song)',
     '    pcm, reason, shape = "", "no_ffmpeg", None',
     "tests/test_compare.py::test_產出端要寫出解碼後的聲音身分"),

    ("比較器不看解碼後身分(重新封裝的同一段聲音會被當兩個來源)",
     "比較.py",
     '                       ("source_audio_pcm_sha256",\n'
     '                        "同一段聲音(解碼後在同一個格式面上完全相同)被放進來兩次"),\n',
     "",
     "tests/test_compare.py::test_換個容器的同一段聲音不可以當兩首"),

    ("成功標記在清理之前就發布(清理失敗時同時有 OK 與 BAD)",
     "完整驗證.py",
     "                    verified = True      # ⛔ 先記著,清理確認乾淨後才發布",
     '                    print("VERIFY_OK 九柱完整、格式合格、本輪新產物")\n'
     "                    verified = True",
     "tests/test_installer_order.py::test_清理沒過時不可以出現成功標記"),

    ("重試的等待不受總預算限制(封頂的不再是牆上時間)",
     "分軌線檢查.py",
     "        nap = max(0.0, min(pause, deadline - time.monotonic()))",
     "        nap = pause   # 變異:等待不吃預算",
     "tests/test_demucs_resolve.py::test_等待也要吃預算"),

    # ── Codex 第十九輪:PCM 正規化撞號、空字串身分、rc 混用、設定沒統一 ──────
    # ⚠ 要**兩個一起退**才是 R18 當時的行為:只改其中一半,另一半仍會讓雜湊分開
    #   (結構前綴 or 原生解碼各自都足以分辨)—— 變異驗證抓到我這個裝飾品。
    # ⚠ 要**整段退回**才是 R18 當時的行為:只拿掉結構前綴、或只改 ffmpeg 參數,
    #   另一半都還能讓兩個版本的雜湊分開 —— 變異驗證抓到我這個裝飾品。
    ("PCM canonical 退回「浮點以外一律 s32le」(s64 與低振幅浮點都會撞號)",
     "評審團.py",
     '    return _CANONICAL_BY_FMT.get((sample_fmt or "").lower(), "")',
     '    return _CANONICAL_BY_FMT.get((sample_fmt or "").lower(), "s32le")',
     "tests/test_pillars.py::test_canonical格式是白名單而且分得出寬度"),

    ("算不到身分時照樣寫空字串(整份報告被 schema 判畸形)",
     "評審團.py",
     "    if fh:\n        out[\"source_file_sha256\"] = fh",
     "    out[\"source_file_sha256\"] = fh   # 變異:無條件寫入",
     "tests/test_compare.py::test_算不出PCM時不可以寫空字串"),

    ("空字串身分被當成畸形(沒有 ffmpeg 的舊報告整份不合法)",
     "驗證報告.py",
     '        if isinstance(v, str) and v == "":\n            continue                      # 缺席,不是畸形',
     "        pass   # 變異:空字串當成畸形",
     "tests/test_compare.py::test_算不出PCM時不可以寫空字串"),

    # ⚠️ 「缺解碼雜湊要被擋」現在有三道防線(成對規則、缺版本分支、政策分支)——
    #    拿掉其中一道時另外兩道會接住,所以那個方向驗不到東西。這條改指
    #    這段程式**獨佔**的行為:合法的宣告降級在正式批次要能過。
    ("身分政策整段被拿掉(合法的宣告降級在正式批次被誤擋)",
     "驗證報告.py",
     '        if not d.get("source_audio_pcm_sha256"):',
     "        if False:   # 變異:政策分支整段失效",
     "tests/test_來源身分.py::test_正式批次接受產出端明講的降級"),

    # ⚠ 「版本前綴」與「版本白名單」是同一件事的兩半:白名單那道(見上一條)
    #   已經把未知版本的雜湊整個丟掉,所以單獨拔前綴測不出差別 —— 那不是缺陷,
    #   是冗餘。前綴留著是為了將來出現第二個合法版本時仍然分得開。
    ("helper 自己出錯又跟設定錯誤共用碼(使用者被導去改沒問題的環境變數)",
     "分軌線檢查.py",
     "        _write_status(status, ok=False, kind=INTERNAL, rc=4,\n"
     '                      why=f"{type(e).__name__}: {e}", recovered=False)\n'
     "        return 4",
     "        _write_status(status, ok=False, kind=CONFIG, rc=3,\n"
     '                      why=f"{type(e).__name__}: {e}", recovered=False)\n'
     "        return 3",
     "tests/test_installer_order.py::test_helper自己出錯要用專屬退出碼"),

    ("完整驗證的 timeout 又直接 float(env)(設定 typo 變裸 traceback)",
     "完整驗證.py",
     '        default_timeout = positive_finite("SONG_JURY_VERIFY_TIMEOUT", 7200.0,\n'
     "                                          lo=0.0, hi=86400.0)",
     '        default_timeout = float(os.environ.get("SONG_JURY_VERIFY_TIMEOUT", "7200"))',
     "tests/test_installer_order.py::test_完整驗證的timeout也要走共用設定解析"),

    ("安裝器不看狀態檔的種類(退回只憑退出碼猜,建議就會給錯)",
     "install.ps1",
     "            $lineKind = $parts[0]",
     '            $lineKind = ""   # 變異:不看狀態檔,退回猜',
     "tests/test_installer_order.py::test_ps1要照狀態檔的種類給建議",
     "win32"),

    # ── Codex 第二十輪:浮點碰撞、fail-open、狀態檔誤信、例外沒收斂 ──────────
    ("ffprobe 退回靠欄位順序(sample_fmt 被當成別的欄位 → 浮點來源全走整數路徑)",
     "評審團.py",
     '                            "-of", "default=nw=0", str(p)],',
     '                            "-of", "default=nw=1:nk=1", str(p)],',
     "tests/test_pillars.py::test_ffprobe要讀keyvalue不可以靠欄位順序"),

    ("批次對本輪新產物退回相容驗證(半殘身分靜靜收件)",
     "批次評測.py",
     "validate(out_json, require_contract=True, require_identity=\"declared\")",
     "validate(out_json, require_contract=True)",
     "tests/test_batch_and_windows.py::test_full模式對本輪新產物要用strict身分"),

    ("strict 不要求解碼身分的**版本**(換過標準面的舊雜湊照樣當強證據)",
     "驗證報告.py",
     "        if pc not in PCM_CONTRACTS:",
     "        if False:   # 變異:什麼版本都收",
     "tests/test_compare.py::test_安裝證據要求三個身分欄位都在"),

    ("比較器又自己補一個版本(對不存在的證據蓋章)",
     "比較.py",
     '            if (d.get("source_audio_pcm_sha256")\n'
     '                and d.get("source_audio_pcm_contract") in PCM_CONTRACTS) else ""),',
     '            if d.get("source_audio_pcm_sha256") else ""),',
     "tests/test_compare.py::test_不同PCM版本的雜湊不可以互比"),

    ("安裝器採信與實際 rc 矛盾的狀態檔(殘留或被改過的檔會給錯建議)",
     "install.ps1",
     '        if ($parts[0] -eq "MISMATCH") {',
     "        if ($false) {",
     "tests/test_installer_order.py::test_ps1不可以採信與實際結果矛盾的狀態檔",
     "win32"),

    ("install.sh 採信矛盾狀態檔(同上,POSIX 版)",
     "install.sh",
     "      MISMATCH*)",
     "      永遠不會符合的樣式*)",
     "tests/test_installer_order.py::test_sh不可以採信與實際結果矛盾的狀態檔"),

    ("helper 的未預期例外又落回 rc=1(被安裝器讀成缺套件)",
     "分軌線檢查.py",
     "    except Exception as e:      # noqa: BLE001 —— 這支自己出事也不能被說成缺套件",
     "    except ZeroDivisionError as e:      # 變異:只接一種不可能發生的例外",
     "tests/test_installer_order.py::test_helper的未預期例外一律收斂成4"),

    ("bootstrap 那層也不接(main 之外的例外會裸奔)",
     "分軌線檢查.py",
     "    except Exception as e:      # noqa: BLE001\n"
     "        import traceback\n"
     "        traceback.print_exc()",
     "    except ZeroDivisionError as e:      # 變異:保護傘破洞\n"
     "        import traceback\n"
     "        traceback.print_exc()",
     "tests/test_installer_order.py::test_import階段就爆掉也要收斂成4並寫得出狀態檔"),

    ("import 階段的例外沒被接住(連狀態檔都寫不出來)",
     "分軌線檢查.py",
     "    if _IMPORT_ERROR is not None:\n"
     "        # 在保護傘裡丟出來 → bootstrap 收斂成 internal_error / rc 4\n"
     "        raise _IMPORT_ERROR",
     "    pass   # 變異:import 失敗不處理",
     "tests/test_installer_order.py::test_每一個本地模組的import爆掉都要收斂成4"),

    ("完整驗證的 CLI --timeout 不驗(nan 一路傳到 subprocess)",
     "完整驗證.py",
     '    ap.add_argument("--timeout", type=_secs, default=default_timeout)',
     '    ap.add_argument("--timeout", type=float, default=default_timeout)',
     "tests/test_installer_order.py::test_完整驗證的CLI_timeout也要驗"),

    ("網頁版的 timeout 又被 int() 截成 0",
     "app.py",
     "    _JOB_TIMEOUT = max(1, round(positive_finite(\"SONG_JURY_WEB_TIMEOUT\", 7200.0,\n"
     "                                                lo=0.0, hi=86400.0)))",
     '    _JOB_TIMEOUT = int(positive_finite("SONG_JURY_WEB_TIMEOUT", 7200.0, lo=0.0, hi=86400.0))',
     "tests/test_installer_order.py::test_網頁版的timeout不可以被截成0"),

    ("狀態檔退回非原子寫入(半份 JSON 會被讀成有效狀態)",
     "分軌線檢查.py",
     "        tmp.write_text(json.dumps(fields, ensure_ascii=False), encoding=\"utf-8\")\n"
     "        os.replace(tmp, p)",
     '        p.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")',
     "tests/test_installer_order.py::test_狀態檔要原子寫入且用完就清"),

    # ── Codex 第二十一輪:s64 碰撞、layout 漂移、狀態 schema、import 傘、CLI 標籤 ──
    ("s64 來源給了會撞號的 canonical(1 個 LSB 的差異被抹平)",
     "評審團.py",
     '    "s64": "", "s64p": "",',
     '    "s64": "s32le", "s64p": "s32le",',
     "tests/test_pillars.py::test_s64來源寧可不發布身分也不要撞號"),

    ("解碼身分又把 channel_layout 字面值吃進去(換容器就換身分)",
     "評審團.py",
     '        shape_txt = "|".join(f"{k}={shape.get(k, \'\')}" for k in _IDENTITY_SHAPE_KEYS)',
     '        shape_txt = "|".join(f"{k}={shape.get(k, \'\')}" for k in _SHAPE_KEYS)',
     "tests/test_pillars.py::test_layout字面值不可以進身分雜湊"),

    ("狀態檔只驗 rc/ok 不驗 kind↔rc(rc=4 卻說是設定問題)",
     "狀態驗證.py",
     '    if not isinstance(kind, str) or kind not in KIND_BY_RC.get(actual_rc, set()):\n'
     '        return f"kind={kind!r:.40} 不屬於退出碼 {actual_rc} 的合法種類"',
     "    pass   # 變異:不驗 kind 與 rc 的對應",
     "tests/test_狀態驗證.py::test_矛盾或型別不對一律不採信"),

    ("recovered 不驗型別與成套(字串 'false' 也會觸發假警告)",
     "狀態驗證.py",
     '    rec = data.get("recovered", False)\n'
     '    if not isinstance(rec, bool):\n'
     '        return f"recovered 不是布林:{rec!r:.40}"',
     '    rec = data.get("recovered", False)',
     "tests/test_狀態驗證.py::test_矛盾或型別不對一律不採信"),

    ("驗證器又搬回被驗的那支程式自己",
     "install.ps1",
     "        $chk = (& .venv\\Scripts\\python.exe 狀態驗證.py $lineStatusRaw $lineRc 2>$null | Select-Object -First 1)",
     "        $chk = (& .venv\\Scripts\\python.exe 分軌線檢查.py --check-status $lineStatusRaw $lineRc 2>$null | Select-Object -First 1)",
     "tests/test_狀態驗證.py::test_驗證器不可以是被驗的那支程式自己"),

    ("設定讀取 的 import 又被放到保護傘外(爆掉變裸 traceback rc=1)",
     "分軌線檢查.py",
     "try:\n    from 設定讀取 import ConfigError, positive_finite   # noqa: E402",
     "from 設定讀取 import ConfigError, positive_finite   # noqa: E402\ntry:",
     "tests/test_installer_order.py::test_每一個本地模組的import爆掉都要收斂成4"),

    # ── Codex R25 ────────────────────────────────────────────────
    # ⚠️ 這三件事(兩道訊號各自送、有上限的等待、逾時升級 KILL)**互相是後備**:
    #    單獨拿掉任何一條,另外兩條都會把它接住 —— 那是好的防禦深度,但也表示
    #    單條變異觀察不到。所以合併成一條「整段退回 R24 的寫法」,那才是真的會壞。
    ("中斷時的終止程序退回 R24 寫法(群組 kill 回 0 就不補送、又無上限地等)",
     "install.sh",
     '''      kill -TERM -"$_line_pid" 2>/dev/null
      kill -TERM "$_line_pid"  2>/dev/null
      # ⛔ 等待要有上限:到期就升級成 KILL,再收屍 —— 不可以無上限地等一個
      #    「已經被要求結束、卻還活著」的程序。
      _n=0
      while kill -0 "$_line_pid" 2>/dev/null && [ "$_n" -lt 30 ]; do
        sleep 0.1; _n=$((_n + 1))
      done
      if kill -0 "$_line_pid" 2>/dev/null; then
        kill -KILL -"$_line_pid" 2>/dev/null
        kill -KILL "$_line_pid"  2>/dev/null
      fi
      wait "$_line_pid" 2>/dev/null''',
     '''      kill -TERM -"$_line_pid" 2>/dev/null || kill -TERM "$_line_pid" 2>/dev/null
      wait "$_line_pid" 2>/dev/null''',
     "tests/test_installer_order.py::test_sh在探針不理會TERM時要升級成KILL"),

    ('批次把退出碼 4 當成失敗(丟掉一份有效的昂貴評測)',
     '批次評測.py',
     '    if r.returncode not in (0, 2, 4):',
     '    if r.returncode not in (0, 2):',
     'tests/test_來源身分.py::test_批次遇到退出碼4要繼續讀報告'),

    ('網頁版把退出碼 4 當成評分失敗',
     'app.py',
     '    if r.returncode not in (0, 2, 4):',
     '    if r.returncode not in (0, 2):',
     'tests/test_rubric_pick.py::test_網頁版要處理快照殘留的退出碼'),

    ('完整驗證把退出碼 4 壓成 1 而且不跑裁判',
     '完整驗證.py',
     '            elif r.returncode == 4:',
     '            elif False:   # 變異:4 當一般失敗',
     'tests/test_來源身分.py::test_完整驗證遇到退出碼4要驗報告但不可以說VERIFY_OK'),

    ('快照殘留改回模組全域(上一輪的殘留算到下一輪)',
     '評審團.py',
     '    left = []                       # ⭐ 本輪的快照殘留(每次 main 都是新的)',
     '    left = main.__dict__.setdefault("_left", [])   # 變異:跨呼叫共用',
     'tests/test_來源身分.py::test_同一個程序連跑兩次不可以繼承上一輪的快照殘留'),

    ("樣本格式表少一列(u8 的來源從此走不到 canonical)",
     "評審團.py",
     '    "u8": "s32le", "u8p": "s32le",',
     '    "u8p": "s32le",',
     "tests/test_來源身分.py::test_樣本格式表是鎖住的契約_整份都要對"),



    ('冒煙測試的暫存檔不進統一清理(中斷就留在 TEMP)',
     'install.sh',
     '  [ -n "$_smoke_json" ] && rm -f "$_smoke_json"',
     '  :   # 變異:不清冒煙暫存檔',
     'tests/test_installer_order.py::test_sh在冒煙測試階段被中斷也不可以留下暫存檔'),

    # ── Codex R24 ────────────────────────────────────────────────
    ('快照改回 copy2(連唯讀屬性一起複製 → 收工刪不掉)',
     '評審團.py',
     '            shutil.copyfile(song, snap)',
     '            shutil.copy2(song, snap)',
     'tests/test_來源身分.py::test_唯讀來源的快照也要刪得掉'),

    ('快照收尾改回 ignore_errors(刪不掉整個吞掉,音訊留在 TEMP)',
     '評審團.py',
     '    for i in range(retries):\n        if not d.exists():\n            return ""',
     '    for i in range(0):\n        if not d.exists():\n            return ""',
     'tests/test_來源身分.py::test_唯讀來源的快照也要刪得掉'),

    ("快照沒收乾淨時沿用正常退出碼(自動化永遠不知道 TEMP 留了音訊)",
     "評審團.py",
     "    if left:",
     "    if False:   # 變異:當作沒事",
     "tests/test_來源身分.py::test_快照刪不掉時要大聲講而且退出碼要不一樣"),

    ('快照建不出來時直接讓 OSError 冒出去(使用者只拿到 traceback)',
     '評審團.py',
     '        except OSError as e:\n            # ⛔ 空間不足/權限/路徑太長要給人話,不是裸 traceback(那是給機器讀的介面)\n            _force_rmtree(d)',
     '        except ZeroDivisionError as e:\n            _force_rmtree(d)',
     'tests/test_來源身分.py::test_快照建不出來時要給人話不是traceback'),

    ('降級 shape 不驗完整欄位(殘缺的證據也算數)',
     '驗證報告.py',
     '    missing = [k for k in SHAPE_KEYS if k not in shape]',
     '    missing = []   # 變異:不驗完整性',
     'tests/test_來源身分.py::test_降級的shape要完整而且自洽'),

    ('canonical 只驗有沒有寫,不驗是不是那個格式該有的值',
     '驗證報告.py',
     '    if canonical != want_canonical:',
     '    if False:   # 變異:canonical 隨便寫都行',
     'tests/test_來源身分.py::test_降級的shape要完整而且自洽'),

    ('canonical_speakers 不驗(把已知的 5.1 說成講不出配置也收)',
     '驗證報告.py',
     '    if speakers != want_speakers:',
     '    if False:   # 變異:喇叭亂寫都行',
     'tests/test_來源身分.py::test_降級的shape要完整而且自洽'),

    ('未知版本的宣告照樣套這一版的語意去判(升級產出端變破壞性事件)',
     '驗證報告.py',
     '    if gc not in PCM_CONTRACTS:',
     '    if False:   # 變異:不分版',
     'tests/test_來源身分.py::test_未來版本的宣告不可以被這一版的規則整份擋掉'),

    ('裁判 CLI 靜靜忽略不認得的參數(strict 拼錯退成相容驗證)',
     '驗證報告.py',
     '        print(f"VERIFY_BAD 不認得的參數:{tok!r} —— 認得的是 "\n              f"{list(_VALUED_FLAGS) + list(_KNOWN_FLAGS)}")\n        return 1',
     '        _i += 1\n        continue',
     'tests/test_來源身分.py::test_裁判不可以靜靜忽略打錯的參數'),

    ('install.sh 在探針那段又自己裝 EXIT trap(蓋掉全域清理 → sj_step 留下)',
     'install.sh',
     "  # ⛔ 這裡**不可以**再裝 EXIT trap:那會蓋掉全域的 cleanup_all(R24-P2-1)\n  trap '_line_stop INT' INT",
     '  trap \'rm -f "$_line_status"\' EXIT\n  trap \'_line_stop INT\' INT',
     'tests/test_installer_order.py::test_sh正常跑完不可以留下任何暫存檔'),

    # ⚠️ 這條原本改的是**測試自己**的斷言,再用同一條測試去驗 —— 永遠抓不到
    #    (把守門員拿掉,守門員當然不會抗議)。要驗的是「產品表少一列時 runtime
    #    那條也會紅」:golden 那條已經守整份,這條守「跟真的 ffmpeg 對答案」。
    ("喇叭表少一列(runtime 對答案那條也要抓到)",
     "評審團.py",
     '    "cube": "FL+FR+BL+BR+TFL+TFR+TBL+TBR",\n',
     "",
     "tests/test_pillars.py::test_喇叭表要對得上這台ffmpeg的分解"),

    # ── Codex R23 ────────────────────────────────────────────────
    ('身分改回算使用者給的路徑(評測中途換檔 → 分數 A、身分 B)',
     '評審團.py',
     '    merged.update(_identity_fields(audio))',
     '    merged.update(_identity_fields(song))',
     'tests/test_來源身分.py::test_評分階段與來源身分只讀同一份不可變快照'),

    ('某個評分階段改回讀原路徑(各階段可能讀到不同版本)',
     '評審團.py',
     '    cmd = [_venv_py(".venv"), str(BASE / "song_scorer.py"), str(audio), "--json", str(phys_json)]',
     '    cmd = [_venv_py(".venv"), str(BASE / "song_scorer.py"), str(song), "--json", str(phys_json)]',
     'tests/test_來源身分.py::test_評分階段與來源身分只讀同一份不可變快照'),

    ('快照改用隨機檔名(分軌快取每次都 miss,白燒 GPU)',
     '評審團.py',
     '        snap = d / song.name',
     '        snap = d / ("snap" + song.suffix)',
     'tests/test_來源身分.py::test_評分階段與來源身分只讀同一份不可變快照'),

    ("降級宣告不驗 reason 與 shape 是否互相成立(漏寫 PCM 換個殼就過關)",
     "驗證報告.py",
     '    return _shape_matches_reason(reason, shape, "shape" in st)',
     '    return ""   # 變異:只驗型別',
     "tests/test_來源身分.py::test_降級原因要跟shape互相成立"),

    ('兩個互斥的身分旗標同時給時不擋(比較鬆的那個無聲勝出)',
     '驗證報告.py',
     '    if "--require-identity" in argv and "--allow-declared-downgrade" in argv:',
     '    if False:   # 變異:不擋互斥旗標',
     'tests/test_來源身分.py::test_兩個互斥的身分旗標不可以同時給'),

    ('喇叭表刪掉最稀有的一列(22.2 的來源從此靜靜降級)',
     '評審團.py',
     '    "22.2": ("FL+FR+FC+LFE+BL+BR+FLC+FRC+BC+SL+SR+TC+TFL+TFC+TFR+TBL+TBC+TBR"\n             "+LFE2+TSL+TSR+BFC+BFL+BFR"),\n',
     '',
     'tests/test_pillars.py::test_喇叭表是鎖住的契約_整份都要對'),

    ('探測音訊結構時用系統 code page 解輸出(繁中 Windows 靜靜失去解碼身分)',
     '評審團.py',
     '                           capture_output=True, text=True, encoding="utf-8",\n                           errors="replace", timeout=120)',
     '                           capture_output=True, text=True, timeout=120)',
     'tests/test_pillars.py::test_探測音訊結構不可以用系統code_page解輸出'),

    ('分軌探針改回前景執行(只送主 PID 的中斷要等它跑完)',
     'install.sh',
     '  PYTHONUTF8=1 .venv/bin/python 分軌線檢查.py --status-json "$_line_status" &\n  _line_pid=$!\n  wait "$_line_pid"',
     '  PYTHONUTF8=1 .venv/bin/python 分軌線檢查.py --status-json "$_line_status"',
     'tests/test_installer_order.py::test_sh在分軌體檢被中斷時要立刻停下來'),


    ("單檔驗證把相容模式的舊報告說成「本輪新產物」",
     "驗證報告.py",
     '    if newer is not None and strict_contract and strict_identity:',
     "    if True:",
     "tests/test_installer_order.py::test_單檔驗證不可以把舊報告說成本輪新產物"),

    # ── Codex R22 ────────────────────────────────────────────────
    ('解碼身分不看喇叭語意(5.1 與 5.1(side) 撞成同一個身分)',
     '評審團.py',
     'h.update(f"{PCM_IDENTITY_CONTRACT}|{shape_txt}|speakers={speakers}"\n                 f"|canonical={fmt}|".encode("utf-8"))',
     'h.update(f"{PCM_IDENTITY_CONTRACT}|{shape_txt}"\n                 f"|canonical={fmt}|".encode("utf-8"))',
     'tests/test_pillars.py::test_喇叭配置不同不可以撞成同一個身分'),

    ('多聲道講不出配置時硬補立體聲預設(製造碰撞)',
     '評審團.py',
     '    return _DEFAULT_SPEAKERS.get(n, "")',
     '    return _DEFAULT_SPEAKERS.get(n, "FL+FR")',
     'tests/test_pillars.py::test_多聲道講不出喇叭配置就不發布身分'),

    ('安裝證據也接受宣告降級(沒裝好的機器照樣 VERIFY_OK)',
     '驗證報告.py',
     '            if policy == IDENTITY_DECLARED and why_declared:',
     '            if why_declared:',
     'tests/test_來源身分.py::test_安裝證據不接受宣告降級'),

    ('降級宣告不驗產出端版本(舊版漏寫可以偽裝成刻意降級)',
     '驗證報告.py',
     '    return st.get("reason", "") if st.get("generator_contract") in PCM_CONTRACTS else ""',
     '    return st.get("reason", "")',
     'tests/test_來源身分.py::test_舊版產出端不可以假裝成刻意降級'),

    ('--newer-than 不驗有限值(nan 讓舊報告蓋上『本輪新產物』)',
     '驗證報告.py',
     '            newer = finite_number("newer-than", argv[i] if i < len(argv) else None)',
     '            newer = float(argv[i])',
     'tests/test_來源身分.py::test_newer_than不是有限數字一律不可以蓋章'),

    ('PS 的狀態檔清理搬出 finally(中斷就留檔)',
     'install.ps1',
     '        # ⛔ 狀態檔的清理**一定要在同一個 finally**(Codex R22-P2-3 實測):\n        #    舊版寫在 try 之後 20 行,helper 跑完到那一行之間若 Ctrl+C 或\n        #    發生終止性錯誤,隨機檔名的狀態檔就會一直留在 TEMP 裡累積。\n        Remove-Item -LiteralPath $statusFile -Force -EA SilentlyContinue\n    }',
     '    }\n    Remove-Item -LiteralPath $statusFile -Force -EA SilentlyContinue',
     'tests/test_installer_order.py::test_ps1的狀態檔清理必須在finally區塊裡',
     'win32'),

    ("POSIX 的 INT trap 只清檔不結束(Ctrl+C 之後照樣往下裝)",
     "install.sh",
     '    trap - "$1"\n    kill -"$1" $$',
     "    :   # 變異:清完就繼續",
     "tests/test_installer_order.py::test_sh在分軌體檢被中斷時要立刻停下來"),

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
    ("白名單漏放行 分軌線檢查.py(安裝器自檢第一步就找不到檔)",
     "分軌線檢查.py",
     "tests/test_packaging.py::test_安裝腳本呼叫的py也要在repo裡"),
    ("白名單漏放行 狀態驗證.py(安裝器驗狀態檔時找不到檔)",
     "狀態驗證.py",
     "tests/test_packaging.py::test_安裝腳本呼叫的py也要在repo裡"),
    ("白名單漏放行 完整驗證.py(只有 shell 腳本會叫它 → 掃 import 的檢查看不到)",
     "完整驗證.py",
     "tests/test_packaging.py::test_安裝腳本呼叫的py也要在repo裡"),
    ("四把尺其中一把沒進 repo(詞柱評不出來)",
     "rubrics/JA_lyric_rubric_v3.md",
     "tests/test_packaging.py::test_規則與尺都隨包"),
    ("四語範例歌曲其中一首沒進 repo(開源門面缺一角)",
     "examples/中文範例-貓步友情進行式.mp3",
     "tests/test_packaging.py::test_四語範例歌曲成對且語言對得上"),
]


def in_worktree() -> bool:
    """這裡到底有沒有 git worktree —— ⛔ 這是判斷「ZIP 版」的**唯一**依據。

    🔴 Codex R17-4:舊版把「`git rm` 回非零」一律當成 ZIP 版跳過。
       但 index.lock 競態、index 唯讀、repo 損壞也都回非零(實測 rc=128)——
       於是**最需要打包變異保護的 clone**,會在 git 故障時把整組打包檢查靜靜關掉,
       報表還寫著「只是 ZIP 限制」,而整支照樣 exit 0。"""
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                       cwd=REPO, capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


class GitFailure(RuntimeError):
    """在 worktree 裡跑 git 卻失敗 —— 這是硬錯誤,不是「這個環境驗不了」。"""


def git_must(args):
    """跑一個一定要成功的 git 指令;失敗就帶著 rc/stderr 炸出來。
    ⛔ 還原用的 `git add` 也要驗:還原失敗卻宣稱乾淨,比不還原更糟。"""
    r = subprocess.run(["git"] + args, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise GitFailure(f"git {' '.join(args)} 失敗(rc={r.returncode}):"
                         f"{(r.stderr or r.stdout or '').strip()[:300]}")
    return r


def run_pytest(target):
    """回 (是否有測試真的 failed, 是否有測試真的跑到, 選到幾條測試, 子程序輸出)。

    🔴 2026-08-02 踩到:測試改名後,變異裡的目標 id 選不到任何測試 →
       XML 是空的 → 舊版把它算成「被 skip」(平台限制)。結果是**改名等於
       靜靜關掉一條驗證**,而報表看起來一切正常。選到 0 條要當硬錯誤。

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
        # ⛔ -B / PYTHONDONTWRITEBYTECODE(Codex R25-P2-4):變異是「寫檔 → 立刻跑」,
        #    留下的 __pycache__ 會讓下一次執行有機會載到**上一版**的 bytecode ——
        #    那正是本機 7/8 vs CI 8/8 這種非決定性最常見的來源。
        r = subprocess.run([PY, "-B", "-m", "pytest", target, "--no-header",
                            "-p", "no:cacheprovider", f"--junit-xml={xml}"],
                           cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**__import__("os").environ, "PYTHONUTF8": "1",
                                "PYTHONDONTWRITEBYTECODE": "1"})
        out = ((r.stdout or "") + (r.stderr or ""))[-1500:]
        if not xml.exists():
            return False, False, 0, out    # 連 XML 都沒產出 = 這次沒驗到
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
    return n_fail > 0, (n_fail + n_pass) > 0, n_fail + n_pass + n_skip, out


def main(argv=None):
    """--only-platform win32:只跑標了那個平台的變異(給 Windows CI 用)。

    🔴 Codex R18-6:CI 的變異工作固定在 ubuntu,Windows 專屬的變異(Job handle、
       venv layout、PowerShell 130)在那裡一律誠實跳過 —— 於是「兩平台合起來
       全覆蓋」實際上是「ubuntu CI + 某次人工 Windows 執行」,不是每次 commit 的保證。
       有了這個開關,Windows job 只跑那幾條,幾十秒就跑完,不必重跑整套。"""
    argv = sys.argv[1:] if argv is None else argv
    only_platform = None
    if "--only-platform" in argv:
        only_platform = argv[argv.index("--only-platform") + 1]

    print("=" * 66)
    print("  變異驗證:把真實 bug 塞回去,確認測試抓得到"
          + (f"(只跑 {only_platform} 專屬那幾條)" if only_platform else ""))
    print("=" * 66)

    # 先確認乾淨狀態全綠,否則後面的結果沒有意義
    _failed, _, _n, _out = run_pytest("tests")
    if _failed:
        print("\n✗ 乾淨狀態下測試就沒過,先修好再跑變異驗證。")
        # ⛔ 要看得到是**哪一條**沒過(Codex R25-P2-4):只印一句「先修好」的話,
        #    在 CI 上得重跑一次完整測試才知道原因。
        print(_out[-1200:])
        return 1

    # ⭐ 還原基準:跑之前先把每個會被動到的檔案存成 bytes(見最後的還原檢查)
    BEFORE = {m[1]: (REPO / m[1]).read_bytes() for m in MUTATIONS}

    bad, skipped = [], []
    for i, item in enumerate(MUTATIONS, 1):
        # 第 6 個元素(可選)= 這條變異只在哪個平台成立。
        # ⛔ 有些 bug 是平台專屬的(例:Windows venv 的 site-packages layout),
        #    在別的平台上「沒抓到」不是測試爛,是那個 bug 在那裡根本不存在。
        #    硬算進 bad 會逼人去修一個假問題(CI ubuntu 實際踩到)。
        desc, fname, old, new, target = item[:5]
        only = item[5] if len(item) > 5 else None
        if only_platform and only != only_platform:
            continue          # --only-platform:只跑指定平台專屬的那幾條
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
        # ⛔ 先確認磁碟上真的是變異版才跑(Codex R25-P2-4):否則「寫入沒生效」
        #    會被報成「測試是裝飾品」—— 那是完全不同的問題,會白追很久。
        if p.read_bytes() != mutated.encode("utf-8"):
            p.write_bytes(raw)
            print(f"\n[{i}/{len(MUTATIONS)}] ❌ 寫不進去:{fname}(檔案被鎖住?)")
            bad.append(desc + "(變異寫不進磁碟)")
            continue
        try:
            failed, ran, picked, out = run_pytest(target)
        finally:
            p.write_bytes(raw)                        # 一定要逐位元還原
        if picked == 0:
            print(f"\n[{i}/{len(MUTATIONS)}] ❌ 選不到測試:{target}")
            print(f"        → 測試改名或刪掉了?這條變異等於被靜靜關掉({desc})")
            bad.append(desc + "(目標測試不存在)")
            continue
        if failed:
            print(f"\n[{i}/{len(MUTATIONS)}] ✅ 抓到了:{desc}")
        elif not ran:
            print(f"\n[{i}/{len(MUTATIONS)}] ⏭ 無法驗證:{desc}")
            print(f"        → {target} 在這個平台被 skip,這次沒驗到(不是通過)")
            skipped.append(("platform", desc))
        else:
            print(f"\n[{i}/{len(MUTATIONS)}] ❌ 沒抓到:{desc}")
            print(f"        → {target} 在缺陷存在時仍然通過,這條測試是裝飾品")
            # ⛔ 把子 pytest 的輸出印出來(Codex R25-P2-4):不然「存活」只是一句
            #    結論,查不到是真的沒抓到、還是這次根本沒跑到那條測試。
            print("        ↳ 子 pytest 輸出尾段:")
            for _ln in out.strip().splitlines()[-12:]:
                print(f"          {_ln}")
            bad.append(desc)

    # ── 打包類:用 git rm --cached 模擬「這個檔沒進 repo」 ──────────────
    n0 = len(MUTATIONS)
    is_repo = in_worktree()
    git_items = [] if only_platform else GIT_MUTATIONS   # 打包類不分平台,只在全跑時做
    for j, (desc, fname, target) in enumerate(git_items, n0 + 1):
        if not is_repo:
            # ⛔ 只有「明確不是 worktree」才算環境限制(ZIP 版沒有 .git)——
            #    算進 bad 會讓 ZIP 版永遠報一堆缺陷沒抓到,那是假警報。
            print(f"\n[{j}/{n0 + len(git_items)}] ⏭ 無法驗證:{desc}")
            print(f"        → 這個環境沒有 git worktree(ZIP 版),打包類變異只能在 clone 裡驗")
            skipped.append(("zip", desc))
            continue
        try:
            # ⛔ 在 worktree 裡 git 還失敗 = 硬錯誤(index.lock / 唯讀 / 損壞),
            #    不可以偽裝成 ZIP skip(Codex R17-4)
            # ⚠️ -f:開發中工作區常有未 stage 的修改,不加會被 git 擋下來
            #    (我們在 finally 立刻 git add 回去,index 不會留下副作用)
            git_must(["rm", "--cached", "-q", "-f", "--", fname])
        except GitFailure as e:
            print(f"\n[{j}/{n0 + len(git_items)}] ❌ Git 故障,這條驗不了也不能當沒事:{desc}")
            print(f"        → {e}")
            bad.append(desc + "(git 故障,打包變異沒驗到)")
            continue
        try:
            failed, ran, picked, out = run_pytest(target)
        finally:
            git_must(["add", "--", fname])          # 還原失敗也要炸,不可以靜靜留著
        if picked == 0:
            print(f"\n[{j}/{n0 + len(git_items)}] ❌ 選不到測試:{target}")
            bad.append(desc + "(目標測試不存在)")
        elif failed:
            print(f"\n[{j}/{n0 + len(git_items)}] ✅ 抓到了:{desc}")
        elif not ran:
            print(f"\n[{j}/{n0 + len(git_items)}] ⏭ 無法驗證:{desc}(被 skip,不是通過)")
            skipped.append(("platform", desc))
        else:
            print(f"\n[{j}/{n0 + len(git_items)}] ❌ 沒抓到:{desc}")
            print(f"        → {target} 在缺陷存在時仍然通過,這條測試是裝飾品")
            bad.append(desc)

    print("\n" + "=" * 66)
    if bad:
        print(f"  ❌ 有 {len(bad)} 條缺陷不會被測試抓到:")
        for b in bad:
            print(f"     · {b}")
        return 1
    total = (len([m for m in MUTATIONS
                  if (len(m) > 5 and m[5] == only_platform)]) if only_platform
             else len(MUTATIONS) + len(GIT_MUTATIONS))
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
    # 最後再確認一次:所有被動過的檔案都逐位元還原了。
    # ⛔ 不可以拿 git(diff 或 status)當標準:開發中的工作區本來就有未提交的修改,
    #    那不是變異殘留 —— 用 git 比會**每次都誤報**,誤報看久了就沒人看了(自己踩過)。
    #    唯一正確的基準是「這支程式跑之前的那份 bytes」,所以開頭先自己存一份。
    dirty = sorted(n for n, b in BEFORE.items() if (REPO / n).read_bytes() != b)
    if dirty:
        print(f"  ⚠️ 變異後沒還原乾淨:{dirty}")
        return 1
    print("  ✅ 原始碼已全部還原")
    return 0


if __name__ == "__main__":
    sys.exit(main())
