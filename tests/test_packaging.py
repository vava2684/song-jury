# -*- coding: utf-8 -*-
"""打包自足性 —— 這一支是「別人 clone 下來到底跑不跑得起來」的守門員。

🔴 真實事故(2026-07-31):白名單制 .gitignore 漏放行 分軌快取.py 與 伴奏混音.py,
   而 編曲層次.py 與 和聲分析.py 是**頂層 import** 它。每個新使用者一定 ModuleNotFoundError,
   和聲柱整根消失、41.4% 權重失去量測基礎 —— 而安裝自我檢查照樣印綠色「完整」。
   當時只用 `git status` 看「該進的都進了」就宣告完成,所以什麼都沒發現。

   ⛔ 白名單制的代價就是「忘了寫就少檔」,必須有機器把關,不能靠人記。
"""
import ast
import re
import shutil
import subprocess
import sys
import pytest
from conftest import REPO

HEAVY = {"torch", "torchaudio", "librosa", "numpy", "demucs", "gradio",
         "matplotlib", "soundfile", "parselmouth", "scipy", "reportlab", "gradio_client"}


def _tracked():
    r = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"],
                       cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        # ⚠️ 這個訊息要講清楚跳了什麼 —— 從 GitHub 下載 ZIP 的人沒有 .git,
        #    這幾條會全部跳過。程式本身不受影響(產品程式完全不依賴 git),
        #    但「打包有沒有漏檔」這一層就沒被驗到。要驗請改用 git clone。
        pytest.skip("這是 ZIP/非 git 目錄 → 跳過打包自足性檢查"
                    "(程式功能不受影響;要驗打包請用 git clone 後再跑)")
    return {p.strip() for p in r.stdout.splitlines() if p.strip()}


def _tracked_py():
    """只掃「產品程式」—— tests/ 底下的檔案裡有拿 BASE / "X.py" 當範例的字串,
    不排除的話這些檢查會抓到自己身上。"""
    return sorted(p for p in _tracked()
                  if p.endswith(".py") and not p.startswith("tests/"))


def test_每個被引用的本地模組都在repo裡():
    """掃所有追蹤到的 .py,凡是 import 了同目錄的本地模組,那個檔必須也被追蹤。"""
    tracked = _tracked()
    local = {p.stem for p in REPO.glob("*.py")}
    missing = []
    for f in _tracked_py():
        tree = ast.parse((REPO / f).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in local and f"{n}.py" != f and f"{n}.py" not in tracked:
                    missing.append(f"{f} import 了 {n}.py,但它沒進 repo")
    assert not missing, "\n".join(missing)


def test_每個被subprocess呼叫的腳本都在repo裡():
    """評審團.py 會 subprocess 跑 BASE / "X.py";那些檔一樣不能漏。"""
    tracked = _tracked()
    missing = []
    for f in _tracked_py():
        src = (REPO / f).read_text(encoding="utf-8")
        for m in re.finditer(r'BASE\s*/\s*"([^"]+\.py)"', src):
            if m.group(1) not in tracked:
                missing.append(f"{f} 會執行 {m.group(1)},但它沒進 repo")
    assert not missing, "\n".join(missing)


def test_安裝腳本呼叫的py也要在repo裡():
    """🔴 真的踩到(2026-08-02):`完整驗證.py` 是 install.ps1 / install.sh 直接跑的,
    但白名單 .gitignore 忘了放行 —— 它**不被任何 .py import**,所以上面那兩層
    掃 import / 掃 `BASE / "X.py"` 的檢查完全看不到它。別人 clone 下來
    `-VerifyModels` 第一步就找不到檔。

    ⛔ 白名單制的破口從來不是「常用的那些檔」,是這種**只有 shell 會叫**的檔。"""
    tracked = _tracked()
    local = {p.name for p in REPO.glob("*.py")}
    missing = []
    for f in ("install.ps1", "install.sh", "一鍵安裝.bat", "run_web.ps1", "run_web.sh"):
        p = REPO / f
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"[\w一-鿿]+\.py", src):
            name = m.group(0)
            if name in local and name not in tracked:
                missing.append(f"{f} 會執行 {name},但它沒進 repo(.gitignore 漏放行)")
    assert not missing, "\n".join(sorted(set(missing)))


