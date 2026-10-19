"""Leitura de formatos além de Markdown: HTML (exports do Confluence/Notion) e PDF."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

RICH_EXT = {".html", ".htm", ".pdf"}
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 300


class _HTMLToMarkdown(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "tr", "table", "ul", "ol", "blockquote", "pre", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ""
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "noscript"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("br",):
            self.out.append("\n")
        elif tag in self.BLOCK:
            self.out.append("\n\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag == "code":
            self.out.append("`")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif re.fullmatch(r"h[1-6]", tag) or tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag == "code":
            self.out.append("`")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(re.sub(r"\s+", " ", data))


def html_to_markdown(raw: str) -> tuple[str, str]:
    """(título, texto em Markdown simples)."""
    p = _HTMLToMarkdown()
    p.feed(raw)
    text = "".join(p.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return p.title.strip(), text


def pdf_to_text(path: Path) -> tuple[str, str] | None:
    """(título, texto) de um PDF com camada de texto. PDFs só de imagem não têm texto para indexar."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        if path.stat().st_size > MAX_PDF_BYTES:
            return None
        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages):
            if i >= MAX_PDF_PAGES:
                break
            pages.append((page.extract_text() or "").strip())
        title = ""
        try:
            title = (reader.metadata.title or "") if reader.metadata else ""
        except Exception:  # noqa: BLE001 - metadados quebrados não impedem a leitura
            title = ""
        text = "\n\n".join(p for p in pages if p)
        return title.strip(), text
    except Exception:  # noqa: BLE001 - PDF corrompido/criptografado: ignora o arquivo
        return None
