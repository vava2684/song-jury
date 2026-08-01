#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  song-jury 一鍵安裝(Linux / macOS)
#
#  用法:
#    bash install.sh                完整安裝(自動補齊 uv/git/ffmpeg)
#    bash install.sh --skip-ml      只裝量測+報告(輕量,不含模型耳朵)
#    bash install.sh --no-auto-tools 不要自動幫我裝 uv/git/ffmpeg
#    bash install.sh --check-only   什麼都不裝,只檢查現在哪幾根柱子能用
#    bash install.sh --verify-models 自我檢查後再實跑一遍九柱(模型下載/載入/推論全來真的)
#
#  設計原則:**任何一步失敗都不中斷整個安裝**。失敗的記下來,最後一次告訴你
#  哪幾根柱子會缺、怎麼補。半套能用,總比裝到一半炸掉什麼都沒有好。
# ══════════════════════════════════════════════════════════════════════
# ⛔ 刻意不用 -e:單步失敗要能繼續往下走
# ⛔ 也不用 -u:macOS 內建的 bash 3.2 在 set -u 下碰到空陣列(${#PROBLEMS[@]})會直接爆
set -o pipefail
cd "$(dirname "$0")"

SKIP_ML=0; NO_AUTO=0; CHECK_ONLY=0; VERIFY_MODELS=0
for a in "$@"; do
  case "$a" in
    --skip-ml)        SKIP_ML=1 ;;
    --no-auto-tools)  NO_AUTO=1 ;;
    --check-only)     CHECK_ONLY=1 ;;
    --verify-models)  VERIFY_MODELS=1 ;;
    *) echo "未知參數:$a"; exit 1 ;;
  esac
done

# ⚠️ 步數要跟實際的 step 呼叫數一致(Codex R11:完整安裝最後印 [10/9])
if   [ "$CHECK_ONLY" = 1 ]; then TOTAL=1
elif [ "$SKIP_ML"    = 1 ]; then TOTAL=5
else TOTAL=10; fi
N=0
PROBLEMS=()
C_CYAN='\033[36m'; C_GREEN='\033[32m'; C_YEL='\033[33m'; C_RED='\033[31m'; C_DIM='\033[90m'; C_OFF='\033[0m'

step() { N=$((N+1)); printf "\n${C_CYAN}[%d/%d] %s${C_OFF}\n" "$N" "$TOTAL" "$1"; }
ok()   { printf "      ${C_GREEN}OK  %s${C_OFF}\n" "$1"; }
warn() { printf "      ${C_YEL}!   %s${C_OFF}\n" "$1"; }
bad()  { printf "      ${C_RED}X   %s${C_OFF}\n" "$1"; PROBLEMS+=("$1 —— $2"); }
have() { command -v "$1" >/dev/null 2>&1; }

# 跑一步,炸了就記下來繼續
# ⛔ log 不可用固定檔名(舊版共用一個 /tmp 下的固定 log):兩個安裝同時跑會互相
#    truncate,失敗原因顯示成別人那一步的;固定名稱也有 symlink 風險(Codex R10)。
#    mktemp 專屬檔 + trap 收尾。
SJ_STEP_LOG="$(mktemp "${TMPDIR:-/tmp}/sj_step.XXXXXX" 2>/dev/null)" || SJ_STEP_LOG="${TMPDIR:-/tmp}/sj_step_$$.log"
trap 'rm -f "$SJ_STEP_LOG"' EXIT
try_step() {
  local what="$1"; shift
  if "$@" >"$SJ_STEP_LOG" 2>&1; then return 0; fi
  bad "$what" "$(tail -n 2 "$SJ_STEP_LOG" | tr '\n' ' ')"
  return 1
}

if [ "$CHECK_ONLY" = 0 ]; then
cat <<'BANNER'

  ╔══════════════════════════════════════════════╗
  ║   song-jury 歌曲評審團 · 安裝程式            ║
  ╚══════════════════════════════════════════════╝
  這會下載數 GB 的模型,依網速大約 15～60 分鐘。
  ⚠️ 開頭會問你一個問題(Gemini 金鑰),回答完就可以放著不管 ——
     之後全程自動,失敗的部分最後會一次列給你。

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
# ⛔ ffmpeg 不是「只影響 YouTube」:Gemini 內嵌上限約 20MB(base64 後),一般 WAV
#    (4 分鐘 PCM ≈ 40MB)必超限,要靠它轉 320k;缺它=評 WAV 時 Gemini 六柱項全缺(Codex R10)
ensure_tool ffmpeg ffmpeg "Gemini 聽大檔(一般 WAV)要靠它轉檔;沒有它會評不完整 + YouTube 連結不可用" soft