def test_規則與尺都隨包():
    """系統的靈魂:四把語言尺與評分規則缺一份,詞柱就評不出來。"""
    tracked = _tracked()
    need = ["評詞標準.md", "得獎精神綱要.md", "親聽檢查清單.md",
            "rubrics/ZH_lyric_rubric_v5.md", "rubrics/EN_lyric_rubric_v2.md",
            "rubrics/JA_lyric_rubric_v3.md", "rubrics/KO_lyric_rubric_v4.md"]
    assert [n for n in need if n not in tracked] == []


def test_不可外洩版權音檔與金鑰():
    """⛔ 公開 repo 絕不可以出現得獎歌 mp3、分軌、測試歌、金鑰、個人校準層。

    ⚠️ 唯一的 mp3 例外是 `examples/` 底下的**四語範例**(作者自己的 SUNO 作品,
       同意公開散布)——窄門只開這一格,其他任何位置的 mp3 照樣全擋。"""
    bad = [p for p in _tracked()
           if re.search(r"(^|/)_stems/|(^|/)_C層|(^|/)下載/|\.mp3$|\.flac$|(^|/)\.env$|個人整理", p)
           and not (p.startswith("examples/") and p.endswith(".mp3"))]
    assert bad == [], f"這些不該進 repo:{bad}"
    # 窄門的邊界也要驗:examples/ 只准 mp3+txt,別的類型(flac/wav/zip…)不准搭便車
    ex_bad = [p for p in _tracked()
              if p.startswith("examples/") and not p.endswith((".mp3", ".txt"))]
    assert ex_bad == [], f"examples/ 只准範例 mp3 與歌詞 txt:{ex_bad}"


# 哪支程式跑在哪個環境 → 它的頂層第三方相依必須由那個環境的 requirements 宣告。
# ⚠️ 一定要**分環境**檢查,不可以把所有 requirements 併成一池:
#    真實事故就是 requests 只寫在 requirements-web.txt(網頁版),
#    而 Gemini曲評.py 跑在 .venv —— 併成一池的話這個 bug 檢查不出來。
環境相依 = {
    "requirements.txt": ["評審團", "song_scorer", "Gemini曲評", "情感弧線", "報告轉PDF",
                         "轉PNG", "顯示規則", "批次評測", "曲評測清單", "brand_logo",
                         "setup_nrcvad", "make_demo_song", "狀態目錄", "子程序",
                         "金鑰驗證", "驗證報告"],
    "requirements-demucs.txt": ["分軌快取", "編曲層次", "和聲分析", "伴奏混音"],
    "requirements-audition.txt": ["演唱聽感", "真實距離"],
    "requirements-web.txt": ["app"],
}
# 套件名 ≠ import 名
別名 = {"pillow": "pil", "praat_parselmouth": "parselmouth", "python_dotenv": "dotenv"}


def _declared(*req_files):
    out = set()
    for fn in req_files:
        p = REPO / fn
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.split("#")[0].strip()
            if not ln or ln.startswith("-"):
                continue
            name = re.split(r"[<>=!\[;]", ln)[0].strip().lower().replace("-", "_")
            out.add(name)
            if name in 別名:
                out.add(別名[name])
    return out


@pytest.mark.parametrize("req_file", list(環境相依))
def test_每個環境的第三方相依都由該環境的requirements宣告(req_file):
    """🔴 真實事故:Gemini曲評.py 頂層 import requests,但 requests 只寫在
    requirements-web.txt。作者本機靠別的套件間接帶進來所以沒事,
    別人照 README 裝完,Gemini 曲評直接 ModuleNotFoundError —— CI 才抓出來。

    ⛔ 「我本機跑得動」不是相依有沒有宣告的證據;靠別人的相依樹更不算。"""
    import sys as _sys
    stdlib = set(getattr(_sys, "stdlib_module_names", ()))
    local = {p.stem for p in REPO.glob("*.py")}
    # 網頁版是疊在 .venv 上跑的,所以它可以用 requirements.txt 宣告過的東西
    reqs = (req_file, "requirements.txt") if req_file == "requirements-web.txt" else (req_file,)
    declared = _declared(*reqs)

    missing = []
    for mod in 環境相依[req_file]:
        p = REPO / f"{mod}.py"
        if not p.exists():
            continue
        for node in ast.parse(p.read_text(encoding="utf-8")).body:   # 只看頂層
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for n in names:
                if n in stdlib or n in local:
                    continue
                if n.lower().replace("-", "_") in declared:
                    continue
                missing.append(f"{mod}.py 頂層 import {n},但 {req_file} 沒宣告它")
    assert not missing, "\n".join(sorted(set(missing)))


