"""Partes do suporte ao Windows que dá para verificar em qualquer sistema."""
import sys
import xml.etree.ElementTree as ET

from sunhub import osutil, runtime, winsched

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_task_definition_is_valid_xml(env):
    xml = winsched._definition(15)
    root = ET.fromstring(xml.encode("utf-16"))
    assert root.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", NS).text == "PT15M"
    assert root.find("t:Settings/t:Hidden", NS).text == "true"
    exec_ = root.find("t:Actions/t:Exec", NS)
    # no Windows o agendador usa o pythonw.exe (sem janela de console); no Linux, o próprio python
    assert exec_.find("t:Command", NS).text == runtime.self_command(background=True)[0]
    assert exec_.find("t:Arguments", NS).text.endswith("sync --quiet")


def test_task_rewrite_only_when_needed(env):
    assert winsched.write_units(15) is True
    assert winsched.write_units(15) is False  # só a data de início mudaria
    assert winsched.write_units(30) is True


def test_background_command_uses_pythonw(tmp_path, monkeypatch, env):
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(osutil, "IS_WINDOWS", True)
    monkeypatch.setattr(sys, "executable", str(py))
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert runtime.self_command(background=True)[0] == str(tmp_path / "pythonw.exe")
    assert runtime.self_command()[0] == str(py)


def test_frozen_commands(tmp_path, monkeypatch, env):
    for name in ("Sunhub.exe", "sunhub-cli.exe"):
        (tmp_path / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Sunhub.exe"))
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert runtime.is_gui_exe()
    assert runtime.self_command(background=True)[0].endswith("Sunhub.exe")
    assert runtime.self_command()[0].endswith("sunhub-cli.exe")


def test_link_helpers(tmp_path):
    target = tmp_path / "alvo"
    target.mkdir()
    link = tmp_path / "link"
    osutil.make_dir_link(link, target)
    assert osutil.is_link(link) and osutil.same_path(osutil.link_target(link), target)
    osutil.remove_link(link)
    assert not link.exists() and target.exists()


def test_lock_is_exclusive(tmp_path):
    lock = tmp_path / "x.lock"
    with osutil.file_lock(lock):
        try:
            with osutil.file_lock(lock, blocking=False):
                raise AssertionError("deveria estar ocupado")
        except osutil.LockBusy:
            pass
