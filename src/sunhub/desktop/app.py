"""App de desktop do Sunhub: janela nativa (Qt), sem navegador e sem servidor web.

Toda operação demorada (clone, sync, registro MCP, doctor) roda numa thread de
trabalho; a janela nunca congela.
"""
from __future__ import annotations

import html
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIntValidator, QKeySequence, QShortcut
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSystemTrayIcon,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from sunhub import __version__, actions, clients, config, doctor, indexer, linker, notify, paths, runtime, scheduler
from sunhub import lint as lint_mod
from sunhub import search as search_mod
from sunhub.sync import overlay_base
from sunhub.config import LINK_MODES, LINK_TARGETS, ConfigError
from sunhub.desktop import theme
from sunhub.models import KIND_LABELS, KINDS, Document
from sunhub.render import short
from sunhub.store import Store

# ------------------------------------------------------------------ helpers


def tilde(p) -> str:
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def ago(iso: str | None) -> str:
    if not iso:
        return "nunca"
    secs = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    if secs < 60:
        return "agora"
    if secs < 3600:
        return f"há {int(secs // 60)} min"
    if secs < 86400:
        return f"há {int(secs // 3600)} h"
    return f"há {int(secs // 86400)} dias"


def label(text: str = "", *, wrap: bool = False, select: bool = False, **props) -> QLabel:
    w = QLabel(text)
    for k, v in props.items():
        w.setProperty(k, v)
    w.setWordWrap(wrap)
    if select:
        w.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return w


