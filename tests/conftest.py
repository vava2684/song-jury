# -*- coding: utf-8 -*-
"""測試共用設定。

⚠️ 設計原則:**測試不可以依賴那四個重量級 venv**(torch / demucs / SongEval 合計十幾 GB)。
   CI 只裝標準庫 + numpy + pytest 就要能跑完。所以測的是「接線與邏輯」,
   不是模型推論本身 —— 而這個專案出過的 bug 幾乎全部都在接線與邏輯上。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def load(module_name: str):
    """用檔名載入 repo 根目錄的模組(檔名是中文,不能直接 import)。"""
    import importlib.util
    p = REPO / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = m
    spec.loader.exec_module(m)
    return m


import pytest


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path, monkeypatch):
    """每條測試都把全域狀態目錄(工作鎖/Gemini 冷卻)導到自己的 tmp。

    ⛔ 不隔離的話,鎖類測試會把鎖檔寫進使用者真正的 %LOCALAPPDATA%/song-jury
       (鎖檔設計上**永不刪**,測試一跑就在別人機器上留垃圾);
       子程序測試(_CHILD_HOLD)靠環境變數繼承,一樣被導進來。"""
    monkeypatch.setenv("SONG_JURY_STATE_DIR", str(tmp_path / "_sj_state"))
