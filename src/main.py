"""
Desktop Application Entry Point for Personal Currency Debt Tracking App.
"""

import sys
import os
import threading
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import migrate_legacy_data_if_needed, get_database_path
from src.server import start_server
from src.database import init_db
from src.accounting_service import AccountingService

PORT = 8765

def seed_sample_data():
    """İlk kurulumda veritabanı tamamen boşsa örnek veriler ekler."""
    migrate_legacy_data_if_needed()
    conn = init_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM accounts")
    if cur.fetchone()[0] == 0:
        srv = AccountingService(conn)
        abc_id = srv.create_person(
            name="ABC Firması",
            phone="0532 100 20 30",
            description="İhracat iş ortaklığı cari takibi"
        )
        srv.give_debt(abc_id, "10000.00", "USD", "2026-10-01", "34.20", "Ticari finansman desteği")
        srv.receive_payment(abc_id, "2000.00", "USD", "2026-10-10", "34.30", "1. Taksit Ödemesi")
        srv.receive_payment(abc_id, "3000.00", "USD", "2026-10-25", "34.50", "2. Taksit Ödemesi")

        mehmet_id = srv.create_person(
            name="Mehmet Demir",
            phone="0555 444 33 22",
            description="Kişisel borç"
        )
        srv.give_debt(mehmet_id, "2500.00", "EUR", "2026-09-05", "37.10", "Emanet verildi")
        srv.receive_payment(mehmet_id, "2500.00", "EUR", "2026-09-20", "37.40", "Borcun tamamı elden ödendi")

        yildiz_id = srv.create_person(
            name="Yıldız Mühendislik Ltd.",
            phone="0212 999 88 77",
            description="Proje avansı takibi"
        )
        srv.give_debt(yildiz_id, "4000.00", "EUR", "2026-10-15", "37.20", "EUR Proje borcu")
        srv.give_debt(yildiz_id, "1500.00", "USD", "2026-10-18", "34.40", "USD Ek malzeme borcu")
        srv.receive_payment(yildiz_id, "1000.00", "EUR", "2026-11-01", "37.50", "EUR kısmi ödeme")

        conn.commit()
    conn.close()

def main():
    print("[+] Döviz Borç ve Alacak Takip Uygulaması Hazırlanıyor...")
    seed_sample_data()

    # Sunucuyu başlat
    try:
        start_server(PORT)
    except KeyboardInterrupt:
        print("\n[!] Sistem kapatıldı.")

if __name__ == "__main__":
    main()
