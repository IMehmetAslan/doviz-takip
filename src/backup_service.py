"""
Enterprise-Grade Backup, Restore, Integrity Check & Export Service.
Features:
- Online, non-blocking backups using SQLite Backup API
- Automatic startup backup check
- Pre-transaction safety snapshots
- Daily automatic backups with 30-day retention pruning
- ZIP export for USB/External drive/Cloud transfer
- Restore with pre-restore safety snapshot, integrity verification, and rollback protection
"""

import shutil
import sqlite3
import zipfile
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from src.config import get_database_path, get_backups_dir, get_exports_dir

def check_integrity(db_file_path: Path) -> bool:
    """SQLite veritabanı dosyasının bütünlüğünü kontrol eder (PRAGMA integrity_check)."""
    if not Path(db_file_path).exists():
        return False
    try:
        conn = sqlite3.connect(str(db_file_path))
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        res = cur.fetchone()[0]
        conn.close()
        return res == "ok"
    except Exception:
        return False

def backup_database(
    prefix: str = "manual",
    custom_db_path: Optional[Path] = None,
    custom_backup_dir: Optional[Path] = None
) -> Path:
    """
    SQLite Online Backup API kullanarak veri bütünlüğünü bozmadan,
    kilitleme yapmadan zaman damgalı güvenli bir yedek oluşturur.
    """
    db_path = custom_db_path or get_database_path()
    backup_dir = custom_backup_dir or get_backups_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        raise FileNotFoundError(f"Yedeklenecek veritabanı bulunamadı: {db_path}")

    # Önce kaynak veritabanının bütünlüğünü doğrula
    if not check_integrity(db_path):
        raise ValueError("Kaynak veritabanında bütünlük bozulması tespit edildi! Yedekleme durduruldu.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{prefix}_{timestamp}.db"
    backup_file_path = backup_dir / backup_filename

    # SQLite Backup API: canlı ve güvenli kopya
    src_conn = sqlite3.connect(str(db_path))
    try:
        dst_conn = sqlite3.connect(str(backup_file_path))
        src_conn.backup(dst_conn)
        dst_conn.close()
    finally:
        src_conn.close()

    # Alınan yedeğin bütünlüğünü doğrula
    if not check_integrity(backup_file_path):
        if backup_file_path.exists():
            backup_file_path.unlink()
        raise ValueError("Oluşturulan yedek dosyasının bütünlük doğrulaması başarısız oldu!")

    return backup_file_path

def prune_old_backups(retention_days: int = 30, custom_backup_dir: Optional[Path] = None) -> int:
    """
    Son 30 günden eski olan yedekleri otomatik temizler.
    Kullanıcının diskini gereksiz doldurmaz, ancak son 30 günlük geçmişi korur.
    Silinen dosya sayısını döner.
    """
    backup_dir = custom_backup_dir or get_backups_dir()
    cutoff_time = datetime.now() - timedelta(days=retention_days)
    deleted_count = 0

    for f in backup_dir.glob("backup_*.db"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff_time:
                f.unlink()
                deleted_count += 1
        except Exception:
            pass

    return deleted_count

def perform_startup_backup_if_needed() -> Optional[Path]:
    """
    Uygulama açılışında çağrılır:
    1. Son 30 günden eski yedekleri temizler.
    2. Bugün için henüz 'daily' yedek alınmamışsa otomatik olarak günlük yedek alır.
    """
    prune_old_backups(retention_days=30)

    db_path = get_database_path()
    if not db_path.exists():
        return None

    today_str = datetime.now().strftime("%Y%m%d")
    backup_dir = get_backups_dir()
    
    # Bugün alınmış daily veya auto yedek var mı kontrol et
    today_backups = list(backup_dir.glob(f"backup_daily_{today_str}*.db"))
    if not today_backups:
        return backup_database(prefix="daily")
    return None

def create_pre_action_snapshot() -> Path:
    """Yeni bir borç verme veya ödeme alma öncesinde otomatik güvenlik anlık görüntüsü alır."""
    return backup_database(prefix="pre_action")

def export_backup_zip(custom_db_path: Optional[Path] = None) -> Path:
    """
    Veritabanını, metadata dosyasını (versiyon, zaman damgası, kayıt sayıları)
    tek bir taşınabilir ZIP dosyası haline getirir. USB veya Harici Diske kopyalamaya uygundur.
    """
    db_path = custom_db_path or get_database_path()
    exports_dir = get_exports_dir()
    exports_dir.mkdir(parents=True, exist_ok=True)

    # Önce temiz ve doğrulanmış bir snapshot al
    snapshot_path = backup_database(prefix="export_snapshot")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = exports_dir / f"DovizBorcTakip_Yedek_{timestamp}.zip"

    # Veritabanı istatistikleri
    conn = sqlite3.connect(str(snapshot_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM accounts")
        accounts_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM transactions")
        tx_count = cur.fetchone()[0]
    finally:
        conn.close()

    metadata = {
        "app_name": "Döviz Borç ve Alacak Takip Sistemi",
        "export_date": datetime.now().isoformat(),
        "database_file": "accounting.db",
        "accounts_count": accounts_count,
        "transactions_count": tx_count,
        "integrity": "verified"
    }

    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(snapshot_path), arcname="accounting.db")
        zf.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))

    # Snapshot geçici dosyasını temizle
    if snapshot_path.exists():
        snapshot_path.unlink()

    return zip_path

def restore_database_safely(backup_file_path: Path, target_db_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Kusursuz ve Güvenli Geri Yükleme Motoru:
    1. Yedeğin geçerliliğini ve PRAGMA integrity_check bütünlüğünü doğrular.
    2. Eğer bir ZIP dosyasıysa içindeki accounting.db'yi çıkarıp doğrular.
    3. Mevcut aktif veritabanının otomatik geri dönüş noktasını (pre_restore_safety) alır.
    4. Yedeği hedef veritabanına kopyalar.
    5. Hedef veritabanında tekrar integrity_check yapar.
    6. Hata olursa mevcut verileri anında kurtarıp eski haline döndürür.
    """
    target_path = target_db_path or get_database_path()
    backup_file = Path(backup_file_path)

    if not backup_file.exists():
        raise FileNotFoundError(f"Geri yüklenecek yedek dosyası bulunamadı: {backup_file}")

    temp_extracted_db: Optional[Path] = None

    # ZIP dosyası ise ayıkla
    if backup_file.suffix.lower() == ".zip":
        with zipfile.ZipFile(str(backup_file), "r") as zf:
            if "accounting.db" not in zf.namelist():
                raise ValueError("Seçilen ZIP dosyası geçerli bir Döviz Borç Takip yedeği içermiyor (accounting.db bulunamadı).")
            temp_extracted_db = backup_file.parent / f"temp_extracted_{datetime.now().strftime('%Y%m%d%H%M%S')}.db"
            with open(str(temp_extracted_db), "wb") as f_out:
                f_out.write(zf.read("accounting.db"))
            db_to_restore = temp_extracted_db
    else:
        db_to_restore = backup_file

    # 1. Bütünlük kontrolü
    if not check_integrity(db_to_restore):
        if temp_extracted_db and temp_extracted_db.exists():
            temp_extracted_db.unlink()
        raise ValueError("Geri yüklenecek yedek dosyası bozuk veya geçersiz bir SQLite veritabanı! İşlem iptal edildi.")

    # 2. Mevcut veritabanının güvenlik yedeğini (roll-back point) al
    safety_backup_path: Optional[Path] = None
    if target_path.exists():
        safety_backup_path = backup_database(prefix="pre_restore_safety", custom_db_path=target_path)

    try:
        # WAL dosyalarını temizlemek için hedef bağlantıları kapatıp kopyala
        shutil.copy2(db_to_restore, target_path)

        # Yan WAL ve SHM dosyalarını temizle ki eski loglar yeni DB ile çakışmasın
        wal_file = target_path.with_name(target_path.name + "-wal")
        shm_file = target_path.with_name(target_path.name + "-shm")
        if wal_file.exists():
            wal_file.unlink()
        if shm_file.exists():
            shm_file.unlink()

        # Hedef veritabanı bütünlüğünü kontrol et
        if not check_integrity(target_path):
            raise ValueError("Geri yüklenen hedef veritabanında bütünlük hatası oluştu!")

        # İstatistikleri al
        conn = sqlite3.connect(str(target_path))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM accounts")
        accounts_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM transactions")
        tx_count = cur.fetchone()[0]
        conn.close()

        return {
            "success": True,
            "message": "Veritabanı başarıyla ve eksiksiz geri yüklendi.",
            "accounts_count": accounts_count,
            "transactions_count": tx_count,
            "safety_backup_path": str(safety_backup_path) if safety_backup_path else None
        }

    except Exception as e:
        # Rollback: Hata durumunda güvenlik yedeğini geri yükle
        if safety_backup_path and safety_backup_path.exists():
            shutil.copy2(safety_backup_path, target_path)
        raise e
    finally:
        if temp_extracted_db and temp_extracted_db.exists():
            temp_extracted_db.unlink()

def get_backup_status_summary() -> Dict[str, Any]:
    """
    Yedekleme durum paneli için istatistikleri döner:
    - Son başarılı yedekleme tarihi
    - Toplam yedek sayısı
    - Veritabanı dosya boyutu
    - Son bütünlük kontrolü (OK / Hata)
    - Yedek listesi (indirme ve geri yükleme için)
    """
    db_path = get_database_path()
    backup_dir = get_backups_dir()

    db_exists = db_path.exists()
    db_size_bytes = db_path.stat().st_size if db_exists else 0
    db_size_formatted = f"{db_size_bytes / 1024:.1f} KB" if db_size_bytes < 1024*1024 else f"{db_size_bytes / (1024*1024):.2f} MB"

    integrity_ok = check_integrity(db_path) if db_exists else False

    backups = []
    for f in sorted(backup_dir.glob("backup_*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        size_kb = f.stat().st_size / 1024
        backups.append({
            "filename": f.name,
            "path": str(f),
            "date": mtime.strftime("%d.%m.%Y %H:%M:%S"),
            "size": f"{size_kb:.1f} KB"
        })

    last_backup_date = backups[0]["date"] if backups else "Henüz yedek alınmadı"

    return {
        "db_exists": db_exists,
        "db_path": str(db_path),
        "db_size": db_size_formatted,
        "integrity_status": "Başarılı (Sağlam)" if integrity_ok else "Hata",
        "last_backup_date": last_backup_date,
        "total_backups_count": len(backups),
        "backups": backups[:15]  # En son 15 yedek listelenir
    }