def test_不可洩漏作者本機路徑或指向沒進repo的內部檔案():
    """公開 repo 裡不該出現 `D:\\Source\\...`、`C:\\Users\\某人\\...` 這種本機路徑,
    也不該叫讀者去看一個不隨包散布的內部檔案 —— 對他們是死連結,對作者是資訊外洩。"""
    tracked = _tracked()
    # ⚠️ `_批次結果` 不列入:那是批次工具自己會建的輸出夾,不是作者的內部資料夾。
    內部 = re.compile(r"[A-Za-z]:\\+(Users|Source)\\+[^\s\"']+|多語詞評計畫|權重辯論_\d+|_審核[\\/]")
    白名單 = {"tests/test_packaging.py"}          # 這條規則自己會寫出範例字串
    bad = []
    for f in sorted(tracked):
        if f in 白名單 or not f.endswith((".py", ".md", ".txt", ".ps1", ".sh", ".yml")):
            continue
        p = REPO / f
        if not p.exists():
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = 內部.search(ln)
            if m:
                bad.append(f"{f}:{i} 出現 {m.group(0)!r}")
    assert not bad, "\n".join(bad)


def test_下載ZIP的人拿到的換行是對的():
    """大部分使用者不會 git clone,而是**在 GitHub 按「Download ZIP」**。

    那份 ZIP 是 GitHub 用 `git archive` 產的,會套用 .gitattributes ——
    所以 .sh 必須是純 LF(否則 Linux 一跑就噴 `bash: \\r: command not found`)、
    .bat 必須是 CRLF(否則 cmd 會碎字)。
    分兩層驗:
      ① .gitattributes 有沒有宣告規則 —— 規則被刪掉,下一次提交就會生出壞掉的 ZIP
      ② git archive 吐出來的內容對不對 —— 這才是使用者真正拿到的東西
    ⚠️ 只驗 ② 是不夠的:archive 讀的是**已提交**的 .gitattributes,
       工作區把規則刪掉它照樣是對的,要等提交後才爆(變異驗證抓到過)。
    """
    # ① 規則還在嗎 —— ⚠️ 這一段**離線就能驗**,要放在 _tracked() 之前。
    #    放在後面的話,ZIP 環境會整條 skip,連能驗的部分都沒驗到(Codex 抓到)。
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    for rule in ("*.sh", "*.bat"):
        assert re.search(rf"^\s*\{rule}\s+.*eol=", attrs, re.M), \
            f".gitattributes 沒有鎖 {rule} 的換行 → 下載 ZIP 的人會拿到壞掉的檔案"

    _tracked()      # ② 以下要比對 archive 內容,沒有 git 就跳過(ZIP 環境本身)
    r = subprocess.run(["git", "archive", "HEAD"], cwd=REPO, capture_output=True)
    if r.returncode != 0:
        pytest.skip("git archive 失敗,跳過")

    import io
    import tarfile
    bad = []
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            name = m.name
            if not name.endswith((".sh", ".bat")):
                continue
            b = tf.extractfile(m).read()
            crlf = b.count(b"\r\n")
            lf = b.count(b"\n") - crlf
            if name.endswith(".sh") and crlf:
                bad.append(f"{name} 在 ZIP 裡有 {crlf} 個 CRLF → Linux 會噴 bash: \\r")
            if name.endswith(".bat") and lf:
                bad.append(f"{name} 在 ZIP 裡有 {lf} 個純 LF → cmd 會碎字")
    assert not bad, "\n".join(bad)


