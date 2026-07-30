#!/usr/bin/env bash
# song-jury 一鍵安裝(Linux / macOS)
#   用法: bash install.sh          完整安裝(含第二關 ML,會下載數 GB)
#         bash install.sh --skip-ml  只裝第一關+報告(輕量)
set -euo pipefail
cd "$(dirname "$0")"
SKIP_ML=0; [ "${1:-}" = "--skip-ml" ] && SKIP_ML=1
step() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  OK: %s\n' "$1"; }
warn() { printf '  ! %s\n' "$1"; }
die()  { printf '  X %s\n' "$1"; exit 1; }

step "檢查先決條件"
command -v uv  >/dev/null 2>&1 || die "找不到 uv。請先裝:https://github.com/astral-sh/uv"
command -v git >/dev/null 2>&1 || die "找不到 git。"
ok "uv / git 就緒"
if command -v ffmpeg >/dev/null 2>&1; then ok "ffmpeg 就緒(YouTube 輸入可用)"
else warn "沒有 ffmpeg → YouTube 連結輸入不可用(SUNO/本機檔不受影響)。裝法:apt install ffmpeg / brew install ffmpeg"; fi

step "第一關 + 報告工具(.venv)"
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
ok ".venv 完成"

if [ "$SKIP_ML" -eq 0 ]; then
  step "第二關 ML(.venv-ml) —— 會下載數 GB,請耐心"
  uv venv --python 3.11 .venv-ml
  PY=.venv-ml/bin/python
  if command -v nvidia-smi >/dev/null 2>&1; then
    ok "偵測到 NVIDIA GPU → CUDA 12.4 版 torch"; TORCH_ARGS="--index-url https://download.pytorch.org/whl/cu124"
  else
    warn "沒偵測到 NVIDIA GPU → CPU 版 torch(第二關會很慢;macOS 用預設 index)"; TORCH_ARGS=""
  fi
  uv pip install --python "$PY" torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 $TORCH_ARGS
  uv pip install --python "$PY" -r requirements-ml.txt

  step "SongEval(第二關 A;CC BY-NC-SA,自取)"
  if [ ! -f SongEval/eval.py ]; then
    git clone --depth 1 https://github.com/ASLP-lab/SongEval.git SongEval
    [ -f SongEval/requirements.txt ] && uv pip install --python "$PY" -r SongEval/requirements.txt || true
  fi
  [ -f SongEval/eval.py ] && ok "SongEval 就緒" || warn "SongEval clone 失敗,第二關 A 不可用"

  # SongEval/muq 的 requirements 常把 cu124 torch 換掉 → 最後鎖回一致版(否則 torchaudio 載入失敗)
  step "鎖定 torch 版本(最後一步)"
  uv pip install --python "$PY" torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 $TORCH_ARGS
  ok "torch 鎖定 2.6.0"

  step "新柱管線(.venv-audition)—— SingMOS 演唱聽感 + MuQ 真實距離 + SONICS AI 感"
  # ⚠️ 必須獨立第三個環境:這三個模型的 torch/transformers 版本與 .venv-ml 相衝,
  #    硬裝同一個環境會互相踩死(2026-07 實測結論)。缺這個環境 → 三個柱會永久缺項。
  uv venv --python 3.11 .venv-audition
  PYA=".venv-audition/bin/python"
  uv pip install --python "$PYA" -r requirements-audition.txt
  uv pip install --python "$PYA" "git+https://github.com/awsaf49/sonics.git" || \
    warn "SONICS 安裝失敗 → AI 感顯示軸不可用(不影響計分)"
  [ -x "$PYA" ] && ok ".venv-audition 完成(權重首次執行時自動下載,約 3GB)" || \
    warn ".venv-audition 建立失敗 → 人聲/真實風格/律動柱會缺項"

  step "NRC-VAD 情緒詞典(情感弧線用;禁再散布,自官方源代取)"
  .venv/bin/python setup_nrcvad.py || true
fi

step "驗證安裝(跑 demo)"
export PYTHONUTF8=1
if .venv/bin/python song_scorer.py demo_mix.wav 2>&1 | grep -q "總分"; then
  ok "demo 跑通:$(.venv/bin/python song_scorer.py demo_mix.wav 2>&1 | grep '總分' | tr -d ' ')"
  printf '\n安裝完成。用法見 README.md。\n'
else
  die "demo 驗證失敗。"
fi
