#!/usr/bin/env bash
# song-jury 地端網頁版啟動器(Linux / macOS)
#   前提:已跑過 install.sh(建好 .venv / .venv-ml);第三關詞評需要 Ollama。
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv-web/bin/python ]; then
  echo "首次使用:建立網頁環境(gradio)…"
  uv venv --python 3.11 .venv-web
  uv pip install --python .venv-web/bin/python -r requirements-web.txt
fi

if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "OK: Ollama 就緒,第三關詞評可在網頁直接產出。"
else
  echo "! 沒偵測到 Ollama(第三關詞評需要它):"
  echo "    1) 裝 Ollama:https://ollama.com"
  echo "    2) 拉一個模型:ollama pull qwen3"
  echo "  仍可先開網頁跑物理+美學+情感弧線;裝好 Ollama 再重開本頁,第三關詞評才會出現。"
fi

echo ""
echo "開啟網頁中…(Ctrl+C 停止伺服器)"
export PYTHONUTF8=1
exec .venv-web/bin/python app.py