def test_產品程式不可依賴git():
    """ZIP 下載沒有 .git。任何產品程式若呼叫 git,ZIP 使用者就會壞掉。
    (測試可以依賴 git —— 它們會誠實跳過並說明原因。)"""
    bad = []
    for f in _tracked_py():
        src = (REPO / f).read_text(encoding="utf-8")
        for pat in (r'"git"', r"'git'", r"\bgit ls-files\b", r"\bgit rev-parse\b"):
            if re.search(pat, src):
                bad.append(f"{f} 疑似呼叫 git({pat})—— ZIP 使用者沒有 .git")
    assert not bad, "\n".join(sorted(set(bad)))


def test_評審團py頂層只用標準庫():
    """演唱聽感.py / 真實距離.py 跑在 .venv-audition,卻要 import 評審團 的 iter_windows。
    評審團.py 頂層一旦多一個重相依,那兩支就會在別的 venv 裡爆掉。"""
    tree = ast.parse((REPO / "評審團.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module.split(".")[0])
    assert not (top & HEAVY), f"評審團.py 頂層出現重相依:{sorted(top & HEAVY)}"


def test_安裝腳本會建立全部四個環境():
    """少建一個環境 = 對應的柱永久缺項。這條擋的是「新增環境卻忘了寫進安裝腳本」。"""
    for script in ("install.ps1", "install.sh"):
        src = (REPO / script).read_text(encoding="utf-8")
        for venv in (".venv", ".venv-ml", ".venv-demucs", ".venv-audition"):
            assert venv in src, f"{script} 沒有處理 {venv}"


def test_每個requirements檔都被安裝腳本用到():
    tracked = _tracked()
    reqs = [p for p in tracked if re.fullmatch(r"requirements(-\w+)?\.txt", p)]
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    # 網頁版由 run_web 腳本裝;dev 是給改程式的人裝測試相依,兩者都不該進一般安裝流程
    可選 = {"requirements-web.txt", "requirements-dev.txt"}
    for r in reqs:
        if r in 可選:
            continue
        assert r in ps1 and r in sh, f"{r} 沒有被兩個安裝腳本用到"


def test_安裝腳本不可用PS51沒有的API或會寫BOM的寫法():
    """🔴 Codex R10 三條 PS5.1 地雷,全部用內容檢查釘死:
    · [double]::IsFinite 是 .NET Core 才有 → 5.1 直接拋例外,完整安裝也 exit 1;
    · Out-File -Encoding utf8 在 5.1 會寫 BOM → 搬去 WSL 後 install.sh 的
      行首 grep 對不上第一行,誤報「沒有金鑰」。"""
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    assert "IsFinite" not in ps1, \
        "🔴 [double]::IsFinite 回來了 —— PowerShell 5.1(.NET Framework)沒有這個方法"
    assert "IsNaN" in ps1 and "IsInfinity" in ps1, "有限性檢查不可以整個拿掉"
    assert not re.search(r"Out-File[^\n]*\.env|\.env[^\n]*Out-File", ps1), \
        "🔴 .env 不可用 Out-File 寫(PS5.1 會帶 BOM);用 [IO.File]::WriteAllText + UTF8Encoding($false)"
    assert "UTF8Encoding" in ps1, ".env 要用無 BOM 的 UTF-8 寫"


def test_install_sh不可用固定tmp檔且要容忍BOM():
    """🔴 Codex R10:固定 /tmp/_sj_step.log 兩個安裝並行會互相 truncate + symlink 風險;
    行首 grep 金鑰要先剝 BOM(PS5.1 寫的 .env 開頭是 EF BB BF)。"""
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "/tmp/_sj_step.log" not in sh, "🔴 固定共用 log 檔回來了"
    assert "mktemp" in sh, "step log 要用 mktemp 建專屬檔"
    # ⚠️ BOM 容忍已經不在 shell 裡了:R15 起 .env 一律交給 金鑰政策 解析(utf-8-sig)。
    #    shell 不可以再自己 grep/剝 —— 那就是第二套政策,會漏掉專用變數。
    assert "utf-8-sig" in (REPO / "金鑰政策.py").read_text(encoding="utf-8"), \
        "🔴 金鑰政策沒用 utf-8-sig 讀 .env —— PS5.1 寫的 BOM 檔會被誤判成沒金鑰"
    assert "GEMINI_API_KEYS?" not in sh, \
        "🔴 install.sh 又自己 grep .env 的金鑰了 —— 前置判斷會漏掉專用變數(R15)"


def test_安裝腳本把ffmpeg當完整安裝必要件():
    """🔴 Codex R10:Gemini 內嵌上限約 20MB(base64 後),一般 WAV 必超限,
    要靠 ffmpeg 轉檔 —— 缺它=評 WAV 時 Gemini 六柱項全缺,不可標成
    「本機檔不受影響」,也不可讓完整安裝在缺 ffmpeg 時 exit 0。"""
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "本機檔不受影響" not in ps1 and "本機檔不受影響" not in sh, \
        "🔴 誤導文案回來了:ffmpeg 缺席時本機 WAV 會評不完整"
    # ⚠️ 要釘在「退出碼那一行」:hasFfmpeg 在顯示區也有出現,只 grep 全文會被顯示區騙過
    assert re.search(r"\$failed\s*=.*\$hasFfmpeg", ps1),         "install.ps1 的退出碼($failed)要把 ffmpeg 算進去"
    assert re.search(r"\{#PROBLEMS\[@\]\}.*HAS_FFMPEG", sh),         "install.sh 的退出碼(exit 1 條件)要把 ffmpeg 算進去"


def test_安裝腳本真的驗金鑰有效性且有完整驗證開關():
    """🔴 Codex R11/R12:光 grep 格式不算驗證;內嵌探針只驗第一把、429 被洗成成功。
    驗證邏輯集中在 金鑰驗證.py / 驗證報告.py(有行為測試+變異的 python 模組),
    兩個安裝腳本必須**呼叫它們**,不准再自己內嵌探針。"""
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    for name, src in (("install.ps1", ps1), ("install.sh", sh)):
        assert "金鑰驗證.py" in src, f"🔴 {name} 沒有呼叫 金鑰驗證.py(逐把驗、三態)"
        # ⚠️ R16 起裁判是由 完整驗證.py 呼叫的(整段流程收進 python,
        #    因為 shell 對真 Ctrl+C 不可靠)—— shell 只負責看退出碼。
        assert "完整驗證.py" in src, f"🔴 {name} 的 VerifyModels 沒有走共用流程"
        assert "generativelanguage" not in src,             f"🔴 {name} 又內嵌探針了 —— 只驗第一把/429 洗白的老路;一律走 金鑰驗證.py"
    assert "VerifyModels" in ps1, "install.ps1 少了 -VerifyModels 完整驗證開關"
    assert "--verify-models" in sh, "install.sh 少了 --verify-models 完整驗證開關"
    # 真探針本體要在 金鑰驗證.py 裡
    kp = (REPO / "金鑰驗證.py").read_text(encoding="utf-8")
    assert "generativelanguage.googleapis.com" in kp
    # 獨立裁判仍然要被呼叫,只是呼叫者換成 helper
    assert "驗證報告 import" in (REPO / "完整驗證.py").read_text(encoding="utf-8")



def test_安裝步數要跟實際步驟一致():
    """🔴 Codex R11:完整安裝最後印 [10/9] —— TOTAL 少算一步。"""
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    assert re.search(r"\{ 1 \} elseif \(\$SkipML\) \{ 5 \} else \{ 10 \}", ps1), \
        "install.ps1 的 TOTAL 要是 完整10/SkipML5/CheckOnly1"
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    assert "TOTAL=10" in sh and "TOTAL=5" in sh, "install.sh 的 TOTAL 要是 完整10/skip-ml5"


def test_批次與網頁版要接受退出碼2的不完整報告():
    """🔴 Codex R11:評審團對不完整評測回專用退出碼 2(報告已完整發布)。
    批次/網頁版把 2 當一般失敗會**丟掉昂貴產物**;要照樣讀報告、顯示不可採信。"""
    for f in ("批次評測.py", "app.py"):
        src = (REPO / f).read_text(encoding="utf-8")
        assert "not in (0, 2)" in src, \
            f"🔴 {f} 要把 exit 2(完成但缺柱)當可讀結果處理,不是當失敗丟掉"


def test_四語範例歌曲成對且語言對得上():
    """examples/ 是開源展示的門面:中/英/日/韓各一首 mp3+歌詞 txt,成對缺一不可。
    🔴 真實事故:「英文範例」實際上整首是葡萄牙文(Me Brilha)——詞柱四把尺
    沒有葡文,掛英文範例會被英文尺亂評。語言用文字系統啟發式擋住明顯掛錯的。"""
    ex = REPO / "examples"
    assert ex.is_dir(), "examples/ 不見了"
    for lang in ("中文範例", "英文範例", "日文範例", "韓文範例"):
        mp3s = sorted(ex.glob(f"{lang}-*.mp3"))
        txts = sorted(ex.glob(f"{lang}-*.txt"))
        assert len(mp3s) == 1 and len(txts) == 1, \
            f"{lang} 要恰好一首 mp3+一份歌詞 txt(現在 mp3={len(mp3s)}, txt={len(txts)})"
        body = txts[0].read_text(encoding="utf-8")
        # 去掉 SUNO 段落標記([Verse…]/(演奏描述)),只看歌詞本體
        lyric = "\n".join(l for l in body.splitlines()
                          if l.strip() and not l.strip().startswith(("[", "(")))
        assert lyric, f"{lang} 歌詞 txt 裡沒有歌詞本體"
        has_kana = any("぀" <= c <= "ヿ" for c in lyric)
        has_hangul = any("가" <= c <= "힣" for c in lyric)
        has_han = any("一" <= c <= "鿿" for c in lyric)
        letters = [c for c in lyric if c.isalpha()]
        accented = [c for c in letters if c in "ãõçáéíóúâêôàüñèìòù"]
        if lang == "中文範例":
            assert has_han and not has_kana and not has_hangul, "中文範例的歌詞不像中文"
        elif lang == "日文範例":
            assert has_kana, "日文範例的歌詞沒有假名,不像日文"
        elif lang == "韓文範例":
            assert has_hangul, "韓文範例的歌詞沒有諺文,不像韓文"
        else:  # 英文
            assert not (has_kana or has_hangul or has_han), "英文範例混進了 CJK 歌詞"
            assert letters and len(accented) / len(letters) < 0.005, \
                f"🔴 英文範例的重音字母比例 {len(accented)}/{len(letters)} 太高 —— " \
                f"看起來是葡文/西文/法文之類,不是英文(詞柱沒有那把尺)"
    # 而且要真的進 repo(白名單漏放行=別人 clone 拿不到門面)
    tracked = _tracked()
    if tracked:
        got = [p for p in tracked if p.startswith("examples/")]
        assert len(got) >= 8, f"examples/ 只有 {len(got)} 個檔進 repo,四首 mp3+四份 txt 要都在"


def test_SKILL有實作退出碼契約():
    """🔴 Codex R12:SKILL.md 直接執行評審團卻不看 $LASTEXITCODE ——
    Claude Code 依 SKILL 跑時,exit 2 可能被當一般錯誤丟掉報告,
    或被無視後做出沒標「不完整」的交付。契約必須寫死在 SKILL 裡。"""
    s = (REPO / "SKILL.md").read_text(encoding="utf-8")
    assert "$juryRc = $LASTEXITCODE" in s, "SKILL 沒保存評審團的退出碼"
    assert "退出碼契約" in s and "不可排行" in s, "SKILL 沒寫 0/2/其他 的處置規則"
    assert "pillar_totals.完整評測" in s, "SKILL 要求二次確認 JSON 完整性"


def test_範例歌的實際位元率要跟README對得上():
    """🔴 Codex R13:README 寫「64 kbps」,ffprobe 實測 177–183 kbps ——
    我沒量就寫了。文件與實體資產不一致會讓讀者對聲學柱分數做出錯誤推論。
    有 ffprobe 就真的量;沒有就退回檔案大小的合理性(擋掉整批被換成低碼率檔)。"""
    import json as _json
    import shutil
    import subprocess as _sp
    ex = REPO / "examples"
    mp3s = sorted(ex.glob("*.mp3"))
    assert len(mp3s) == 4
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "64kbps" not in readme and "64 kbps" not in readme,         "🔴 README 又寫回 64 kbps —— 實測是 ~180 kbps VBR"
    if shutil.which("ffprobe"):
        rates = []
        for p in mp3s:
            r = _sp.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                         "-show_format", str(p)], capture_output=True)
            rates.append(int(_json.loads(r.stdout.decode("utf-8", "replace"))["format"]["bit_rate"]) / 1000)
        lo, hi = min(rates), max(rates)
        assert 150 <= lo and hi <= 210, f"位元率跑出 README 宣稱的 177–183 區間:{rates}"
        assert "177" in readme and "183" in readme, "README 要寫出實測到的區間"
    else:
        for p in mp3s:
            mb = p.stat().st_size / 1024 / 1024
            assert 2.5 <= mb <= 12, f"{p.name} 大小 {mb:.1f}MB 不像 ~180kbps 的 3–5 分鐘歌"


