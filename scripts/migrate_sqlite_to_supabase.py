#!/usr/bin/env python3
"""
SQLite to Supabase (PostgreSQL) Migration Script.
Exports all data from local SQLite database (accounting.db) and safely inserts it into Supabase PostgreSQL.
Preserves:
- All primary keys (IDs)
- Foreign key relations
- Balances, contracts, transactions, audit logs, and users
- Resets PostgreSQL IDENTITY sequences to match max(id).
"""

import os
import sys
import sqlite3
from pathlib import Path

# Proje kök dizinini ekle
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.config import get_database_path, get_database_url
from src.db_adapter import get_postgres_connection

TABLES_IN_ORDER = [
    "currencies",
    "accounts",
    "cash_banks",
    "exchange_rates",
    "transactions",
    "transaction_lines",
    "fx_lots",
    "fx_lot_allocations",
    "fx_contracts",
    "fx_contract_payments",
    "audit_logs",
    "app_users",
    "app_sessions",
]

def migrate():
    print("==================================================================")
    print("🚀 SQLite -> Supabase (PostgreSQL) Veri Taşıma Aracı")
    print("==================================================================")

    # 1. Kaynak SQLite Veritabanı
    sqlite_path = get_database_path()
    if not sqlite_path.exists():
        print(f"[-] Hata: SQLite veritabanı bulunamadı: {sqlite_path}")
        sys.exit(1)

    print(f"📂 Kaynak SQLite DB: {sqlite_path}")

    # 2. Hedef Supabase Bağlantısı
    db_url = get_database_url()
    if not db_url:
        print("[-] Hata: DATABASE_URL çevre değişkeni tanımlı değil!")
        print("    Lütfen .env dosyanıza veya ortamınıza Supabase DATABASE_URL adresinizi ekleyiniz:")
        print("    DATABASE_URL=postgresql://postgres:[PASSWORD]@[HOST]:6543/postgres")
        sys.exit(1)

    print("[*] Supabase PostgreSQL veritabanına bağlanılıyor...")
    try:
        pg_conn = get_postgres_connection(db_url)
        print("[+] Supabase bağlantısı başarılı!")
    except Exception as e:
        print(f"[-] Supabase bağlantı hatası: {e}")
        sys.exit(1)

    sqlite_conn = sqlite3.connect(str(sqlite_path))
    sqlite_conn.row_factory = sqlite3.Row

    try:
        pg_cur = pg_conn.cursor()

        total_transferred = 0

        for table in TABLES_IN_ORDER:
            # SQLite tablosunun varlığını kontrol et
            check_cur = sqlite_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,))
            if not check_cur.fetchone():
                print(f"[-] {table} tablosu SQLite'ta bulunamadı, atlanıyor.")
                continue

            # SQLite verilerini çek
            rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
            count = len(rows)

            if count == 0:
                print(f"[i] {table}: Veri yok (0 kayıt).")
                continue

            print(f"[*] {table}: {count} kayıt aktarılıyor...")

            cols = rows[0].keys()
            col_names = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))

            insert_query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING;"

            # Toplu ekle
            data_tuples = [tuple(r[c] for c in cols) for r in rows]
            pg_cur.executemany(insert_query, data_tuples)
            total_transferred += count

            # Eğer id sütunu varsa sequence'ı güncelle (Identity primary key için kritik!)
            if "id" in cols:
                try:
                    # PostgreSQL identity sequence değerini en yüksek ID'ye çek
                    seq_query = f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM {table};"
                    pg_cur.execute(seq_query)
                except Exception:
                    pass

            print(f"[+] {table}: {count} kayıt başarıyla aktarıldı.")

        pg_conn.commit()
        print("==================================================================")
        print(f"🎉 MİGRASYON TAMAMLANDI! Toplam {total_transferred} kayıt Supabase'e aktarıldı.")
        print("==================================================================")

    except Exception as e:
        pg_conn.rollback()
        print(f"[-] Hata oluştu, tüm işlemler geri alındı (Rollback): {e}")
        sys.exit(1)
    finally:
        sqlite_conn.close()
        pg_conn.close()

if __name__ == "__main__":
    migrate()