# ── [2] Gemini 金鑰 ─────────────────────────────────────────────────
# ⚠️ 這一步**故意排在所有下載之前**:原本放在最後,而橫幅又叫使用者「中途可以去泡杯茶」——
#    人走開了,安裝就卡在 read 等輸入,回來才發現半小時原地不動。互動一律放最前面。
step "Gemini 金鑰(先問完,後面就可以放著讓它自己下載)"
if [ -f .env ]; then
  ok ".env 已存在,保留你原本的金鑰設定"
else
  echo
  printf "  ${C_YEL}⛔ 這一把是【必要】的,不是可選:${C_OFF}\n"
  printf "  ${C_YEL}   律動柱(4%%)100%% 靠 Gemini,沒有它那根柱子整根評不出來 →${C_OFF}\n"
  printf "  ${C_YEL}   依九柱制的定義,這台機器就【評不出有效分數】(另有五柱各缺一項)。${C_OFF}\n"
  echo  "  申請:https://aistudio.google.com/apikey  ← Google 帳號登入就能拿,免費額度夠用"
  # ⚠️ 非互動執行(CI、管線)時 read 會遇到 EOF 直接回非零,不可以讓它中斷安裝
  KEY=""
  read -r -p "  貼上金鑰後按 Enter(沒有的話直接按 Enter 先跳過,裝完再補):" KEY || KEY=""
  if [ -n "${KEY// /}" ]; then
    echo "GEMINI_API_KEYS=${KEY// /}" > .env
    ok "金鑰已寫入 .env(這個檔被 .gitignore 擋著,不會被上傳)"
  else
    # ⛔ 不複製 .env.example:裡面的「你的第一把金鑰」是佔位字串,
    #    複製過去會被當成三把真金鑰拿去打 Google API,錯誤訊息還很難懂。
    warn "跳過金鑰 → 裝完會顯示【評不出有效分數】;把 .env.example 複製成 .env 填進去即可"
  fi
fi

# ── [3] 量測環境 ─────────────────────────────────────────────────────
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

# (Gemini 金鑰已移到最前面 —— 見上方 [2] 步。互動一律放在下載之前。)
fi   # ← CHECK_ONLY 結束:上面全是「安裝」,以下是「檢查」

# ── [9] 自我檢查:哪幾根柱子真的能用 ────────────────────────────────
step "自我檢查 —— 實際確認九根柱子哪些可用"
# (HAS_ENV 在下面用真 import 判定,不是只看直譯器在不在)
# ⛔ 不能只看 python 在不在:`uv venv` 建完就有直譯器,套件裝失敗時環境是空的,
#    照樣會被判「完整」。一定要實際 import 關鍵套件才算數。
test_import() {   # $1=venv $2...=模組名
  local py="$1/bin/python"; shift
  [ -x "$py" ] || return 1
  for m in "$@"; do "$py" -c "import $m" >/dev/null 2>&1 || return 1; done
  return 0
}
# 基礎環境也要真檢查 —— 它撐著聲學、人聲量測與報告,最不能假
HAS_ENV=0;  test_import .venv librosa numpy soundfile pyloudnorm reportlab && HAS_ENV=1
HAS_ML=0;   test_import .venv-ml torch muq audiobox_aesthetics && HAS_ML=1
HAS_SE=0;   [ -f SongEval/eval.py ] && [ "$HAS_ML" = 1 ] && HAS_SE=1
HAS_AUD=0;  test_import .venv-audition torch s3prl muq && HAS_AUD=1
# ⛔ 這裡**故意不做前置正則判斷**(Codex R15):舊版先自己 grep .env 的
#    GEMINI_API_KEY(S),有中才呼叫共用驗證器 —— 政策正式支援的專用變數
#    SONG_JURY_GEMINI_API_KEYS(process env 或 .env)整個被漏掉:
#    runtime 會用那把 key,安裝器卻說「沒有金鑰、律動柱缺席」。
#    → 有 python 就無條件呼叫 金鑰驗證.py,由 effective_keys() 唯一決定。
HAS_KEY=0