def button(text: str, slot: Callable | None = None, *, primary: bool = False, ghost: bool = False,
           tip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if primary:
        b.setProperty("primary", True)
    if ghost:
        b.setProperty("ghost", True)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    return b


def card(spacing: int = 12) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setProperty("card", True)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(spacing)
    return f, lay


def row(*widgets, spacing: int = 10, stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(0, 0, 0, 0)
    for i, w in enumerate(widgets):
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w, 1 if stretch_at == i else 0)
    return lay


def clear(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            w = item.widget()
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout():
            clear(item.layout())


def dot(color: str) -> QLabel:
    d = QLabel()
    d.setFixedSize(9, 9)
    d.setStyleSheet(f"background:{color}; border-radius:4px;")
    return d


def tag(text: str, kind: str = "neutral") -> QLabel:
    t = label(text, tag=kind)
    t.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return t


def schedule_text(cfg: config.Config) -> tuple[str, str]:
    """(texto curto, nível) da próxima verificação automática."""
    with Store() as st:
        last_sync = st.last_sync()
    info = scheduler.schedule(cfg.settings.sync_interval_minutes, last_sync)
    if info["status"] != "active":
        return "Atualização automática desligada (ative em Integrações)", "warn"
    every = f"a cada {cfg.settings.sync_interval_minutes} min"
    nxt = info["next"]
    if not nxt:
        return f"Atualização automática ligada, {every}", "ok"
    mins = int((nxt - datetime.now()).total_seconds() // 60)
    when = "em instantes" if mins <= 0 else (f"em {mins} min" if mins < 120 else f"em {mins // 60} h {mins % 60} min")
    last = f" · última às {info['last']:%H:%M}" if info["last"] else ""
    return f"Próxima verificação às {nxt:%H:%M} ({when}), {every}{last}", "ok"


SINGULAR = {"skill": "skill", "adr": "ADR", "guideline": "guideline", "antipattern": "antipattern", "doc": "doc"}


def counts_text(counts: dict) -> str:
    return " · ".join(f"{n} {SINGULAR[k]}{'' if n == 1 else 's'}" for k, n in sorted(counts.items())) or "vazia"


def breakable(text: str) -> str:
    """Permite quebrar URLs/caminhos longos (sem espaços) em várias linhas."""
    return text.replace("/", "/\u200b").replace("-", "-\u200b").replace("_", "_\u200b")


def display_ref(d: Document) -> str:
    return d.ref_id if d.kind == "adr" and d.ref_id else d.ref


# ------------------------------------------------------------------ trabalho em segundo plano

class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Job(QRunnable):
    def __init__(self, fn: Callable):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as e:  # noqa: BLE001 - qualquer erro vira mensagem na janela
            self.signals.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.signals.done.emit(result)


# ------------------------------------------------------------------ páginas

class Page(QScrollArea):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("page")
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(0, 0, 10, 8)
        self.body.setSpacing(16)
        self.setWidget(inner)

    def header(self, title: str, subtitle: str, right: QWidget | None = None) -> QLabel:
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(title, h1=True))
        sub = label(subtitle, wrap=True, muted=True)
        text.addWidget(sub)
        lay = QHBoxLayout()
        lay.addLayout(text, 1)
        if right:
            lay.addWidget(right, 0, Qt.AlignBottom)
        self.body.addLayout(lay)
        return sub

    def refresh(self) -> None:  # pragma: no cover - sobrescrito
        pass


class WizardPage(Page):
    def __init__(self, win):
        super().__init__(win)
        self.subtitle = self.header("Vamos vincular seus repositórios", "")
        box, lay = card(10)
        lay.addWidget(label("URLs dos repositórios: uma por linha (públicos ou onde você é colaborador)", muted=True))
        self.urls = QPlainTextEdit()
        self.urls.setProperty("mono", True)
        self.urls.setPlaceholderText("https://github.com/acme/eng-guidelines\ngit@github.com:acme/architecture-adrs.git")
        self.urls.setFixedHeight(120)
        lay.addWidget(self.urls)
        lay.addSpacing(6)
        lay.addWidget(label("Publicar skills em", muted=True))
        self.targets_box = QVBoxLayout()
        lay.addLayout(self.targets_box)
        lay.addSpacing(6)
        lay.addWidget(label("Registrar o servidor MCP em", muted=True))
        self.clients_box = QVBoxLayout()
        lay.addLayout(self.clients_box)
        lay.addSpacing(6)
        self.timer = QCheckBox()
        self.timer.setChecked(True)
        lay.addWidget(self.timer)
        lay.addSpacing(6)
        self.go_btn = button("Vincular e configurar", self.submit, primary=True)
        win.register_action(self.go_btn)
        lay.addLayout(row(self.go_btn, None))
        self.body.addWidget(box)
        self.body.addStretch(1)
        self.target_checks: dict[str, QCheckBox] = {}
        self.client_checks: dict[str, QCheckBox] = {}

    def refresh(self):
        cfg = self.win.cfg()
        from sunhub import git

        self.subtitle.setText(f"O Sunhub clona cada repositório em {tilde(paths.sources_dir())}, mantém tudo "
                              "atualizado sozinho e liga o conteúdo às suas ferramentas.")
        if not git.available():
            hint = "winget install Git.Git" if sys.platform == "win32" else "sudo apt install git"
            self.win.show_message(f"O Git não está instalado, e o Sunhub precisa dele para baixar os repositórios. "
                                  f"Instale ({hint}) e abra o Sunhub de novo.", "error")
        self.timer.setText(f"Atualizar sozinho a cada {cfg.settings.sync_interval_minutes} min ({scheduler.NAME})")
        clear(self.targets_box)
        self.target_checks = {}
        for t in LINK_TARGETS:
            cb = QCheckBox(f"~/{linker.TARGET_DIRS[t]}")
            cb.setChecked(t == "copilot" or (Path.home() / linker.TARGET_DIRS[t]).parent.exists())
            self.target_checks[t] = cb
            self.targets_box.addWidget(cb)
        clear(self.clients_box)
        self.client_checks = {}
        for c in clients.CLIENTS:
            det = c.detected()
            cb = QCheckBox(c.label)
            cb.setChecked(det)
            self.client_checks[c.key] = cb
            self.clients_box.addLayout(row(cb, tag("detectado", "ok") if det else tag("não detectado"), None))

    def submit(self):
        urls = [u.strip() for u in self.urls.toPlainText().splitlines() if u.strip() and not u.strip().startswith("#")]
        if not urls:
            self.win.show_message("Cole ao menos uma URL de repositório.", "warn")
            return
        targets = [k for k, cb in self.target_checks.items() if cb.isChecked()]
        chosen = [k for k, cb in self.client_checks.items() if cb.isChecked()]
        timer = self.timer.isChecked()

        def done(result):
            self.urls.clear()
            self.win.show_message(*result)
            self.win.go("sources")

        self.win.run(lambda: actions.first_setup(urls, targets, chosen, timer), done,
                     busy=f"Clonando {len(urls)} repositório(s) e configurando…")


class SourcesPage(Page):
    def __init__(self, win):
        super().__init__(win)
        sync_btn = button("Sincronizar tudo", self.sync_all, primary=True, tip="git fetch em todas as fontes (Ctrl+R)")
        win.register_action(sync_btn)
        self.subtitle = self.header("Fontes", "", sync_btn)
        self.schedule = label("", wrap=True, muted=True)
        self.body.addWidget(self.schedule)

        box, lay = card(10)
        lay.addWidget(label("Adicionar repositório (URL Git), pasta local ou export .zip", muted=True))
        self.url = QLineEdit()
        self.url.setProperty("mono", True)
        self.url.setPlaceholderText("https://github.com/acme/security-playbook")
        self.url.returnPressed.connect(self.add)
        self.branch = QLineEdit()
        self.branch.setProperty("mono", True)
        self.branch.setPlaceholderText("branch (opcional)")
        self.branch.setMaximumWidth(200)
        self.branch.returnPressed.connect(self.add)
        add_btn = button("Adicionar", self.add, primary=True)
        win.register_action(add_btn)
        lay.addLayout(row(self.url, self.branch, add_btn, stretch_at=0))
        pick_dir = button("Pasta local…", self.pick_dir, ghost=True, tip="Indexa uma pasta do seu computador (sem clonar)")
        pick_zip = button("Export .zip…", self.pick_zip, ghost=True,
                          tip="Export do Confluence/Notion: HTML, Markdown e PDF são indexados")
        lay.addLayout(row(label("ou", muted=True), pick_dir, pick_zip, None, spacing=8))
        self.body.addWidget(box)

        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(10)
        self.body.addLayout(self.list_box)

        self.body.addWidget(label("Atividade recente", muted=True))
        self.log = QPlainTextEdit()
        self.log.setProperty("log", True)
        self.log.setReadOnly(True)
        self.log.setFixedHeight(150)
        self.body.addWidget(self.log)
        self.body.addStretch(1)

    def refresh(self):
        cfg = self.win.cfg()
        self.subtitle.setText(f"Repositórios vinculados. Os clones ficam em {tilde(paths.sources_dir())} "
                              "e se atualizam sozinhos.")
        self.update_schedule(cfg)
        clear(self.list_box)
        with Store() as st:
            for s in cfg.sources:
                state = st.get_state(s.name) or {}
                self.list_box.addWidget(self._row(s, state, st.counts_by_kind(s.name), overlay_base(cfg, s)))
        try:
            lines = paths.log_file().read_text("utf-8").splitlines()[-12:][::-1]
        except OSError:
            lines = []
        self.log.setPlainText("\n".join(f"› {line}" for line in lines) or "Nada por enquanto.")

    def update_schedule(self, cfg: config.Config | None = None) -> None:
        text, level = schedule_text(cfg or self.win.cfg())
        color = theme.SUN if level == "ok" else theme.WARN
        self.schedule.setText(f'<span style="color:{color}">●</span>&nbsp; {html.escape(text)}')

    def _row(self, s, state: dict, counts: dict, base=None) -> QFrame:
        box = QFrame()
        box.setProperty("card", True)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(4)
        err = state.get("last_error")
        color = theme.ERR if err else (theme.DIM if not s.enabled else theme.OK)
        where = {"git": f"{s.branch or 'padrão'} @ {short(state.get('commit_sha'))}", "archive": "export .zip",
                 "local": "pasta local"}[s.kind]
        name = label(s.name)
        name.setStyleSheet("font-weight:600;")
        left.addLayout(row(dot(color), name, label(where, mono=True, muted=True), None, spacing=8))
        url = label(breakable(s.location), wrap=True, select=True, mono=True, dim=True)
        url.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        left.addWidget(url)
        if base is not None:
            left.addWidget(label(f"Variação de “{base.name}”: mostra só o que a branch {s.branch or 'padrão'} tem de "
                                 "diferente", wrap=True, tag="neutral"))
        left.addWidget(label(f"{counts_text(counts)}   ·   sync {ago(state.get('last_sync'))}", muted=True, wrap=True))
        if err:
            e = label(breakable(actions.first_line(err)), wrap=True, select=True, error=True)
            e.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            left.addWidget(e)
        lay.addLayout(left, 1)
        sync_b = button("Sincronizar", lambda n=s.name: self.sync_one(n))
        rm_b = button("Remover", lambda n=s.name: self.remove(n), ghost=True)
        for b in (sync_b, rm_b):
            self.win.register_action(b)
            lay.addWidget(b, 0, Qt.AlignVCenter)
        return box

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Escolher pasta de conhecimento", str(Path.home()))
        if d:
            self.url.setText(d)
            self.branch.clear()

    def pick_zip(self):
        f, _ = QFileDialog.getOpenFileName(self, "Escolher export .zip", str(Path.home()), "Arquivos .zip (*.zip)")
        if f:
            self.url.setText(f)
            self.branch.clear()

    def add(self):
        url = self.url.text().strip()
        if not url:
            self.win.show_message("Informe a URL do repositório.", "warn")
            self.url.setFocus()
            return
        branch = self.branch.text().strip() or None

        def done(result):
            self.url.clear()
            self.branch.clear()
            self.win.show_message(*result)
            self.win.refresh_current()

        self.win.run(lambda: actions.add_source(url, None, branch), done, busy=f"Clonando {url}…")

    def sync_all(self):
        self.win.run(actions.sync, busy="Sincronizando todas as fontes…")

    def sync_one(self, name: str):
        self.win.run(lambda: actions.sync([name]), busy=f"Sincronizando {name}…")

    def remove(self, name: str):
        answer = QMessageBox.question(self, "Remover fonte",
                                      f"Remover “{name}”?\n\nO clone gerenciado será apagado e as skills dela "
                                      "deixam de aparecer nas ferramentas. O repositório original não é tocado.")
        if answer == QMessageBox.Yes:
            self.win.run(lambda: actions.remove_source(name), busy=f"Removendo {name}…")


class ExplorePage(Page):
    def __init__(self, win):
        super().__init__(win)
        self.header("Explorar", "É o que o agente enxerga pelo MCP. Use a referência de cada item no prompt.")
        self.q = QLineEdit()
        self.q.setPlaceholderText("Buscar: paginação, autenticação, god object…   (Ctrl+F)")
        self.q.setClearButtonEnabled(True)
        self._debounce = QTimer(self, singleShot=True, interval=220)
        self._debounce.timeout.connect(self.search)
        self.q.textChanged.connect(lambda _t: self._debounce.start())
        self.body.addWidget(self.q)

        self.chips = QButtonGroup(self)
        self.chips.setExclusive(True)
        self.chip_buttons: dict[str, QPushButton] = {}
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        for key in ["", *KINDS]:
            b = QPushButton()
            b.setProperty("chip", True)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False: self.search())
            self.chips.addButton(b)
            self.chip_buttons[key] = b
            chips_row.addWidget(b)
        chips_row.addStretch(1)
        self.chip_buttons[""].setChecked(True)
        self.body.addLayout(chips_row)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.results = QListWidget()
        self.results.setMinimumWidth(300)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.results.currentItemChanged.connect(lambda cur, _prev: self.show(cur.data(Qt.UserRole) if cur else None))
        split.addWidget(self.results)

        preview = QFrame()
        preview.setProperty("card", True)
        pv = QVBoxLayout(preview)
        pv.setContentsMargins(18, 16, 18, 16)
        pv.setSpacing(10)
        self.where = label("", wrap=True, select=True, mono=True, muted=True)
        self.where.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.ref = label("", ref=True, select=True)
        pv.addLayout(row(self.where, self.ref, stretch_at=0))
        self.warn = label("", wrap=True, tag="warn")
        self.warn.hide()
        pv.addWidget(self.warn)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet("QTextBrowser { border: none; padding: 0; }")
        pv.addWidget(self.view, 1)
        self.hint = label("", muted=True, select=True, wrap=True)
        copy_b = button("Copiar referência", self.copy_ref)
        open_b = button("Abrir arquivo", self.open_file, ghost=True)
        pv.addLayout(row(self.hint, copy_b, open_b, stretch_at=0))
        split.addWidget(preview)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        self.body.addWidget(split, 1)
        self.current: Document | None = None
        self.setWidgetResizable(True)

    def refresh(self):
        with Store() as st:
            counts = st.counts_by_kind()
        total = sum(counts.values())
        self.chip_buttons[""].setText(f"Tudo  {total}")
        for k in KINDS:
            self.chip_buttons[k].setText(f"{KIND_LABELS[k]}  {counts.get(k, 0)}")
        self.search()

    def kind(self) -> str | None:
        for k, b in self.chip_buttons.items():
            if b.isChecked():
                return k or None
        return None

    def search(self):
        q, kind = self.q.text().strip(), self.kind()
        keep = self.current.uid if self.current else None
        with Store() as st:
            items = (search_mod.search(st, q, kind, limit=80) if q
                     else [(d, d.description or "") for d in st.list(kind, limit=500)])
        self.results.blockSignals(True)
        self.results.clear()
        select_row = 0
        for i, (d, snip) in enumerate(items):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, d.uid)
            w = self._item_widget(d, snip)
            item.setSizeHint(w.sizeHint())
            self.results.addItem(item)
            self.results.setItemWidget(item, w)
            if d.uid == keep:
                select_row = i
        self.results.blockSignals(False)
        if items:
            self.results.setCurrentRow(select_row)
            self.show(items[select_row][0].uid)
        else:
            self.show(None, empty=("Nada encontrado. Nenhum documento cobre isso, e o agente deve dizer exatamente isso."
                                   if q else "Nenhum documento ainda. Adicione uma fonte em Fontes."))

    def _item_widget(self, d: Document, snip: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)
        top = [label(display_ref(d), ref=True)]
        if d.status:
            top.append(tag(d.status, d.status if d.status in ("accepted", "superseded", "deprecated", "rejected") else "neutral"))
        lay.addLayout(row(*top, None, spacing=8))
        title = d.title if len(d.title) <= 70 else d.title[:68] + "…"
        for text, props in ((title, {}), (self._sub(d, snip), {"dim": True})):
            lab = label(text, **props)
            lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lay.addWidget(lab)
        return w

    @staticmethod
    def _sub(d: Document, snip: str) -> str:
        extra = " ".join((snip or "").split()).replace("«", "").replace("»", "")
        return d.source + (f" · {extra[:80]}{'…' if len(extra) > 80 else ''}" if extra else "")

    def show(self, uid: str | None, empty: str = ""):
        doc = None
        if uid:
            with Store() as st:
                doc = st.get(uid)
        self.current = doc
        if not doc:
            self.where.setText("")
            self.ref.setText("")
            self.warn.hide()
            self.hint.setText("")
            self.view.setMarkdown(f"*{empty}*" if empty else "")
            return
        self.where.setText(breakable(f"{doc.source}/{doc.path}") + f" @ {short(doc.commit_sha)}")
        self.ref.setText(display_ref(doc))
        sb = doc.extra.get("superseded_by")
        if sb or doc.status in ("deprecated", "rejected"):
            self.warn.setText(f"Substituído por {sb}: o agente não vai segui-lo." if sb
                              else f"Status {doc.status}: não vale como padrão vigente.")
            self.warn.show()
        else:
            self.warn.hide()
        fm, body = indexer.split_frontmatter(doc.body)
        md = body
        if doc.kind == "skill" and doc.description:
            md = f"**Skill `{doc.ref_id}`** — {doc.description}\n\n---\n\n{body}"
        self.view.setMarkdown(md)
        self.hint.setText(f"No prompt: usando como referência {display_ref(doc)}")

    def copy_ref(self):
        if self.current:
            QGuiApplication.clipboard().setText(display_ref(self.current))
            self.win.show_message(f"Referência copiada: {display_ref(self.current)}", "ok")

    def open_file(self):
        if not self.current:
            return
        src = self.win.cfg().get(self.current.source)
        if src:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(src.checkout_dir() / self.current.path)))