def test_VerifyModels要有外層timeout():
    """🔴 Codex R15:`& python 評審團.py` 沒有任何外層 timeout —— 模型載入真的
    deadlock 時只能靠人工中斷,而硬 kill 不保證跑得到 finally(清理與環境還原)。"""
    # ⭐ R16 起整段流程收進 完整驗證.py(PowerShell 對真 Ctrl+C 不可靠進 finally)
    helper = (REPO / "完整驗證.py").read_text(encoding="utf-8")
    assert 'os.environ.get("SONG_JURY_VERIFY_TIMEOUT"' in helper,         "完整驗證.py 沒有真的讀 SONG_JURY_VERIFY_TIMEOUT"
    assert "run_tree" in helper, "jury 沒有用可殺整棵樹的 runner 包住"
    assert "return 124" in helper and "return 130" in helper,         "逾時(124)與使用者中斷(130)要有各自的退出碼"
    for name in ("install.ps1", "install.sh"):
        src = (REPO / name).read_text(encoding="utf-8")
        assert "完整驗證.py" in src, f"{name} 沒有呼叫共用的驗證流程"
        assert "124" in src and "130" in src, f"{name} 沒有分開處理逾時與中斷"


def test_比較器要隨包且被文件指到():
    """PK/抽卡的規則寫死在 比較.py —— 它必須進 repo,而且 README/SKILL 要指到它,
    否則使用者還是會在對話裡自己發明公式(Codex R15)。"""
    tracked = _tracked()
    assert "比較.py" in tracked, "🔴 比較.py 沒進 repo"
    for doc in ("README.md", "SKILL.md"):
        assert "比較.py" in (REPO / doc).read_text(encoding="utf-8"), f"{doc} 沒有指到比較器"