# ⛔ ffmpeg 是完整安裝的必要件:一般 WAV 超過 Gemini 內嵌上限,靠它轉檔才評得了
HAS_FFMPEG=0; have ffmpeg && HAS_FFMPEG=1

# ⛔ 金鑰驗證交給 金鑰驗證.py(共用實作,逐把真打 Google、絕不只驗第一把)。
#    Codex R12:內嵌版只驗第一把(第一把好第二把壞=假陽性、反過來=假陰性),
#    而且 429/網路/TLS 全被洗成成功。三態:verified=綠燈資格;invalid=視同沒金鑰;
#    cooling/unknown=「未能驗證」→ 最後 exit 3(獨立退出碼,跟缺柱 exit 1 分開)。
KEY_UNVERIFIED=0
if [ -x .venv/bin/python ]; then PROBE_PY=.venv/bin/python
elif have python3; then PROBE_PY=python3
elif have python; then PROBE_PY=python
else PROBE_PY=""; fi
if [ -n "$PROBE_PY" ]; then
  PYTHONUTF8=1 "$PROBE_PY" 金鑰驗證.py .env
  case "$?" in
    0) HAS_KEY=1; ok "Gemini 金鑰驗證通過(逐把真打 Google;各把狀態見上)" ;;
    1) bad "Gemini 金鑰全部無效" "格式像金鑰但 Google 全不認;請到 https://aistudio.google.com/apikey 重新申請並填進 .env" ;;
    3) KEY_UNVERIFIED=1; HAS_KEY=1
       warn "金鑰有效性未能驗證(全部限流中或網路/TLS 問題)—— 不給完整綠燈;恢復後請重跑 --check-only" ;;
    4) warn "找不到可用金鑰(.env 沒填、只有佔位字串,或環境變數名字用錯)" ;;
    5) bad "Gemini 金鑰政策無效" "拒絕名單格式錯,或 .env 來源可疑(symlink/硬連結/父目錄連結)。⛔ 這不是「沒填金鑰」—— 去申請新 key 沒有用,請照上面的訊息修設定" ;;
    *) KEY_UNVERIFIED=1; HAS_KEY=1; warn "金鑰驗證工具異常 —— 不給完整綠燈" ;;
  esac
else
  KEY_UNVERIFIED=1
  warn "找不到任何 python 可跑金鑰驗證 —— 這台無法確認金鑰;裝完請重跑 --check-only"
fi

# ⛔ 不自己猜 demucs 在哪 —— 問評審團.py 自己解析出來的那條路徑(唯一真理來源),
#    再實際 import 一次確認那個 python 真的有 demucs。
HAS_DEMUCS=0
if [ "$HAS_ENV" = 1 ]; then
  DEMUCS_PY=$(PYTHONUTF8=1 .venv/bin/python -c "import 評審團 as J; print(J.DEMUCS_PY)" 2>/dev/null | tail -n 1)
  # ⛔ 整條線一起驗:和聲分析.py 也在這個環境跑,它要 librosa。只驗 demucs 的話,
  #    缺 librosa 時分軌成功、和聲柱(13.6%)整根降級,安裝器卻印「九柱齊全」(Codex R13)。
  if [ -n "$DEMUCS_PY" ] && [ -x "$DEMUCS_PY" ]; then
    if "$DEMUCS_PY" -c "import demucs, librosa, numpy, soundfile" >/dev/null 2>&1; then
      HAS_DEMUCS=1
    elif "$DEMUCS_PY" -c "import demucs" >/dev/null 2>&1; then
      bad "分軌環境缺依賴" "有 demucs 但缺 librosa/numpy/soundfile 其一 → 和聲柱會整根降級;請重跑安裝或 uv pip install -r requirements-demucs.txt"
    fi
  fi
fi

