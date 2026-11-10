"""
Unit Test Suite for Authentication & Concurrency.
Tests:
1. PBKDF2 Password Hashing & Verification
2. Default Admin Creation
3. Login & Session Token Generation
4. Rate Limiting / Brute-Force Protection
5. Session Validation & Expiry
6. Logout & Session Termination
7. Multi-threaded Concurrent Operations (Simulating PC + Phone simultaneous actions)
"""

import unittest
import sqlite3
import threading
from decimal import Decimal
from src.database import init_db
from src.auth_service import AuthService, hash_password, verify_password, RateLimiter
from src.accounting_service import AccountingService

class TestAuthAndConcurrency(unittest.TestCase):
    def setUp(self):
        self.conn = init_db(":memory:")
        self.auth = AuthService(self.conn)
        self.accounting = AccountingService(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_password_hashing_and_verify(self):
        pwd = "GizliSifre!2026"
        pwd_hash, salt = hash_password(pwd)
        self.assertTrue(verify_password(pwd, pwd_hash, salt))
        self.assertFalse(verify_password("YanlisSifre", pwd_hash, salt))

    def test_default_admin_creation(self):
        # init_db zaten admin oluşturduğu için tekrar çağrıldığında False dönmeli
        created_again = self.auth.init_default_admin("admin", "admin123")
        self.assertFalse(created_again)

        # Tablo boşken çağrıldığında True dönmeli
        self.conn.execute("DELETE FROM app_users")
        self.conn.commit()
        created = self.auth.init_default_admin("admin", "admin123")
        self.assertTrue(created)

    def test_login_success_and_session_validation(self):
        self.auth.create_user("testuser", "parola123", "Test Kullanıcı")
        res = self.auth.login("testuser", "parola123", ip_address="127.0.0.1")
        token = res["token"]
        self.assertEqual(len(token), 64)

        # Oturum doğrulama
        user = self.auth.validate_session(token)
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "testuser")

    def test_login_failure(self):
        self.auth.create_user("testuser2", "parola123")
        with self.assertRaises(ValueError) as ctx:
            self.auth.login("testuser2", "hatalisifre", ip_address="127.0.0.1")
        self.assertIn("hatalı", str(ctx.exception).lower())

    def test_rate_limiter_brute_force(self):
        limiter = RateLimiter(max_attempts=3, lock_duration_minutes=5)
        key = "1.2.3.4:user"
        limiter.record_failure(key)
        self.assertFalse(limiter.is_locked(key))
        limiter.record_failure(key)
        self.assertFalse(limiter.is_locked(key))
        limiter.record_failure(key)
        self.assertTrue(limiter.is_locked(key))

    def test_logout(self):
        self.auth.create_user("user_out", "pwd12345")
        res = self.auth.login("user_out", "pwd12345")
        token = res["token"]
        self.assertIsNotNone(self.auth.validate_session(token))

        self.auth.logout(token)
        self.assertIsNone(self.auth.validate_session(token))

    def test_concurrent_read_write(self):
        """Telefon ve bilgisayarın aynı anda işlem yapmasını simüle eden eşzamanlılık testi (WAL disk DB)."""
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Şema ve tabloları ana thread'de 1 kez kur
            main_conn = init_db(tmp_path)
            main_accounting = AccountingService(main_conn)
            p_id = main_accounting.create_person("Eşzamanlı Cari")
            main_conn.commit()
            main_conn.close()

            errors = []

            def client_action(currency, amount):
                try:
                    # Her istemci (thread) kendi bağlantısını açar
                    thread_conn = sqlite3.connect(tmp_path, timeout=30.0)
                    thread_conn.execute("PRAGMA foreign_keys = ON;")
                    thread_conn.execute("PRAGMA busy_timeout = 15000;")
                    thread_conn.row_factory = sqlite3.Row
                    thread_accounting = AccountingService(thread_conn)
                    thread_accounting.give_debt(p_id, str(amount), currency, "2026-11-06")
                    thread_conn.commit()
                    thread_conn.close()
                except Exception as e:
                    errors.append(e)

            # Bilgisayar ve Telefon eşzamanlı istek gönderiyor
            t1 = threading.Thread(target=client_action, args=("USD", 1000))
            t2 = threading.Thread(target=client_action, args=("EUR", 2000))

            t1.start()
            t2.start()
            t1.join()
            t2.join()

            self.assertEqual(len(errors), 0, f"Eşzamanlılık hataları oluştu: {errors}")

            verify_conn = sqlite3.connect(tmp_path)
            verify_conn.row_factory = sqlite3.Row
            verify_accounting = AccountingService(verify_conn)
            details = verify_accounting.get_person_details(p_id)
            self.assertEqual(details["currency_balances"]["USD"]["remaining"], Decimal("1000.00"))
            self.assertEqual(details["currency_balances"]["EUR"]["remaining"], Decimal("2000.00"))
            verify_conn.close()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            wal_file = f"{tmp_path}-wal"
            shm_file = f"{tmp_path}-shm"
            if os.path.exists(wal_file):
                os.remove(wal_file)
            if os.path.exists(shm_file):
                os.remove(shm_file)

if __name__ == "__main__":
    unittest.main()