def test_一鍵安裝bat要保住子程序退出碼():
    """🔴 Codex R16-11:`if errorlevel 1 pause` 是最後一行 → .bat 回的是 pause
    自己的碼(0),安裝器的 1/3/5 全被洗成成功。必須存 %errorlevel% 再 exit /b。"""
    bat = (REPO / "一鍵安裝.bat").read_text(encoding="utf-8", errors="replace")
    assert 'set "rc=%errorlevel%"' in bat, "🔴 沒有保存 child 的退出碼"
    assert "exit /b %rc%" in bat, "🔴 沒有把保存的退出碼傳出去"
    # 順序也要對:保存必須緊接在呼叫之後、pause 之前
    # ⚠️ 要抓**真正那一行**:註解裡也有 "pause" 這個字(自己踩到)
    i_call = bat.rfind("install.ps1")
    i_save = bat.find('set "rc=%errorlevel%"')
    i_pause = bat.find('if not "%rc%"=="0" pause')
    assert i_call < i_save < i_pause, "保存要在呼叫之後、pause 之前"


def _pwsh():
    return shutil.which("pwsh") or (shutil.which("powershell")
                                    if sys.platform == "win32" else None)


def _bash():
    """⚠️ Windows 上 `bash` 常常是 **WSL 的** bash(C:\\Windows\\System32\\bash.exe):
    它吃不了 Windows 路徑,拿它跑 install.sh 只會得到 127 —— 那是環境問題,
    不是安裝器的錯,不可以讓它變成假的紅燈(在乾淨 clone 裡實測踩到)。
    要驗 install.sh 就用 Git Bash,或到 Linux/macOS(CI 兩邊都會跑)。"""
    exe = shutil.which("bash")
    if exe and sys.platform == "win32" and "system32" in exe.lower():
        return None
    return exe


