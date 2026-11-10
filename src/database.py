"""
Database models, WAL mode configuration, and Schema Migration Engine.
Ensures:
- WAL (Write-Ahead Logging) mode for crash resilience and concurrency
- Foreign keys enforcement
- PRAGMA synchronous = NORMAL for maximum ACID durability and high performance
- Safe, idempotent schema migrations without overwriting existing data.
"""

import sqlite3
from pathlib import Path
from src.config import get_database_path, is_postgres_enabled, get_database_url
from src.db_adapter import DBConnectionWrapper, get_postgres_connection

CURRENT_SCHEMA_VERSION = 3

SCHEMA_SQL_V3 = """
-- 12. KULLANICI YÖNETİMİ & GÜVENLİK (APP_USERS)
CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    full_name TEXT,
    role TEXT DEFAULT 'ADMIN',
    active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 13. OTURUM YÖNETİMİ (APP_SESSIONS)
CREATE TABLE IF NOT EXISTS app_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    expires_at DATETIME NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES app_users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON app_sessions(token);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON app_sessions(user_id);
"""

SCHEMA_SQL_V2 = """
-- 10. DÖVİZ BAĞLANTI İŞLEMLERİ (FX_CONTRACTS)
CREATE TABLE IF NOT EXISTS fx_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_number TEXT UNIQUE NOT NULL,
    account_id INTEGER NOT NULL,
    sell_currency TEXT NOT NULL,
    sell_amount TEXT NOT NULL,
    buy_currency TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    total_target_amount TEXT NOT NULL,
    collected_amount TEXT DEFAULT '0.00',
    remaining_amount TEXT NOT NULL,
    contract_date DATE NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'PENDING',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT,
    FOREIGN KEY (sell_currency) REFERENCES currencies(code) ON DELETE RESTRICT,
    FOREIGN KEY (buy_currency) REFERENCES currencies(code) ON DELETE RESTRICT
);

-- 11. BAĞLANTI PARÇALI TAHSİLAT HAREKETLERİ (FX_CONTRACT_PAYMENTS)
CREATE TABLE IF NOT EXISTS fx_contract_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL,
    transaction_id INTEGER,
    amount TEXT NOT NULL,
    currency_code TEXT NOT NULL,
    payment_date DATE NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contract_id) REFERENCES fx_contracts(id) ON DELETE CASCADE,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE SET NULL,
    FOREIGN KEY (currency_code) REFERENCES currencies(code) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_contracts_account ON fx_contracts(account_id);
CREATE INDEX IF NOT EXISTS idx_contracts_status ON fx_contracts(status);
CREATE INDEX IF NOT EXISTS idx_contracts_date ON fx_contracts(contract_date);
CREATE INDEX IF NOT EXISTS idx_contract_payments_contract ON fx_contract_payments(contract_id);
"""

SCHEMA_SQL_V1 = """
PRAGMA foreign_keys = ON;

-- 0. SCHEMA MIGRATION VERSIONS TABLE
CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- 1. PARA BİRİMLERİ (CURRENCIES)
CREATE TABLE IF NOT EXISTS currencies (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    is_base INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. GÜNLÜK / GEÇMİŞ KURLAR (EXCHANGE_RATES)
CREATE TABLE IF NOT EXISTS exchange_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rate_date DATE NOT NULL,
    currency_code TEXT NOT NULL,
    buying_rate TEXT NOT NULL,
    selling_rate TEXT NOT NULL,
    source TEXT DEFAULT 'MANUAL',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (currency_code) REFERENCES currencies(code) ON DELETE RESTRICT,
    UNIQUE(rate_date, currency_code)
);

-- 3. CARİ HESAPLAR / KİŞİLER (ACCOUNTS)
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL,
    tax_office TEXT,
    tax_number TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    authorized_person TEXT,
    description TEXT,
    default_currency TEXT DEFAULT 'TRY',
    active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (default_currency) REFERENCES currencies(code) ON DELETE RESTRICT
);

-- 4. KASA VE BANKA HESAPLARI (CASH_BANKS)
CREATE TABLE IF NOT EXISTS cash_banks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    currency_code TEXT NOT NULL,
    account_number TEXT,
    bank_name TEXT,
    branch_name TEXT,
    description TEXT,
    active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (currency_code) REFERENCES currencies(code) ON DELETE RESTRICT
);

-- 5. İŞLEMLER (TRANSACTIONS)
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_number TEXT UNIQUE NOT NULL,
    tx_date DATE NOT NULL,
    tx_type TEXT NOT NULL,
    reference_no TEXT,
    description TEXT,
    status TEXT DEFAULT 'CONFIRMED',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    cancelled_at DATETIME,
    cancellation_reason TEXT
);

-- 6. İŞLEM HAREKETLERİ (TRANSACTION_LINES)
CREATE TABLE IF NOT EXISTS transaction_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL,
    line_no INTEGER NOT NULL,
    account_id INTEGER,
    cash_bank_id INTEGER,
    direction TEXT NOT NULL,
    currency_code TEXT NOT NULL,
    amount TEXT NOT NULL,
    exchange_rate TEXT NOT NULL,
    base_amount TEXT NOT NULL,
    description TEXT,
    due_date DATE,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT,
    FOREIGN KEY (cash_bank_id) REFERENCES cash_banks(id) ON DELETE RESTRICT,
    FOREIGN KEY (currency_code) REFERENCES currencies(code) ON DELETE RESTRICT
);

-- 7. DÖVİZ PARTİLERİ (FX_LOTS)
CREATE TABLE IF NOT EXISTS fx_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_number TEXT UNIQUE NOT NULL,
    account_id INTEGER,
    cash_bank_id INTEGER,
    transaction_line_id INTEGER,
    entry_date DATE NOT NULL,
    currency_code TEXT NOT NULL,
    initial_amount TEXT NOT NULL,
    remaining_amount TEXT NOT NULL,
    exchange_rate TEXT NOT NULL,
    base_amount TEXT NOT NULL,
    status TEXT DEFAULT 'OPEN',
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE RESTRICT,
    FOREIGN KEY (cash_bank_id) REFERENCES cash_banks(id) ON DELETE RESTRICT,
    FOREIGN KEY (transaction_line_id) REFERENCES transaction_lines(id) ON DELETE SET NULL,
    FOREIGN KEY (currency_code) REFERENCES currencies(code) ON DELETE RESTRICT
);

-- 8. PARTİ ÇIKIŞ TAHSİSLERİ (FX_LOT_ALLOCATIONS)
CREATE TABLE IF NOT EXISTS fx_lot_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER NOT NULL,
    transaction_line_id INTEGER NOT NULL,
    allocated_amount TEXT NOT NULL,
    exit_rate TEXT NOT NULL,
    entry_rate TEXT NOT NULL,
    profit_loss TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lot_id) REFERENCES fx_lots(id) ON DELETE RESTRICT,
    FOREIGN KEY (transaction_line_id) REFERENCES transaction_lines(id) ON DELETE CASCADE
);

-- 9. DENETİM VE GÜVENLİK İZİ (AUDIT_LOGS)
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    entity_id TEXT,
    details TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- İNDEKS LER
CREATE INDEX IF NOT EXISTS idx_tx_lines_account ON transaction_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_tx_lines_currency ON transaction_lines(currency_code);
CREATE INDEX IF NOT EXISTS idx_tx_lines_tx_id ON transaction_lines(transaction_id);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(tx_date);
"""

