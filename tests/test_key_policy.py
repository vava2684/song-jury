# -*- coding: utf-8 -*-
"""金鑰政策:驗證器與執行期必須看到**同一組**金鑰,產線隔離要是程式規則。

🔴 Codex R13 兩條:
· 驗證器只讀 .env、執行期還讀 process env → 驗 A 跑 B;
  `KEYS = A`(等號旁有空白)一邊讀得到一邊讀不到;multi 與 single 會被相加。
· 產線隔離只寫在註解裡,程式一行都沒執行 —— 別條產線的金鑰只要 export
  到環境就會被借走(這個專案真的把別人的付費額度打光過)。
"""
import pytest

from conftest import load

P = load("金鑰政策")
G = load("Gemini曲評")
K = load("金鑰驗證")

A = "AAAA" + "a" * 21
B = "BBBB" + "b" * 21
C = "CCCC" + "c" * 21


def _env(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


def test_驗證器與執行期看到同一組金鑰(tmp_path, monkeypatch):
    """🔴 探針:.env=A、process env=B/C → 驗證器驗 A、執行期用 B/C。
    兩邊都走 effective_keys 之後,結論必須一致。"""
    envp = _env(tmp_path, f"GEMINI_API_KEYS={A}")
    monkeypatch.setenv("GEMINI_API_KEYS", B)
    monkeypatch.setenv("GEMINI_API_KEY", C)
    monkeypatch.setattr(G, "ENV_FILE", envp)
    assert K.parse_keys(envp) == G.load_keys(), "🔴 驗的與跑的不是同一組金鑰"


def test_process環境的一般金鑰不被借用(tmp_path, monkeypatch):
    """⛔ 產線隔離:process env 的 GEMINI_API_KEY(S) 多半是別條產線 export 的,
    借了就是吃別人的付費額度。只有 .env 或專用變數才算數。"""
    envp = _env(tmp_path, f"GEMINI_API_KEYS={A}")
    monkeypatch.setenv("GEMINI_API_KEYS", B)
    keys, notes = P.effective_keys(envp)
    assert keys == [A], f"🔴 借用了環境變數裡別條產線的金鑰:{keys}"
    assert any("不被採用" in n for n in notes), "要講出來,不能默默忽略"


def test_專用變數最優先且可跨process(tmp_path, monkeypatch):
    envp = _env(tmp_path, f"GEMINI_API_KEYS={A}")
    monkeypatch.setenv(P.PRIMARY_ENV, f"{B},{C}")
    keys, _ = P.effective_keys(envp)
    assert keys == [B, C], "專用變數是明確指名給 song-jury 的,最優先"


def test_等號兩邊有空白也讀得到(tmp_path):
    """🔴 `GEMINI_API_KEYS = A`:執行期讀得到、驗證器讀不到 → 驗證形同虛設。"""
    assert P.effective_keys(_env(tmp_path, f"GEMINI_API_KEYS = {A}"))[0] == [A]


def test_多把存在時不可把單把也追加進來(tmp_path):
    """🔴 multi 與 single 同時存在時,舊碼把 single 追加進池 ——
    那把沒被驗過的金鑰會偷渡進真正的呼叫。只在多把缺席時才吃單把。"""
    envp = _env(tmp_path, f"GEMINI_API_KEYS={A}\nGEMINI_API_KEY={B}")
    assert P.effective_keys(envp)[0] == [A]
    envp2 = _env(tmp_path, f"GEMINI_API_KEY={B}")
    assert P.effective_keys(envp2)[0] == [B], "只有單把時才用它"


def test_拒絕名單用完整SHA256硬擋(tmp_path, monkeypatch):
    """⛔ 產線隔離要 fail-closed:把別條產線的金鑰指紋填進拒絕名單,
    它就永遠不會被這個工具拿去打 API。末四碼比對太弱,不採用。"""
    envp = _env(tmp_path, f"GEMINI_API_KEYS={A},{B}")
    monkeypatch.setenv(P.DENY_ENV, P.key_fingerprint(A))
    keys, notes = P.effective_keys(envp)
    assert keys == [B], f"🔴 拒絕名單裡的金鑰還是被放行了:{keys}"
    assert any("拒絕名單" in n for n in notes)
    assert all(A not in n for n in notes), "警告訊息不可以印出完整金鑰"


def test_拒絕名單也可以寫在env檔裡(tmp_path):
    envp = _env(tmp_path, f"GEMINI_API_KEYS={A}\n{P.DENY_ENV}={P.key_fingerprint(A)}")
    assert P.effective_keys(envp)[0] == []


def test_佔位字串與重複都要濾掉(tmp_path):
    envp = _env(tmp_path, f"GEMINI_API_KEYS=你的第一把金鑰,{A},{A},short")
    assert P.effective_keys(envp)[0] == [A]
