# -*- coding: utf-8 -*-
# song-jury 一鍵安裝(Windows / PowerShell)
#   用法: ./install.ps1          完整安裝(含第二關 ML,會下載數 GB)
#         ./install.ps1 -SkipML  只裝第一關+報告(輕量,不含 SongEval/Audiobox)
param([switch]$SkipML)
$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot
Set-Location $ROOT
function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK: $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "  X $m" -ForegroundColor Red; exit 1 }

Step "檢查先決條件"
if (-not (Get-Command uv  -ErrorAction SilentlyContinue)) { Die "找不到 uv。請先裝:https://github.com/astral-sh/uv" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "找不到 git。" }
Ok "uv / git 就緒"
$hasFfmpeg = [bool](Get-Command ffmpeg -ErrorAction SilentlyContinue)
if ($hasFfmpeg) { Ok "ffmpeg 就緒(YouTube 輸入可用)" } else { Warn "沒有 ffmpeg → YouTube 連結輸入不可用(SUNO/本機檔不受影響)。裝法:winget install ffmpeg 或 https://ffmpeg.org" }

Step "第一關 + 報告工具(.venv)"
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Ok ".venv 完成"

if (-not $SkipML) {
    Step "第二關 ML(.venv-ml) —— 會下載數 GB,請耐心"
    uv venv --python 3.11 .venv-ml
    $py = ".venv-ml\Scripts\python.exe"
    $cuda = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    if ($cuda) { $torchIdx = "https://download.pytorch.org/whl/cu124"; Ok "偵測到 NVIDIA GPU → CUDA 12.4 版 torch" }
    else       { $torchIdx = "https://download.pytorch.org/whl/cpu";   Warn "沒偵測到 NVIDIA GPU → CPU 版 torch(第二關會很慢)" }
    uv pip install --python $py torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 --index-url $torchIdx
    uv pip install --python $py -r requirements-ml.txt

    Step "SongEval(第二關 A;CC BY-NC-SA,自取)"
    if (-not (Test-Path "SongEval\eval.py")) {
        git clone --depth 1 https://github.com/ASLP-lab/SongEval.git SongEval
        if (Test-Path "SongEval\requirements.txt") { uv pip install --python $py -r SongEval\requirements.txt }
    }
    if (Test-Path "SongEval\eval.py") { Ok "SongEval 就緒" } else { Warn "SongEval clone 失敗,第二關 A 不可用" }

    # ⚠️ SongEval/muq 的 requirements 常把 cu124 torch 換成別版 → 最後鎖回一致版(否則 torchaudio 載入會 WinError 127)
    Step "鎖定 torch 版本(最後一步)"
    uv pip install --python $py torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 --index-url $torchIdx
    Ok "torch 鎖定 2.6.0($(if($cuda){'cu124'}else{'cpu'}))"

    Step "新柱管線(.venv-audition)—— SingMOS 演唱聽感 + MuQ 真實距離 + SONICS AI 感"
    # ⚠️ 必須獨立第三個環境:這三個模型的 torch/transformers 版本與 .venv-ml 相衝,
    #    硬裝同一個環境會互相踩死(2026-07 實測結論)。缺這個環境 → 三個柱會永久缺項。
    uv venv --python 3.11 .venv-audition
    $pyA = ".venv-audition\Scripts\python.exe"
    uv pip install --python $pyA -r requirements-audition.txt
    uv pip install --python $pyA "git+https://github.com/awsaf49/sonics.git"
    if (Test-Path $pyA) { Ok ".venv-audition 完成(權重首次執行時自動下載,約 3GB)" }
    else { Warn ".venv-audition 建立失敗 → 人聲/真實風格/律動柱會缺項" }

    Step "NRC-VAD 情緒詞典(情感弧線用;禁再散布,自官方源代取)"
    & .venv\Scripts\python.exe setup_nrcvad.py
}

Step "驗證安裝(跑 demo)"
$env:PYTHONUTF8 = "1"
$out = & .venv\Scripts\python.exe song_scorer.py demo_mix.wav 2>&1 | Out-String
if ($out -match "總分") {
    Ok "demo 跑通:$(( $out -split "`n" | Select-String '總分').Line.Trim())"
    Write-Host "`n安裝完成。用法見 README.md。" -ForegroundColor Green
} else {
    Die "demo 驗證失敗,輸出:`n$out"
}
