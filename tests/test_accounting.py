"""
Test Suite for Personal Currency Debt Tracking App.
Validates:
1. Person & Company Creation
2. Debt Giving (Borç Verme) & Balance Increase
3. Payment Receiving (Ödeme Alma), Partial Payments & Balance Reduction
4. Multi-currency Isolation (USD, EUR, GBP never mixed)
5. Debt Closing & Archive Transition
6. Transaction Cancellation & Audit Trail
"""

import unittest
import sqlite3
from decimal import Decimal
from src.database import init_db
from src.accounting_service import AccountingService
from src.reporting_engine import ReportingEngine

class TestDebtAppCore(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")
        self.service = AccountingService(self.conn)
        self.reporting = ReportingEngine(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_debt_and_partial_payments(self):
        """
        Talepteki birebir senaryo testi:
        - Verilen borç: 10.000 USD
        - İlk ödeme: 2.000 USD -> Kalan: 8.000 USD
        - İkinci ödeme: 3.000 USD -> Kalan: 5.000 USD
        """
        p_id = self.service.create_person("ABC Firması", phone="0532 000 0000", description="İhracat işi")
        
        # 1. 10.000 USD Borç Verildi
        self.service.give_debt(p_id, "10000.00", "USD", "2026-10-01", description="Avans verildi")
        details = self.service.get_person_details(p_id)
        self.assertEqual(details["currency_balances"]["USD"]["remaining"], Decimal("10000.00"))
        self.assertEqual(details["has_active_debt"], True)

        # 2. 2.000 USD İlk Ödeme
        self.service.receive_payment(p_id, "2000.00", "USD", "2026-10-10", description="1. Ödeme")
        details = self.service.get_person_details(p_id)
        self.assertEqual(details["currency_balances"]["USD"]["remaining"], Decimal("8000.00"))

        # 3. 3.000 USD İkinci Ödeme
        self.service.receive_payment(p_id, "3000.00", "USD", "2026-10-25", description="2. Ödeme")
        details = self.service.get_person_details(p_id)
        self.assertEqual(details["currency_balances"]["USD"]["remaining"], Decimal("5000.00"))
        self.assertEqual(details["currency_balances"]["USD"]["is_closed"], False)

    def test_multi_currency_isolation(self):
        """Dövizlerin asla birbirine karışmadığını ve ayrı ayrı hesaplandığını test eder."""
        p_id = self.service.create_person("Mehmet Bey", phone="0555 111 2233")
        
        # USD borç
        self.service.give_debt(p_id, "5000.00", "USD", "2026-10-01")
        # EUR borç
        self.service.give_debt(p_id, "3000.00", "EUR", "2026-10-02")

        # EUR ödeme alındı (tamamı)
        self.service.receive_payment(p_id, "3000.00", "EUR", "2026-10-15")

        details = self.service.get_person_details(p_id)
        
        # USD hala açık ve 5.000 USD
        self.assertEqual(details["currency_balances"]["USD"]["remaining"], Decimal("5000.00"))
        self.assertEqual(details["currency_balances"]["USD"]["is_closed"], False)

        # EUR kapandı ve 0.00 EUR
        self.assertEqual(details["currency_balances"]["EUR"]["remaining"], Decimal("0.00"))
        self.assertEqual(details["currency_balances"]["EUR"]["is_closed"], True)

    def test_archive_status_on_debt_close(self):
        """Borç tamamen kapandığında arşiv durumuna geçtiğini test eder."""
        p_id = self.service.create_person("Caner Bey")
        self.service.give_debt(p_id, "1000.00", "USD", "2026-10-01")
        
        # Öncesinde aktif borçlu
        active_list = self.reporting.get_debtors_list(filter_status="ACTIVE")
        self.assertTrue(any(d["id"] == p_id for d in active_list))

        # Borcun tamamı ödeniyor
        self.service.receive_payment(p_id, "1000.00", "USD", "2026-10-10")

        # Artık aktifte değil, arşivde (CLOSED)
        active_list_after = self.reporting.get_debtors_list(filter_status="ACTIVE")
        self.assertFalse(any(d["id"] == p_id for d in active_list_after))

        closed_list = self.reporting.get_debtors_list(filter_status="CLOSED")
        self.assertTrue(any(d["id"] == p_id for d in closed_list))

    def test_cancellation_rolls_back_balance(self):
        """Bir işlem iptal edildiğinde bakiyenin hatasız düzeldiğini test eder."""
        p_id = self.service.create_person("Selin Hanım")
        tx_id = self.service.give_debt(p_id, "4000.00", "EUR", "2026-10-01")
        
        self.service.cancel_transaction(tx_id, reason="Yanlış tutar girildi")
        details = self.service.get_person_details(p_id)
        
        # İptal edilen işlem bakiyeye yansımaz
        self.assertEqual(len(details["currency_balances"]), 0)

    def test_delete_closed_record_success(self):
        """Kapanmış bir borç kaydının ilişkili geçmişiyle birlikte kalıcı olarak silindiğini test eder."""
        # Kişi 1: Kapanacak kişi
        p1_id = self.service.create_person("Kapanan Borçlu")
        self.service.give_debt(p1_id, "2500.00", "USD", "2026-10-01")
        self.service.receive_payment(p1_id, "2500.00", "USD", "2026-10-05")

        # Kişi 2: Başka bir aktif kişi (etkilenmemeli)
        p2_id = self.service.create_person("Aktif Borçlu")
        self.service.give_debt(p2_id, "5000.00", "EUR", "2026-10-02")

        # p1'in kapandığını doğrula
        closed_list = self.reporting.get_debtors_list(filter_status="CLOSED")
        self.assertTrue(any(d["id"] == p1_id for d in closed_list))

        # p1'i arşivden manuel sil
        res = self.service.delete_closed_record(p1_id)
        self.assertEqual(res["person_id"], p1_id)

        # p1 artık sistemde yok
        all_debtors = self.reporting.get_debtors_list(filter_status="ALL")
        self.assertFalse(any(d["id"] == p1_id for d in all_debtors))

        # p2'nin bakiyesi ve verisi kesinlikle etkilenmedi
        p2_details = self.service.get_person_details(p2_id)
        self.assertEqual(p2_details["currency_balances"]["EUR"]["remaining"], Decimal("5000.00"))

    def test_delete_closed_record_rejects_active_debt(self):
        """Aktif borcu olan bir kişinin silinmesinin kesinlikle engellendiğini test eder."""
        p_id = self.service.create_person("Borçlu Firma")
        self.service.give_debt(p_id, "10000.00", "USD", "2026-10-01")
        self.service.receive_payment(p_id, "3000.00", "USD", "2026-10-05")
        # Kalan: 7000.00 USD (aktif)

        with self.assertRaises(ValueError) as ctx:
            self.service.delete_closed_record(p_id)
        self.assertIn("aktif borcu bulunmaktadır", str(ctx.exception))

        # Veri yerinde duruyor
        details = self.service.get_person_details(p_id)
        self.assertEqual(details["currency_balances"]["USD"]["remaining"], Decimal("7000.00"))

if __name__ == "__main__":
    unittest.main()
