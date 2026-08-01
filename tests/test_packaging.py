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
import subprocess
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


def test_規則與尺都隨包():
    """系統的靈魂:四把語言尺與評分規則缺一份,詞柱就評不出來。"""
    tracked = _tracked()
    need = ["評詞標準.md", "得獎精神綱要.md", "親聽檢查清單.md",
            "rubrics/ZH_lyric_rubric_v5.md", "rubrics/EN_lyric_rubric_v2.md",
            "rubrics/JA_lyric_rubric_v3.md", "rubrics/KO_lyric_rubric_v4.md"]
    assert [n for n in need if n not in tracked] == []


def test_不可外洩版權音檔與金鑰():
    """⛔ 公開 repo 絕不可以出現得獎歌 mp3、分軌、測試歌、金鑰、個人校準層。"""
    bad = [p for p in _tracked()
           if re.search(r"(^|/)_stems/|(^|/)_C層|(^|/)下載/|\.mp3$|\.flac$|(^|/)\.env$|個人整理", p)]
    assert bad == [], f"這些不該進 repo:{bad}"


# 哪支程式跑在哪個環境 → 它的頂層第三方相依必須由那個環境的 requirements 宣告。
# ⚠️ 一定要**分環境**檢查,不可以把所有 requirements 併成一池:
#    真實事故就是 requests 只寫在 requirements-web.txt(網頁版),
#    而 Gemini曲評.py 跑在 .venv —— 併成一池的話這個 bug 檢查不出來。
環境相依 = {
    "requirements.txt": ["評審團", "song_scorer", "Gemini曲評", "情感弧線", "報告轉PDF",
                         "轉PNG", "顯示規則", "批次評測", "曲評測清單", "brand_logo",
                         "setup_nrcvad", "make_demo_song"],
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
    assert r"\357\273\277" in sh, "🔴 金鑰自檢沒剝 BOM —— PS5.1 寫的 .env 會被誤判成沒金鑰"


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
    """🔴 Codex R11:任意長字串也照樣「九柱齊全、exit 0」—— 光 grep 格式不算驗證。
    兩個安裝腳本都要真打一次 Google API(models 端點)驗金鑰;
    另外要有 -VerifyModels/--verify-models 把九柱實跑一遍(import 成功≠模型能推論)。"""
    ps1 = (REPO / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "install.sh").read_text(encoding="utf-8")
    for name, src in (("install.ps1", ps1), ("install.sh", sh)):
        assert "generativelanguage.googleapis.com" in src, \
            f"🔴 {name} 沒有真打 Google 驗金鑰(任意字串會被當有效金鑰)"
    assert "VerifyModels" in ps1, "install.ps1 少了 -VerifyModels 完整驗證開關"
    assert "--verify-models" in sh, "install.sh 少了 --verify-models 完整驗證開關"


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
