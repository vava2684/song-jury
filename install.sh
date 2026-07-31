#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  song-jury 一鍵安裝(Linux / macOS)
#
#  用法:
#    bash install.sh                完整安裝(自動補齊 uv/git/ffmpeg)
#    bash install.sh --skip-ml      只裝量測+報告(輕量,不含模型耳朵)
#    bash install.sh --no-auto-tools 不要自動幫我裝 uv/git/ffmpeg
#    bash install.sh --check-only   什麼都不裝,只檢查現在哪幾根柱子能用
#
#  設計原則:**任何一步失敗都不中斷整個安裝**。失敗的記下來,最後一次告訴你
#  哪幾根柱子會缺、怎麼補。半套能用,總比裝到一半炸掉什麼都沒有好。
# ══════════════════════════════════════════════════════════════════════
# ⛔ 刻意不用 -e:單步失敗要能繼續往下走
# ⛔ 也不用 -u:macOS 內建的 bash 3.2 在 set -u 下碰到空陣列(${#PROBLEMS[@]})會直接爆
set -o pipefail
cd "$(dirname "$0")"

SKIP_ML=0; NO_AUTO=0; CHECK_ONLY=0
for a in "$@"; do
  case "$a" in
    --skip-ml)        SKIP_ML=1 ;;
    --no-auto-tools)  NO_AUTO=1 ;;
    --check-only)     CHECK_ONLY=1 ;;
    *) echo "未知參數:$a"; exit 1 ;;
  esac
done

if   [ "$CHECK_ONLY" = 1 ]; then TOTAL=1
elif [ "$SKIP_ML"    = 1 ]; then TOTAL=4
else TOTAL=9; fi
N=0
PROBLEMS=()
C_CYAN='\033[36m'; C_GREEN='\033[32m'; C_YEL='\033[33m'; C_RED='\033[31m'; C_DIM='\033[90m'; C_OFF='\033[0m'

step() { N=$((N+1)); printf "\n${C_CYAN}[%d/%d] %s${C_OFF}\n" "$N" "$TOTAL" "$1"; }
ok()   { printf "      ${C_GREEN}OK  %s${C_OFF}\n" "$1"; }
warn() { printf "      ${C_YEL}!   %s${C_OFF}\n" "$1"; }
bad()  { printf "      ${C_RED}X   %s${C_OFF}\n" "$1"; PROBLEMS+=("$1 —— $2"); }
have() { command -v "$1" >/dev/null 2>&1; }

# 跑一步,炸了就記下來繼續
try_step() {
  local what="$1"; shift
  if "$@" >/tmp/_sj_step.log 2>&1; then return 0; fi
  bad "$what" "$(tail -n 2 /tmp/_sj_step.log | tr '\n' ' ')"
  return 1
}

if [ "$CHECK_ONLY" = 0 ]; then
cat <<'BANNER'

  ╔══════════════════════════════════════════════╗
  ║   song-jury 歌曲評審團 · 安裝程式            ║
  ╚══════════════════════════════════════════════╝
  這會下載數 GB 的模型,依網速大約 15～60 分鐘。
  中途可以去泡杯茶,失敗的部分最後會一次列給你。

BANNER

