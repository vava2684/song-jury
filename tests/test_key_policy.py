# -*- coding: utf-8 -*-
"""金鑰政策:驗證器與執行期必須看到**同一組**金鑰,產線隔離要是程式規則。

🔴 Codex R13 兩條:
· 驗證器只讀 .env、執行期還讀 process env → 驗 A 跑 B;
  `KEYS = A`(等號旁有空白)一邊讀得到一邊讀不到;multi 與 single 會被相加。
· 產線隔離只寫在註解裡,程式一行都沒執行 —— 別條產線的金鑰只要 export
  到環境就會被借走(這個專案真的把別人的付費額度打光過)。
"""
import sys

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
    assert K.parse_keys(envp)[0] == G.load_keys(), "🔴 驗的與跑的不是同一組金鑰"


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


# ── Codex R14:政策本身壞掉時必須 fail-closed ──────────────────────────

@pytest.mark.parametrize("壞名單", [
    "a" * 63,                       # 少一碼
    "z" * 64,                       # 64 個非 hex
    "g" * 64,
    P.key_fingerprint("x") + ",badtoken",   # 混一個壞的
])
def test_拒絕名單格式錯要fail_closed(tmp_path, monkeypatch, 壞名單):
    """🔴 Codex R14:打錯一碼時舊碼靜默放行(effective=1、notes=0)——
    使用者以為擋住了、其實沒有,那正是這個名單要防的事故。"""
    envp = _env(tmp_path, f"GEMINI_API_KEYS={A}")
    monkeypatch.setenv(P.DENY_ENV, 壞名單)
    keys, notes = P.effective_keys(envp)
    assert keys == [], f"🔴 政策壞掉卻照樣放行 {len(keys)} 把金鑰"
    assert any("政策無效" in n for n in notes), f"要講清楚原因:{notes}"


def test_env裡重複的拒絕名單要聯集不可被空值蓋掉(tmp_path):
    """🔴 Codex R14:一般 dotenv 是 last-one-wins,後面一行空值就把 hard deny
    無聲清掉了。安全敏感的設定要取所有非空值的聯集。"""
    envp = _env(tmp_path, f"GEMINI_API_KEYS={A}\n"
                          f"{P.DENY_ENV}={P.key_fingerprint(A)}\n"
                          f"{P.DENY_ENV}=\n")
    keys, notes = P.effective_keys(envp)
    assert keys == [], "🔴 後面的空值把前面的 hard deny 清掉了"
    assert any("拒絕名單" in n for n in notes)


def test_env是硬連結要拒絕(tmp_path):
    """🔴 Codex R14 實測:把 .env 做成指向 website-production.env 的 hardlink,
    金鑰照樣被採用 —— 產線隔離就這樣被繞過去。"""
    import os as _os
    other = tmp_path / "website-production.env"
    other.write_text(f"GEMINI_API_KEYS={B}", encoding="utf-8")
    envp = tmp_path / ".env"
    _os.link(other, envp)
    keys, notes = P.effective_keys(envp)
    assert keys == [], "🔴 硬連結到別條產線的秘密檔,金鑰還是被拿去用了"
    assert any("硬連結" in n for n in notes), f"要說清楚為什麼:{notes}"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建 symlink 需特權")
def test_env是符號連結要拒絕(tmp_path):
    other = tmp_path / "other.env"
    other.write_text(f"GEMINI_API_KEYS={B}", encoding="utf-8")
    envp = tmp_path / ".env"
    envp.symlink_to(other)
    keys, notes = P.effective_keys(envp)
    assert keys == []
    assert any("符號連結" in n for n in notes)


def test_父目錄是連結要拒絕(tmp_path):
    """🔴 Codex R15:只驗 leaf 還是能繞 —— 把**專案資料夾本身**做成指向另一條
    產線的 junction/symlink,.env 自己是普通單連結檔,一路綠燈。"""
    import os as _os
    real = tmp_path / "website-production"
    real.mkdir()
    (real / ".env").write_text(f"GEMINI_API_KEYS={B}", encoding="utf-8")
    link_dir = tmp_path / "song-jury-project"
    try:
        _os.symlink(real, link_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("這個環境不能建 directory symlink(Windows 需特權)")
    keys, notes = P.effective_keys(link_dir / ".env")
    assert keys == [], "🔴 父目錄指向別條產線,金鑰還是被拿去用了"
    assert any("上層目錄" in n for n in notes), f"要說清楚為什麼:{notes}"
