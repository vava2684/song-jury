# -*- coding: utf-8 -*-
# song-jury 地端網頁版啟動器(Windows)
#   前提:已跑過 install.ps1(建好 .venv / .venv-ml);第三關詞評需要 Ollama。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 首次:自動建立網頁環境
if (-not (Test-Path ".venv-web\Scripts\python.exe")) {
    Write-Host "首次使用:建立網頁環境(gradio)…" -ForegroundColor Cyan
    uv venv --python 3.11 .venv-web
    uv pip install --python .venv-web\Scripts\python.exe -r requirements-web.txt
}

# 檢查 Ollama(第三關詞評用;必裝)
$hasOllama = $false
try { $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3; $hasOllama = $true } catch {}
if ($hasOllama) {
    Write-Host "OK: Ollama 就緒,第三關詞評可在網頁直接產出。" -ForegroundColor Green
} else {
    Write-Host "! 沒偵測到 Ollama(第三關詞評需要它):" -ForegroundColor Yellow
    Write-Host "    1) 裝 Ollama:https://ollama.com"
    Write-Host "    2) 拉一個模型:ollama pull qwen3"
    Write-Host "  仍可先開網頁跑物理+美學+情感弧線;裝好 Ollama 再重開本頁,第三關詞評才會出現。"
}

Write-Host "`n開啟網頁中…(關閉此視窗即停止伺服器)" -ForegroundColor Cyan
$env:PYTHONUTF8 = "1"
& .venv-web\Scripts\python.exe app.py