CHANGE_COLORS = {"added": theme.OK, "removed": theme.ERR, "status": theme.WARN, "source_added": theme.SUN}


class ActivityPage(Page):
    def __init__(self, win):
        super().__init__(win)
        self.header("Atividade", "O que mudou nas fontes e o que o agente tem consultado (últimos 30 dias).")
        self.content = QVBoxLayout()
        self.content.setSpacing(16)
        self.body.addLayout(self.content)
        self.body.addStretch(1)

    def refresh(self):
        clear(self.content)
        since = datetime.now(timezone.utc) - timedelta(days=30)
        with Store() as st:
            rows = st.changes(since, limit=150)
            rep = st.usage_report(30)

        box, lay = card(4)
        lay.addWidget(label("Novidades", h2=True))
        if not rows:
            lay.addWidget(label("Nada mudou nas fontes nos últimos 30 dias.", muted=True))
        day = None
        for c in rows:
            dt = datetime.fromisoformat(c["ts"]).astimezone()
            if dt.date() != day:
                day = dt.date()
                lay.addSpacing(8)
                lay.addWidget(label("Hoje" if day == datetime.now().date() else dt.strftime("%d/%m/%Y"), muted=True))
            text = label(notify.describe(c), wrap=True, select=True)
            text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lay.addLayout(row(dot(CHANGE_COLORS.get(c["change"], theme.DIM)), label(f"{dt:%H:%M}", mono=True, dim=True),
                              text, tag(c["source"]), stretch_at=2, spacing=10))
        self.content.addWidget(box)

        box, lay = card(6)
        lay.addLayout(row(label("Uso do agente", h2=True), label(f"{rep['total']} consulta(s)", muted=True), None))
        if not rep["total"]:
            lay.addWidget(label("O agente ainda não consultou o Sunhub. No VS Code, peça algo citando adr:14 ou uma "
                                "skill e volte aqui.", muted=True, wrap=True))
        else:
            lay.addWidget(label(" · ".join(f"{k} {v}" for k, v in rep["by_tool"].items()), dim=True, wrap=True))
            cols = QHBoxLayout()
            cols.setSpacing(24)
            for title, items, hint in (
                ("Mais lidos", [f"{r['n']}×  {r['ref_id']} — {r['title']}" for r in rep["top"][:8]], ""),
                ("Buscas sem resposta", [f"{r['n']}×  {r['query']}" for r in rep["gaps"][:8]],
                 "Lacunas: documente ou proponha um ADR."),
                ("Nunca lidos", [f"{r['ref_id']} — {r['title']}" for r in rep["unread"][:8]],
                 f"{len(rep['unread'])} documento(s) sem leitura no período."),
            ):
                col = QVBoxLayout()
                col.setSpacing(4)
                col.addWidget(label(title, muted=True))
                for it in items or ["—"]:
                    lab = label(it, wrap=True, select=True)
                    lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                    col.addWidget(lab)
                if hint:
                    col.addWidget(label(hint, dim=True, wrap=True))
                col.addStretch(1)
                cols.addLayout(col, 1)
            lay.addLayout(cols)
        self.content.addWidget(box)
        self.win.mark_changes_seen()


