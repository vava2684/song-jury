# -*- coding: utf-8 -*-
"""狀態目錄.py:互斥域的地基。

🔴 Codex R11 三條:
· 相對的 SONG_JURY_STATE_DIR 會隨 cwd 漂移 → 兩個工作目錄各鎖各的,互斥域分裂。
· 覆寫指到普通檔案 → FileExistsError 原始 traceback 噴在保護層外。
· 共享目錄(0777)可預植 symlink 鎖檔,把鎖的寫入導到任意檔案。
"""
import os
import sys

import pytest

from conftest import load

S = load("狀態目錄")


def test_相對override要被拒絕(monkeypatch):
    monkeypatch.setenv("SONG_JURY_STATE_DIR", "relative-state")
    with pytest.raises(S.StateDirError):
        S.state_root()


def test_override指到普通檔案要講人話不可原始traceback(tmp_path, monkeypatch):
    f = tmp_path / "occupied"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SONG_JURY_STATE_DIR", str(f))
    with pytest.raises(S.StateDirError) as ei:
        S.state_root()
    assert str(f) in str(ei.value), "錯誤訊息要含真正的狀態路徑,使用者才修得動"


def test_絕對深路徑含中文與空格可以建(tmp_path, monkeypatch):
    d = tmp_path / "深 一層" / "中文 目錄" / "state"
    monkeypatch.setenv("SONG_JURY_STATE_DIR", str(d))
    assert S.state_root() == d
    assert d.is_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 權限語意")
def test_POSIX狀態目錄一律鎖0700(tmp_path, monkeypatch):
    """🔴 0777 共享目錄=任何人都能預植 symlink 鎖檔。固定 0700 + 驗擁有者。"""
    d = tmp_path / "state"
    d.mkdir(mode=0o777)
    monkeypatch.setenv("SONG_JURY_STATE_DIR", str(d))
    S.state_root()
    assert (os.stat(d).st_mode & 0o777) == 0o700


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink 語意")
def test_safe_open_lock拒開symlink且不碰目標(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("珍貴資料", encoding="utf-8")
    link = tmp_path / "job.lock"
    link.symlink_to(victim)
    with pytest.raises(OSError):
        S.safe_open_lock(link)
    assert victim.read_text(encoding="utf-8") == "珍貴資料"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink 語意")
def test_locks目錄本身是symlink要拒絕(tmp_path, monkeypatch):
    """🔴 Codex R12:只驗鎖檔不驗目錄 —— `_locks` 被換成指向外部的 symlink,
    鎖檔照樣被導出互斥域。目錄本身也要過同一套私人資料夾標準。"""
    root = tmp_path / "s"
    monkeypatch.setenv("SONG_JURY_STATE_DIR", str(root))
    S.state_root()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "_locks").symlink_to(outside, target_is_directory=True)
    with pytest.raises(S.StateDirError):
        S.locks_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink 語意")
def test_狀態根目錄是symlink要拒絕且不chmod到目標(tmp_path, monkeypatch):
    """🔴 Codex R12:STATE_DIR 指向 symlink 被接受,還把目標目錄 chmod 成 0700。"""
    target = tmp_path / "real"
    target.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("SONG_JURY_STATE_DIR", str(link))
    before = os.stat(target).st_mode & 0o777
    with pytest.raises(S.StateDirError):
        S.state_root()
    assert (os.stat(target).st_mode & 0o777) == before, \
        "🔴 symlink 目標目錄被 chmod 了 —— 動到別人的目錄"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX umask 語意")
def test_umask全開時建立當下就是0700沒有窗口(tmp_path, monkeypatch):
    """🔴 Codex R12:mkdir(預設)→chmod 之間有 0777 窗口可塞 symlink。
    mkdir(mode=0o700) 讓目錄從建立那一刻就 0700,窗口不存在。"""
    d = tmp_path / "s2"
    monkeypatch.setenv("SONG_JURY_STATE_DIR", str(d))
    old = os.umask(0)
    try:
        S.state_root()
    finally:
        os.umask(old)
    assert (os.stat(d).st_mode & 0o777) == 0o700


def test_鎖檔hardlink要拒絕且不碰目標(tmp_path):
    """🔴 Codex R12:O_NOFOLLOW 擋不住 hardlink —— job_<hash>.lock 硬連結到
    victim.txt,fstat 看到的仍是普通檔案,寫 pid 就把 victim 蓋了
    (實測 st_nlink=2、victim 變 pid=…)。link count 必須是 1。"""
    victim = tmp_path / "victim.txt"
    victim.write_text("KEEP-ME", encoding="utf-8")
    lock = tmp_path / "job.lock"
    os.link(victim, lock)                      # 跨平台:NTFS 也支援 hardlink
    with pytest.raises(OSError):
        S.safe_open_lock(lock)
    assert victim.read_text(encoding="utf-8") == "KEEP-ME", \
        "🔴 victim 被動到了 —— hardlink 繞過了鎖檔防線"


def test_safe_open_lock正常路徑可開可重開(tmp_path):
    p = tmp_path / "x.lock"
    f = S.safe_open_lock(p)
    f.close()
    f2 = S.safe_open_lock(p)          # 鎖檔永不刪 → 一定要能重複開
    f2.close()
    assert p.exists()
