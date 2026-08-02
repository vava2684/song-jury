# -*- coding: utf-8 -*-
"""狀態檔的嚴格 schema —— ⛔ 它鬆掉的代價是「安裝器給出錯的修復指示」。

🔴 Codex R21-P2-2 實測:只驗 rc/ok 相符時,
   · 狀態檔寫 rc=4 卻寫 kind=config_error → 三個 shell 都採信,顯示「設定值有問題」;
   · `"recovered": "false"`(字串)在三個 shell 都是 truthy → 假的重試警告。
"""
import pytest

from conftest import load

S = load("狀態驗證")
OKJ = {"ok": True, "rc": 0, "kind": "ok"}


def test_正常成功可以採信():
    assert S.status_problem(OKJ, 0) == ""


@pytest.mark.parametrize("data,rc,hint", [
    ({**OKJ, "rc": 3}, 4, "rc"),                       # rc 對不上
    ({"ok": True, "rc": 4, "kind": "internal_error"}, 4, "ok"),   # ok 與 rc 不符
    ({"ok": False, "rc": 4, "kind": "config_error"}, 4, "kind"),  # kind↔rc 矛盾
    ({"ok": False, "rc": 1, "kind": "timeout"}, 1, "kind"),
    ({"ok": True, "rc": True, "kind": "ok"}, 1, "rc"),            # bool 不是 int
    ({"ok": "yes", "rc": 0, "kind": "ok"}, 0, "ok"),              # ok 不是布林
    # ⚠️ 期望字眼要指到**型別**:拿掉型別檢查後,字串 "false" 會落進
    #    「tries 不足」那條訊息,裡面照樣有 recovered 三個字(變異驗證抓到)
    ({**OKJ, "recovered": "false"}, 0, "不是布林"),
    ({**OKJ, "recovered": True, "tries": 1, "first_error": "x"}, 0, "tries"),
    ({**OKJ, "recovered": True, "tries": 2}, 0, "第一次的錯誤"),
    ({"ok": False, "rc": 4, "kind": "internal_error", "recovered": True,
      "tries": 2, "first_error": "x"}, 4, "成功"),
    ("不是物件", 0, "物件"),
])
def test_矛盾或型別不對一律不採信(data, rc, hint):
    why = S.status_problem(data, rc)
    assert why and hint in why, f"應該擋下({hint}):{why!r}"


def test_合法的重試成功可以採信():
    assert S.status_problem({**OKJ, "recovered": True, "tries": 2,
                             "first_error": "第一次 DLL 載入失敗"}, 0) == ""


def test_驗證器不可以是被驗的那支程式自己():
    """⛔ helper 自己出事(rc=4)時,再叫同一支去驗它寫的狀態檔只會拿到第二個未知狀態;
    測試也會用 stub 換掉 helper。所以驗證邏輯必須住在獨立的一支。"""
    from conftest import REPO as R
    for name in ("install.ps1", "install.sh"):
        src = (R / name).read_text(encoding="utf-8")
        assert "狀態驗證.py" in src, f"🔴 {name} 沒有用獨立的驗證器"
        assert "--check-status" not in src, "🔴 又回去叫被驗的那支自己驗了"
