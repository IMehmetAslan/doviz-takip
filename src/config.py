"""
Path and Environment Configuration for Permanent Data Storage.
Determines OS-specific user data directory, database path, backup directory, and exports directory.
Ensures data persistence across app restarts, re-installations, and code updates.
Gracefully handles restricted sandboxes by falling back to workspace persistent directory if needed.
"""

import os
import platform
import shutil
import sqlite3
from pathlib import Path

APP_NAME = "DovizBorcAlacakTakip"

# İsteğe bağlı ortam değişkeni ile veri dizini ezilebilir (örn. testler veya özel kurulumlar için)
CUSTOM_DATA_DIR_ENV = "DOVIZ_TAKIP_DATA_DIR"

def get_user_data_dir() -> Path:
    """
    İşletim sistemine göre kalıcı kullanıcı veri dizinini döner:
    - Özel Env: DOVIZ_TAKIP_DATA_DIR tanımlıysa onu kullanır
    - macOS: ~/Library/Application Support/DovizBorcAlacakTakip
    - Windows: %APPDATA%/DovizBorcAlacakTakip
    - Linux: ~/.local/share/DovizBorcAlacakTakip
    - Sandbox / Korumalı Ortam: Eğer OS seviyesinde erişim engellenirse, güvenli yerel data klasörünü kullanır.
    """
    custom_dir = os.environ.get(CUSTOM_DATA_DIR_ENV)
    if custom_dir:
        p = Path(custom_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    sys_name = platform.system()
    home = Path.home()

    if sys_name == "Darwin":
        candidate = home / "Library" / "Application Support" / APP_NAME
    elif sys_name == "Windows":
        app_data = os.environ.get("APPDATA")
        candidate = Path(app_data) / APP_NAME if app_data else home / "AppData" / "Roaming" / APP_NAME
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        candidate = Path(xdg_data) / APP_NAME if xdg_data else home / ".local" / "share" / APP_NAME

    try:
        candidate.mkdir(parents=True, exist_ok=True)
        # Yazma testi
        test_file = candidate / ".perm_check"
        test_file.touch()
        test_file.unlink()
        return candidate
    except Exception:
        # Sandbox kısıtı varsa, workspace içindeki güvenli kalıcı storage dizinine geç
        fallback = Path(__file__).resolve().parent.parent / "persistent_data"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

def get_database_path() -> Path:
    """Kalıcı veritabanı dosyasının mutlak yolunu döner."""
    return get_user_data_dir() / "accounting.db"

def get_backups_dir() -> Path:
    """Otomatik ve manuel yedeklerin tutulacağı ayrı yedek klasörünü döner."""
    b_dir = get_user_data_dir() / "backups"
    b_dir.mkdir(parents=True, exist_ok=True)
    return b_dir

def get_exports_dir() -> Path:
    """Dışa aktarılan ZIP / harici yedeklerin geçici/indirme klasörünü döner."""
    e_dir = get_user_data_dir() / "exports"
    e_dir.mkdir(parents=True, exist_ok=True)
    return e_dir

def load_env_file():
    """Varsa .env dosyasındaki değişkenleri yükler."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v

load_env_file()

def get_database_url() -> str:
    """Supabase / PostgreSQL bağlantı URL'sini döner (tanımlı değilse boş döner)."""
    return os.environ.get("DATABASE_URL", "").strip()

def is_postgres_enabled() -> bool:
    """PostgreSQL / Supabase modunun aktif olup olmadığını belirtir."""
    url = get_database_url()
    return bool(url and (url.startswith("postgres://") or url.startswith("postgresql://")))

def migrate_legacy_data_if_needed(legacy_db_path: str = "data/accounting.db"):
    """
    Eğer eski uygulama klasöründe (data/accounting.db) bir veritabanı varsa ve 
    kalıcı kullanıcı dizininde henüz veritabanı yoksa, mevcut verileri kaybetmeden
    güvenli bir şekilde kalıcı kullanıcı veri klasörüne kopyalar ve bütünlüğünü doğrular.
    """
    target_path = get_database_path()
    legacy_path = Path(legacy_db_path)

    # Eğer target ve legacy aynı yolsa bir şey yapma
    if legacy_path.resolve() == target_path.resolve():
        return

    if target_path.exists() and target_path.stat().st_size > 0:
        # Zaten kalıcı veri dizininde veritabanı var, üzerine yazma
        return

    if legacy_path.exists():
        print(f"[!] Mevcut veritabanı tespit edildi: {legacy_path}")
        print(f"[*] Kalıcı kullanıcı veri dizinine güvenle taşınıyor: {target_path}")
        
        # Güvenli kopyalama
        shutil.copy2(legacy_path, target_path)

        # Bütünlük kontrolü
        conn = sqlite3.connect(str(target_path))
        try:
            res = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            if res != "ok":
                raise ValueError(f"Taşınan veritabanı bütünlük hatası: {res}")
            print("[+] Veritabanı başarıyla kalıcı dizine aktarıldı ve doğrulandı.")
        finally:
            conn.close()
