"""
Comprehensive Data Persistence, Backup, Recovery, and Integrity Test Suite.
Verifies all 7 critical scenarios required:
1. Persistence of newly created person across restart
2. Persistence of debt (10,000 USD) across restart
3. Persistence of partial payment (3,000 USD -> 7,000 USD remaining) across restart
4. Full Backup, Mutation, and Safe Restore (Data integrity intact)
5. Non-destructive schema updates / migrations
6. Database integrity checks (PRAGMA integrity_check)
7. Corrupted backup rejection without damaging active database
"""

import unittest
import sqlite3
import shutil
import tempfile
import zipfile
from pathlib import Path
from decimal import Decimal

from src.database import init_db
from src.accounting_service import AccountingService
from src.reporting_engine import ReportingEngine
from src.backup_service import (
    backup_database,
    restore_database_safely,
    export_backup_zip,
    check_integrity,
    prune_old_backups
)

class TestDataPersistenceAndRecovery(unittest.TestCase):
    def setUp(self):
        # Gerçek kullanıcı verilerine dokunmamak için tamamen izole geçici klasör
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_debt_security_"))
        self.db_path = self.test_dir / "test_accounting.db"
        self.backup_dir = self.test_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # İlk temiz DB oluştur
        conn = init_db(str(self.db_path))
        conn.close()

    def tearDown(self):
        # Test klasörünü temizle
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_scenario_1_person_persistence_across_restart(self):
        """Senaryo 1: Yeni kişi ekle, veritabanı bağlantısını kapat ve yeniden aç. Kişi duruyor mu?"""
        conn = init_db(str(self.db_path))
        srv = AccountingService(conn)
        p_id = srv.create_person("Ahmet Kaya", phone="0532 999 11 22", description="Test Kişisi")
        conn.commit()
        conn.close()

        # Uygulama simülasyonu: Yeni bağlantı ile aç
        conn2 = init_db(str(self.db_path))
        srv2 = AccountingService(conn2)
        person = srv2.get_person_details(p_id)
        conn2.close()

        self.assertIsNotNone(person)
        self.assertEqual(person["name"], "Ahmet Kaya")
        self.assertEqual(person["phone"], "0532 999 11 22")

    def test_scenario_2_debt_persistence_across_restart(self):
        """Senaryo 2: Bir kişiye 10.000 USD borç ver. Uygulamayı yeniden başlat. Borç korunuyor mu?"""
        conn = init_db(str(self.db_path))
        srv = AccountingService(conn)
        p_id = srv.create_person("ABC Lojistik", phone="0212 555 44 33")
        srv.give_debt(p_id, "10000.00", "USD", "2026-11-01", description="10K USD Avans")
        conn.commit()
        conn.close()

        # Yeniden başlat
        conn2 = init_db(str(self.db_path))
        srv2 = AccountingService(conn2)
        person = srv2.get_person_details(p_id)
        conn2.close()

        self.assertIn("USD", person["currency_balances"])
        self.assertEqual(person["currency_balances"]["USD"]["total_given"], Decimal("10000.00"))
        self.assertEqual(person["currency_balances"]["USD"]["remaining"], Decimal("10000.00"))
        self.assertEqual(person["has_active_debt"], True)

    def test_scenario_3_payment_and_remaining_balance_across_restart(self):
        """Senaryo 3: 3.000 USD ödeme kaydet. Yeniden başlat. Kalan 7.000 USD doğru mu?"""
        conn = init_db(str(self.db_path))
        srv = AccountingService(conn)
        p_id = srv.create_person("ABC Lojistik")
        srv.give_debt(p_id, "10000.00", "USD", "2026-11-01")
        srv.receive_payment(p_id, "3000.00", "USD", "2026-11-03", description="1. Taksit")
        conn.commit()
        conn.close()

        # Yeniden başlat
        conn2 = init_db(str(self.db_path))
        srv2 = AccountingService(conn2)
        person = srv2.get_person_details(p_id)
        conn2.close()

        usd_bal = person["currency_balances"]["USD"]
        self.assertEqual(usd_bal["total_given"], Decimal("10000.00"))
        self.assertEqual(usd_bal["total_paid"], Decimal("3000.00"))
        self.assertEqual(usd_bal["remaining"], Decimal("7000.00"))
        self.assertFalse(usd_bal["is_closed"])

    def test_scenario_4_backup_and_full_restore(self):
        """Senaryo 4: Yedek al, verileri değiştir, eski yedeği geri yükle. Veriler hatasız geri geliyor mu?"""
        conn = init_db(str(self.db_path))
        srv = AccountingService(conn)
        p1 = srv.create_person("Orijinal Kişi 1")
        srv.give_debt(p1, "5000.00", "EUR", "2026-10-01")
        conn.commit()
        conn.close()

        # 1. Yedek al
        backup_file = backup_database(
            prefix="test_point",
            custom_db_path=self.db_path,
            custom_backup_dir=self.backup_dir
        )
        self.assertTrue(backup_file.exists())

        # 2. Aktif veritabanını boz / değiştir / yeni kayıtlar ekle
        conn2 = init_db(str(self.db_path))
        srv2 = AccountingService(conn2)
        p2 = srv2.create_person("Değişen Yeni Kişi")
        srv2.give_debt(p2, "9999.00", "USD", "2026-11-05")
        conn2.commit()
        conn2.close()

        # 3. Eski yedeği geri yükle
        res = restore_database_safely(backup_file, target_db_path=self.db_path)
        self.assertTrue(res["success"])

        # 4. Doğrula: Yalnızca Orijinal Kişi 1 ve 5000 EUR borç olmalı, sonradan eklenen olmamalı
        conn3 = init_db(str(self.db_path))
        srv3 = AccountingService(conn3)
        rep = ReportingEngine(conn3)
        debtors = rep.get_debtors_list()
        conn3.close()

        self.assertEqual(len(debtors), 1)
        self.assertEqual(debtors[0]["name"], "Orijinal Kişi 1")
        self.assertEqual(debtors[0]["currencies"][0]["remaining"], Decimal("5000.00"))
        self.assertEqual(debtors[0]["currencies"][0]["currency_code"], "EUR")

    def test_scenario_5_app_update_preserves_data(self):
        """Senaryo 5: Uygulama güncellemesi / şema migrasyonu sonrasında veriler korunuyor mu?"""
        conn = init_db(str(self.db_path))
        srv = AccountingService(conn)
        p = srv.create_person("Kalıcı Müşteri A.Ş.")
        srv.give_debt(p, "15000.00", "USD", "2026-09-01")
        conn.commit()
        conn.close()

        # Uygulama güncellendi ve init_db tekrar çalıştı simülasyonu
        conn2 = init_db(str(self.db_path))
        srv2 = AccountingService(conn2)
        details = srv2.get_person_details(p)
        conn2.close()

        self.assertEqual(details["name"], "Kalıcı Müşteri A.Ş.")
        self.assertEqual(details["currency_balances"]["USD"]["remaining"], Decimal("15000.00"))

    def test_scenario_6_integrity_check(self):
        """Senaryo 6: Veritabanı bütünlük kontrolü (PRAGMA integrity_check) başarılı mı?"""
        self.assertTrue(check_integrity(self.db_path))

    def test_scenario_7_corrupted_backup_rejected_and_active_db_protected(self):
        """Senaryo 7: Bozuk bir yedek dosyası verildiğinde geri yükleme engelleniyor mu ve aktif veriler korunuyor mu?"""
        conn = init_db(str(self.db_path))
        srv = AccountingService(conn)
        p_id = srv.create_person("Değerli Müşteri")
        srv.give_debt(p_id, "25000.00", "USD", "2026-10-01")
        conn.commit()
        conn.close()

        # Tamamen bozuk bir dosya oluştur
        corrupted_file = self.test_dir / "corrupted_backup.db"
        corrupted_file.write_text("BU GECERSIZ VE BOZUK BIR SQLITE DOSYASIDIR")

        # Geri yüklemeyi dene -> Hata fırlatmalı ve işlemi durdurmalı
        with self.assertRaises(ValueError):
            restore_database_safely(corrupted_file, target_db_path=self.db_path)

        # Aktif veritabanı sapasağlam duruyor mu?
        conn2 = init_db(str(self.db_path))
        srv2 = AccountingService(conn2)
        p = srv2.get_person_details(p_id)
        conn2.close()

        self.assertIsNotNone(p)
        self.assertEqual(p["name"], "Değerli Müşteri")
        self.assertEqual(p["currency_balances"]["USD"]["remaining"], Decimal("25000.00"))

if __name__ == "__main__":
    unittest.main()

