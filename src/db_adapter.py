"""
Unified Database Adapter for SQLite and Supabase PostgreSQL.
Provides a transparent, drop-in replacement interface for:
- Connection, Cursor, Row, and Transaction management
- Automatic '?' to '%s' parameter placeholder conversion for PostgreSQL
- Automatic 'RETURNING id' injection for PostgreSQL cursor.lastrowid compatibility
- SQLite PRAGMA filtering on PostgreSQL
- Dictionary and index-based column access (row['name'] and row[0])
"""

import os
import re
from typing import Any, List, Dict, Optional, Tuple

class AdaptableRow:
    """Hem dict (row['name']) hem index (row[0]) erişimini destekleyen satır nesnesi."""
    def __init__(self, columns: List[str], values: Tuple[Any, ...]):
        self._columns = [c.lower() for c in columns]
        self._values = list(values)
        self._mapping = {c: v for c, v in zip(self._columns, self._values)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[str(key).lower()]

    def __contains__(self, key):
        return str(key).lower() in self._mapping

    def get(self, key, default=None):
        return self._mapping.get(str(key).lower(), default)

    def keys(self):
        return self._columns

    def values(self):
        return self._values

    def items(self):
        return self._mapping.items()

    def __repr__(self):
        return f"AdaptableRow({self._mapping})"

class PostgresCursorWrapper:
    def __init__(self, pg_cursor, is_postgres: bool = True):
        self._cursor = pg_cursor
        self.is_postgres = is_postgres
        self.lastrowid = None

    def _adapt_query(self, query: str) -> Tuple[str, bool]:
        """Sorguyu PostgreSQL sözdizimine uyarlar."""
        sql = query.strip()

        # SQLite PRAGMA komutlarını PostgreSQL'de yok say
        if sql.upper().startswith("PRAGMA"):
            return "SELECT 1;", False

        # SQLite datetime fonksiyon uyarlamaları
        sql = re.sub(r"datetime\('now',\s*'-(\d+)\s+seconds'\)", r"NOW() - INTERVAL '\1 seconds'", sql, flags=re.IGNORECASE)
        sql = re.sub(r"datetime\('now'\)", r"NOW()", sql, flags=re.IGNORECASE)
        sql = re.sub(r"strftime\('%Y-%m-%d',\s*([^)]+)\)", r"TO_CHAR(\1, 'YYYY-MM-DD')", sql, flags=re.IGNORECASE)
        sql = re.sub(r"strftime\('%Y-%m',\s*([^)]+)\)", r"TO_CHAR(\1, 'YYYY-MM')", sql, flags=re.IGNORECASE)
        sql = re.sub(r"strftime\('%Y',\s*([^)]+)\)", r"TO_CHAR(\1, 'YYYY')", sql, flags=re.IGNORECASE)

        # ? placeholder'larını %s'e dönüştür
        sql = sql.replace("?", "%s")

        # INSERT sorgularında lastrowid için RETURNING id ekle
        needs_returning = False
        if sql.upper().startswith("INSERT INTO") and "RETURNING" not in sql.upper():
            # Noktalı virgülü kaldır ve RETURNING id ekle
            sql = sql.rstrip(";") + " RETURNING id;"
            needs_returning = True

        return sql, needs_returning

    def execute(self, query: str, params: Optional[Any] = None):
        self.lastrowid = None
        if not self.is_postgres:
            if params is not None:
                res = self._cursor.execute(query, params)
            else:
                res = self._cursor.execute(query)
            self.lastrowid = getattr(self._cursor, "lastrowid", None)
            return self

        sql, needs_returning = self._adapt_query(query)
        if sql == "SELECT 1;":
            return self

        # Parametreleri tuple/list olarak geçir
        args = params if params is not None else ()
        if isinstance(args, (list, tuple)):
            self._cursor.execute(sql, list(args))
        else:
            self._cursor.execute(sql, args)

        if needs_returning:
            try:
                row = self._cursor.fetchone()
                if row:
                    self.lastrowid = row[0]
            except Exception:
                pass
        return self

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def executemany(self, query: str, seq_of_params):
        if not self.is_postgres:
            return self._cursor.executemany(query, seq_of_params)
        sql, _ = self._adapt_query(query)
        return self._cursor.executemany(sql, seq_of_params)

    def executescript(self, script: str):
        if not self.is_postgres:
            return self._cursor.executescript(script)
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                self.execute(stmt)

    def fetchone(self):
        row = self._cursor.fetchone()
        if not row:
            return None
        if not self.is_postgres:
            return row
        if hasattr(self._cursor, "description") and self._cursor.description:
            cols = [col[0] for col in self._cursor.description]
            return AdaptableRow(cols, row)
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        if not self.is_postgres:
            return rows
        if hasattr(self._cursor, "description") and self._cursor.description:
            cols = [col[0] for col in self._cursor.description]
            return [AdaptableRow(cols, r) for r in rows]
        return rows

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

class DBConnectionWrapper:
    """PostgreSQL ve SQLite bağlantılarını tek bir arayüz arkasında birleştiren wrapper."""
    def __init__(self, raw_conn, is_postgres: bool = True):
        self._raw_conn = raw_conn
        self.is_postgres = is_postgres

    def cursor(self):
        return PostgresCursorWrapper(self._raw_conn.cursor(), is_postgres=self.is_postgres)

    def execute(self, query: str, params: Optional[Any] = None):
        cur = self.cursor()
        if params is not None:
            cur.execute(query, params)
        else:
            cur.execute(query)
        return cur

    def commit(self):
        self._raw_conn.commit()

    def rollback(self):
        try:
            self._raw_conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._raw_conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()

    def __getattr__(self, name):
        return getattr(self._raw_conn, name)

def get_postgres_connection(db_url: str) -> DBConnectionWrapper:
    """Supabase / PostgreSQL veritabanına bağlanır (psycopg2 veya pg8000)."""
    # 1. psycopg2 dene
    try:
        import psycopg2
        import psycopg2.extensions
        # URL içinde sslmode yoksa otomatik ekle (Supabase için zorunludur)
        url = db_url
        if "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
        conn = psycopg2.connect(url)
        return DBConnectionWrapper(conn, is_postgres=True)
    except ImportError:
        pass

    # 2. pg8000 (Pure Python DB-API 2.0) dene
    try:
        import pg8000.dbapi
        import urllib.parse
        parsed = urllib.parse.urlparse(db_url)
        conn = pg8000.dbapi.connect(
            user=parsed.username,
            password=parsed.password,
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=parsed.path.lstrip("/"),
            ssl_context=True
        )
        return DBConnectionWrapper(conn, is_postgres=True)
    except ImportError:
        pass

    raise ImportError(
        "PostgreSQL bağlantısı için 'psycopg2-binary' veya 'pg8000' kütüphanesi kurulu olmalıdır.\n"
        "Kurulum için: pip install psycopg2-binary"
    )