class IntegrationsPage(Page):
    def __init__(self, win):
        super().__init__(win)
        self.header("Integrações", "Cada ferramenta só olha os próprios caminhos. O Sunhub liga o hub a eles, "
                                   "com backup e como desfazer (sunhub uninstall).")
        self.content = QVBoxLayout()
        self.content.setSpacing(16)
        self.body.addLayout(self.content)
        self.body.addStretch(1)
        self._checks = None
        self._lint = None

    def refresh(self):
        clear(self.content)
        cfg = self.win.cfg()

        # skills
        box, lay = card()
        lay.addWidget(label("Pastas de skills", h2=True))
        lay.addWidget(label("Cada skill das fontes vira um link nestas pastas, junto com a skill “sunhub”, "
                            "que ensina o agente a consultar o hub.", muted=True, wrap=True))
        self.target_checks = {}
        for t in LINK_TARGETS:
            cb = QCheckBox(f"~/{linker.TARGET_DIRS[t]}")
            cb.setChecked(t in cfg.settings.link_targets)
            self.target_checks[t] = cb
            lay.addWidget(cb)
        self.mode = QComboBox()
        for m in LINK_MODES:
            self.mode.addItem("symlink (atualiza na hora)" if m == "symlink" else "cópia (ferramentas que não seguem symlink)", m)
        self.mode.setCurrentIndex(max(0, list(LINK_MODES).index(cfg.settings.link_mode) if cfg.settings.link_mode in LINK_MODES else 0))
        save = button("Salvar e religar", self.save_links)
        self.win.register_action(save)
        lay.addLayout(row(label("Modo", muted=True), self.mode, save, None))
        self.content.addWidget(box)

        # MCP
        box, lay = card(0)
        lay.addWidget(label("Servidor MCP", h2=True))
        lay.addSpacing(4)
        lay.addWidget(label("Registra o Sunhub na configuração de cada ferramenta (o arquivo original ganha um backup).",
                            muted=True, wrap=True))
        lay.addSpacing(8)
        for i, c in enumerate(clients.CLIENTS):
            if i:
                sep = QFrame()
                sep.setProperty("sep", True)
                lay.addWidget(sep)
            installed, detected = clients.installed(c), c.detected()
            info = QVBoxLayout()
            info.setSpacing(2)
            name = label(c.label)
            name.setStyleSheet("font-weight:600;")
            head = [name]
            if detected:
                head.append(tag("detectado", "ok"))
            if installed:
                head.append(tag("registrado", "ok"))
            info.addLayout(row(*head, None, spacing=8))
            info.addWidget(label(c.location, mono=True, dim=True, select=True))
            b = (button("Remover", lambda k=c.key: self.mcp(k, False), ghost=True) if installed
                 else button("Registrar", lambda k=c.key: self.mcp(k, True)))
            self.win.register_action(b)
            line = QHBoxLayout()
            line.setContentsMargins(0, 10, 0, 10)
            line.addLayout(info, 1)
            line.addWidget(b)
            lay.addLayout(line)
        self.content.addWidget(box)

        # timer
        box, lay = card()
        lay.addWidget(label("Atualização automática", h2=True))
        status = scheduler.status()
        names = {"active": "ativo", "inactive": "inativo", "not-installed": "desligado",
                 "unavailable": f"{scheduler.NAME} indisponível"}
        self.interval = QLineEdit(str(cfg.settings.sync_interval_minutes))
        self.interval.setValidator(QIntValidator(1, 1440, self.interval))
        self.interval.setFixedWidth(80)
        self.interval.setAlignment(Qt.AlignRight)
        self.interval.setToolTip("De 1 a 1440 minutos")
        self.interval.returnPressed.connect(lambda: self.timer(True))
        st_label = label(f"Status: <b>{names[status]}</b>")
        widgets = [st_label, label("a cada", muted=True), self.interval, label("min", muted=True)]
        if status == "active":
            on = button("Aplicar", lambda: self.timer(True))
            off = button("Desligar", lambda: self.timer(False), ghost=True)
            widgets += [on, off, None]
            self.win.register_action(on)
            self.win.register_action(off)
        else:
            on = button("Ligar", lambda: self.timer(True))
            self.win.register_action(on)
            widgets += [on, None]
        lay.addLayout(row(*widgets))
        text, level = schedule_text(cfg)
        lay.addWidget(label(text, wrap=True, tag="ok" if level == "ok" else "warn"))
        lay.addWidget(label(f"Usa o {scheduler.NAME}: as fontes se atualizam mesmo com o app fechado.",
                            muted=True, wrap=True))
        self.content.addWidget(box)

        # recursos
        box, lay = card(8)
        lay.addWidget(label("Recursos", h2=True))
        feats = (
            ("notify", "Notificar novidades", "Notificação do sistema quando um sync automático traz ADR, skill ou "
                                              "mudança de status."),
            ("semantic", "Busca por significado", "Acha “como paginar uma lista” mesmo sem a palavra exata. Roda no seu "
                                                  "PC; na primeira vez baixa um modelo de ~220 MB."),
            ("allow_proposals", "O agente pode propor ADRs", "Quando nada cobre uma decisão, o agente cria o ADR numa "
                                                             "branch e abre um PR para você revisar."),
            ("tray", "Ficar na bandeja ao fechar", "A janela some, o Sunhub continua acessível pelo ícone do sol."),
        )
        for key, title, desc in feats:
            cb = QCheckBox(title)
            cb.setChecked(bool(getattr(cfg.settings, key)))
            cb.toggled.connect(lambda on, k=key: self.set_feature(k, on))
            self.win.register_action(cb)
            lay.addWidget(cb)
            d = label(desc, muted=True, wrap=True)
            d.setContentsMargins(28, 0, 0, 6)
            lay.addWidget(d)
        self.content.addWidget(box)

        # qualidade
        box, lay = card(6)
        lint_b = button("Verificar", self.run_lint)
        self.win.register_action(lint_b)
        lay.addLayout(row(label("Qualidade das fontes", h2=True), None, lint_b))
        lay.addWidget(label("ADR sem status, ADR substituído por um que não existe, skill sem descrição, links "
                            "quebrados. O mesmo que `sunhub lint`, que também roda na CI do repositório.",
                            muted=True, wrap=True))
        if self._lint is not None:
            if not self._lint:
                lay.addWidget(label("✓ nenhum problema encontrado", tag="ok"))
            current = None
            for i in sorted(self._lint, key=lambda x: (x.source, x.path, x.level)):
                key = f"{i.source}/{i.path}"
                if key != current:
                    current = key
                    lay.addSpacing(4)
                    lay.addWidget(label(breakable(key), mono=True, muted=True, wrap=True))
                color = theme.ERR if i.level == "error" else theme.WARN
                msg = label(f'<span style="color:{color}">{i.sign}</span> {html.escape(i.message)}', wrap=True)
                msg.setTextFormat(Qt.RichText)
                msg.setContentsMargins(16, 0, 0, 0)
                lay.addWidget(msg)
        self.content.addWidget(box)

        # doctor
        box, lay = card()
        run_b = button("Rodar diagnóstico", self.run_doctor)
        self.win.register_action(run_b)
        lay.addLayout(row(label("Diagnóstico", h2=True), None, run_b))
        if self._checks:
            colors = {"ok": theme.OK, "warn": theme.WARN, "error": theme.ERR}
            text = "<br>".join(f'<span style="color:{colors[c.level]}">{c.sign} {html.escape(c.text)}</span>'
                               for c in self._checks)
            out = label(text, wrap=True, select=True, mono=True)
            out.setTextFormat(Qt.RichText)
            lay.addWidget(out)
        self.content.addWidget(box)

        # hub
        box, lay = card()
        lay.addWidget(label("Hub", h2=True))
        lay.addWidget(label(tilde(paths.home()), mono=True, select=True))
        lay.addLayout(row(button("Abrir pasta", lambda: self._open(paths.home())),
                          button("Editar config.toml", lambda: self._open(paths.config_file())),
                          button("Ver log de sync", lambda: self._open(paths.log_file())), None))
        self.content.addWidget(box)

    def _open(self, p: Path):
        if not p.exists():
            self.win.show_message(f"{tilde(p)} ainda não existe.", "warn")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def save_links(self):
        targets = [t for t, cb in self.target_checks.items() if cb.isChecked()]
        mode = self.mode.currentData()

        def work():
            cfg = config.load()
            cfg.settings.link_targets = targets
            cfg.settings.link_mode = mode
            config.save(cfg)
            with Store() as st:
                rep = linker.relink(cfg, st)
            msg = f"{rep.total} link(s) de skills ativos"
            if rep.conflicts:
                return msg + f"; {len(rep.conflicts)} nome(s) já usados por skills suas (mantidas)", "warn"
            return msg, "ok"

        self.win.run(work, busy="Religando skills…")

    def mcp(self, key: str, install: bool):
        c = clients.get(key)
        self.win.run(lambda: ((clients.install if install else clients.uninstall)(c), "ok"),
                     busy=f"{'Registrando' if install else 'Removendo'} MCP em {c.label}…")

    def timer(self, enable: bool):
        try:
            interval = int(self.interval.text())
        except ValueError:
            interval = 0
        if enable and not 1 <= interval <= 1440:
            self.win.show_message("Informe um intervalo entre 1 e 1440 minutos.", "warn")
            self.interval.setFocus()
            return
        interval = interval or 30

        def work():
            cfg = config.load()
            cfg.settings.sync_interval_minutes = interval
            config.save(cfg)
            return (scheduler.enable(interval) if enable else scheduler.disable()), "ok"

        self.win.run(work, busy="Configurando o timer…")

    def set_feature(self, key: str, on: bool):
        def work():
            if key == "semantic" and on:
                from sunhub import semantic

                semantic.install()
                cfg = config.load()
                cfg.settings.semantic = True
                config.save(cfg)
                with Store() as st:
                    n = semantic.update_index(st, semantic.get_embedder())
                return f"Busca por significado ligada ({n} documento(s) indexado(s))", "ok"
            cfg = config.load()
            setattr(cfg.settings, key, on)
            config.save(cfg)
            return f"{'Ligado' if on else 'Desligado'}: {key.replace('_', ' ')}", "ok"

        busy = "Instalando a busca por significado e baixando o modelo…" if key == "semantic" and on else "Salvando…"
        if not self.win.run(work, busy=busy):
            self.refresh()

    def run_lint(self):
        def work():
            cfg = config.load()
            with Store() as st:
                return lint_mod.lint_sources(cfg, st)

        def done(issues):
            self._lint = issues
            e = sum(1 for i in issues if i.level == "error")
            self.win.show_message(f"Qualidade: {e} erro(s), {len(issues) - e} aviso(s)", "warn" if issues else "ok")
            self.refresh()

        self.win.run(work, done, busy="Verificando as fontes…")

    def run_doctor(self):
        def work():
            cfg = config.load()
            with Store() as st:
                return doctor.run(cfg, st)

        def done(checks):
            self._checks = checks
            self.refresh()

        self.win.run(work, done, busy="Rodando diagnóstico…")


