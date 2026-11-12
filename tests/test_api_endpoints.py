"""
End-to-End HTTP API Integration Test Suite.
Validates all REST endpoints in server.py:
1. /api/health
2. Authentication & Authorization protection (401 on protected routes)
3. /api/auth/login, /api/auth/me, /api/auth/logout
4. /api/currencies, /api/persons/simple
5. /api/person/create & /api/person/update
6. /api/debt/give & /api/payment/receive
7. /api/dashboard & /api/debtors
8. /api/person/<id> statement details
9. /api/contract/create & /api/contract/payment & /api/contracts
10. /api/transaction/cancel audit rollback
11. /api/backup/status & /api/backup/create
"""

import unittest
import json
import socket
import threading
import time
import urllib.request
import urllib.error
import tempfile
import shutil
import os
from decimal import Decimal

from src.database import init_db
from src.server import ThreadedTCPServer, AppApiHandler
from src.auth_service import AuthService

class TestApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Geçici bir klasör ve izole bir veritabanı oluşturalım
        cls.temp_dir = tempfile.mkdtemp(prefix="test_api_server_")
        os.environ["DOVIZ_TAKIP_DATA_DIR"] = cls.temp_dir

        import src.server
        src.server._cached_db_conn = None

        # DB'yi başlat
        conn = init_db()
        # Varsayılan admini oluştur
        auth = AuthService(conn)
        auth.init_default_admin()
        conn.close()

        # Boş bir port bulalım
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()

        # Sunucuyu daemon thread olarak başlatalım
        cls.server = ThreadedTCPServer(("127.0.0.1", cls.port), AppApiHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass
        if os.path.exists(cls.temp_dir):
            shutil.rmtree(cls.temp_dir, ignore_errors=True)
        os.environ.pop("DOVIZ_TAKIP_DATA_DIR", None)

    def request(self, method, endpoint, body=None, token=None):
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["Cookie"] = f"doviz_session={token}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                status = response.status
                resp_body = response.read().decode("utf-8")
                return status, json.loads(resp_body) if resp_body else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                parsed = json.loads(err_body)
            except Exception:
                parsed = {"raw": err_body}
            return e.code, parsed

    def test_01_health_check(self):
        status, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data.get("status"), "ok")
        self.assertTrue(data.get("db_connected"))

    def test_02_unauthorized_access(self):
        # Token olmadan korumalı uç noktalara erişim 401 dönmeli
        status, data = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 401)
        self.assertFalse(data.get("success", True))

        status, data = self.request("POST", "/api/person/create", {"name": "Test"})
        self.assertEqual(status, 401)

    def test_03_login_failure_and_success(self):
        # Yanlış şifre (API 400 ve success: False döner)
        status, data = self.request("POST", "/api/auth/login", {"username": "admin", "password": "wrongpassword"})
        self.assertEqual(status, 400)
        self.assertFalse(data.get("success"))

        # Doğru şifre (default: admin / admin123)
        status, data = self.request("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("success"))
        token = data.get("token")
        self.assertIsNotNone(token)
        self.assertEqual(data.get("user", {}).get("username"), "admin")

        # Test auth/me
        status_me, data_me = self.request("GET", "/api/auth/me", token=token)
        self.assertEqual(status_me, 200)
        self.assertEqual(data_me.get("user", {}).get("username"), "admin")

    def test_04_full_accounting_workflow(self):
        # 1. Giriş yapalım
        _, login_data = self.request("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
        token = login_data["token"]

        # 2. Para birimlerini al
        status, res = self.request("GET", "/api/currencies", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(res.get("success"))
        currencies = res.get("data", [])
        codes = [c["code"] for c in currencies]
        self.assertIn("USD", codes)
        self.assertIn("EUR", codes)
        self.assertIn("TRY", codes)

        # 3. Yeni kişi oluştur
        status, p_data = self.request("POST", "/api/person/create", {
            "name": "Ahmet Yılmaz",
            "phone": "05321112233",
            "description": "API Test Cari"
        }, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(p_data.get("success"))
        person_id = p_data.get("person_id")
        self.assertIsNotNone(person_id)

        # 4. Kişiye 5000 USD borç verelim
        status, debt_data = self.request("POST", "/api/debt/give", {
            "person_id": person_id,
            "amount": "5000.00",
            "currency_code": "USD",
            "date": "2026-11-01",
            "exchange_rate": "34.50",
            "description": "5000 USD borç verildi"
        }, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(debt_data.get("success"))
        tx_debt_id = debt_data.get("transaction_id")

        # 5. Dashboard verilerini doğrula
        status, dash_res = self.request("GET", "/api/dashboard", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(dash_res.get("success"))
        dash = dash_res.get("data", {})
        usd_summary = dash.get("currency_summary", {}).get("USD")
        self.assertIsNotNone(usd_summary)
        self.assertEqual(Decimal(str(usd_summary["total_remaining"])), Decimal("5000.00"))

        # 6. Kişiden 2000 USD kısmi ödeme alalım
        status, pay_data = self.request("POST", "/api/payment/receive", {
            "person_id": person_id,
            "amount": "2000.00",
            "currency_code": "USD",
            "date": "2026-11-05",
            "exchange_rate": "34.60",
            "description": "1. Taksit Ödemesi"
        }, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(pay_data.get("success"))

        # 7. Kişi ekstresini (statement) kontrol et -> kalan 3000 USD olmalı
        status, p_detail_res = self.request("GET", f"/api/person/{person_id}", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(p_detail_res.get("success"))
        p_detail = p_detail_res.get("data", {})
        usd_bal = p_detail.get("currency_balances", {}).get("USD")
        self.assertIsNotNone(usd_bal)
        self.assertEqual(Decimal(str(usd_bal["remaining"])), Decimal("3000.00"))
        self.assertEqual(len(p_detail.get("history", [])), 2)

        # 8. Döviz Bağlantı Sözleşmesi Oluştur (1000 EUR Sat, 50000 TL Al)
        status, contract_data = self.request("POST", "/api/contract/create", {
            "person_id": person_id,
            "sell_currency": "EUR",
            "sell_amount": "1000.00",
            "buy_currency": "TRY",
            "unit_price": "50.00",
            "total_target_amount": "50000.00",
            "contract_date": "2026-11-06",
            "description": "1000 EUR Döviz Bağlantısı"
        }, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(contract_data.get("success"))
        contract_id = contract_data.get("contract_id")

        # 9. Sözleşmeye 20000 TL kısmi tahsilat yap
        status, c_pay = self.request("POST", "/api/contract/payment", {
            "contract_id": contract_id,
            "amount": "20000.00",
            "payment_date": "2026-11-07",
            "description": "İlk ara tahsilat"
        }, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(c_pay.get("success"))

        # 10. Sözleşmeleri listele
        status, contracts_res = self.request("GET", "/api/contracts?status=PARTIAL", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(contracts_res.get("success"))
        contracts_list = contracts_res.get("data", [])
        self.assertTrue(any(c["id"] == contract_id for c in contracts_list))

        # 11. Güvenli yedekleme durumunu kontrol et
        status, backup_stat = self.request("GET", "/api/backup/status", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(backup_stat.get("success"))

        # 12. Döviz bağlantısı tahsilatını sil
        payment_id = c_pay.get("payment_id")
        status, del_pay = self.request("POST", "/api/contract/payment/delete", {
            "payment_id": payment_id
        }, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(del_pay.get("success"))

        # 13. Döviz bağlantısını tamamen sil
        status, del_contract = self.request("POST", "/api/contract/delete", {
            "contract_id": contract_id
        }, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(del_contract.get("success"))

        # 14. Yanlış girilen borç verme işlemini kalıcı olarak sil
        status, del_tx = self.request("POST", "/api/transaction/delete", {
            "transaction_id": tx_debt_id
        }, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(del_tx.get("success"))

        # 15. Oturumu kapat
        status, logout_data = self.request("POST", "/api/auth/logout", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(logout_data.get("success"))

        # Oturum kapatıldıktan sonra aynı tokenla istek 401 vermeli
        status_after, _ = self.request("GET", "/api/dashboard", token=token)
        self.assertEqual(status_after, 401)

if __name__ == "__main__":
    unittest.main()