# ── [1] 基本工具 ─────────────────────────────────────────────────────
step "檢查並補齊基本工具(uv / git / ffmpeg)"
pkg_install() {   # $1=套件名;自動挑這台機器的套件管理員
  if   have brew;    then brew install "$1"
  elif have apt-get; then sudo apt-get update -qq && sudo apt-get install -y "$1"
  elif have dnf;     then sudo dnf install -y "$1"
  elif have pacman;  then sudo pacman -S --noconfirm "$1"
  else return 1; fi
}
ensure_tool() {   # $1=指令 $2=套件名 $3=沒有它會怎樣 $4=fatal?
  if have "$1"; then ok "$1 已就緒"; return 0; fi
  if [ "$NO_AUTO" = 1 ]; then
    [ "$4" = fatal ] && bad "$1 沒裝" "$3;請手動安裝後重跑" || warn "$1 沒裝 → $3"
    return 1
  fi
  printf "      ${C_DIM}... 沒有 %s,幫你裝(可能要輸入 sudo 密碼)${C_OFF}\n" "$1"
  if [ "$1" = uv ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  else
    pkg_install "$2" >/dev/null 2>&1
  fi
  if have "$1"; then ok "$1 安裝完成"; return 0; fi
  [ "$4" = fatal ] && bad "$1 自動安裝失敗" "$3;請手動裝好再重跑" || warn "$1 自動安裝失敗 → $3"
  return 1
}
ensure_tool uv     uv     "建立 Python 環境用,沒有它什麼都裝不了" fatal || { echo; printf "${C_RED}✗ 沒有 uv 就無法繼續:https://github.com/astral-sh/uv${C_OFF}\n"; exit 1; }
ensure_tool git    git    "取得 SongEval 原始碼用" fatal
ensure_tool ffmpeg ffmpeg "YouTube 連結輸入會不可用(SUNO / 本機檔不受影響)" soft

# ── [2] 量測環境 ─────────────────────────────────────────────────────
step "建立量測環境 .venv(響度/動態/頻譜/和弦/演唱量測 + 報告)"
if try_step ".venv 建立" uv venv --python 3.11 .venv \
   && try_step ".venv 套件安裝" uv pip install --python .venv/bin/python -r requirements.txt; then
  ok "量測與報告就緒"
fi

if [ "$SKIP_ML" = 0 ]; then
  # GPU 偵測(決定 torch 版本)
  # TORCH_IDX  = 裝 torch 本體時用(--index-url,獨佔那個索引,確保拿到對的 CUDA/CPU 版)
  # TORCH_XIDX = 裝整份 requirements 時用(--extra-index-url,PyPI 仍可用,numpy 之類才找得到)
  if have nvidia-smi; then
    TORCH_IDX=(--index-url https://download.pytorch.org/whl/cu124)
    TORCH_XIDX=(--extra-index-url https://download.pytorch.org/whl/cu124)
    ok "偵測到 NVIDIA GPU → 裝 CUDA 12.4 版 torch"
  elif [ "$(uname)" = "Darwin" ]; then
    TORCH_IDX=(); TORCH_XIDX=()
    ok "macOS → 用官方預設 wheel(Apple Silicon 走 MPS)"
  else
    TORCH_IDX=(--index-url https://download.pytorch.org/whl/cpu)
    TORCH_XIDX=(--extra-index-url https://download.pytorch.org/whl/cpu)
    warn "沒偵測到 NVIDIA GPU → 裝 CPU 版 torch(能跑,但每首會慢很多)"
  fi

  # ── [3] 模型環境 ────────────────────────────────────────────────
  step "建立模型環境 .venv-ml(SongEval + Audiobox)—— 這步最久,會下載數 GB"
  PY_ML=".venv-ml/bin/python"
  try_step ".venv-ml 建立" uv venv --python 3.11 .venv-ml \
    && try_step "torch 安裝" uv pip install --python "$PY_ML" torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 "${TORCH_IDX[@]}" \
    && try_step ".venv-ml 套件安裝" uv pip install --python "$PY_ML" -r requirements-ml.txt

  # ── [4] SongEval 原始碼 ─────────────────────────────────────────
  step "取得 SongEval 原始碼(CC BY-NC-SA 授權,不隨本專案散布)"
  if [ ! -f SongEval/eval.py ]; then
    try_step "SongEval clone" git clone --depth 1 https://github.com/ASLP-lab/SongEval.git SongEval
    [ -f SongEval/requirements.txt ] && try_step "SongEval 依賴" uv pip install --python "$PY_ML" -r SongEval/requirements.txt
  fi
  if [ -f SongEval/eval.py ]; then ok "SongEval 就緒"
  else bad "SongEval 取得失敗" "五個模型聽感細項會缺(連貫/記憶點/結構清晰/人聲自然/音樂性)"; fi

  # SongEval 的 requirements 常把 torch 換掉 → 鎖回來,否則 torchaudio 載入失敗
  step "鎖回 torch 版本(SongEval 的依賴常把它換掉,這步是修回來)"
  try_step "torch 鎖版" uv pip install --python "$PY_ML" torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0 "${TORCH_IDX[@]}" \
    && ok "torch 鎖定 2.6.0"

  # ── [6] 分軌環境 ────────────────────────────────────────────────
  step "建立分軌環境 .venv-demucs(結構編曲柱 + 和聲柱都吃它,合計 26.2% 權重)"
  if [ -n "${SONG_JURY_DEMUCS_PY:-}" ] && [ -x "${SONG_JURY_DEMUCS_PY}" ]; then
    ok "你已用 SONG_JURY_DEMUCS_PY 指定現成的 demucs,跳過"
  # ⛔ 索引由這裡傳(macOS 時 TORCH_IDX 是空陣列 → 走官方 PyPI):requirements 檔裡寫死 cu124
  #    會讓 Mac 必失敗、沒 GPU 的人白載 2.5GB。torch 先明確裝一次拿到對的版本,再裝其餘;
  #    第二道用 unsafe-best-match,否則 numpy 這類套件會卡在 uv 的 first-index 策略上。
  elif try_step ".venv-demucs 建立" uv venv --python 3.11 .venv-demucs \
       && try_step "demucs 的 torch" uv pip install --python .venv-demucs/bin/python torch==2.6.0 torchaudio==2.6.0 "${TORCH_IDX[@]}" \
       && try_step "demucs 安裝" uv pip install --python .venv-demucs/bin/python -r requirements-demucs.txt "${TORCH_XIDX[@]}" --index-strategy unsafe-best-match; then
    ok "Demucs 六軌分離就緒(模型權重首次分離時自動下載,約 300MB)"
  else
    bad "Demucs 安裝失敗" "結構編曲柱與和聲柱會缺項,總分失真"
  fi

  # ── [7] 新耳朵環境 ──────────────────────────────────────────────
  step "建立新耳朵環境 .venv-audition(SingMOS 演唱聽感 + MuQ 真實距離 + SONICS AI 感)"
  PY_A=".venv-audition/bin/python"
  if try_step ".venv-audition 建立" uv venv --python 3.11 .venv-audition \
     && try_step "新耳朵的 torch" uv pip install --python "$PY_A" torch==2.6.0 torchaudio==2.6.0 "${TORCH_IDX[@]}" \
     && try_step ".venv-audition 套件安裝" uv pip install --python "$PY_A" -r requirements-audition.txt "${TORCH_XIDX[@]}" --index-strategy unsafe-best-match; then
    try_step "SONICS 安裝" uv pip install --python "$PY_A" "git+https://github.com/awsaf49/sonics.git" \
      || warn "SONICS 裝不起來 → AI 感只是顯示軸,不影響計分"
    ok ".venv-audition 完成(模型權重首次執行時下載,約 3GB)"
  else
    bad ".venv-audition 失敗" "人聲柱的 SingMOS 與真實風格柱會缺項"
  fi

  # ── [8] 詞典 ────────────────────────────────────────────────────
  step "情緒詞典(情感弧線用;禁再散布,自官方源代取)"
  if [ -x .venv/bin/python ] && .venv/bin/python setup_nrcvad.py >/dev/null 2>&1; then
    ok "NRC-VAD 情緒詞典就緒"
  else
    warn "NRC-VAD 詞典沒取到 → 情感弧線圖不可用(不計分,不影響總分)"
  fi
else
  step "略過模型安裝(--skip-ml)"
  warn "只有量測與報告可用;九柱中有六根會缺模型細項"
fi

# ── Gemini 金鑰(互動輸入)───────────────────────────────────────────
if [ ! -f .env ]; then
  echo
  echo "  Gemini 曲評需要一把 API 金鑰(免費額度就夠用)。"
  printf "  ${C_DIM}申請:https://aistudio.google.com/apikey  ← 用 Google 帳號登入就能拿${C_OFF}\n"
  printf "  ${C_DIM}沒有也能跑,但律動柱(4%%)會整根缺,結構/旋律/人聲/整體/曲風各缺一項。${C_OFF}\n"
  read -r -p "  貼上金鑰後按 Enter(直接按 Enter = 跳過,之後改 .env 也行):" KEY
  if [ -n "${KEY// /}" ]; then
    echo "GEMINI_API_KEYS=${KEY// /}" > .env
    ok "金鑰已寫入 .env(這個檔被 .gitignore 擋著,不會被上傳)"
  else
    cp .env.example .env 2>/dev/null || true
    warn "跳過金鑰 → 之後編輯 .env 填入 GEMINI_API_KEYS 即可"
  fi
else
  ok ".env 已存在,保留你原本的金鑰設定"
fi
fi   # ← CHECK_ONLY 結束:上面全是「安裝」,以下是「檢查」

# ── [9] 自我檢查:哪幾根柱子真的能用 ────────────────────────────────
step "自我檢查 —— 實際確認九根柱子哪些可用"
HAS_ENV=0;  [ -x .venv/bin/python ] && HAS_ENV=1
HAS_ML=0;   [ -x .venv-ml/bin/python ] && HAS_ML=1
HAS_SE=0;   [ -f SongEval/eval.py ] && [ "$HAS_ML" = 1 ] && HAS_SE=1
HAS_AUD=0;  [ -x .venv-audition/bin/python ] && HAS_AUD=1
# ⛔ 錨在行首(排除註解行),並排除 .env.example 的佔位字串
HAS_KEY=0
if [ -f .env ] && grep -qE '^[[:space:]]*GEMINI_API_KEYS?[[:space:]]*=[[:space:]]*[^[:space:]]' .env \
   && ! grep -qE '^[[:space:]]*GEMINI_API_KEYS?[[:space:]]*=[[:space:]]*.*(你的.*金鑰|^your|xxx)' .env; then
  HAS_KEY=1
fi

# ⛔ 不自己猜 demucs 在哪 —— 問評審團.py 自己解析出來的那條路徑(唯一真理來源),
#    再實際 import 一次確認那個 python 真的有 demucs。
HAS_DEMUCS=0
if [ "$HAS_ENV" = 1 ]; then
  DEMUCS_PY=$(PYTHONUTF8=1 .venv/bin/python -c "import 評審團 as J; print(J.DEMUCS_PY)" 2>/dev/null | tail -n 1)
  if [ -n "$DEMUCS_PY" ] && [ -x "$DEMUCS_PY" ] && "$DEMUCS_PY" -c "import demucs" >/dev/null 2>&1; then HAS_DEMUCS=1; fi
fi

LOST=0
echo
printf "      ${C_DIM}柱             權重    狀態${C_OFF}\n"
printf "      ${C_DIM}────────────────────────────────────────────────────────${C_OFF}\n"
# 名稱|權重|整柱是否成立|「細項名:是否有」以逗號分隔
row() {
  local name="$1" w="$2" okp="$3" parts="$4" missing="" mark col
  IFS=',' read -ra ps <<< "$parts"
  for p in "${ps[@]}"; do
    [ -z "$p" ] && continue
    if [ "${p##*:}" = 0 ]; then missing="${missing}${missing:+、}${p%%:*}"; fi
  done
  if [ "$okp" = 1 ] && [ -z "$missing" ]; then mark="完整"; col="$C_GREEN"
  elif [ "$okp" = 1 ];                   then mark="部分 —— 缺 $missing"; col="$C_YEL"
  else mark="缺項(整柱不計)"; col="$C_RED"; LOST=$(awk "BEGIN{print $LOST+$w}"); fi
  # ⚠️ 柱名的空白是「呼叫時就補好的」:${#字串} 在不同 locale 下有時數位元組、
  #    有時數字元,拿它算中文字寬會歪掉(git-bash 實測)。不猜,直接給對齊好的字串。
  printf "      %s%-7s ${col}%s${C_OFF}\n" "$name" "$w%" "$mark"
}
or1() { [ "$1" = 1 ] || [ "$2" = 1 ] && echo 1 || echo 0; }
and1() { [ "$1" = 1 ] && [ "$2" = 1 ] && echo 1 || echo 0; }
row "詞            " 25.3 1 ""
# ⚠️ 這張表必須跟 評審團.py 的 PILLAR_ITEMS 實際行為對得起來(已用乾淨 clone 實跑對照):
#    人聲柱的量測項吃 Demucs 人聲軌;結構編曲柱缺 demucs 只是少一半、不是整柱不計;
#    和聲柱六項全靠和弦辨識(吃分軌),缺 demucs 才真的整根消失。
row "人聲演唱      " 15.2 "$HAS_ENV" "演唱量測(需分軌):$HAS_DEMUCS,SingMOS 聽感:$HAS_AUD,SongEval 自然度:$HAS_SE,Gemini 人聲表現:$HAS_KEY"
row "和聲          " 13.6 "$(and1 "$HAS_ENV" "$HAS_DEMUCS")" ""
row "結構與編曲    " 12.6 "$(or1 "$HAS_DEMUCS" "$(or1 "$HAS_SE" "$HAS_KEY")")" "編曲量測(需分軌):$HAS_DEMUCS,SongEval 連貫:$HAS_SE,Gemini 結構/配器:$HAS_KEY"
row "聲學製作      " 12.1 "$HAS_ENV" "SongEval 清晰:$HAS_SE,Audiobox PQ:$HAS_ML"
row "旋律與記憶    " 6.1  "$(or1 "$HAS_KEY" "$HAS_SE")" "Gemini 旋律:$HAS_KEY,SongEval 記憶點:$HAS_SE"
row "真實性與風格  " 6.1  "$(or1 "$HAS_AUD" "$HAS_KEY")" "MuQ 真實距離:$HAS_AUD,Gemini 曲風:$HAS_KEY"
row "整體音樂性    " 5.1  "$(or1 "$HAS_KEY" "$HAS_SE")" "Gemini 總評:$HAS_KEY,SongEval 音樂性:$HAS_SE"
row "律動          " 4.0  "$HAS_KEY" "Gemini 節奏(唯一來源):$HAS_KEY"

echo
if [ "$(awk "BEGIN{print ($LOST>0)}")" = 1 ]; then
  printf "      ${C_YEL}⚠️ 有 %s%% 的權重整根缺席 —— 總分會在剩下的柱子間重新歸一化,${C_OFF}\n" "$LOST"
  printf "      ${C_YEL}   分數仍會出來,但與完整安裝的結果不可互比。${C_OFF}\n"
else
  printf "      ${C_GREEN}九根柱子都有東西可算。${C_OFF}\n"
fi

# ── 冒煙測試 ────────────────────────────────────────────────────────
if [ "$HAS_ENV" = 1 ]; then
  printf "\n      ${C_DIM}跑一首內建測試音(確認量測管線真的活著)...${C_OFF}\n"
  OUT=$(PYTHONUTF8=1 .venv/bin/python song_scorer.py demo_mix.wav 2>&1)
  if echo "$OUT" | grep -q "總分"; then ok "冒煙測試通過:$(echo "$OUT" | grep '總分' | head -n1 | tr -s ' ')"
  else bad "冒煙測試沒過" "量測管線有問題,先看上面的錯誤訊息"; fi
fi

# ── 總結 ────────────────────────────────────────────────────────────
printf "\n══════════════════════════════════════════════════\n"
if [ "${#PROBLEMS[@]}" -eq 0 ]; then
  printf "${C_GREEN}  安裝完成,沒有任何失敗項目。${C_OFF}\n"
else
  printf "${C_YEL}  安裝完成,但有 %d 項沒成功:${C_OFF}\n" "${#PROBLEMS[@]}"
  for p in "${PROBLEMS[@]}"; do printf "${C_YEL}    · %s${C_OFF}\n" "$p"; done
  printf "${C_DIM}\n  多數是網路問題,重跑一次這個安裝檔就會補上(已裝好的不會重裝)。${C_OFF}\n"
fi
cat <<'USAGE'

  接下來怎麼用:
    評一首歌   .venv/bin/python 評審團.py "<SUNO 連結或音檔路徑>"
    網頁版     bash run_web.sh
    詳細說明   README.md
══════════════════════════════════════════════════
USAGE