LOST=0
PARTIAL=0
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
  elif [ "$okp" = 1 ];                   then mark="部分 —— 缺 $missing"; col="$C_YEL"; PARTIAL=1
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
  printf "      ${C_RED}⛔ 安裝不完整 —— 有 %s%% 的權重整根缺席,這台機器目前【評不出有效分數】。${C_OFF}\n" "$LOST"
  printf "      ${C_RED}   九柱制的滿分定義是九根柱子都在;少一根就是換了一把尺,${C_OFF}\n"
  printf "      ${C_RED}   算出來的分數不可與別人互比、不可拿去排行。${C_OFF}\n"
  # ⚠️ 要講出這一台缺的真正原因:沒填金鑰的人重跑一百次也不會好
  if [ "$HAS_KEY" != 1 ]; then
    printf "      ${C_YEL}   → 你缺的是 Gemini 金鑰(律動柱 100%% 靠它,其餘五柱各缺一項)。${C_OFF}\n"
    printf "      ${C_YEL}     ⛔ 重跑安裝檔沒有用 —— 去 https://aistudio.google.com/apikey 申請(免費),${C_OFF}\n"
    printf "      ${C_YEL}     把 .env.example 複製成 .env 填 GEMINI_API_KEYS,再跑 --check-only 確認。${C_OFF}\n"
  else
    printf "      ${C_YEL}   → 把上面紅字的部分補起來再評(多半是網路問題,重跑這個安裝檔就會補上)。${C_OFF}\n"
  fi
elif [ "$PARTIAL" = 1 ]; then
  printf "      ${C_YEL}⚠️ 九根柱子都算得出分,但有柱子缺細項(上面黃字)——${C_OFF}\n"
  printf "      ${C_YEL}   柱內會重新歸一化,分數出得來但與完整安裝的結果有落差,建議補齊。${C_OFF}\n"
elif [ "$HAS_FFMPEG" != 1 ]; then
  printf "      ${C_RED}⛔ 缺 ffmpeg —— 一般 WAV(4 分鐘 PCM ≈ 40MB)超過 Gemini 內嵌上限,${C_OFF}\n"
  printf "      ${C_RED}   沒有它轉檔,評 WAV 時 Gemini 餵的六個柱項全缺 → 不算完整安裝。${C_OFF}\n"
  printf "      ${C_YEL}   → 用套件管理員裝 ffmpeg 後重跑 --check-only。${C_OFF}\n"
elif [ "$KEY_UNVERIFIED" = 1 ]; then
  # ⛔ 「組件都在」≠「驗證通過」:金鑰 429/網路問題時不可宣稱九柱齊全(Codex R12)
  printf "      ${C_YEL}⚠️ 九柱組件都在,但 Gemini 金鑰有效性【未能驗證】(限流/網路/TLS)——${C_OFF}
"
  printf "      ${C_YEL}   不給完整綠燈。等恢復後重跑 --check-only 拿驗證通過的結論。${C_OFF}
"
else
  printf "      ${C_GREEN}✅ 九柱齊全、細項無缺 —— 這才是可以拿來評分的完整安裝。${C_OFF}\n"
fi
if [ "$HAS_FFMPEG" != 1 ] && [ "$(awk "BEGIN{print ($LOST>0)}")" != 1 ]; then
  bad "ffmpeg 沒裝" "一般 WAV 超過 Gemini 內嵌上限,沒它轉檔會評不完整(視為安裝未完成)"
fi

