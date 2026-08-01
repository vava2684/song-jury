# -*- coding: utf-8 -*-
# ══════════════════════════════════════════════════════════════════════
#  song-jury 一鍵安裝(Windows / PowerShell)
#
#  用法:
#    雙擊 一鍵安裝.bat            ← 最簡單,什麼都不用打
#    ./install.ps1                完整安裝(自動補齊 uv/git/ffmpeg)
#    ./install.ps1 -SkipML        只裝量測+報告(輕量,不含模型耳朵)
#    ./install.ps1 -NoAutoTools   不要自動幫我裝 uv/git/ffmpeg
#    ./install.ps1 -CheckOnly     什麼都不裝,只檢查現在哪幾根柱子能用
#    ./install.ps1 -VerifyModels  自我檢查後再實跑一遍九柱(模型下載/載入/推論全來真的)
#
#  設計原則:**任何一步失敗都不中斷整個安裝**。失敗的記下來,最後一次告訴你
#  哪幾根柱子會缺、怎麼補。半套能用,總比裝到一半炸掉什麼都沒有好。
# ══════════════════════════════════════════════════════════════════════
param([switch]$SkipML, [switch]$NoAutoTools, [switch]$CheckOnly, [switch]$VerifyModels,
      [Parameter(ValueFromRemainingArguments = $true)]$Unknown)

# ⛔ 未知參數一律擋下(Codex R16-11):舊版靜默忽略 —— 把 -VerifyModels 拼錯的人
#    會拿到「普通安裝的綠燈」,還以為做過完整模型驗證,那是最危險的假證據。
if ($Unknown) {
    Write-Host "⛔ 不認得的參數:$($Unknown -join ' ')" -ForegroundColor Red
    Write-Host "   可用的是:-SkipML  -NoAutoTools  -CheckOnly  -VerifyModels" -ForegroundColor Yellow
    exit 64        # sysexits.h 的 EX_USAGE
}

$ROOT = $PSScriptRoot
Set-Location $ROOT
$ErrorActionPreference = "Continue"      # ⛔ 不用 Stop:單步失敗要能繼續往下走
$PSNativeCommandUseErrorActionPreference = $false

# ⚠️ 步數要跟實際的 Step 呼叫數一致(Codex R11:完整安裝最後印 [10/9])
$TOTAL = if ($CheckOnly) { 1 } elseif ($SkipML) { 5 } else { 10 }
$script:N = 0
$script:Problems = @()

function Step($m) { $script:N++; Write-Host "`n[$($script:N)/$TOTAL] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "      OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "      !   $m" -ForegroundColor Yellow }
function Bad($m, $why) { Write-Host "      X   $m" -ForegroundColor Red; $script:Problems += "$m —— $why" }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

# 一步一步跑,炸了就記下來繼續。回傳 $true/$false
function Try-Step([string]$what, [scriptblock]$body) {
    try {
        & $body
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { throw "指令回傳 $LASTEXITCODE" }
        return $true
    } catch {
        Bad $what $_.Exception.Message
        return $false
    }
}

