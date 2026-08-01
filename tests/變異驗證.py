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

    # ── Codex 第十輪:鎖跨副本、狀態 schema、冷卻持久化、顯示層防炸 ──────
    ("鎖位置退回 BASE(兩份 ZIP 副本各鎖各的,互斥只在單一副本內成立)",
     "評審團.py",
     '    d = state_root() / "_locks"',
     '    d = BASE / "_locks"',
     "tests/test_lock_and_gate.py::test_鎖的位置跟工具副本無關"),

    ("狀態檔頂層不驗型別(合法 JSON 的 [] 讓 Gemini 整關炸掉)",
     "Gemini曲評.py",
     '    if not isinstance(raw, dict):\n        _quarantine_state(f"頂層是 {type(raw).__name__},應為 dict")\n        return {}',
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

    ("金鑰自檢不剝 BOM(PS5.1 寫的 .env 在 WSL 被判沒金鑰)",
     "install.sh",
     r'''  _ENV_TEXT="$(sed "1s/^$(printf '\357\273\277')//" .env 2>/dev/null || cat .env)"''',
     r'''  _ENV_TEXT="$(cat .env)"''',
     "tests/test_packaging.py::test_install_sh不可用固定tmp檔且要容忍BOM"),

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