def test_安裝器要擋下未知參數_真的跑一次():
    """🔴 Codex R16-11:未知 switch 被靜默忽略 —— 把 -VerifyModels 拼錯的人
    會拿到普通安裝的綠燈,卻以為做過完整模型驗證(最危險的假證據)。

    ⚠️ 這條**實際執行安裝器**(擋參數在最前面,不會裝任何東西)——
    只 grep 字串的版本被變異驗證證明是裝飾品:把守門條件改成 `if ($false)`
    照樣全綠。

    ⚠️ 拼錯的樣本要選**真的不認得**的:PowerShell 會做參數前綴比對,
    `-VerifyModel` 其實會正常綁到 `-VerifyModels`(那不是 bug)。
    ⚠️ 同時帶 -CheckOnly -NoAutoTools:萬一守門真的壞了(變異注入時),
    這條測試也只會走「什麼都不裝的自我檢查」,不會在誰的機器上亂裝東西。"""
    ran = []
    exe = _pwsh()
    if exe:
        r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(REPO / "install.ps1"),
                            "-CheckOnly", "-NoAutoTools", "-VerifyModles"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, cwd=str(REPO))
        assert r.returncode == 64, \
            f"🔴 install.ps1 沒擋下拼錯的參數(拿到 {r.returncode}):{r.stdout[-300:]}"
        ran.append("ps1")
    bash = _bash()
    if bash:
        r = subprocess.run([bash, str(REPO / "install.sh"),
                            "--check-only", "--no-auto-tools", "--verify-modles"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, cwd=str(REPO))
        assert r.returncode == 64, \
            f"🔴 install.sh 沒擋下拼錯的參數(拿到 {r.returncode}):{(r.stdout + r.stderr)[-300:]}"
        ran.append("sh")
    if not ran:
        pytest.skip("這台機器沒有 pwsh 也沒有 bash,兩支安裝器都跑不起來")


# 總結區的每個退出碼,各自該由哪個旗標守門(⛔ 值就是契約,不可以改成常數真值)
_PS1_GUARDS = {"5": "PolicyError", "1": "$failed", "3": "KeyUnverified"}
_SH_GUARDS = {"5": "POLICY_ERROR", "1": "PROBLEMS", "3": "KEY_UNVERIFIED"}


def _guard_of(text, exit_line):
    """往回找 exit 這一行所屬的那個 if 條件。"""
    i = text.index(exit_line)
    j = max(text.rfind("if (", 0, i), text.rfind("if [", 0, i))
    return text[j:i]


def test_安裝器要原樣傳出政策錯誤碼5():
    """🔴 Codex R16-11:PolicyError 被 Problems 洗成 1 —— 自動化分不出
    「沒裝好」與「安全設定壞了(去申請新 key 沒有用)」。

    ⚠️ 不能只 grep「有沒有 exit 5」:變異驗證把守門改成 `if ($false)` 時
    字串全都還在,測試照樣綠。這裡驗的是**每個退出碼被哪個旗標守著**。"""
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    tail = ps1[ps1.index("$failed = "):]
    for code, flag in _PS1_GUARDS.items():
        guard = _guard_of(tail, f"exit {code}")
        assert flag in guard, f"🔴 install.ps1 的 exit {code} 沒有被 {flag} 守著:{guard!r}"
    sh_tail = sh[sh.rindex("if [ \"$POLICY_ERROR\""):]
    for code, flag in _SH_GUARDS.items():
        guard = _guard_of(sh_tail, f"exit {code}")
        assert flag in guard, f"🔴 install.sh 的 exit {code} 沒有被 {flag} 守著:{guard!r}"
    # 順序:政策碼要排在一般失敗(exit 1)之前,否則永遠走不到
    assert tail.index("exit 5") < tail.index("exit 1"), "exit 5 要排在 exit 1 之前"
    assert sh_tail.index("exit 5") < sh_tail.index("exit 1"), "install.sh 同理"
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "| **5** |" in readme, "README 的退出碼表要列出 5"