# ------------------------------------------------------------------ janela

class MainWindow(QMainWindow):
    NAV = (("sources", "Fontes", "Ctrl+1"), ("explore", "Explorar", "Ctrl+2"), ("activity", "Atividade", "Ctrl+3"),
           ("integrations", "Integrações", "Ctrl+4"))

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sunhub")
        self.setWindowIcon(theme.app_icon())
        self.resize(1200, 800)
        self.setMinimumSize(920, 620)
        self._busy = False
        self._jobs: set = set()
        self._actions: list[QPushButton] = []
        self.current_key = "sources"

        root = QWidget()
        root.setObjectName("content")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_side())
        right = QWidget()
        right.setObjectName("content")
        v = QVBoxLayout(right)
        v.setContentsMargins(36, 28, 26, 16)
        v.setSpacing(14)
        self.flash = QLabel()
        self.flash.setObjectName("flash")
        self.flash.setWordWrap(True)
        self.flash.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.flash.hide()
        v.addWidget(self.flash)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        h.addWidget(right, 1)
        self.setCentralWidget(root)

        self.wizard = WizardPage(self)
        self.sources = SourcesPage(self)
        self.explore = ExplorePage(self)
        self.activity = ActivityPage(self)
        self.integrations = IntegrationsPage(self)
        for p in (self.wizard, self.sources, self.explore, self.activity, self.integrations):
            self.stack.addWidget(p)
        with Store() as st:
            self._seen_change = st.last_change_id()
        self._quitting = False
        self._tray_hint_shown = False
        self._setup_tray()

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(140)
        self.progress.hide()
        self.status_text = QLabel("Pronto")
        self.statusBar().addWidget(self.status_text, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(QLabel(f"v{__version__}  "))

        self._flash_timer = QTimer(self, singleShot=True)
        self._flash_timer.timeout.connect(self.flash.hide)
        self._auto = QTimer(self, interval=60_000)
        self._auto.timeout.connect(self._auto_refresh)
        self._auto.start()
        self._clock = QTimer(self, interval=30_000)
        self._clock.timeout.connect(lambda: self.stack.currentWidget() is self.sources and self.sources.update_schedule())
        self._clock.start()

        for key, _text, keys in self.NAV:
            QShortcut(QKeySequence(keys), self, activated=lambda k=key: self.go(k))
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.sources.sync_all)
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_current)
        self.go("sources")

    def _build_side(self) -> QWidget:
        side = QWidget()
        side.setObjectName("side")
        side.setFixedWidth(232)
        v = QVBoxLayout(side)
        v.setContentsMargins(16, 22, 16, 16)
        v.setSpacing(4)
        logo = QLabel()
        logo.setPixmap(theme.sun_pixmap(26))
        v.addLayout(row(logo, label("sunhub", brand=True), None, spacing=10))
        v.addSpacing(22)
        self.nav: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        for key, text, keys in self.NAV:
            b = QPushButton(text)
            b.setProperty("nav", True)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(keys)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            group.addButton(b)
            v.addWidget(b)
            self.nav[key] = b
        v.addStretch(1)
        box, lay = card(4)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(label("Hub", muted=True))
        lay.addWidget(label(tilde(paths.home()), mono=True, wrap=True, select=True))
        lay.addWidget(label("Terminal: sunhub --help", dim=True))
        v.addWidget(box)
        return side

    # ---------------------------------------------------------- navegação
    def cfg(self) -> config.Config:
        try:
            return config.load()
        except ConfigError as e:
            self.show_message(str(e), "error")
            return config.Config()

    def go(self, key: str) -> None:
        self.current_key = key
        self.nav[key].setChecked(True)
        if key == "sources":
            page = self.sources if self.cfg().sources else self.wizard
        else:
            page = getattr(self, key)
        page.refresh()
        self.stack.setCurrentWidget(page)
        self._apply_busy()

    def refresh_current(self) -> None:
        self.go(self.current_key)

    def _auto_refresh(self) -> None:
        self._check_changes()
        if not self._busy and self.isActiveWindow() and self.stack.currentWidget() is self.sources:
            self.sources.refresh()

    # ---------------------------------------------------------- novidades e bandeja
    def _check_changes(self) -> None:
        with Store() as st:
            pending = st.changes(after_id=self._seen_change, limit=50)
        b = self.nav["activity"]
        b.setText(f"Atividade  ({len(pending)})" if pending else "Atividade")
        if self.tray:
            self.tray.setToolTip(f"Sunhub · {len(pending)} novidade(s)" if pending else "Sunhub")

    def mark_changes_seen(self) -> None:
        with Store() as st:
            self._seen_change = st.last_change_id()
        self._check_changes()

    def _setup_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(theme.app_icon(), self)
        self.tray.setToolTip("Sunhub")
        menu = QMenu(self)
        menu.addAction("Abrir Sunhub", self.bring_to_front)
        menu.addAction("Sincronizar agora", lambda: self.sources.sync_all())
        self._tray_status = menu.addAction("")
        self._tray_status.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Sair", self.quit_app)
        menu.aboutToShow.connect(lambda: self._tray_status.setText(schedule_text(self.cfg())[0]))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle_visible() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def bring_to_front(self) -> None:
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh_current()

    def toggle_visible(self) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self.bring_to_front()

    def quit_app(self) -> None:
        self._quitting = True
        if self.close():
            QApplication.instance().quit()
        else:
            self._quitting = False

    def _focus_search(self) -> None:
        self.go("explore")
        self.explore.q.setFocus()
        self.explore.q.selectAll()

    # ---------------------------------------------------------- mensagens
    def show_message(self, text: str, level: str = "ok") -> None:
        self.flash.setText(text)
        self.flash.setProperty("level", level)
        self.flash.style().unpolish(self.flash)
        self.flash.style().polish(self.flash)
        self.flash.show()
        self.status_text.setText(actions.first_line(text)[:160])
        if level == "ok":
            self._flash_timer.start(8000)
        else:
            self._flash_timer.stop()

    # ---------------------------------------------------------- trabalho
    def register_action(self, b: QPushButton) -> None:
        self._actions.append(b)
        b.setEnabled(not self._busy)

    def _apply_busy(self) -> None:
        alive = []
        for b in self._actions:
            try:
                b.setEnabled(not self._busy)
                alive.append(b)
            except RuntimeError:  # widget já destruído num refresh
                pass
        self._actions = alive

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.status_text.setText(text)
            QApplication.setOverrideCursor(Qt.BusyCursor)
        else:
            QApplication.restoreOverrideCursor()
        self._apply_busy()

    @property
    def busy(self) -> bool:
        return self._busy

    def run(self, fn: Callable, on_done: Callable | None = None, busy: str = "Trabalhando…") -> bool:
        if self._busy:
            self.show_message("Aguarde a operação atual terminar.", "warn")
            return False
        self._set_busy(True, busy)
        job = _Job(fn)
        sig = job.signals
        self._jobs.add(sig)

        def finished():
            self._jobs.discard(sig)
            self._set_busy(False)
            self.status_text.setText("Pronto")

        def ok(result):
            finished()
            if on_done:
                on_done(result)
            else:
                if isinstance(result, tuple) and len(result) == 2:
                    self.show_message(*result)
                self.refresh_current()

        def bad(message):
            finished()
            self.show_message(message, "error")
            self.refresh_current()

        sig.done.connect(ok)
        sig.failed.connect(bad)
        QThreadPool.globalInstance().start(job)
        return True

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._quitting and self.tray and self.cfg().settings.tray:
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage("Sunhub continua aberto", "Clique no sol na bandeja para voltar; "
                                      "use “Sair” no menu para fechar de vez.", theme.app_icon(), 4000)
            event.ignore()
            return
        if self._busy:
            answer = QMessageBox.question(self, "Sair", "Uma operação ainda está em andamento. Sair mesmo assim?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()
        if self.tray:  # com bandeja o app não fecha sozinho ao fechar a janela
            self.tray.hide()
            QTimer.singleShot(0, QApplication.instance().quit)


def create_app(argv: list[str] | None = None) -> QApplication:
    if sys.platform == "win32":  # ícone e agrupamento próprios na barra de tarefas
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Sunhub.Sunhub")
        except Exception:  # noqa: BLE001
            pass
    QApplication.setApplicationName("Sunhub")
    QApplication.setOrganizationName("sunhub")
    QGuiApplication.setDesktopFileName("sunhub")  # liga a janela ao atalho/ícone do menu
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationDisplayName("Sunhub")
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.QSS)
    app.setWindowIcon(theme.app_icon())
    font = app.font()
    if font.pointSizeF() < 10.5:
        font.setPointSizeF(10.5)
        app.setFont(font)
    return app


