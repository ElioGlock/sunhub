"""Smoke test do app de desktop (renderização offscreen, sem tela)."""
import os

import pytest

from sunhub import osutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication
except ImportError as e:  # bibliotecas gráficas do sistema ausentes (ex.: servidor sem libEGL)
    pytest.skip(f"Qt indisponível: {e}", allow_module_level=True)

from sunhub.desktop.app import MainWindow, create_app  # noqa: E402


def _wait(app):
    for _ in range(200):
        QThreadPool.globalInstance().waitForDone(50)
        app.processEvents()
        if QThreadPool.globalInstance().activeThreadCount() == 0:
            app.processEvents()
            return


def test_wizard_to_sources_explore_integrations(knowledge_repo, env):
    app = create_app([])
    win = MainWindow()
    assert win.stack.currentWidget() is win.wizard
    win.wizard.urls.setPlainText(knowledge_repo.as_uri())
    win.wizard.timer.setChecked(False)
    for cb in win.wizard.client_checks.values():
        cb.setChecked(False)
    win.wizard.submit()
    assert win.busy
    _wait(app)
    assert not win.busy
    assert win.stack.currentWidget() is win.sources
    assert "1 fonte(s) vinculada(s)" in win.flash.text()
    assert osutil.is_link(env / ".copilot/skills/api-standards")

    win.go("explore")
    win.explore.q.setText("cursor")
    win.explore.search()
    assert win.explore.results.count() >= 1
    assert win.explore.ref.text() == "ADR-014"
    assert "cursor opaco" in win.explore.view.toPlainText()

    win.go("integrations")
    win.integrations.run_doctor()
    _wait(app)
    assert win.integrations._checks

    # ações em sequência não podem rodar duas ao mesmo tempo
    win.go("sources")
    assert win.run(lambda: ("a", "ok"))
    assert not win.run(lambda: ("b", "ok"))
    _wait(app)
    win.sources.remove.__func__  # existe
    win.close()


def test_activity_integrations_and_tray(knowledge_repo, env, monkeypatch):
    from sunhub import actions, config

    app = create_app([])
    actions.add_source(knowledge_repo.as_uri())
    win = MainWindow()
    win.go("activity")
    assert win.stack.currentWidget() is win.activity
    win.go("integrations")
    win.integrations.run_lint()
    _wait(app)
    assert win.integrations._lint is not None
    # liga um recurso pela tela e confere que foi salvo
    win.integrations.set_feature("notify", False)
    _wait(app)
    assert config.load().settings.notify is False
    win.close()