# ── 冒煙測試 ────────────────────────────────────────────────────────
SMOKE_OK=0
if [ "$HAS_ENV" = 1 ]; then
  printf "\n      ${C_DIM}跑一首內建測試音(確認量測管線真的活著)...${C_OFF}\n"
  # ⛔ 不可以只 grep 顯示文字:看不出退出碼、失敗時也查不到原因。
  #    也不可以只判「非空」:"N/A"、None、NaN、999 都會被 [ -n ] 當成成功
  #    (Codex 抓到 install.sh 沒跟上 PowerShell 版的驗證)。
  _sj="${TMPDIR:-/tmp}/song_jury_smoke_$$.json"
  rm -f "$_sj"          # 先刪舊產物,免得誤收上一次的檔
  OUT=$(PYTHONUTF8=1 .venv/bin/python song_scorer.py demo_mix.wav --json "$_sj" 2>&1); RC=$?
  if [ "$RC" -ne 0 ]; then
    bad "冒煙測試沒過(退出碼 $RC)" "量測管線有問題"
    printf "      ${C_DIM}↳ 原始輸出尾段:\n%s${C_OFF}\n" "$(echo "$OUT" | tail -n 12)"
  elif [ ! -f "$_sj" ]; then
    bad "冒煙測試沒產出 JSON" "程式回報成功卻沒寫檔"
    printf "      ${C_DIM}↳ 原始輸出尾段:\n%s${C_OFF}\n" "$(echo "$OUT" | tail -n 12)"
  else
    # 與 install.ps1 同一套驗證:非 bool、有限數字、0-100,否則不算通過
    TOT=$(PYTHONUTF8=1 .venv/bin/python -c "
import json,math,sys
v=json.load(open(sys.argv[1],encoding='utf-8'))['scores']['total']
assert isinstance(v,(int,float)) and not isinstance(v,bool)
assert math.isfinite(v) and 0<=v<=100
print(v)" "$_sj" 2>/dev/null)
    if [ -n "$TOT" ]; then ok "冒煙測試通過:總分 $TOT / 100"; SMOKE_OK=1
    else bad "冒煙測試的 scores.total 不是 0-100 的有限數字" "產出格式不對"; fi
  fi
  rm -f "$_sj"          # 成功失敗都清,不留舊產物給下一輪誤收
else
  bad "基礎環境 .venv 不可用" "連量測都跑不了,九柱全部評不出來"
fi

# ── 完整驗證(--verify-models)──────────────────────────────────────
# ⛔ import 檢查證明不了「權重下載得動、模型載入得了、推論跑得完」
#    (Codex R11:綠燈之後首次實跑才下載 2.86GiB)。這個開關把九柱真的跑一遍,
#    退出碼交給 評審團.py 的完整性契約(0=完整、2=缺柱)。
VERIFY_OK=1
if [ "$VERIFY_MODELS" = 1 ]; then
  if [ "$HAS_ENV" = 1 ]; then
    # ⛔ 三件事缺一不可(Codex R12):
    #    ① 唯一檔名 verify_<id>.wav → 分軌快取鍵含檔名,強迫 Demucs 真的重新推論,
    #       不會沿用 demo_mix 的舊 stems;
    #    ② 子環境用 env -u 清掉 SKIP_GEMINI/TRUST_LEGACY_STEMS ——
    #       呼叫 shell 遺留的變數會讓驗證跳關或信任舊快取;也不動呼叫者的環境;
    #    ③ 不信 exit 0:用 驗證報告.py 獨立解析 JSON —— stub 寫個 {} 也騙不過。
    # <verify-block-start>  ⚠️ 這對標記給 tests/test_installer_order.py 抽取用
    VID="verify_$(date +%s)_$$"
    printf "
      ${C_DIM}--verify-models:實跑 評審團.py ${VID}.wav(唯一檔名,強迫全模型路徑;首次會下載數 GB)...${C_OFF}
"
    cp demo_mix.wav "${VID}.wav"
    V_EPOCH=$(date +%s)
    # ⛔ 清理用 trap:評測中途炸掉/Ctrl+C 時,已寫出的 _評分.json、_編曲層次.json、
    #    _和聲分析.json、_伴奏節奏軌.wav… 都會留在專案裡(Codex R13 故障注入實測)。
    #    清所有 $VID 前綴的產物,不是只清 wav 與最終報告。
    _sj_verify_cleanup() { rm -rf "${VID}"* _stems/"${VID}"*; }
    trap '_sj_verify_cleanup' EXIT INT TERM
    # ⛔ 外層 timeout(Codex R15):模型載入 deadlock 時直接跑會永遠掛著。
    #    用可殺整棵樹的 runner 包住;首次下載很久,預設給寬,可用
    #    SONG_JURY_VERIFY_TIMEOUT 調整。
    V_TIMEOUT="${SONG_JURY_VERIFY_TIMEOUT:-7200}"
    env -u SONG_JURY_SKIP_GEMINI -u SONG_JURY_TRUST_LEGACY_STEMS         PYTHONUTF8=1 .venv/bin/python -c '
import subprocess, sys
sys.path.insert(0, ".")
from 子程序 import run_tree
try:
    r = run_tree([sys.executable, "評審團.py", sys.argv[1]], timeout=float(sys.argv[2]))
    sys.stdout.write(r.stdout or ""); sys.stderr.write(r.stderr or "")
    sys.exit(r.returncode)
except subprocess.TimeoutExpired:
    print("⛔ 評審團逾時(已中止整棵程序樹)", file=sys.stderr)
    sys.exit(124)
' "${VID}.wav" "$V_TIMEOUT"
    VRC=$?
    if [ "$VRC" -eq 124 ]; then
      bad "完整驗證逾時(超過 ${V_TIMEOUT}s,已中止整棵程序樹)" "首次下載模型可能不夠久 —— 設 SONG_JURY_VERIFY_TIMEOUT 加長再試"; VERIFY_OK=0
    elif [ "$VRC" -eq 0 ]; then
      if PYTHONUTF8=1 .venv/bin/python 驗證報告.py "${VID}_評審團.json" --newer-than "$V_EPOCH"; then
        ok "完整驗證通過:九柱實跑+獨立 JSON 解析都過(載入/推論驗證;模型權重可沿用既有快取)"
      else
        bad "完整驗證:評審團回報成功但 JSON 驗不過(見上一行 VERIFY_BAD)" "退出碼契約與產出內容不一致,不可採信"
        VERIFY_OK=0
      fi
    elif [ "$VRC" -eq 2 ]; then
      bad "完整驗證:評測跑完但缺柱(退出碼 2)" "缺柱清單見上面評審團的輸出"; VERIFY_OK=0
    else
      bad "完整驗證沒過(退出碼 $VRC)" "模型下載/載入/推論其中一環失敗,原始輸出在上面"; VERIFY_OK=0
    fi
    _sj_verify_cleanup                       # 正常路徑也清一次
    trap 'rm -f "$SJ_STEP_LOG"' EXIT INT TERM   # 還原原本的 EXIT trap
    # <verify-block-end>
  else
    bad "--verify-models 需要 .venv 可用" "先完成安裝再驗"; VERIFY_OK=0
  fi
fi

# ── 總結 ────────────────────────────────────────────────────────────
printf "\n══════════════════════════════════════════════════\n"
# ⚠️ 總結要跟柱狀判定一致 ——「每一步都沒報錯」≠「裝好了」:
#    沒填金鑰時每一步都會成功,但律動柱評不出來(冷安裝實測撞到的自相矛盾)。
if [ "${#PROBLEMS[@]}" -gt 0 ]; then
  printf "${C_YEL}  有 %d 項沒成功:${C_OFF}\n" "${#PROBLEMS[@]}"
  for p in "${PROBLEMS[@]}"; do printf "${C_YEL}    · %s${C_OFF}\n" "$p"; done
  printf "${C_DIM}\n  多數是網路問題,重跑一次這個安裝檔就會補上(已裝好的不會重裝)。${C_OFF}\n"
fi
if [ "$(awk "BEGIN{print ($LOST>0)}")" = 1 ]; then
  printf "${C_RED}  ⛔ 尚未完成:九柱沒齊,現在還評不出有效分數(原因見上面的柱狀表)。${C_OFF}\n"
elif [ "${#PROBLEMS[@]}" -eq 0 ] && [ "$SMOKE_OK" = 1 ]; then
  printf "${C_GREEN}  ✅ 安裝完成,九柱齊全,可以開始評分了。${C_OFF}\n"
else
  printf "${C_YEL}  ⚠️ 安裝大致完成,但有項目沒過(見上面)。${C_OFF}\n"
fi
cat <<'USAGE'

  接下來怎麼用:
    評一首歌   .venv/bin/python 評審團.py "<SUNO 連結或音檔路徑>"
    網頁版     bash run_web.sh
    詳細說明   README.md
══════════════════════════════════════════════════
USAGE

# ⛔ 退出碼一定要反映結果:失敗項不為零、九柱沒齊、或冒煙測試沒過 → exit 1。
#    否則自動化/CI/包裝層看到 exit 0 會以為裝好了。
# ffmpeg 缺席也算未完成:一般 WAV 會評不完整(見自我檢查段);--verify-models 沒過同理
if [ "${#PROBLEMS[@]}" -gt 0 ] || [ "$(awk "BEGIN{print ($LOST>0)}")" = 1 ] || [ "$SMOKE_OK" != 1 ] || [ "$HAS_FFMPEG" != 1 ] || [ "$VERIFY_OK" != 1 ]; then
  printf "${C_DIM}  (退出碼 1:安裝未完全成功)${C_OFF}\n"
  exit 1
fi
if [ "$KEY_UNVERIFIED" = 1 ]; then
  # ⛔ 組件齊但金鑰未能驗證(429/網路/TLS)→ 獨立退出碼 3,不冒充完整成功(Codex R12)
  printf "${C_DIM}  (退出碼 3:組件齊全,但金鑰有效性未能驗證 —— 恢復後重跑 --check-only)${C_OFF}
"
  exit 3
fi
exit 0