if (-not $CheckOnly) {
Write-Host @"

  ╔══════════════════════════════════════════════╗
  ║   song-jury 歌曲評審團 · 安裝程式            ║
  ╚══════════════════════════════════════════════╝
  這會下載數 GB 的模型,依網速大約 15～60 分鐘。
  ⚠️ 開頭會問你一個問題(Gemini 金鑰),回答完就可以放著不管 ——
     之後全程自動,失敗的部分最後會一次列給你。

"@ -ForegroundColor White

# ── [1] 基本工具 ─────────────────────────────────────────────────────
Step "檢查並補齊基本工具(uv / git / ffmpeg)"
$hasWinget = Have "winget"
function Ensure-Tool($cmd, $wingetId, $whatFor, $fatal) {
    if (Have $cmd) { Ok "$cmd 已就緒"; return $true }
    if ($NoAutoTools -or -not $hasWinget) {
        if ($fatal) { Bad "$cmd 沒裝" "$whatFor;請手動安裝後重跑" }
        else { Warn "$cmd 沒裝 → $whatFor" }
        return $false
    }
    Write-Host "      ... 沒有 $cmd,用 winget 幫你裝(可能跳出 UAC 視窗,請按同意)" -ForegroundColor DarkGray
    winget install --id $wingetId --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
    # winget 裝完當前 session 的 PATH 不會更新 → 手動重讀
    $env:PATH = [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("PATH", "User")
    if (Have $cmd) { Ok "$cmd 安裝完成"; return $true }
    if ($fatal) { Bad "$cmd 自動安裝失敗" "$whatFor;請手動裝好再重跑" }
    else { Warn "$cmd 自動安裝失敗 → $whatFor" }
    return $false
}
$okUv     = Ensure-Tool "uv"     "astral-sh.uv"     "建立 Python 環境用,沒有它什麼都裝不了" $true
$okGit    = Ensure-Tool "git"    "Git.Git"          "取得 SongEval 原始碼用" $true
# ⛔ ffmpeg 不是「只影響 YouTube」:Gemini 聽歌的內嵌上限約 20MB(base64 後),
#    一般 WAV(4 分鐘 PCM ≈ 40MB)必超限,要靠 ffmpeg 轉 320k 才進得去 ——
#    沒有它,評 WAV 時 Gemini 餵的六個柱項全缺,依九柱制就是不完整安裝(Codex R10)。
$okFfmpeg = Ensure-Tool "ffmpeg" "Gyan.FFmpeg"      "Gemini 聽大檔(一般 WAV)要靠它轉檔;沒有它會評不完整 + YouTube 連結不可用" $false

if (-not $okUv) {
    Write-Host "`n✗ 沒有 uv 就無法繼續。請到 https://github.com/astral-sh/uv 手動安裝後重跑。" -ForegroundColor Red
    Read-Host "`n按 Enter 關閉"
    exit 1
}

# ── [2] Gemini 金鑰 ─────────────────────────────────────────────────
# ⚠️ 這一步**故意排在所有下載之前**。
#    原本放在第 8 步,而橫幅又叫使用者「中途可以去泡杯茶」——
#    人走開了,安裝就卡在 Read-Host 等輸入,回來才發現半小時原地不動。
#    互動一律放最前面:問完就能安心離開。
Step "Gemini 金鑰(先問完,後面就可以放著讓它自己下載)"
if (Test-Path ".env") {
    Ok ".env 已存在,保留你原本的金鑰設定"
} else {
    Write-Host ""
    Write-Host "  ⛔ 這一把是【必要】的,不是可選:" -ForegroundColor Yellow
    Write-Host "     律動柱(4%)100% 靠 Gemini,沒有它那根柱子整根評不出來 →" -ForegroundColor Yellow
    Write-Host "     依九柱制的定義,這台機器就【評不出有效分數】(結構/旋律/人聲/整體/曲風也各缺一項)。" -ForegroundColor Yellow
    Write-Host "  申請:https://aistudio.google.com/apikey  ← Google 帳號登入就能拿,免費額度夠用" -ForegroundColor White
    # ⚠️ 非互動執行(CI、管線)時 Read-Host 會回 $null,直接 .Trim() 會拋例外
    $key = $null
    try { $key = Read-Host "  貼上金鑰後按 Enter(沒有的話直接按 Enter 先跳過,裝完再補)" }
    catch { Warn "沒有互動輸入,跳過金鑰" }
    if (-not [string]::IsNullOrWhiteSpace($key)) {
        # ⛔ 不可用 Out-File -Encoding utf8:PowerShell 5.1 會寫出帶 BOM 的 UTF-8,
        #    之後把專案搬去 WSL/Linux 時,install.sh 錨在行首的 grep 對不上第一行
        #    (前三個 byte 是 EF BB BF)→ 自檢誤報「沒有金鑰」(Codex R10 實測)。
        #    用 .NET API 寫「無 BOM」的 UTF-8,5.1 與 7 都一致。
        [System.IO.File]::WriteAllText((Join-Path $ROOT ".env"),
            "GEMINI_API_KEYS=$($key.Trim())", (New-Object System.Text.UTF8Encoding($false)))
        Ok "金鑰已寫入 .env(這個檔被 .gitignore 擋著,不會被上傳)"
    } else {
        # ⛔ 不複製 .env.example:那裡面的「你的第一把金鑰」是佔位字串,複製過去會被程式
        #    當成真金鑰拿去打 Google API,錯誤訊息還很難懂。乾脆不要有 .env。
        Warn "跳過金鑰 → 裝完會顯示【評不出有效分數】;把 .env.example 複製成 .env 填進去即可"
    }
}

# ── [3] 量測環境 ─────────────────────────────────────────────────────
Step "建立量測環境 .venv(響度/動態/頻譜/和弦/演唱量測 + 報告)"
$okVenv = Try-Step ".venv 建立" { uv venv --python 3.11 .venv }
if ($okVenv) { $okVenv = Try-Step ".venv 套件安裝" { uv pip install --python .venv\Scripts\python.exe -r requirements.txt } }
if ($okVenv) { Ok "量測與報告就緒" }

if (-not $SkipML) {
    # GPU 偵測(決定 torch 版本)
    $cuda = Have "nvidia-smi"
    $torchIdx = if ($cuda) { "https://download.pytorch.org/whl/cu124" } else { "https://download.pytorch.org/whl/cpu" }
    if ($cuda) { Ok "偵測到 NVIDIA GPU → 裝 CUDA 12.4 版 torch" }
    else       { Warn "沒偵測到 NVIDIA GPU → 裝 CPU 版 torch(能跑,但每首會慢很多)" }

    # ── [3] 模型環境 ────────────────────────────────────────────────
    Step "建立模型環境 .venv-ml(SongEval + Audiobox)—— 這步最久,會下載數 GB"
    $pyMl = ".venv-ml\Scripts\python.exe"
    $okMl = Try-Step ".venv-ml 建立" { uv venv --python 3.11 .venv-ml }
    if ($okMl) { $okMl = Try-Step "torch 安裝" { uv pip install --python $pyMl torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 --index-url $torchIdx } }
    if ($okMl) { $okMl = Try-Step ".venv-ml 套件安裝" { uv pip install --python $pyMl -r requirements-ml.txt } }

    # ── [4] SongEval 原始碼 ─────────────────────────────────────────
    Step "取得 SongEval 原始碼(CC BY-NC-SA 授權,不隨本專案散布)"
    if (-not (Test-Path "SongEval\eval.py")) {
        Try-Step "SongEval clone" { git clone --depth 1 https://github.com/ASLP-lab/SongEval.git SongEval } | Out-Null
        if (Test-Path "SongEval\requirements.txt") {
            Try-Step "SongEval 依賴" { uv pip install --python $pyMl -r SongEval\requirements.txt } | Out-Null
        }
    }
    if (Test-Path "SongEval\eval.py") { Ok "SongEval 就緒" }
    else { Bad "SongEval 取得失敗" "五個模型聽感細項會缺(連貫/記憶點/結構清晰/人聲自然/音樂性)" }

    # SongEval 的 requirements 常把 torch 換掉 → 鎖回來,否則 torchaudio 載入會 WinError 127
    Step "鎖回 torch 版本(SongEval 的依賴常把它換掉,這步是修回來)"
    if (Try-Step "torch 鎖版" { uv pip install --python $pyMl torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 --index-url $torchIdx }) {
        Ok "torch 鎖定 2.6.0 ($(if($cuda){'cu124'}else{'cpu'}))"
    }

    # ── [6] 分軌環境 ────────────────────────────────────────────────
    Step "建立分軌環境 .venv-demucs(結構編曲柱 + 和聲柱都吃它,合計 26.2% 權重)"
    $pyDe = ".venv-demucs\Scripts\python.exe"
    if ($env:SONG_JURY_DEMUCS_PY -and (Test-Path $env:SONG_JURY_DEMUCS_PY)) {
        Ok "你已用 SONG_JURY_DEMUCS_PY 指定現成的 demucs,跳過"
    } else {
        $okDe = Try-Step ".venv-demucs 建立" { uv venv --python 3.11 .venv-demucs }
        # ⛔ 索引由這裡傳,不寫死在 requirements(寫死會讓 Mac 直接失敗、沒 GPU 的人白載 2.5GB)。
        #    torch 先用 --index-url 明確裝一次(確保拿到對的 CUDA/CPU 版),再裝其餘;
        #    第二道用 unsafe-best-match,否則 numpy 這類套件會卡在 uv 的 first-index 策略上。
        if ($okDe) { $okDe = Try-Step "demucs 的 torch" { uv pip install --python $pyDe torch==2.6.0 torchaudio==2.6.0 --index-url $torchIdx } }
        if ($okDe) { $okDe = Try-Step "demucs 安裝" { uv pip install --python $pyDe -r requirements-demucs.txt --extra-index-url $torchIdx --index-strategy unsafe-best-match } }
        if ($okDe) { Ok "Demucs 六軌分離就緒(模型權重首次分離時自動下載,約 300MB)" }
        else { Bad "Demucs 安裝失敗" "結構編曲柱與和聲柱會缺項,總分失真" }
    }

    # ── [7] 新耳朵環境 ──────────────────────────────────────────────
    Step "建立新耳朵環境 .venv-audition(SingMOS 演唱聽感 + MuQ 真實距離 + SONICS AI 感)"
    $pyA = ".venv-audition\Scripts\python.exe"
    $okA = Try-Step ".venv-audition 建立" { uv venv --python 3.11 .venv-audition }
    # ⛔ 同上:索引由這裡傳,並用 unsafe-best-match 讓 numpy 這類套件能退回 PyPI
    #    (uv 預設 first-index:套件名在 pytorch 索引找得到但版本不在 → 整份解析失敗)
    if ($okA) { $okA = Try-Step "新耳朵的 torch" { uv pip install --python $pyA torch==2.6.0 torchaudio==2.6.0 --index-url $torchIdx } }
    if ($okA) { $okA = Try-Step ".venv-audition 套件安裝" { uv pip install --python $pyA -r requirements-audition.txt --extra-index-url $torchIdx --index-strategy unsafe-best-match } }
    if ($okA) {
        if (-not (Try-Step "SONICS 安裝" { uv pip install --python $pyA "git+https://github.com/awsaf49/sonics.git" })) {
            Warn "SONICS 裝不起來 → AI 感只是顯示軸,不影響計分"
        }
        Ok ".venv-audition 完成(模型權重首次執行時下載,約 3GB)"
    } else { Bad ".venv-audition 失敗" "人聲柱的 SingMOS 與真實風格柱會缺項" }

    # ── [8] 詞典與金鑰 ──────────────────────────────────────────────
    Step "情緒詞典(情感弧線用;禁再散布,自官方源代取)"
    if ($okVenv) {
        & .venv\Scripts\python.exe setup_nrcvad.py
        if ($LASTEXITCODE -ne 0) { Warn "NRC-VAD 詞典沒取到 → 情感弧線圖不可用(不計分,不影響總分)" }
        else { Ok "NRC-VAD 情緒詞典就緒" }
    }
} else {
    Step "略過模型安裝(-SkipML)"
    Warn "只有量測與報告可用;九柱中有六根會缺模型細項"
}

}   # ← if (-not $CheckOnly) 結束:上面全是「安裝」,以下是「檢查」,-CheckOnly 直接跳到這

# ── [9] 自我檢查:哪幾根柱子真的能用 ────────────────────────────────
Step "自我檢查 —— 實際確認九根柱子哪些可用"

$hasEnvExe  = Test-Path ".venv\Scripts\python.exe"

# ⛔ 不能只看 python.exe 在不在:`uv venv` 建完就有 python.exe,套件裝失敗時環境是空的
#    (實測只有 0.5MB),照樣會被判「完整」。一定要實際 import 關鍵套件才算數。
function Test-Import($venv, $mods) {
    $py = "$venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { return $false }
    foreach ($m in $mods) {
        & $py -c "import $m" 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
    }
    return $true
}
# 基礎環境也要真檢查 —— 它撐著聲學、人聲量測與報告,是最不能假的一個
$hasEnv = Test-Import ".venv" @("librosa", "numpy", "soundfile", "pyloudnorm", "reportlab")

# ── 分軌線(結構編曲柱 + 和聲柱,合計 26.2%)────────────────────────
# ⛔ 判斷全部交給 分軌線檢查.py(與 install.sh 共用的唯一實作,有測試在守):
#    ① 用哪支 python 由 評審團.py 決定(唯一真理來源),安裝器不自己猜;
#    ② 不可以只驗 import demucs —— 和聲分析.py 要 librosa,只驗 demucs 會在缺 librosa 時
#       印「九柱齊全」(Codex R13 的假陽性);
#    ③ 失敗要印出真正的原因、暫時性失敗會再試一次、救回來的會標 RECOVERED ——
#       2026-08-02 實跑:自我檢查靜靜判「和聲缺項」exit 1,同一次執行接下來的
#       -VerifyModels 卻用同一條線跑完九柱、拿到 VERIFY_OK。沒有原因的假警報最難修。
# ⚠️ 順序要跟 install.sh 一致(Codex R17-5):base venv 都不成立時不要先去跑
#    這支可能等好幾分鐘的探針 —— 那時候結論早就註定了。
if ($hasEnv) {
    # ⚠️ PYTHONUTF8 要**存了再設、finally 還原**:這是呼叫者的環境,不是我們的
    #    (同檔金鑰探針那段已經是這個寫法;R17-5 抓到這裡漏了)。
    $oldUtf8Line = $env:PYTHONUTF8
    try {
        $env:PYTHONUTF8 = "1"
        $lineOut = (& .venv\Scripts\python.exe 分軌線檢查.py 2>&1 | Out-String).TrimEnd()
        $lineRc = $LASTEXITCODE
    } finally {
        if ($null -eq $oldUtf8Line) { Remove-Item Env:PYTHONUTF8 -EA SilentlyContinue }
        else { $env:PYTHONUTF8 = $oldUtf8Line }
    }
    $hasDemucs = ($lineRc -eq 0)
    if ($hasDemucs) {
        if ($lineOut -match "DEMUCS_LINE_RECOVERED") {
            # ⛔ 救回來≠沒事:這條線在這台機器上是不穩的,不可以靜靜給綠燈
            Write-Host $lineOut -ForegroundColor DarkGray
            Warn "分軌線是重試後才成功的 —— 這台機器的這條線不穩,正式評分可能掉柱(原因見上面)"
        }
    } else {
        Write-Host $lineOut -ForegroundColor DarkGray
        if ($lineRc -eq 1) {
            Bad "分軌環境缺套件" "上面那行有指名缺哪個模組 → 重跑安裝,或 uv pip install -r requirements-demucs.txt"
        } else {
            Bad "分軌線不可用(結構編曲柱 + 和聲柱,合計 26.2%)" "⛔ 不是缺套件 —— 逾時/啟動失敗/DLL 或快取損壞,照上面的『種類』與『實際』查;重裝 requirements 多半沒用"
        }
    }
}
$hasMl  = Test-Import ".venv-ml"       @("torch", "muq", "audiobox_aesthetics")
$hasAud = Test-Import ".venv-audition" @("torch", "s3prl", "muq")
# SongEval 不能只看 eval.py 在不在:它要用 .venv-ml 跑,那個環境沒裝好就等於沒有
$hasSongEval = (Test-Path "SongEval\eval.py") -and $hasMl
# ⛔ 正則要錨在行首(多行模式),否則 `# GEMINI_API_KEY=...` 這種註解行也會被當成有金鑰;
#    也要排除 .env.example 的佔位字串。
# ⛔ 這裡**故意不做任何前置正則判斷**(Codex R15):
#    舊版先自己 grep .env 的 GEMINI_API_KEY(S),有中才呼叫共用驗證器 ——
#    於是政策正式支援的專用變數 SONG_JURY_GEMINI_API_KEYS(process env 或 .env)
#    整個被漏掉:runtime 會用那把 key,安裝器卻宣稱「沒有金鑰、律動柱缺席」。
#    「驗證器與執行期共用唯一真理來源」只做到後半段等於沒做。
#    → 有 python 就無條件呼叫 金鑰驗證.py,由 effective_keys() 唯一決定
#      process env / .env / 佔位字串 / deny / PolicyError。
$hasKey = $false
# ⛔ ffmpeg 是完整安裝的必要件:一般 WAV 超過 Gemini 內嵌上限,靠它轉檔才評得了
#    (缺它=評 WAV 時 Gemini 六柱項全缺=不完整)。-CheckOnly 也要驗。
$hasFfmpeg = Have "ffmpeg"

# ⛔ 金鑰驗證交給 金鑰驗證.py(共用實作,逐把真打 Google、絕不只驗第一把)。
#    Codex R12 兩條:內嵌版只驗第一把(第一把好第二把壞=假陽性、反過來=假陰性);
#    429/網路/TLS 全被洗成成功。python 驗還順帶根治 PS5.1 的 TLS 協商問題。
#    三態:verified=綠燈資格;invalid=視同沒金鑰;cooling/unknown=「未能驗證」,
#    不給綠燈、最後 exit 3(獨立退出碼,跟「缺柱 exit 1」分開)。
$script:KeyUnverified = $false
$script:PolicyError = $false
$probePy = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" }
           elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" }
           elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" }
           else { $null }
if ($probePy) {
    $oldUtf8Probe = $env:PYTHONUTF8       # ⚠️ 先存再設(同 -VerifyModels 那段的教訓)
    $env:PYTHONUTF8 = "1"
    try {
        & $probePy "金鑰驗證.py" ".env"
        $krc = $LASTEXITCODE
    } finally {
        if ($null -eq $oldUtf8Probe) { Remove-Item Env:PYTHONUTF8 -EA SilentlyContinue }
        else { $env:PYTHONUTF8 = $oldUtf8Probe }
    }
    switch ($krc) {
        0 { $hasKey = $true; Ok "Gemini 金鑰驗證通過(逐把真打 Google;各把狀態見上)" }
        1 { Bad "Gemini 金鑰全部無效" "格式像金鑰但 Google 全不認;請到 https://aistudio.google.com/apikey 重新申請並填進 .env" }
        3 { $script:KeyUnverified = $true; $hasKey = $true
            Warn "金鑰有效性未能驗證(全部限流中或網路/TLS 問題)—— 不給完整綠燈;恢復後請重跑 -CheckOnly" }
        4 { Warn "找不到可用金鑰(.env 沒填、只有佔位字串,或環境變數名字用錯)" }
        5 { $script:PolicyError = $true
            Bad "Gemini 金鑰政策無效" "拒絕名單格式錯,或 .env 來源可疑(symlink/硬連結/父目錄連結)。⛔ 這不是「沒填金鑰」—— 去申請新 key 沒有用,請照上面的訊息修設定" }
        default { $script:KeyUnverified = $true; $hasKey = $true
                  Warn "金鑰驗證工具異常(退出碼 $krc)—— 不給完整綠燈" }
    }
} else {
    $script:KeyUnverified = $true
    Warn "找不到任何 python 可跑金鑰驗證 —— 這台無法確認金鑰;裝完請重跑 -CheckOnly"
}

# 每根柱子:名稱 / 權重 / 完整需要什麼 / 缺了會怎樣
$sev = ($hasMl -and $hasSongEval)
# ⚠️ 這張表必須跟 評審團.py 的 PILLAR_ITEMS 實際行為對得起來(已用乾淨 clone 實跑對照):
#    · 人聲柱的量測項(音準/顫音/音域/長音/嗓音/動態)吃 Demucs 分出來的人聲軌 → 缺 demucs 就缺一大半
#    · 結構編曲柱缺 demucs 時**不是整柱不計** —— Gemini M1/M4 與 SongEval 連貫還在(柱內 50%)
#    · 和聲柱六項全部來自和弦辨識(吃分軌)→ 缺 demucs 才是真的整柱消失
$pillars = @(
  @{n="詞";         w=25.3; ok=$true;                     part=@{} }
  @{n="人聲演唱";   w=15.2; ok=$hasEnv;                   part=@{ "演唱量測(需分軌)"=$hasDemucs; "SingMOS 聽感"=$hasAud; "SongEval 自然度"=$sev; "Gemini 人聲表現"=$hasKey } }
  @{n="和聲";       w=13.6; ok=($hasEnv -and $hasDemucs); part=@{} }
  @{n="結構與編曲"; w=12.6; ok=($hasDemucs -or $sev -or $hasKey); part=@{ "編曲量測(需分軌)"=$hasDemucs; "SongEval 連貫"=$sev; "Gemini 結構/配器"=$hasKey } }
  @{n="聲學製作";   w=12.1; ok=$hasEnv;                   part=@{ "SongEval 清晰"=$sev; "Audiobox PQ"=$hasMl } }
  @{n="旋律與記憶"; w=6.1;  ok=($hasKey -or $sev);        part=@{ "Gemini 旋律"=$hasKey; "SongEval 記憶點"=$sev } }
  @{n="真實性與風格";w=6.1; ok=($hasAud -or $hasKey);     part=@{ "MuQ 真實距離"=$hasAud; "Gemini 曲風"=$hasKey } }
  @{n="整體音樂性"; w=5.1;  ok=($hasKey -or $sev);        part=@{ "Gemini 總評"=$hasKey; "SongEval 音樂性"=$sev } }
  @{n="律動";       w=4.0;  ok=$hasKey;                   part=@{ "Gemini 節奏(唯一來源)"=$hasKey } }
)
Write-Host ""
Write-Host "      柱             權重    狀態" -ForegroundColor DarkGray
Write-Host "      ────────────────────────────────────────────────────────" -ForegroundColor DarkGray
$lost = 0.0
foreach ($p in $pillars) {
    $missing = @($p.part.Keys | Where-Object { -not $p.part[$_] })
    if ($p.ok -and $missing.Count -eq 0) { $mark = "完整"; $col = "Green" }
    elseif ($p.ok)                       { $mark = "部分 —— 缺 $($missing -join '、')"; $col = "Yellow" }
    else                                 { $mark = "缺項(整柱不計)"; $col = "Red"; $lost += $p.w }
    # 中文字寬 2:自己補空白才對得齊(PowerShell 的 -f 只算字元數)
    $pad = " " * [Math]::Max(1, 15 - ($p.n.Length * 2))
    Write-Host ("      {0}{1}{2,-7} " -f $p.n, $pad, ("{0:0.0}%" -f $p.w)) -NoNewline
    Write-Host $mark -ForegroundColor $col
}

Write-Host ""
# ⚠️ 外層與內層 Where-Object 都會佔用 $_ → 外層先接成 $p,不然 $_.part[$_] 會取錯東西
$partial = @($pillars | Where-Object { $p = $_; @($p.part.Keys | Where-Object { -not $p.part[$_] }).Count -gt 0 })
if ($lost -gt 0) {
    Write-Host "      ⛔ 安裝不完整 —— 有 $lost% 的權重整根缺席,這台機器目前【評不出有效分數】。" -ForegroundColor Red
    Write-Host "         九柱制的滿分定義是九根柱子都在;少一根就是換了一把尺," -ForegroundColor Red
    Write-Host "         算出來的分數不可與別人互比、不可拿去排行。" -ForegroundColor Red
    # ⚠️ 要講出**這一台**缺的真正原因。舊版一律寫「多半是網路問題,重跑就會補上」——
    #    但沒填金鑰的人重跑一百次也不會好,那句話會把人帶去錯的方向。
    if (-not $hasKey) {
        Write-Host "         → 你缺的是 Gemini 金鑰(律動柱 100% 靠它,其餘五柱各缺一項)。" -ForegroundColor Yellow
        Write-Host "           ⛔ 重跑安裝檔沒有用 —— 去 https://aistudio.google.com/apikey 申請(免費)," -ForegroundColor Yellow
        Write-Host "           把 .env.example 複製成 .env,填 GEMINI_API_KEYS=你的金鑰,再跑 -CheckOnly 確認。" -ForegroundColor Yellow
    } else {
        Write-Host "         → 把上面紅字的部分補起來再評(多半是網路問題,重跑這個安裝檔就會補上)。" -ForegroundColor Yellow
    }
} elseif ($partial.Count -gt 0) {
    Write-Host "      ⚠️ 九根柱子都算得出分,但有柱子缺細項(上面黃字)——" -ForegroundColor Yellow
    Write-Host "         柱內會重新歸一化,分數出得來但與完整安裝的結果有落差,建議補齊。" -ForegroundColor Yellow
} elseif (-not $hasFfmpeg) {
    Write-Host "      ⛔ 缺 ffmpeg —— 一般 WAV(4 分鐘 PCM ≈ 40MB)超過 Gemini 內嵌上限," -ForegroundColor Red
    Write-Host "         沒有它轉檔,評 WAV 時 Gemini 餵的六個柱項全缺 → 不算完整安裝。" -ForegroundColor Red
    Write-Host "         → winget install Gyan.FFmpeg(或手動裝好加入 PATH)後重跑 -CheckOnly。" -ForegroundColor Yellow
} elseif ($script:KeyUnverified) {
    # ⛔ 「組件都在」≠「驗證通過」:金鑰 429/網路問題時不可宣稱九柱齊全(Codex R12)
    Write-Host "      ⚠️ 九柱組件都在,但 Gemini 金鑰有效性【未能驗證】(限流/網路/TLS)——" -ForegroundColor Yellow
    Write-Host "         不給完整綠燈。等網路/限流恢復後重跑 -CheckOnly 拿驗證通過的結論。" -ForegroundColor Yellow
} else {
    Write-Host "      ✅ 九柱齊全、細項無缺 —— 這才是可以拿來評分的完整安裝。" -ForegroundColor Green
}
if (-not $hasFfmpeg -and $lost -eq 0) {
    Bad "ffmpeg 沒裝" "一般 WAV 超過 Gemini 內嵌上限,沒它轉檔會評不完整(視為安裝未完成)"
}

# ── 冒煙測試 ────────────────────────────────────────────────────────
if ($hasEnv) {
    Write-Host "`n      跑一首內建測試音(確認量測管線真的活著)..." -ForegroundColor DarkGray
    $env:PYTHONUTF8 = "1"
    # ⛔ 不可以只 grep 顯示文字:那樣既看不出程式是不是真的成功(退出碼),
    #    失敗時又完全查不到原因(Codex 實測遇過一次「冒煙測試沒過」但立刻重跑就過,
    #    因為沒印原始輸出,根本無從追查)。改成:看退出碼 + 解析 JSON + 失敗印輸出。
    # ⛔ 同一個視窗重跑時 $PID 不變 → 一定要先刪,否則可能收到上一次的舊產物而誤判成功
    $smokeJson = Join-Path $env:TEMP "song_jury_smoke_$PID.json"
    Remove-Item $smokeJson -EA SilentlyContinue
    $out = & .venv\Scripts\python.exe song_scorer.py demo_mix.wav --json $smokeJson 2>&1 | Out-String
    $rc = $LASTEXITCODE
    $script:SmokeOk = $false
    if ($rc -ne 0) {
        Bad "冒煙測試沒過(退出碼 $rc)" "量測管線有問題"
        Write-Host ("      ↳ 原始輸出尾段:`n" + (($out -split "`n" | Select-Object -Last 12) -join "`n")) -ForegroundColor DarkGray
    } elseif (-not (Test-Path $smokeJson)) {
        Bad "冒煙測試沒產出 JSON" "程式回報成功卻沒寫檔"
        Write-Host ("      ↳ 原始輸出尾段:`n" + (($out -split "`n" | Select-Object -Last 12) -join "`n")) -ForegroundColor DarkGray
    } else {
        try {
            $j = Get-Content $smokeJson -Raw -Encoding UTF8 | ConvertFrom-Json
            # ⛔ 不可以只檢查「非空」:字串 "N/A"、NaN、負數、超過 100 都代表產出不對
            $tot = $j.scores.total
            $isNum = ($tot -is [double] -or $tot -is [int] -or $tot -is [decimal])
            # ⛔ 不可用 .NET Core 才有的「Double 有限性單一方法」:PowerShell 5.1
            #    (.NET Framework)沒有 → 方法不存在直接拋例外,被 catch 誤報成
            #    「JSON 讀不了」,完整安裝也永遠 exit 1(Codex R10 實測)。
            #    改用 5.1 也有的 IsNaN/IsInfinity(test_packaging 有內容檢查釘住)。
            if ($isNum -and -not [double]::IsNaN([double]$tot) -and -not [double]::IsInfinity([double]$tot) `
                -and [double]$tot -ge 0 -and [double]$tot -le 100) {
                Ok "冒煙測試通過:總分 $tot / 100"; $script:SmokeOk = $true
            } else {
                Bad "冒煙測試的 scores.total 不是 0-100 的有限數字(拿到:$tot)" "產出格式不對"
            }
        } catch {
            Bad "冒煙測試的 JSON 讀不了" $_.Exception.Message
        }
        Remove-Item $smokeJson -EA SilentlyContinue
    }
} else {
    Bad "基礎環境 .venv 不可用" "連量測都跑不了,九柱全部評不出來"
    $script:SmokeOk = $false
}

# ── 完整驗證(-VerifyModels)────────────────────────────────────────
# ⛔ 安裝器的 import 檢查證明不了「權重下載得動、模型載入得了、推論跑得完」
#    (Codex R11:綠燈之後首次實跑才下載 2.86GiB)。這個開關把九柱真的跑一遍,
#    退出碼交給 評審團.py 的完整性契約(0=完整、2=缺柱)。
$script:VerifyOk = $true
if ($VerifyModels) {
    if ($hasEnv) {
        # <verify-block-start>  ⚠️ 這對標記給 tests/test_installer_order.py 抽取用
        # ⛔ 整段流程(實跑→裁判→清理)交給 完整驗證.py:PowerShell 對真的 Ctrl+C
        #    不可靠地進入 finally —— Codex R16 送 CTRL_C_EVENT 實測安裝器掛住、
        #    內外層 finally 都沒跑、verify_* 殘留。python 的 try/finally 是語言保證,
        #    而且中斷有明確退出碼(130),自動化分得出「失敗」與「使用者取消」。
        # ⚠️ 環境隔離也在 helper 裡用 subprocess env 做,不動這個 shell 的環境。
        & .venv\Scripts\python.exe 完整驗證.py
        $vrc = $LASTEXITCODE
        switch ($vrc) {
            0   { Ok "完整驗證通過:九柱實跑+獨立 JSON 解析都過(載入/推論驗證;模型權重可沿用既有快取)" }
            2   { Bad "完整驗證:評測跑完但缺柱(退出碼 2)" "缺柱清單見上面評審團的輸出"
                  $script:VerifyOk = $false }
            124 { Bad "完整驗證逾時(已中止整棵程序樹)" "首次下載模型可能不夠久 —— 設環境變數 SONG_JURY_VERIFY_TIMEOUT 加長再試"
                  # ⛔ 124/130 **原樣傳到最外層**,兩個平台一模一樣(Codex R17-2):
                  #    舊版只把 VerifyOk 設 false,最後被一般失敗洗成 1,於是
                  #    Windows 的自動化分不出「逾時 / 使用者取消 / 真的裝壞」,
                  #    Linux 卻分得出來 —— 同一個上層工具在兩邊要寫兩套邏輯。
                  Write-Host "  (退出碼 124:完整驗證逾時)" -ForegroundColor DarkGray
                  exit 124 }
            130 { Bad "完整驗證被使用者中斷(Ctrl+C)" "已中止並清理;要驗請重跑"
                  Write-Host "  (退出碼 130:使用者中斷)" -ForegroundColor DarkGray
                  exit 130 }
            default { Bad "完整驗證沒過(退出碼 $vrc)" "模型下載/載入/推論或 JSON 驗證其中一環失敗,原始輸出在上面"
                      $script:VerifyOk = $false }
        }
        # <verify-block-end>
    } else {
        Bad "-VerifyModels 需要 .venv 可用" "先完成安裝再驗"
        $script:VerifyOk = $false
    }
}

# ── 總結 ────────────────────────────────────────────────────────────
Write-Host "`n══════════════════════════════════════════════════" -ForegroundColor White
# ⚠️ 總結要跟上面的柱狀判定一致。舊版只看 Problems.Count,結果冷安裝實測出現
#    「⛔ 安裝不完整,評不出有效分數」與「安裝完成,沒有任何失敗項目」同時出現的自相矛盾。
#    「每一步都沒報錯」≠「裝好了」—— 沒填金鑰時每一步都會成功,但律動柱評不出來。
if ($script:Problems.Count -gt 0) {
    Write-Host "  有 $($script:Problems.Count) 項沒成功:" -ForegroundColor Yellow
    foreach ($p in $script:Problems) { Write-Host "    · $p" -ForegroundColor Yellow }
    Write-Host "`n  多數是網路問題,重跑一次這個安裝檔就會補上(已裝好的不會重裝)。" -ForegroundColor DarkGray
}
if ($lost -gt 0) {
    Write-Host "  ⛔ 尚未完成:九柱沒齊,現在還評不出有效分數(原因見上面的柱狀表)。" -ForegroundColor Red
} elseif ($script:Problems.Count -eq 0 -and $script:SmokeOk) {
    Write-Host "  ✅ 安裝完成,九柱齊全,可以開始評分了。" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ 安裝大致完成,但有項目沒過(見上面)。" -ForegroundColor Yellow
}
Write-Host @"

  接下來怎麼用:
    評一首歌   .venv\Scripts\python.exe 評審團.py "<SUNO 連結或音檔路徑>"
    網頁版     .\run_web.ps1
    詳細說明   README.md
"@ -ForegroundColor White
Write-Host "══════════════════════════════════════════════════`n" -ForegroundColor White
if ($Host.Name -eq "ConsoleHost" -and -not $CheckOnly) {
    try { Read-Host "按 Enter 關閉" | Out-Null } catch { }
}

# ⛔ 退出碼一定要反映結果:失敗項不為零、九柱沒齊、或冒煙測試沒過 → exit 1。
#    否則自動化/CI/包裝層看到 exit 0 會以為裝好了(Codex 實跑遇到 10 項失敗仍回 0)。
# ffmpeg 缺席也算未完成:一般 WAV 會評不完整(見自我檢查段);-VerifyModels 沒過同理
$failed = ($script:Problems.Count -gt 0) -or ($lost -gt 0) -or (-not $script:SmokeOk) -or (-not $hasFfmpeg) -or (-not $script:VerifyOk)
# ⛔ 金鑰政策錯誤要**原樣傳到最外層**(Codex R16-11):它被 Problems 洗成 1 的話,
#    自動化分不出「安裝沒裝好」與「安全設定壞了(去申請新 key 沒有用)」。
#    這一條要排在 $failed 之前 —— 政策壞掉時 Problems 一定非空。
if ($script:PolicyError) {
    Write-Host "  (退出碼 5:Gemini 金鑰政策無效 —— 修設定,不是重裝或換 key)" -ForegroundColor DarkGray
    exit 5
}
if ($failed) {
    Write-Host "  (退出碼 1:安裝未完全成功)" -ForegroundColor DarkGray
    exit 1
}
if ($script:KeyUnverified) {
    # ⛔ 組件齊但金鑰未能驗證(429/網路/TLS)→ 獨立退出碼 3,不冒充完整成功(Codex R12)
    Write-Host "  (退出碼 3:組件齊全,但金鑰有效性未能驗證 —— 恢復後重跑 -CheckOnly)" -ForegroundColor DarkGray
    exit 3
}
exit 0
