"""Índice local em SQLite + FTS5 (~/sunhub/index.db)."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sunhub import paths
from sunhub.models import Document

TABLES = """
CREATE TABLE IF NOT EXISTS documents(
  uid TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  path TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref_id TEXT,
  title TEXT,
  status TEXT,
  tags TEXT,
  description TEXT,
  body TEXT,
  commit_sha TEXT,
  extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind, ref_id);
CREATE TABLE IF NOT EXISTS sources_state(
  name TEXT PRIMARY KEY,
  commit_sha TEXT,
  last_sync TEXT,
  last_error TEXT
);
"""
FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5("
       "uid UNINDEXED, title, ref_id, tags, body, tokenize='{tok}')")
COLUMNS = "uid, source, path, kind, ref_id, title, status, tags, description, body, commit_sha, extra"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fts_query(text: str, joiner: str = " ") -> str | None:
    tokens = re.findall(r"\w+", text or "", re.UNICODE)
    long_tokens = [t for t in tokens if len(t) > 1] or tokens
    if not long_tokens:
        return None
    return joiner.join(f'"{t}"*' for t in long_tokens[:24])


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or paths.index_db())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(TABLES)
        try:
            self.conn.execute(FTS.format(tok="unicode61 remove_diacritics 2"))
        except sqlite3.OperationalError:
            self.conn.execute(FTS.format(tok="unicode61"))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ escrita
    def _insert(self, d: Document) -> None:
        c = self.conn
        c.execute("DELETE FROM documents_fts WHERE uid = ?", (d.uid,))
        c.execute(
            f"INSERT OR REPLACE INTO documents({COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.uid, d.source, d.path, d.kind, d.ref_id, d.title, d.status, json.dumps(d.tags, ensure_ascii=False),
             d.description, d.body, d.commit_sha, json.dumps(d.extra, ensure_ascii=False)),
        )
        c.execute(
            "INSERT INTO documents_fts(uid, title, ref_id, tags, body) VALUES (?,?,?,?,?)",
            (d.uid, d.title, f"{d.ref_id or ''} {d.kind}", " ".join(d.tags), d.body),
        )

    def upsert(self, docs: list[Document]) -> None:
        with self.conn:
            for d in docs:
                self._insert(d)

    def delete_uids(self, uids: list[str]) -> None:
        with self.conn:
            for uid in uids:
                self.conn.execute("DELETE FROM documents WHERE uid = ?", (uid,))
                self.conn.execute("DELETE FROM documents_fts WHERE uid = ?", (uid,))

    def replace_source(self, source: str, docs: list[Document]) -> None:
        with self.conn:
            self._delete_source(source)
            for d in docs:
                self._insert(d)

    def _delete_source(self, source: str) -> None:
        self.conn.execute(
            "DELETE FROM documents_fts WHERE uid IN (SELECT uid FROM documents WHERE source = ?)", (source,)
        )
        self.conn.execute("DELETE FROM documents WHERE source = ?", (source,))

    def delete_source(self, source: str) -> None:
        with self.conn:
            self._delete_source(source)
            self.conn.execute("DELETE FROM sources_state WHERE name = ?", (source,))

    # ------------------------------------------------------------ leitura
    @staticmethod
    def _doc(row: sqlite3.Row) -> Document:
        return Document(
            source=row["source"], path=row["path"], kind=row["kind"], title=row["title"] or "",
            body=row["body"] or "", ref_id=row["ref_id"], status=row["status"],
            tags=json.loads(row["tags"] or "[]"), description=row["description"],
            commit_sha=row["commit_sha"], extra=json.loads(row["extra"] or "{}"),
        )

    def get(self, uid: str) -> Document | None:
        row = self.conn.execute(f"SELECT {COLUMNS} FROM documents WHERE uid = ?", (uid,)).fetchone()
        return self._doc(row) if row else None

    def count(self, source: str | None = None, kind: str | None = None) -> int:
        sql, args = "SELECT COUNT(*) FROM documents WHERE 1=1", []
        if source:
            sql += " AND source = ?"
            args.append(source)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return self.conn.execute(sql, args).fetchone()[0]

    def counts_by_kind(self, source: str | None = None) -> dict[str, int]:
        sql, args = "SELECT kind, COUNT(*) FROM documents", []
        if source:
            sql += " WHERE source = ?"
            args.append(source)
        return {k: n for k, n in self.conn.execute(sql + " GROUP BY kind", args)}

    def list(self, kind: str | None = None, source: str | None = None, limit: int | None = None) -> list[Document]:
        sql, args = f"SELECT {COLUMNS} FROM documents WHERE 1=1", []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if source:
            sql += " AND source = ?"
            args.append(source)
        sql += " ORDER BY kind, ref_id COLLATE NOCASE, path"
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        return [self._doc(r) for r in self.conn.execute(sql, args)]

    def find(self, kind: str | None, ref_id: str) -> list[Document]:
        sql, args = f"SELECT {COLUMNS} FROM documents WHERE ref_id = ? COLLATE NOCASE", [ref_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return [self._doc(r) for r in self.conn.execute(sql, args)]

    def search(self, text: str, kind: str | None = None, source: str | None = None,
               limit: int = 10) -> list[tuple[Document, str]]:
        results: list[tuple[Document, str]] = []
        for joiner in (" ", " OR "):
            q = fts_query(text, joiner)
            if not q:
                return []
            sql = (f"SELECT {', '.join('d.' + c.strip() for c in COLUMNS.split(','))}, "
                   "snippet(documents_fts, 4, '«', '»', ' … ', 18) AS snip "
                   "FROM documents_fts JOIN documents d ON d.uid = documents_fts.uid "
                   "WHERE documents_fts MATCH ?")
            args: list = [q]
            if kind:
                sql += " AND d.kind = ?"
                args.append(kind)
            if source:
                sql += " AND d.source = ?"
                args.append(source)
            sql += " ORDER BY bm25(documents_fts, 0.0, 10.0, 8.0, 4.0, 1.0) LIMIT ?"
            args.append(int(limit))
            try:
                rows = self.conn.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                rows = []
            results = [(self._doc(r), " ".join((r["snip"] or "").split())) for r in rows]
            if results:
                break
        return results

    # ------------------------------------------------------------ estado das fontes
    def set_state(self, name: str, commit: str | None, error: str | None) -> None:
        with self.conn:
            prev = self.get_state(name)
            commit = commit or (prev or {}).get("commit_sha")
            self.conn.execute(
                "INSERT OR REPLACE INTO sources_state(name, commit_sha, last_sync, last_error) VALUES (?,?,?,?)",
                (name, commit, _now(), error),
            )

    def get_state(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM sources_state WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def last_sync(self) -> datetime | None:
        row = self.conn.execute("SELECT MAX(last_sync) FROM sources_state").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None
