"""Smoke test do app de desktop (renderização offscreen, sem tela)."""
import os

import pytest

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
    win.wizard.urls.setPlainText(f"file://{knowledge_repo}")
    win.wizard.timer.setChecked(False)
    for cb in win.wizard.client_checks.values():
        cb.setChecked(False)
    win.wizard.submit()
    assert win.busy
    _wait(app)
    assert not win.busy
    assert win.stack.currentWidget() is win.sources
    assert "1 fonte(s) vinculada(s)" in win.flash.text()
    assert (env / ".copilot/skills/api-standards").is_symlink()

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
