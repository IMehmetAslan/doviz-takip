"""
Unit & Integration Tests for Transaction & Contract Deletion Features.
Validates:
1. Deleting a debt giving transaction rolls back person balance.
2. Deleting a payment transaction restores outstanding debt balance.
3. Deleting a partial contract payment restores contract remaining amount and deletes ledger entry.
4. Deleting a currency contract cleans up both the contract and the associated ledger debt transaction.
"""

import unittest
import tempfile
import shutil
import os
from decimal import Decimal

from src.database import init_db
from src.accounting_service import AccountingService

class TestDeletionFeatures(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")
        self.service = AccountingService(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_delete_debt_transaction_rolls_back_balance(self):
        person_id = self.service.create_person(name="Test Silme Cari", phone="05550001122")
        
        # 1. 5.000 USD borç ver
        tx_id = self.service.give_debt(person_id, "5000.00", "USD", "2026-11-01", "34.50", "Yanlış girilen borç")
        self.conn.commit()

        # Bakiye kontrolü
        details_before = self.service.get_person_details(person_id)
        self.assertEqual(Decimal(str(details_before["currency_balances"]["USD"]["remaining"])), Decimal("5000.00"))

        # 2. İşlemi sil
        self.service.delete_transaction(tx_id)
        self.conn.commit()

        # Bakiye kontrolü: İşlem silindiği için bakiye sıfırlanmalı
        details_after = self.service.get_person_details(person_id)
        usd_bal = details_after["currency_balances"].get("USD")
        self.assertTrue(usd_bal is None or Decimal(str(usd_bal["remaining"])) == Decimal("0.00"))
        self.assertEqual(len(details_after["history"]), 0)

    def test_delete_payment_transaction_restores_balance(self):
        person_id = self.service.create_person(name="Test Tahsilat Silme", phone="05550003344")

        # 1. 10.000 USD borç ver
        self.service.give_debt(person_id, "10000.00", "USD", "2026-11-01", "34.50", "Ana borç")
        # 2. 3.000 USD ödeme al
        pay_tx_id = self.service.receive_payment(person_id, "3000.00", "USD", "2026-11-05", "34.50", "Yanlış ödeme")
        self.conn.commit()

        details_with_pay = self.service.get_person_details(person_id)
        self.assertEqual(Decimal(str(details_with_pay["currency_balances"]["USD"]["remaining"])), Decimal("7000.00"))

        # 3. Ödeme işlemini sil
        self.service.delete_transaction(pay_tx_id)
        self.conn.commit()

        details_after_delete = self.service.get_person_details(person_id)
        self.assertEqual(Decimal(str(details_after_delete["currency_balances"]["USD"]["remaining"])), Decimal("10000.00"))
        self.assertEqual(len(details_after_delete["history"]), 1)

    def test_delete_contract_and_payments(self):
        person_id = self.service.create_person(name="Test Bağlantı Silme", phone="05550005566")

        # 1. 1.000 EUR Satış, 50.000 TL Alış bağlantısı kur
        contract_id = self.service.create_contract(
            person_id=person_id,
            sell_currency="EUR",
            sell_amount="1000.00",
            buy_currency="TRY",
            total_target_amount="50000.00",
            unit_price="50.00",
            contract_date="2026-11-10",
            description="Bağlantı Sözleşmesi"
        )
        self.conn.commit()

        # Cariye 1.000 EUR borç çıkışı yazılmış olmalı
        c_details = self.service.get_contract_details(contract_id)
        self.assertEqual(c_details["status"], "PENDING")
        self.assertEqual(Decimal(str(c_details["remaining_amount"])), Decimal("50000.00"))

        p_details = self.service.get_person_details(person_id)
        self.assertEqual(Decimal(str(p_details["currency_balances"]["EUR"]["remaining"])), Decimal("1000.00"))

        # 2. 20.000 TL kısmi tahsilat yap
        payment_id = self.service.add_contract_payment(contract_id, "20000.00", "2026-11-11", "Kısmi tahsilat")
        self.conn.commit()

        c_details2 = self.service.get_contract_details(contract_id)
        self.assertEqual(c_details2["status"], "PARTIAL")
        self.assertEqual(Decimal(str(c_details2["remaining_amount"])), Decimal("30000.00"))

        # 3. Kısmi tahsilatı sil
        self.service.delete_contract_payment(payment_id)
        self.conn.commit()

        c_details3 = self.service.get_contract_details(contract_id)
        self.assertEqual(c_details3["status"], "PENDING")
        self.assertEqual(Decimal(str(c_details3["remaining_amount"])), Decimal("50000.00"))
        self.assertEqual(len(c_details3["payments"]), 0)

        # 4. Tüm bağlantıyı sil
        self.service.delete_contract(contract_id)
        self.conn.commit()

        # Bağlantı ve cari döviz çıkışı tamamen silinmeli
        self.assertIsNone(self.service.get_contract_details(contract_id))
        p_details_final = self.service.get_person_details(person_id)
        eur_bal = p_details_final["currency_balances"].get("EUR")
        self.assertTrue(eur_bal is None or Decimal(str(eur_bal["remaining"])) == Decimal("0.00"))
        self.assertEqual(len(p_details_final["history"]), 0)

if __name__ == "__main__":
    unittest.main()
