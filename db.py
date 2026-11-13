"""Base SQLite locale : un seul fichier, aucune installation."""
import os
import sqlite3

DB_PATH = os.environ.get(
    "SEOLIBRE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.sqlite")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL,
  lang TEXT NOT NULL DEFAULT 'fr', country TEXT NOT NULL DEFAULT 'FR');
CREATE TABLE IF NOT EXISTS keywords(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL, engine TEXT NOT NULL,
  last_checked TEXT, last_error TEXT,
  UNIQUE(project_id, keyword, engine));
CREATE TABLE IF NOT EXISTS ranks(
  id INTEGER PRIMARY KEY,
  keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
  date TEXT NOT NULL, position REAL, url TEXT,
  UNIQUE(keyword_id, date));
CREATE TABLE IF NOT EXISTS serp(
  keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
  date TEXT NOT NULL, position INTEGER NOT NULL, url TEXT NOT NULL, domain TEXT NOT NULL,
  PRIMARY KEY(keyword_id, date, position));
CREATE TABLE IF NOT EXISTS gap_probe(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  competitor TEXT NOT NULL, keyword TEXT NOT NULL, date TEXT NOT NULL,
  my_pos INTEGER, my_url TEXT, comp_pos INTEGER, comp_url TEXT,
  PRIMARY KEY(project_id, competitor, keyword));
CREATE TABLE IF NOT EXISTS my_competitors(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  domain TEXT NOT NULL, PRIMARY KEY(project_id, domain));
CREATE TABLE IF NOT EXISTS probe_serp(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL, date TEXT NOT NULL, position INTEGER NOT NULL,
  url TEXT NOT NULL, domain TEXT NOT NULL,
  PRIMARY KEY(project_id, keyword, position));
CREATE TABLE IF NOT EXISTS kw_ideas(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL, source TEXT NOT NULL,
  UNIQUE(project_id, keyword, source));
CREATE TABLE IF NOT EXISTS trends(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL, date TEXT NOT NULL, value REAL,
  UNIQUE(project_id, keyword, date));
CREATE TABLE IF NOT EXISTS trend_meta(
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  keyword TEXT NOT NULL, period TEXT NOT NULL, fetched TEXT NOT NULL,
  PRIMARY KEY(project_id, keyword));
CREATE TABLE IF NOT EXISTS crawls(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  source TEXT NOT NULL, started TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pages(
  id INTEGER PRIMARY KEY,
  crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
  url TEXT NOT NULL, status INTEGER, content_type TEXT,
  title TEXT, meta_desc TEXT, h1 TEXT, h1_count INTEGER,
  word_count INTEGER, depth INTEGER, canonical TEXT, indexable INTEGER,
  inlinks INTEGER, response_ms INTEGER, redirect_to TEXT);
CREATE INDEX IF NOT EXISTS pages_crawl ON pages(crawl_id);
CREATE TABLE IF NOT EXISTS links(
  crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
  source TEXT NOT NULL, target TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS links_target ON links(crawl_id, target);
"""

PAGE_COLS = ["url", "status", "content_type", "title", "meta_desc", "h1", "h1_count",
             "word_count", "depth", "canonical", "indexable", "inlinks", "response_ms",
             "redirect_to"]


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init():
    con = connect()
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def q(sql, args=()):
    con = connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql, args=()):
    con = connect()
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def xmany(sql, seq):
    con = connect()
    try:
        con.executemany(sql, seq)
        con.commit()
    finally:
        con.close()


def insert_pages(con, crawl_id, pages):
    cols = ", ".join(PAGE_COLS)
    marks = ", ".join("?" * len(PAGE_COLS))
    con.executemany(
        f"INSERT INTO pages(crawl_id, {cols}) VALUES(?, {marks})",
        [[crawl_id] + [p.get(c) for c in PAGE_COLS] for p in pages],
    )
