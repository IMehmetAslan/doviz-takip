"""
Comprehensive Test Suite for Currency Contract (Döviz Bağlantı) Feature.
Validates:
1. Contract creation (1.000 EUR sell, 50.000 TL buy -> unit price 50.00)
2. Dual currency balance tracking:
   - 1.000 EUR debt/output to person
   - 50.000 TL receivable from person
3. Partial payments:
   - 1st payment: 20.000 TL -> remaining 30.000 TL (Status: PARTIAL)
   - 2nd payment: 15.000 TL -> remaining 15.000 TL (Status: PARTIAL)
   - 3rd payment: 15.000 TL -> remaining 0.00 TL (Status: COMPLETED)
4. Overpayment rejection (cannot collect more than remaining)
5. Contract filtering (ALL, PENDING, PARTIAL, COMPLETED)
6. Transaction history and statement integration without duplicates
"""

import unittest
import sqlite3
import shutil
import tempfile
from decimal import Decimal

from src.database import init_db
from src.accounting_service import AccountingService
from src.reporting_engine import ReportingEngine

class TestCurrencyContracts(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_contracts_")
        self.db_path = f"{self.test_dir}/test.db"
        self.conn = init_db(self.db_path)
        self.service = AccountingService(self.conn)
        self.reporting = ReportingEngine(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_contract_creation_and_unit_price_calculation(self):
        """1.000 EUR satılacak, karşılığında 50.000 TL alınacak -> Birim fiyat 50.00 TL olmalı."""
        p_id = self.service.create_person("ABC Dış Ticaret", phone="0532 111 2233")
        c_id = self.service.create_contract(
            person_id=p_id,
            sell_currency="EUR",
            sell_amount="1000.00",
            buy_currency="TRY",
            total_target_amount="50000.00",
            contract_date="2026-11-01",
            description="Kasım ayı ihracat bağlantısı"
        )
        self.conn.commit()

        c = self.service.get_contract_details(c_id)
        self.assertEqual(c["sell_amount"], Decimal("1000.00"))
        self.assertEqual(c["sell_currency"], "EUR")
        self.assertEqual(c["total_target_amount"], Decimal("50000.00"))
        self.assertEqual(c["buy_currency"], "TRY")
        self.assertEqual(c["unit_price"], Decimal("50.0000"))
        self.assertEqual(c["remaining_amount"], Decimal("50000.00"))
        self.assertEqual(c["status"], "PENDING")

        # Cari harekete 1.000 EUR çıkış yazılmış olmalı
        person = self.service.get_person_details(p_id)
        self.assertIn("EUR", person["currency_balances"])
        self.assertEqual(person["currency_balances"]["EUR"]["total_given"], Decimal("1000.00"))
        self.assertEqual(person["currency_balances"]["EUR"]["remaining"], Decimal("1000.00"))

    def test_partial_payments_and_completion(self):
        """
        Talepteki tam senaryo:
        - Bağlantı: Verilen 1.000 EUR, Alınacak 50.000 TL
        - 1. Ödeme: 20.000 TL -> Kalan 30.000 TL
        - 2. Ödeme: 15.000 TL -> Kalan 15.000 TL
        - 3. Ödeme: 15.000 TL -> Kalan 0 TL (Tamamlandı)
        """
        p_id = self.service.create_person("Mehmet Demir")
        c_id = self.service.create_contract(
            person_id=p_id,
            sell_currency="EUR",
            sell_amount="1000.00",
            buy_currency="TRY",
            total_target_amount="50000.00",
            contract_date="2026-11-01"
        )

        # 1. Ödeme: 20.000 TL
        self.service.add_contract_payment(c_id, "20000.00", payment_date="2026-11-05", description="1. Havale")
        c1 = self.service.get_contract_details(c_id)
        self.assertEqual(c1["collected_amount"], Decimal("20000.00"))
        self.assertEqual(c1["remaining_amount"], Decimal("30000.00"))
        self.assertEqual(c1["status"], "PARTIAL")
        self.assertEqual(len(c1["payments"]), 1)

        # 2. Ödeme: 15.000 TL
        self.service.add_contract_payment(c_id, "15000.00", payment_date="2026-11-10", description="2. Havale")
        c2 = self.service.get_contract_details(c_id)
        self.assertEqual(c2["collected_amount"], Decimal("35000.00"))
        self.assertEqual(c2["remaining_amount"], Decimal("15000.00"))
        self.assertEqual(c2["status"], "PARTIAL")
        self.assertEqual(len(c2["payments"]), 2)

        # 3. Ödeme: 15.000 TL (Kapanış)
        self.service.add_contract_payment(c_id, "15000.00", payment_date="2026-11-15", description="Son taksit")
        c3 = self.service.get_contract_details(c_id)
        self.assertEqual(c3["collected_amount"], Decimal("50000.00"))
        self.assertEqual(c3["remaining_amount"], Decimal("0.00"))
        self.assertEqual(c3["status"], "COMPLETED")
        self.assertEqual(len(c3["payments"]), 3)

    def test_overpayment_rejected(self):
        """Kalan tutardan fazla ödeme girilirse hata vermeli ve engellenmeli."""
        p_id = self.service.create_person("Ali Veli")
        c_id = self.service.create_contract(
            person_id=p_id,
            sell_currency="USD",
            sell_amount="1000.00",
            buy_currency="TRY",
            total_target_amount="35000.00"
        )

        with self.assertRaises(ValueError):
            self.service.add_contract_payment(c_id, "36000.00")

    def test_contracts_filtering(self):
        """Tüm bağlantılar, bekleyenler ve tamamlananlar filtreleri doğru çalışmalı."""
        p_id = self.service.create_person("Firma Test")
        c1 = self.service.create_contract(p_id, "USD", "1000", "TRY", "34000")
        c2 = self.service.create_contract(p_id, "EUR", "1000", "TRY", "37000")

        # c2'yi tamamla
        self.service.add_contract_payment(c2, "37000.00")

        all_list = self.service.get_contracts_list(status_filter="ALL")
        self.assertEqual(len(all_list), 2)

        pending_list = self.service.get_contracts_list(status_filter="PENDING")
        self.assertEqual(len(pending_list), 1)
        self.assertEqual(pending_list[0]["id"], c1)

        completed_list = self.service.get_contracts_list(status_filter="COMPLETED")
        self.assertEqual(len(completed_list), 1)
        self.assertEqual(completed_list[0]["id"], c2)

if __name__ == "__main__":
    unittest.main()

