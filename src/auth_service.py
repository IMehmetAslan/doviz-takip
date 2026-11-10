"""
Authentication & Session Management Service for Multi-Device Access.
Provides:
- PBKDF2-HMAC-SHA256 salted password hashing (100,000 iterations)
- Cryptographically secure 64-character session token generation
- In-memory rate limiting and brute-force protection
- Secure session validation and cleanup
"""

import sqlite3
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple

DEFAULT_ITERATIONS = 100000

def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """Tuzlu (salted) PBKDF2-HMAC-SHA256 parola hash'i üretir."""
    if not salt:
        salt = secrets.token_hex(16)
    salt_bytes = bytes.fromhex(salt)
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt_bytes,
        DEFAULT_ITERATIONS
    ).hex()
    return pwd_hash, salt

def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Parolayı zamanlama saldırılarına karşı güvenli (constant-time) şekilde doğrular."""
    calculated_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(calculated_hash, password_hash)

class RateLimiter:
    """Brute-force saldırılarına karşı başarısız denemeleri takip eder."""
    def __init__(self, max_attempts: int = 5, lock_duration_minutes: int = 15):
        self.max_attempts = max_attempts
        self.lock_duration = timedelta(minutes=lock_duration_minutes)
        self.attempts: Dict[str, list] = {}

    def is_locked(self, key: str) -> bool:
        now = datetime.now()
        if key not in self.attempts:
            return False
        # Süresi dolan kayıtları temizle
        self.attempts[key] = [t for t in self.attempts[key] if now - t < self.lock_duration]
        return len(self.attempts[key]) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        now = datetime.now()
        if key not in self.attempts:
            self.attempts[key] = []
        self.attempts[key].append(now)

    def record_success(self, key: str) -> None:
        if key in self.attempts:
            del self.attempts[key]

# Global singleton rate limiter
login_rate_limiter = RateLimiter(max_attempts=5, lock_duration_minutes=15)

class AuthService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def init_default_admin(self, default_username: str = "admin", default_password: str = "admin123") -> bool:
        """İlk açılışta veritabanında hiç kullanıcı yoksa varsayılan yöneticiyi oluşturur."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM app_users")
        count = cur.fetchone()[0]
        if count == 0:
            pwd_hash, salt = hash_password(default_password)
            cur.execute(
                """
                INSERT INTO app_users (username, password_hash, salt, full_name, role, active)
                VALUES (?, ?, ?, 'Sistem Yöneticisi', 'ADMIN', 1)
                """,
                (default_username.strip().lower(), pwd_hash, salt)
            )
            self.conn.commit()
            return True
        return False

    def create_user(
        self,
        username: str,
        password: str,
        full_name: str = "Kullanıcı",
        role: str = "USER"
    ) -> int:
        """Yeni bir kullanıcı hesabı oluşturur."""
        clean_user = username.strip().lower()
        if not clean_user:
            raise ValueError("Kullanıcı adı boş olamaz.")
        if len(password) < 6:
            raise ValueError("Şifre en az 6 karakter olmalıdır.")

        pwd_hash, salt = hash_password(password)
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO app_users (username, password_hash, salt, full_name, role, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (clean_user, pwd_hash, salt, full_name.strip(), role)
        )
        self.conn.commit()
        return cur.lastrowid

    def login(
        self,
        username: str,
        password: str,
        ip_address: str = "",
        user_agent: str = ""
    ) -> Dict[str, Any]:
        """Kullanıcı girişini denetler, başarılıysa 30 günlük güvenli oturum token'ı oluşturur."""
        clean_user = username.strip().lower()
        rate_key = f"{ip_address}:{clean_user}"

        if login_rate_limiter.is_locked(rate_key):
            raise ValueError("Çok fazla hatalı giriş denemesi yapıldı. Lütfen 15 dakika sonra tekrar deneyin.")

        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, username, password_hash, salt, full_name, role, active 
            FROM app_users 
            WHERE username = ?
            """,
            (clean_user,)
        )
        user = cur.fetchone()

        if not user or not user["active"]:
            login_rate_limiter.record_failure(rate_key)
            raise ValueError("Kullanıcı adı veya şifre hatalı.")

        if not verify_password(password, user["password_hash"], user["salt"]):
            login_rate_limiter.record_failure(rate_key)
            raise ValueError("Kullanıcı adı veya şifre hatalı.")

        # Giriş başarılı, rate limiter'ı sıfırla
        login_rate_limiter.record_success(rate_key)

        # Güvenli 64 karakter oturum token'ı üret
        token = secrets.token_hex(32)
        expires_at = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        cur.execute(
            """
            INSERT INTO app_sessions (user_id, token, expires_at, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user["id"], token, expires_at, ip_address, user_agent)
        )
        self.conn.commit()

        return {
            "token": token,
            "expires_at": expires_at,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "full_name": user["full_name"],
                "role": user["role"]
            }
        }

    def validate_session(self, token: str) -> Optional[Dict[str, Any]]:
        """Oturum token'ının geçerliliğini ve süresini kontrol eder."""
        if not token or len(token) < 32:
            return None

        cur = self.conn.cursor()

        cur.execute(
            """
            SELECT s.token, s.expires_at, u.id as user_id, u.username, u.full_name, u.role
            FROM app_sessions s
            JOIN app_users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > CURRENT_TIMESTAMP AND u.active = 1
            """,
            (token,)
        )
        row = cur.fetchone()
        if not row:
            return None

        return {
            "token": row["token"],
            "user_id": row["user_id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "role": row["role"]
        }

    def logout(self, token: str) -> bool:
        """Oturum token'ını veritabanından silerek oturumu sonlandırır."""
        if not token:
            return False
        cur = self.conn.cursor()
        cur.execute("DELETE FROM app_sessions WHERE token = ?", (token,))
        self.conn.commit()
        return True

    def change_password(self, user_id: int, old_password: str, new_password: str) -> bool:
        """Mevcut parolayı doğrulayarak yeni parola ile günceller."""
        if len(new_password) < 6:
            raise ValueError("Yeni şifre en az 6 karakter olmalıdır.")

        cur = self.conn.cursor()
        cur.execute("SELECT password_hash, salt FROM app_users WHERE id = ?", (user_id,))
        user = cur.fetchone()
        if not user:
            raise ValueError("Kullanıcı bulunamadı.")

        if not verify_password(old_password, user["password_hash"], user["salt"]):
            raise ValueError("Mevcut şifre hatalı.")

        new_hash, new_salt = hash_password(new_password)
        cur.execute(
            """
            UPDATE app_users 
            SET password_hash = ?, salt = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_hash, new_salt, user_id)
        )
        # Güvenlik gereği diğer tüm açık oturumları sonlandır
        cur.execute("DELETE FROM app_sessions WHERE user_id = ?", (user_id,))
        self.conn.commit()
        return True
