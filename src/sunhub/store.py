"""Índice local em SQLite + FTS5 (~/sunhub/index.db).

Também guarda o histórico de novidades (o que cada sync trouxe), o uso feito pelo agente
(o que ele consultou) e os vetores da busca por significado.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
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
CREATE TABLE IF NOT EXISTS changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  uid TEXT,
  ref TEXT,
  title TEXT,
  kind TEXT,
  change TEXT NOT NULL,
  detail TEXT,
  commit_sha TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_ts ON changes(ts);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  tool TEXT NOT NULL,
  query TEXT,
  uid TEXT,
  source TEXT,
  hits INTEGER
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage(ts);
CREATE TABLE IF NOT EXISTS vectors(
  uid TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  hash TEXT NOT NULL,
  vec BLOB NOT NULL
);
"""
FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5("
       "uid UNINDEXED, title, ref_id, tags, body, tokenize='{tok}')")
COLUMNS = "uid, source, path, kind, ref_id, title, status, tags, description, body, commit_sha, extra"
D_COLUMNS = ", ".join("d." + c.strip() for c in COLUMNS.split(","))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def body_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def fts_query(text: str, joiner: str = " ") -> str | None:
    tokens = re.findall(r"\w+", text or "", re.UNICODE)
    long_tokens = [t for t in tokens if len(t) > 1] or tokens
    if not long_tokens:
        return None
    return joiner.join(f'"{t}"*' for t in long_tokens[:24])


