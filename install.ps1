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
#
#  設計原則:**任何一步失敗都不中斷整個安裝**。失敗的記下來,最後一次告訴你
#  哪幾根柱子會缺、怎麼補。半套能用,總比裝到一半炸掉什麼都沒有好。
# ══════════════════════════════════════════════════════════════════════
param([switch]$SkipML, [switch]$NoAutoTools, [switch]$CheckOnly)

$ROOT = $PSScriptRoot
Set-Location $ROOT
$ErrorActionPreference = "Continue"      # ⛔ 不用 Stop:單步失敗要能繼續往下走
$PSNativeCommandUseErrorActionPreference = $false

$TOTAL = if ($CheckOnly) { 1 } elseif ($SkipML) { 4 } else { 9 }
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
  中途可以去泡杯茶,失敗的部分最後會一次列給你。

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
$okFfmpeg = Ensure-Tool "ffmpeg" "Gyan.FFmpeg"      "YouTube 連結輸入會不可用(SUNO / 本機檔不受影響)" $false

if (-not $okUv) {
    Write-Host "`n✗ 沒有 uv 就無法繼續。請到 https://github.com/astral-sh/uv 手動安裝後重跑。" -ForegroundColor Red
    Read-Host "`n按 Enter 關閉"
    exit 1
}

# ── [2] 量測環境 ─────────────────────────────────────────────────────
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
    Step "情緒詞典 + Gemini 金鑰"
    if ($okVenv) {
        & .venv\Scripts\python.exe setup_nrcvad.py
        if ($LASTEXITCODE -ne 0) { Warn "NRC-VAD 詞典沒取到 → 情感弧線圖不可用(不計分,不影響總分)" }
        else { Ok "NRC-VAD 情緒詞典就緒" }
    }
} else {
    Step "略過模型安裝(-SkipML)"
    Warn "只有量測與報告可用;九柱中有六根會缺模型細項"
}

# ── Gemini 金鑰(互動輸入)───────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "  Gemini 曲評需要一把 API 金鑰(免費額度就夠用)。" -ForegroundColor White
    Write-Host "  申請:https://aistudio.google.com/apikey  ← 用 Google 帳號登入就能拿" -ForegroundColor DarkGray
    Write-Host "  沒有也能跑,但律動柱(4%)會整根缺,結構/旋律/人聲/整體/曲風各缺一項。" -ForegroundColor DarkGray
    $key = Read-Host "  貼上金鑰後按 Enter(直接按 Enter = 跳過,之後改 .env 也行)"
    if ($key.Trim()) {
        "GEMINI_API_KEYS=$($key.Trim())" | Out-File -FilePath ".env" -Encoding utf8 -NoNewline
        Ok "金鑰已寫入 .env(這個檔被 .gitignore 擋著,不會被上傳)"
    } else {
        # ⛔ 不複製 .env.example:那裡面的「你的第一把金鑰」是佔位字串,複製過去會被程式
        #    當成真金鑰拿去打 Google API,錯誤訊息還很難懂。乾脆不要有 .env。
        Warn "跳過金鑰 → 之後把 .env.example 複製成 .env 並填入 GEMINI_API_KEYS 即可"
    }
} else { Ok ".env 已存在,保留你原本的金鑰設定" }
}   # ← if (-not $CheckOnly) 結束:上面全是「安裝」,以下是「檢查」,-CheckOnly 直接跳到這

# ── [9] 自我檢查:哪幾根柱子真的能用 ────────────────────────────────
Step "自我檢查 —— 實際確認九根柱子哪些可用"

$hasEnv     = Test-Path ".venv\Scripts\python.exe"

# ⛔ 不自己猜 demucs 在哪 —— 問評審團.py 自己解析出來的那條路徑(唯一真理來源),
#    再實際 import 一次確認那個 python 真的有 demucs。猜的話會跟實際跑分不一致。
$hasDemucs = $false
if ($hasEnv) {
    $env:PYTHONUTF8 = "1"
    $demucsPy = (& .venv\Scripts\python.exe -c "import 評審團 as J; print(J.DEMUCS_PY)" 2>$null | Select-Object -Last 1)
    if ($demucsPy -and (Test-Path $demucsPy)) {
        & $demucsPy -c "import demucs" 2>$null
        $hasDemucs = ($LASTEXITCODE -eq 0)
    }
}
$hasMl      = Test-Path ".venv-ml\Scripts\python.exe"
$hasSongEval= Test-Path "SongEval\eval.py"
$hasAud     = Test-Path ".venv-audition\Scripts\python.exe"
# ⛔ 正則要錨在行首(多行模式),否則 `# GEMINI_API_KEY=...` 這種註解行也會被當成有金鑰;
#    也要排除 .env.example 的佔位字串。
$hasKey = $false
if (Test-Path ".env") {
    foreach ($line in (Get-Content ".env" -ErrorAction SilentlyContinue)) {
        if ($line -match '^\s*GEMINI_API_KEYS?\s*=\s*(\S.*)$') {
            $v = $Matches[1].Trim().Trim('"').Trim("'")
            if ($v -and $v -notmatch '你的.*金鑰' -and $v -notmatch '^(your|xxx|todo)') { $hasKey = $true }
        }
    }
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
    Write-Host "         → 把上面紅字的部分補起來再評(多半是網路問題,重跑這個安裝檔就會補上)。" -ForegroundColor Yellow
} elseif ($partial.Count -gt 0) {
    Write-Host "      ⚠️ 九根柱子都算得出分,但有柱子缺細項(上面黃字)——" -ForegroundColor Yellow
    Write-Host "         柱內會重新歸一化,分數出得來但與完整安裝的結果有落差,建議補齊。" -ForegroundColor Yellow
} else {
    Write-Host "      ✅ 九柱齊全、細項無缺 —— 這才是可以拿來評分的完整安裝。" -ForegroundColor Green
}

# ── 冒煙測試 ────────────────────────────────────────────────────────
if ($hasEnv) {
    Write-Host "`n      跑一首內建測試音(確認量測管線真的活著)..." -ForegroundColor DarkGray
    $env:PYTHONUTF8 = "1"
    $out = & .venv\Scripts\python.exe song_scorer.py demo_mix.wav 2>&1 | Out-String
    if ($out -match "總分") { Ok "冒煙測試通過:$((($out -split "`n" | Select-String '總分') | Select-Object -First 1).Line.Trim())" }
    else { Bad "冒煙測試沒過" "量測管線有問題,先看上面的錯誤訊息" }
}

# ── 總結 ────────────────────────────────────────────────────────────
Write-Host "`n══════════════════════════════════════════════════" -ForegroundColor White
if ($script:Problems.Count -eq 0) {
    Write-Host "  安裝完成,沒有任何失敗項目。" -ForegroundColor Green
} else {
    Write-Host "  安裝完成,但有 $($script:Problems.Count) 項沒成功:" -ForegroundColor Yellow
    foreach ($p in $script:Problems) { Write-Host "    · $p" -ForegroundColor Yellow }
    Write-Host "`n  多數是網路問題,重跑一次這個安裝檔就會補上(已裝好的不會重裝)。" -ForegroundColor DarkGray
}
Write-Host @"

  接下來怎麼用:
    評一首歌   .venv\Scripts\python.exe 評審團.py "<SUNO 連結或音檔路徑>"
    網頁版     .\run_web.ps1
    詳細說明   README.md
"@ -ForegroundColor White
Write-Host "══════════════════════════════════════════════════`n" -ForegroundColor White
if ($Host.Name -eq "ConsoleHost") { Read-Host "按 Enter 關閉" | Out-Null }
