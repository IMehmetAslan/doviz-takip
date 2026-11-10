"""
REST API and Multi-Threaded Static Server for Personal Currency Debt Tracking App.
Uses Python's standard library (100% offline, zero external dependencies).
Features:
- Multi-threaded concurrent request handling (ThreadingTCPServer) for PC + Phone simultaneous access
- PBKDF2 Session & Token authentication middleware
- Permanent database persistence & automatic WAL safety
- Automatic startup and pre-transaction backups
- ZIP export for USB/External drive
- Secure restore with integrity verification
"""

import http.server
import socketserver
import json
import urllib.parse
from pathlib import Path
from decimal import Decimal
from src.config import get_database_path, migrate_legacy_data_if_needed
from src.database import init_db
from src.accounting_service import AccountingService
from src.reporting_engine import ReportingEngine
from src.auth_service import AuthService
from src.backup_service import (
    backup_database,
    restore_database_safely,
    export_backup_zip,
    get_backup_status_summary,
    create_pre_action_snapshot,
    perform_startup_backup_if_needed
)
from src.decimal_engine import to_decimal

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)

class AppApiHandler(http.server.SimpleHTTPRequestHandler):
    def _get_conn(self):
        return init_db()

    def _get_token(self):
        # 1. Authorization: Bearer <token>
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            return auth_hdr.split("Bearer ")[1].strip()

        # 2. X-Session-Token
        custom_hdr = self.headers.get("X-Session-Token", "")
        if custom_hdr:
            return custom_hdr.strip()

        # 3. Cookie: session_token=...
        cookie_hdr = self.headers.get("Cookie", "")
        if "session_token=" in cookie_hdr:
            for part in cookie_hdr.split(";"):
                part = part.strip()
                if part.startswith("session_token="):
                    return part.split("session_token=")[1].strip()
        return None

    def _get_current_user(self, conn):
        token = self._get_token()
        if not token:
            return None
        auth = AuthService(conn)
        return auth.validate_session(token)

    def _send_json(self, data, status=200, set_cookie=None, clear_cookie=False):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Token")
        if set_cookie:
            self.send_header("Set-Cookie", f"session_token={set_cookie}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax")
        elif clear_cookie:
            self.send_header("Set-Cookie", "session_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(json.dumps(data, cls=DecimalEncoder, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Session-Token")
        self.end_headers()

    def _get_clean_path(self):
        """Vercel serverless ve yerel ortamlar için temiz API yolunu döner."""
        raw = self.path
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)

        candidate = None
        # 1. Query param __path (Vercel rewrite)
        if "__path" in qs:
            candidate = qs["__path"][0]

        # 2. Vercel orijinal yol başlıkları
        if not candidate:
            for h in ["x-vercel-original-path", "x-real-path", "x-forwarded-uri", "x-matched-path"]:
                val = self.headers.get(h)
                if val and "/api/index.py" not in val:
                    candidate = urllib.parse.urlparse(val).path
                    break

        # 3. Path doğrudan index.py içeriyorsa
        if not candidate:
            p = parsed.path
            if "/api/index.py" in p:
                candidate = p.split("/api/index.py")[-1]
            else:
                candidate = p

        if not candidate:
            candidate = "/"

        candidate = candidate.strip()
        # Statik dosyalar
        if candidate in ["/", "/index.html", "/manifest.json", "/service-worker.js", "/icon.svg", "/favicon.ico"]:
            return candidate

        # API endpoint'i olarak normalize et (Her zaman /api/... döner)
        clean = candidate.lstrip("/")
        if clean.startswith("api/"):
            clean = clean[4:]
        return "/api/" + clean.rstrip("/")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = self._get_clean_path()
        query = urllib.parse.parse_qs(parsed.query)

        # Statik UI ve PWA dosyaları (Herkese açık)
        if path == "/api/health":
            db_type = "postgres" if is_postgres_enabled() else "sqlite"
            db_ok = False
            err_msg = ""
            try:
                test_conn = self._get_conn()
                test_cur = test_conn.cursor()
                test_cur.execute("SELECT 1;")
                test_conn.close()
                db_ok = True
            except Exception as e:
                err_msg = str(e)

            self._send_json({
                "status": "ok",
                "db_type": db_type,
                "db_connected": db_ok,
                "db_error": err_msg,
                "has_db_url": bool(get_database_url()),
                "vercel_env": os.environ.get("VERCEL", "0")
            })
            return

        if path == "/" or path == "/index.html":
            self._serve_file("src/web/index.html", "text/html")
            return
        elif path == "/manifest.json":
            self._serve_file("src/web/manifest.json", "application/manifest+json")
            return
        elif path == "/service-worker.js":
            self._serve_file("src/web/service-worker.js", "application/javascript")
            return
        elif path == "/icon.svg" or path == "/favicon.ico":
            self._serve_file("src/web/icon.svg", "image/svg+xml")
            return

        conn = self._get_conn()
        try:
            # Oturum durumu sorgulama endpoint'i
            if path == "/api/auth/me":
                user = self._get_current_user(conn)
                if user:
                    self._send_json({"success": True, "user": user})
                else:
                    self._send_json({"success": False, "error": "Oturum açık değil", "unauthorized": True}, 401)
                return

            # Güvenlik Kuralı: Tüm diğer API endpoint'leri için aktif oturum zorunludur
            current_user = self._get_current_user(conn)
            if not current_user:
                self._send_json({"success": False, "error": "Lütfen önce giriş yapınız.", "unauthorized": True}, 401)
                return

            service = AccountingService(conn)
            reporting = ReportingEngine(conn)

            if path == "/api/dashboard":
                data = reporting.get_dashboard_summary()
                self._send_json({"success": True, "data": data})

            elif path == "/api/debtors":
                status_filter = query.get("status", ["ALL"])[0]
                search = query.get("search", [""])[0]
                data = reporting.get_debtors_list(filter_status=status_filter, search=search)
                self._send_json({"success": True, "data": data})

            elif path.startswith("/api/person/"):
                parts = path.strip("/").split("/")
                if len(parts) >= 3:
                    person_id = int(parts[2])
                    details = service.get_person_details(person_id)
                    if details:
                        self._send_json({"success": True, "data": details})
                    else:
                        self._send_json({"success": False, "error": "Kişi bulunamadı"}, 404)
                else:
                    self._send_json({"error": "Geçersiz istek"}, 400)

            elif path == "/api/contracts":
                status_filter = query.get("status", ["ALL"])[0]
                search = query.get("search", [""])[0]
                data = service.get_contracts_list(status_filter=status_filter, search=search)
                self._send_json({"success": True, "data": data})

            elif path.startswith("/api/contract/"):
                parts = path.strip("/").split("/")
                if len(parts) >= 3:
                    contract_id = int(parts[2])
                    details = service.get_contract_details(contract_id)
                    if details:
                        self._send_json({"success": True, "data": details})
                    else:
                        self._send_json({"success": False, "error": "Bağlantı bulunamadı"}, 404)
                else:
                    self._send_json({"error": "Geçersiz istek"}, 400)

            elif path == "/api/currencies":
                cur = conn.cursor()
                cur.execute("SELECT code, name, symbol FROM currencies WHERE active = 1 ORDER BY is_base DESC, code ASC")
                currs = [dict(r) for r in cur.fetchall()]
                self._send_json({"success": True, "data": currs})

            elif path == "/api/persons/simple":
                cur = conn.cursor()
                cur.execute("SELECT id, name, phone FROM accounts WHERE active = 1 ORDER BY name ASC")
                persons = [dict(r) for r in cur.fetchall()]
                self._send_json({"success": True, "data": persons})

            elif path == "/api/backup/status":
                status = get_backup_status_summary()
                self._send_json({"success": True, "data": status})

            elif path == "/api/backup/download-zip":
                zip_path = export_backup_zip()
                self._serve_download(str(zip_path), zip_path.name, "application/zip")
                return

            elif path.startswith("/api/backup/download-file/"):
                filename = urllib.parse.unquote(path.split("/api/backup/download-file/")[1])
                status = get_backup_status_summary()
                matched = [b for b in status["backups"] if b["filename"] == filename]
                if matched:
                    fpath = matched[0]["path"]
                    self._serve_download(fpath, filename, "application/x-sqlite3")
                    return
                else:
                    self._send_json({"error": "Yedek dosyası bulunamadı"}, 404)

            else:
                self._send_json({"error": "Endpoint bulunamadı"}, 404)
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)
        finally:
            conn.close()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = self._get_clean_path()
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
        data = json.loads(body) if body else {}

        # 1. KULLANICI GİRİŞİ (HERKESE AÇIK)
        if path == "/api/auth/login":
            conn = self._get_conn()
            try:
                ip = self.client_address[0] if self.client_address else "127.0.0.1"
                user_agent = self.headers.get("User-Agent", "")
                username = data.get("username", "")
                password = data.get("password", "")
                auth = AuthService(conn)
                res = auth.login(username, password, ip_address=ip, user_agent=user_agent)
                self._send_json({"success": True, "token": res["token"], "data": res, "user": res["user"]}, 200, set_cookie=res["token"])
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 400)
            finally:
                conn.close()
            return

        # 2. ÇIKIŞ YAPMA
        if path == "/api/auth/logout":
            conn = self._get_conn()
            try:
                token = self._get_token()
                if token:
                    AuthService(conn).logout(token)
                self._send_json({"success": True, "message": "Çıkış yapıldı"}, 200, clear_cookie=True)
            finally:
                conn.close()
            return

        # 3. DİĞER TÜM POST İŞLEMLERİ İÇİN OTURUM DOĞRULAMA
        conn = self._get_conn()
        try:
            current_user = self._get_current_user(conn)
            if not current_user:
                self._send_json({"success": False, "error": "Lütfen önce giriş yapınız.", "unauthorized": True}, 401)
                return

            service = AccountingService(conn)

            if path == "/api/auth/change-password":
                old_pwd = data.get("old_password", "")
                new_pwd = data.get("new_password", "")
                AuthService(conn).change_password(current_user["user_id"], old_pwd, new_pwd)
                self._send_json({"success": True, "message": "Şifreniz güncellendi. Lütfen yeni şifrenizle giriş yapınız."}, 200, clear_cookie=True)

            elif path == "/api/backup/restore":
                backup_file = data.get("backup_file")
                if not backup_file:
                    self._send_json({"success": False, "error": "Yedek dosya yolu belirtilmedi."}, 400)
                    return
                res = restore_database_safely(Path(backup_file))
                self._send_json(res)

            elif path == "/api/person/create":
                person_id = service.create_person(
                    name=data.get("name"),
                    phone=data.get("phone", ""),
                    description=data.get("description", ""),
                    default_currency=data.get("default_currency", "USD")
                )
                conn.commit()
                self._send_json({"success": True, "person_id": person_id})

            elif path == "/api/person/update":
                service.update_person(
                    person_id=int(data.get("person_id")),
                    name=data.get("name"),
                    phone=data.get("phone", ""),
                    description=data.get("description", "")
                )
                conn.commit()
                self._send_json({"success": True, "message": "Kişi bilgileri güncellendi"})

            elif path == "/api/debt/give":
                create_pre_action_snapshot()

                tx_id = service.give_debt(
                    person_id=int(data.get("person_id")),
                    amount=data.get("amount"),
                    currency_code=data.get("currency_code", "USD"),
                    date=data.get("date"),
                    exchange_rate=data.get("exchange_rate"),
                    description=data.get("description", "")
                )
                conn.commit()
                self._send_json({"success": True, "transaction_id": tx_id})

            elif path == "/api/payment/receive":
                create_pre_action_snapshot()

                tx_id = service.receive_payment(
                    person_id=int(data.get("person_id")),
                    amount=data.get("amount"),
                    currency_code=data.get("currency_code", "USD"),
                    date=data.get("date"),
                    exchange_rate=data.get("exchange_rate"),
                    description=data.get("description", "")
                )
                conn.commit()
                self._send_json({"success": True, "transaction_id": tx_id})

            elif path == "/api/transaction/cancel":
                create_pre_action_snapshot()
                tx_id = int(data.get("transaction_id"))
                reason = data.get("reason", "Kullanıcı tarafından iptal edildi")
                service.cancel_transaction(tx_id, reason=reason)
                conn.commit()
                self._send_json({"success": True, "message": "İşlem iptal edildi"})

            elif path == "/api/contract/create":
                create_pre_action_snapshot()
                contract_id = service.create_contract(
                    person_id=int(data.get("person_id")),
                    sell_currency=data.get("sell_currency"),
                    sell_amount=data.get("sell_amount"),
                    buy_currency=data.get("buy_currency"),
                    total_target_amount=data.get("total_target_amount"),
                    unit_price=data.get("unit_price"),
                    contract_date=data.get("contract_date"),
                    description=data.get("description", "")
                )
                conn.commit()
                self._send_json({"success": True, "contract_id": contract_id})

            elif path == "/api/contract/payment":
                create_pre_action_snapshot()
                payment_id = service.add_contract_payment(
                    contract_id=int(data.get("contract_id")),
                    amount=data.get("amount"),
                    payment_date=data.get("payment_date"),
                    description=data.get("description", "")
                )
                conn.commit()
                self._send_json({"success": True, "payment_id": payment_id})

            elif path == "/api/debtors/delete-closed":
                backup_path = backup_database(prefix="pre_delete")
                person_id = int(data.get("person_id"))
                res = service.delete_closed_record(person_id)
                conn.commit()
                self._send_json({
                    "success": True,
                    "message": "Kapanmış borç kaydı ve ilişkili işlem geçmişi başarıyla silindi.",
                    "data": res,
                    "backup_file": backup_path.name
                })

            elif path == "/api/backup/create":
                backup_path = backup_database(prefix="manual")
                self._send_json({"success": True, "backup_path": str(backup_path), "filename": backup_path.name})

            elif path == "/api/backup/export-zip":
                zip_path = export_backup_zip()
                self._send_json({"success": True, "zip_path": str(zip_path), "filename": zip_path.name})

            else:
                self._send_json({"error": "Endpoint bulunamadı"}, 404)
        except Exception as e:
            conn.rollback()
            self._send_json({"success": False, "error": str(e)}, 500)
        finally:
            conn.close()

    def _serve_file(self, filepath, content_type):
        path = Path(filepath)
        if not path.exists():
            root_path = Path(__file__).resolve().parent.parent / filepath
            if root_path.exists():
                path = root_path
            else:
                self.send_error(404, f"Dosya bulunamadı: {filepath}")
                return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_download(self, filepath, download_name, content_type):
        path = Path(filepath)
        if not path.exists():
            self.send_error(404, "İndirilecek dosya bulunamadı")
            return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """
    Çoklu iş parçacığı (multi-threading) desteği:
    Telefon ve bilgisayar aynı anda bağlandığında birbirlerini bloklamadan eşzamanlı çalışır.
    """
    allow_reuse_address = True
    daemon_threads = True

def start_server(port=8765):
    # Başlangıçta legacy veri varsa kalıcıya taşı
    migrate_legacy_data_if_needed()

    # Uygulama açılışında otomatik günlük yedek ve 30 günlük temizlik
    perform_startup_backup_if_needed()

    current_port = port
    max_tries = 10
    httpd = None
    for _ in range(max_tries):
        try:
            httpd = ThreadedTCPServer(("", current_port), AppApiHandler)
            break
        except OSError:
            current_port += 1
            
    if not httpd:
        raise RuntimeError(f"{port} ve ardındaki portlar kullanılamadı.")

    app_url = f"http://127.0.0.1:{current_port}"
    print(f"\n" + "="*65)
    print(f"🚀 [UYGULAMA BAŞLATILDI] Tarayıcınızdan uygulamayı kullanabilirsiniz:")
    print(f"   👉  {app_url}")
    print(f"   📂  Kalıcı Veritabanı: {get_database_path()}")
    print("="*65 + "\n")

    import os, threading, time
    def _open():
        time.sleep(1)
        try:
            os.system(f"open {app_url} 2>/dev/null")
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()

    httpd.serve_forever()

if __name__ == "__main__":
    start_server()
