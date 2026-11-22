import sqlite3
from contextlib import contextmanager
from pathlib import Path
from app import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, source BLOB NOT NULL, book BLOB NOT NULL,
  result TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  run_id TEXT NOT NULL REFERENCES runs(id), version INTEGER NOT NULL,
  decision TEXT NOT NULL, note TEXT NOT NULL, changed TEXT NOT NULL,
  PRIMARY KEY(run_id,version)
);
CREATE TABLE IF NOT EXISTS disburse_batches (
  batch_no INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL,
  contract_hash TEXT NOT NULL,
  estate_hash TEXT NOT NULL,
  balance_hash TEXT NOT NULL,
  contract_total TEXT NOT NULL,
  billed_total TEXT NOT NULL,
  report TEXT NOT NULL,
  source_blob BLOB NOT NULL,
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS disburse_bills (
  batch_no INTEGER NOT NULL REFERENCES disburse_batches(batch_no),
  building TEXT NOT NULL,
  household TEXT NOT NULL,
  owner TEXT NOT NULL,
  amount TEXT NOT NULL,
  area TEXT NOT NULL,
  PRIMARY KEY(batch_no,building,household)
);
CREATE TABLE IF NOT EXISTS disburse_ledger (
  batch_no INTEGER NOT NULL,
  building TEXT NOT NULL,
  household TEXT NOT NULL,
  owner TEXT NOT NULL,
  amount TEXT NOT NULL,
  status TEXT NOT NULL,
  PRIMARY KEY(batch_no,building,household)
);
"""


@contextmanager
def connection():
    path = Path(settings.DB)
    path.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(path,timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys = ON')
        db.executescript(SCHEMA)
        with db:
            yield db
    finally:
        db.close()