def _filters(sql: str, args: list, kind=None, source=None, sources=None, prefix: str = "") -> str:
    if kind:
        sql += f" AND {prefix}kind = ?"
        args.append(kind)
    if source:
        sql += f" AND {prefix}source = ?"
        args.append(source)
    if sources is not None:
        if not sources:
            return sql + " AND 0"
        sql += f" AND {prefix}source IN ({','.join('?' * len(sources))})"
        args.extend(sources)
    return sql


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
                for table in ("documents", "documents_fts", "vectors"):
                    self.conn.execute(f"DELETE FROM {table} WHERE uid = ?", (uid,))

    def replace_source(self, source: str, docs: list[Document]) -> None:
        with self.conn:
            self._delete_source(source)
            for d in docs:
                self._insert(d)

    def _delete_source(self, source: str) -> None:
        for table in ("documents_fts", "vectors"):
            self.conn.execute(f"DELETE FROM {table} WHERE uid IN (SELECT uid FROM documents WHERE source = ?)", (source,))
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

    def count(self, source: str | None = None, kind: str | None = None, sources: list[str] | None = None) -> int:
        args: list = []
        sql = _filters("SELECT COUNT(*) FROM documents WHERE 1=1", args, kind, source, sources)
        return self.conn.execute(sql, args).fetchone()[0]

    def counts_by_kind(self, source: str | None = None, sources: list[str] | None = None) -> dict[str, int]:
        args: list = []
        sql = _filters("SELECT kind, COUNT(*) FROM documents WHERE 1=1", args, None, source, sources)
        return {k: n for k, n in self.conn.execute(sql + " GROUP BY kind", args)}

    def list(self, kind: str | None = None, source: str | None = None, limit: int | None = None,
             sources: list[str] | None = None) -> list[Document]:
        args: list = []
        sql = _filters(f"SELECT {COLUMNS} FROM documents WHERE 1=1", args, kind, source, sources)
        sql += " ORDER BY kind, ref_id COLLATE NOCASE, path"
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        return [self._doc(r) for r in self.conn.execute(sql, args)]

    def find(self, kind: str | None, ref_id: str, sources: list[str] | None = None) -> list[Document]:
        args: list = [ref_id]
        sql = _filters(f"SELECT {COLUMNS} FROM documents WHERE ref_id = ? COLLATE NOCASE", args, kind, None, sources)
        return [self._doc(r) for r in self.conn.execute(sql, args)]

    def search(self, text: str, kind: str | None = None, source: str | None = None,
               limit: int = 10, sources: list[str] | None = None) -> list[tuple[Document, str]]:
        results: list[tuple[Document, str]] = []
        for joiner in (" ", " OR "):
            q = fts_query(text, joiner)
            if not q:
                return []
            args: list = [q]
            sql = (f"SELECT {D_COLUMNS}, snippet(documents_fts, 4, '«', '»', ' … ', 18) AS snip "
                   "FROM documents_fts JOIN documents d ON d.uid = documents_fts.uid WHERE documents_fts MATCH ?")
            sql = _filters(sql, args, kind, source, sources, prefix="d.")
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

    def snapshot(self, source: str) -> dict[str, dict]:
        """Estado resumido de cada documento da fonte (para calcular o que mudou num sync)."""
        rows = self.conn.execute("SELECT uid, kind, ref_id, title, status, body FROM documents WHERE source = ?",
                                 (source,))
        return {r["uid"]: {"kind": r["kind"], "ref": f"{r['kind']}:{r['ref_id']}" if r["ref_id"] else r["uid"],
                           "title": r["title"], "status": r["status"], "hash": body_hash(r["body"] or "")}
                for r in rows}

    # ------------------------------------------------------------ estado das fontes
    def set_state(self, name: str, commit: str | None, error: str | None) -> None:
        with self.conn:
            prev = self.get_state(name)
            commit = commit or (prev or {}).get("commit_sha")
            self.conn.execute(
                "INSERT OR REPLACE INTO sources_state(name, commit_sha, last_sync, last_error) VALUES (?,?,?,?)",
                (name, commit, now(), error),
            )

    def get_state(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM sources_state WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def last_sync(self) -> datetime | None:
        row = self.conn.execute("SELECT MAX(last_sync) FROM sources_state").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    # ------------------------------------------------------------ novidades
    def add_changes(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.conn:
            for r in rows:
                self.conn.execute(
                    "INSERT INTO changes(ts, source, uid, ref, title, kind, change, detail, commit_sha) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (r.get("ts") or now(), r["source"], r.get("uid"), r.get("ref"), r.get("title"), r.get("kind"),
                     r["change"], r.get("detail"), r.get("commit_sha")),
                )

    def changes(self, since: datetime | None = None, sources: list[str] | None = None, limit: int = 200,
                after_id: int | None = None) -> list[dict]:
        args: list = []
        sql = "SELECT * FROM changes WHERE 1=1"
        if since:
            sql += " AND ts >= ?"
            args.append(since.astimezone(timezone.utc).isoformat(timespec="seconds"))
        if after_id is not None:
            sql += " AND id > ?"
            args.append(after_id)
        sql = _filters(sql, args, None, None, sources)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in self.conn.execute(sql, args)]

    def last_change_id(self) -> int:
        return self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM changes").fetchone()[0]

    # ------------------------------------------------------------ uso pelo agente
    def log_usage(self, tool: str, query: str | None = None, uid: str | None = None, source: str | None = None,
                  hits: int | None = None) -> None:
        try:
            with self.conn:
                self.conn.execute("INSERT INTO usage(ts, tool, query, uid, source, hits) VALUES (?,?,?,?,?,?)",
                                  (now(), tool, query, uid, source, hits))
        except sqlite3.Error:
            pass  # registrar uso nunca pode quebrar uma resposta ao agente

    def usage_report(self, days: int = 30) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        c = self.conn
        total = c.execute("SELECT COUNT(*) FROM usage WHERE ts >= ?", (since,)).fetchone()[0]
        by_tool = dict(c.execute("SELECT tool, COUNT(*) FROM usage WHERE ts >= ? GROUP BY tool ORDER BY 2 DESC",
                                 (since,)).fetchall())
        top = [dict(r) for r in c.execute(
            "SELECT u.uid, d.kind, d.ref_id, d.title, d.source, COUNT(*) AS n FROM usage u "
            "JOIN documents d ON d.uid = u.uid WHERE u.ts >= ? AND u.uid IS NOT NULL "
            "GROUP BY u.uid ORDER BY n DESC LIMIT 15", (since,))]
        gaps = [dict(r) for r in c.execute(
            "SELECT query, COUNT(*) AS n, MAX(ts) AS last FROM usage WHERE ts >= ? AND hits = 0 AND query IS NOT NULL "
            "GROUP BY lower(query) ORDER BY n DESC, last DESC LIMIT 15", (since,))]
        unread = [dict(r) for r in c.execute(
            "SELECT d.uid, d.kind, d.ref_id, d.title, d.source FROM documents d WHERE d.kind != 'skill' AND d.uid NOT IN "
            "(SELECT uid FROM usage WHERE uid IS NOT NULL AND ts >= ?) ORDER BY d.kind, d.ref_id", (since,))]
        recent = [dict(r) for r in c.execute("SELECT * FROM usage ORDER BY id DESC LIMIT 20")]
        return {"days": days, "total": total, "by_tool": by_tool, "top": top, "gaps": gaps, "unread": unread,
                "recent": recent}

    # ------------------------------------------------------------ vetores (busca por significado)
    def vector_hashes(self, model: str) -> dict[str, str]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT uid, hash FROM vectors WHERE model = ?", (model,))}

    def put_vectors(self, model: str, items: list[tuple[str, str, bytes]]) -> None:
        with self.conn:
            for uid, h, blob in items:
                self.conn.execute("INSERT OR REPLACE INTO vectors(uid, model, hash, vec) VALUES (?,?,?,?)",
                                  (uid, model, h, blob))

    def prune_vectors(self, model: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM vectors WHERE model != ? OR uid NOT IN (SELECT uid FROM documents)", (model,))

    def vectors(self, model: str, kind: str | None = None, sources: list[str] | None = None) -> list[tuple[str, bytes]]:
        args: list = [model]
        sql = _filters("SELECT v.uid, v.vec FROM vectors v JOIN documents d ON d.uid = v.uid WHERE v.model = ?",
                       args, kind, None, sources, prefix="d.")
        return [(r[0], r[1]) for r in self.conn.execute(sql, args)]