DEFAULT_CURRENCIES = [
    ("TRY", "Türk Lirası", "₺", 1),
    ("USD", "Amerikan Doları", "$", 0),
    ("EUR", "Euro", "€", 0),
    ("GBP", "İngiliz Sterlini", "£", 0),
    ("CHF", "İsviçre Frangı", "CHF", 0),
    ("JPY", "Japon Yeni", "¥", 0),
    ("SAR", "Suudi Arabistan Riyali", "SR", 0),
    ("AED", "BAE Dirhemi", "DH", 0),
    ("RUB", "Rus Rublesi", "₽", 0),
    ("CNY", "Çin Yuanı", "¥", 0),
]

def apply_migrations(conn: sqlite3.Connection):
    """
    Güvenli Şema Migrasyon Sistemi:
    Mevcut verileri silmeden veya üzerine yazmadan versiyon kontrolü ile şemayı günceller.
    """
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP, description TEXT);")
    cur.execute("SELECT MAX(version) FROM schema_versions;")
    row = cur.fetchone()
    current_ver = row[0] if (row and row[0] is not None) else 0

    if current_ver < 1:
        conn.executescript(SCHEMA_SQL_V1)
        cur.execute("INSERT OR REPLACE INTO schema_versions (version, description) VALUES (1, 'V1: Temel Doviz Borc-Alacak Semasi');")
        for code, name, symbol, is_base in DEFAULT_CURRENCIES:
            cur.execute(
                "INSERT OR IGNORE INTO currencies (code, name, symbol, is_base) VALUES (?, ?, ?, ?)",
                (code, name, symbol, is_base)
            )
        conn.commit()

    if current_ver < 2:
        conn.executescript(SCHEMA_SQL_V2)
        cur.execute("INSERT OR REPLACE INTO schema_versions (version, description) VALUES (2, 'V2: Doviz Baglanti Islemleri (FX Contracts)');")
        conn.commit()

    if current_ver < 3:
        conn.executescript(SCHEMA_SQL_V3)
        cur.execute("INSERT OR REPLACE INTO schema_versions (version, description) VALUES (3, 'V3: Guvenlik, Kullanici ve Oturum Yonetimi (Auth)');")
        conn.commit()
        # Varsayılan admin kullanıcısını oluştur
        from src.auth_service import AuthService
        auth = AuthService(conn)
        auth.init_default_admin(default_username="admin", default_password="admin123")

def init_db(db_path: str = None):
    """
    Veritabanını başlatır.
    - DATABASE_URL tanımlıysa: Supabase / PostgreSQL bağlantısı döner.
    - Tanımlı değilse: Kalıcı yerel SQLite (WAL modlu) bağlantısı döner.
    """
    # 1. Supabase / PostgreSQL Kontrolü
    if db_path is None and is_postgres_enabled():
        return get_postgres_connection(get_database_url())

    # 2. SQLite Başlatma
    if db_path is None:
        target_path = get_database_path()
    else:
        target_path = Path(db_path)

    if str(target_path) != ":memory:":
        target_path.parent.mkdir(parents=True, exist_ok=True)

    raw_conn = sqlite3.connect(str(target_path), timeout=30.0)
    
    # Foreign Key kısıtlamaları
    raw_conn.execute("PRAGMA foreign_keys = ON;")
    
    # WAL ve Senkronizasyon ayarları
    if str(target_path) != ":memory:":
        raw_conn.execute("PRAGMA journal_mode = WAL;")
        raw_conn.execute("PRAGMA synchronous = NORMAL;")
        raw_conn.execute("PRAGMA busy_timeout = 10000;")

    raw_conn.row_factory = sqlite3.Row

    # Güvenli migrasyonları uygula
    apply_migrations(raw_conn)

    return DBConnectionWrapper(raw_conn, is_postgres=False)