def _server_name() -> str:
    import getpass

    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"sunhub-{user}"


def _signal_ready() -> None:
    """Avisa o lançador (`source app`) que a janela abriu."""
    ready = os.environ.get("SUNHUB_READY_FILE")
    if ready:
        try:
            Path(ready).write_text(str(os.getpid()), "utf-8")
        except OSError:
            pass


def _already_running() -> bool:
    """Se o app já está aberto, pede para ele vir para frente e retorna True."""
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def _listen(win: "MainWindow") -> QLocalServer:
    QLocalServer.removeServer(_server_name())  # limpa socket órfão de uma execução que caiu
    server = QLocalServer(win)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn:
            conn.disconnectFromServer()
        win.setWindowState((win.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        win.show()
        win.raise_()
        win.activateWindow()

    server.newConnection.connect(on_connection)
    server.listen(_server_name())
    return server


def main() -> int:
    app = create_app()
    if _already_running():
        _signal_ready()
        return 0
    paths.ensure()
    if runtime.appimage() or runtime.is_frozen():  # empacotado: prepara atalho, comando e caminhos
        try:
            from sunhub import setup as setup_mod

            setup_mod.setup()
        except Exception:  # noqa: BLE001 - nunca impede o app de abrir
            pass
    win = MainWindow()
    win._server = _listen(win)
    win.show()
    QTimer.singleShot(0, _signal_ready)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
