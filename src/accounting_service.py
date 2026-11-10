"""
Core Debt and Receivable Service for Personal Currency Debt Tracking.
Handles giving debt, receiving payments, balance tracking per currency, and archiving.
"""

from decimal import Decimal
import sqlite3
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from src.decimal_engine import to_decimal, round_money, round_rate, calc_base_amount

class AccountingService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def log_audit(self, action: str, entity_name: str, entity_id: str, details: Dict[str, Any]):
        """Denetim ve güvenlik izi kaydı tutar."""
        self.conn.execute(
            """
            INSERT INTO audit_logs (action, entity_name, entity_id, details)
            VALUES (?, ?, ?, ?)
            """,
            (action, entity_name, str(entity_id), json.dumps(details, ensure_ascii=False, default=str)),
        )

    # ------------------ KİŞİ / FİRMA YÖNETİMİ ------------------
    def create_person(
        self,
        name: str,
        phone: str = "",
        description: str = "",
        code: Optional[str] = None,
        default_currency: str = "USD"
    ) -> int:
        cur = self.conn.cursor()
        if not code:
            cur.execute("SELECT COUNT(*) FROM accounts")
            count = cur.fetchone()[0] + 1
            code = f"KISI-{count:03d}"

        cur.execute(
            """
            INSERT INTO accounts (
                code, name, account_type, phone, description, default_currency, active
            ) VALUES (?, ?, 'PERSON', ?, ?, ?, 1)
            """,
            (code, name.strip(), phone.strip() if phone else "", description.strip() if description else "", default_currency),
        )
        person_id = cur.lastrowid
        self.log_audit("INSERT", "person", person_id, {"code": code, "name": name, "phone": phone})
        return person_id

    def update_person(self, person_id: int, name: str, phone: str = "", description: str = "") -> None:
        self.conn.execute(
            """
            UPDATE accounts 
            SET name = ?, phone = ?, description = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (name.strip(), phone.strip() if phone else "", description.strip() if description else "", person_id)
        )
        self.log_audit("UPDATE", "person", person_id, {"name": name, "phone": phone, "description": description})

    def delete_person(self, person_id: int) -> bool:
        """Kişiyi siler. Ancak geçmişte işlem fişi varsa kontrollü olarak silmez veya arşive çeker."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM transaction_lines WHERE account_id = ?", (person_id,))
        count = cur.fetchone()[0]
        if count > 0:
            raise ValueError("Bu kişiye ait geçmiş işlem hareketleri bulunmaktadır. Veri güvenliği gereği silinemez; bakiyesi kapandıysa arşive alınır.")
        
        self.conn.execute("DELETE FROM accounts WHERE id = ?", (person_id,))
        self.log_audit("DELETE", "person", person_id, {})
        return True

    def delete_closed_record(self, person_id: int) -> Dict[str, Any]:
        """
        Kapanan Borçlar Arşivinden manuel silme işlemi.
        Güvenlik Kuralları:
        1. Sadece bakiyesi tamamen sıfırlanmış (kapanmış) borçlar silinebilir.
        2. Aktif borcu (kalan > 0) olan veya açık döviz bağlantısı bulunan kayıtlar ASLA silinemez.
        3. Yalnızca seçilen kişiye ait hareketler ve kayıtlar silinir; diğer kişilerin verileri kesinlikle etkilenmez.
        4. Tüm silme adımları atomik bir transaction içinde yürütülür, hata durumunda rollback yapılır.
        """
        cur = self.conn.cursor()

        # 1. Kayıt varlık kontrolü
        cur.execute("SELECT id, name, code FROM accounts WHERE id = ?", (person_id,))
        person = cur.fetchone()
        if not person:
            raise ValueError("Silinecek kişi veya firma kaydı bulunamadı.")
        person_name = person["name"]

        # 2. Aktif borç kontrolü (Herhangi bir dövizde kalan borç > 0 ise silme engellenir)
        cur.execute(
            """
            SELECT 
                l.currency_code,
                SUM(CASE WHEN l.direction = 'DEBIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) -
                SUM(CASE WHEN l.direction = 'CREDIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) as net_balance
            FROM transaction_lines l
            JOIN transactions t ON l.transaction_id = t.id
            WHERE l.account_id = ? AND t.status = 'CONFIRMED'
            GROUP BY l.currency_code
            """,
            (person_id,)
        )
        for r in cur.fetchall():
            net_bal = to_decimal(r["net_balance"])
            if net_bal > Decimal("0.00"):
                raise ValueError(
                    f"Bu kaydın {net_bal} {r['currency_code']} tutarında aktif borcu bulunmaktadır! "
                    "Yalnızca tamamen kapanmış arşiv kayıtları manuel olarak silinebilir."
                )

        # 3. Açık döviz sözleşmesi kontrolü
        cur.execute(
            """
            SELECT COUNT(*) as count FROM fx_contracts
            WHERE account_id = ? AND status != 'COMPLETED'
            """,
            (person_id,)
        )
        active_contracts = cur.fetchone()["count"]
        if active_contracts > 0:
            raise ValueError("Bu kayda ait henüz tamamlanmamış aktif döviz bağlantısı bulunmaktadır. Silinemez.")

        # 4. Yalnızca bu kişiye ait ilişkili kayıtları temizle
        # 4a. FX Lot tahsisatları ve lotlar
        cur.execute(
            """
            DELETE FROM fx_lot_allocations 
            WHERE transaction_line_id IN (SELECT id FROM transaction_lines WHERE account_id = ?)
            """,
            (person_id,)
        )
        cur.execute("DELETE FROM fx_lots WHERE account_id = ?", (person_id,))

        # 4b. Sözleşme ödemeleri ve sözleşmeler
        cur.execute(
            """
            DELETE FROM fx_contract_payments 
            WHERE contract_id IN (SELECT id FROM fx_contracts WHERE account_id = ?)
            """,
            (person_id,)
        )
        cur.execute("DELETE FROM fx_contracts WHERE account_id = ?", (person_id,))

        # 4c. İşlem fişleri (transactions & transaction_lines)
        cur.execute("SELECT DISTINCT transaction_id FROM transaction_lines WHERE account_id = ?", (person_id,))
        tx_ids = [r[0] for r in cur.fetchall()]

        cur.execute("DELETE FROM transaction_lines WHERE account_id = ?", (person_id,))

        for tid in tx_ids:
            cur.execute("SELECT COUNT(*) FROM transaction_lines WHERE transaction_id = ?", (tid,))
            if cur.fetchone()[0] == 0:
                cur.execute("DELETE FROM transactions WHERE id = ?", (tid,))

        # 4d. Cari hesabı / kişiyi sil
        cur.execute("DELETE FROM accounts WHERE id = ?", (person_id,))

        # 4e. Denetim logu kaydı
        self.log_audit("DELETE_CLOSED_RECORD", "account", person_id, {
            "name": person_name,
            "deleted_tx_count": len(tx_ids),
            "timestamp": datetime.now().isoformat()
        })

        return {
            "person_id": person_id,
            "person_name": person_name,
            "deleted_tx_count": len(tx_ids)
        }

    # ------------------ BORÇ VE ÖDEME İŞLEMLERİ ------------------
    def _check_duplicate(self, person_id: int, direction: str, amount: Decimal, currency_code: str, date: str) -> None:
        """Kullanıcının çift tıklaması veya yanlışlıkla aynı işlemi saniyeler içinde tekrarlamasını engeller."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM transaction_lines l
            JOIN transactions t ON l.transaction_id = t.id
            WHERE l.account_id = ? 
              AND l.direction = ? 
              AND l.amount = ? 
              AND l.currency_code = ? 
              AND t.tx_date = ? 
              AND t.status = 'CONFIRMED'
              AND datetime(t.created_at) >= datetime('now', '-30 seconds')
            """,
            (person_id, direction, str(amount), currency_code, date)
        )
        if cur.fetchone()[0] > 0:
            raise ValueError("Bu işlem (aynı kişi, tutar ve tarih) son 30 saniye içinde zaten kaydedilmiş görünüyor. Çift kaydı önlemek için durduruldu.")

    def give_debt(
        self,
        person_id: int,
        amount: Any,
        currency_code: str,
        date: str,
        exchange_rate: Optional[Any] = None,
        description: str = ""
    ) -> int:
        """
        Kişiye borç verme işlemi.
        Kişi borçlanır (DEBIT).
        Kalan alacağımız artar.
        """
        amt = to_decimal(amount)
        if amt <= Decimal("0"):
            raise ValueError("Verilen borç tutarı 0'dan büyük olmalıdır.")

        self._check_duplicate(person_id, "DEBIT", amt, currency_code, date)

        rate = to_decimal(exchange_rate) if exchange_rate else Decimal("1.0000")
        base_amt = round_money(amt * rate)

        import uuid
        tx_number = f"BORC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO transactions (tx_number, tx_date, tx_type, reference_no, description, status)
            VALUES (?, ?, 'DEBT_GIVEN', ?, ?, 'CONFIRMED')
            """,
            (tx_number, date, tx_number, description.strip() if description else f"{amt} {currency_code} Borç Verildi")
        )
        tx_id = cur.lastrowid

        cur.execute(
            """
            INSERT INTO transaction_lines (
                transaction_id, line_no, account_id, direction,
                currency_code, amount, exchange_rate, base_amount, description
            ) VALUES (?, 1, ?, 'DEBIT', ?, ?, ?, ?, ?)
            """,
            (tx_id, person_id, currency_code, str(amt), str(rate), str(base_amt), description)
        )

        self.log_audit("GIVE_DEBT", "transaction", tx_id, {
            "person_id": person_id, "amount": str(amt), "currency": currency_code, "date": date
        })
        return tx_id

    def receive_payment(
        self,
        person_id: int,
        amount: Any,
        currency_code: str,
        date: str,
        exchange_rate: Optional[Any] = None,
        description: str = ""
    ) -> int:
        """
        Borçludan ödeme alma işlemi (Kısmi veya tam tahsilat).
        Kişi alacaklanır / borcu düşer (CREDIT).
        Kalan alacağımız azalır.
        """
        amt = to_decimal(amount)
        if amt <= Decimal("0"):
            raise ValueError("Alınan ödeme tutarı 0'dan büyük olmalıdır.")

        self._check_duplicate(person_id, "CREDIT", amt, currency_code, date)

        rate = to_decimal(exchange_rate) if exchange_rate else Decimal("1.0000")
        base_amt = round_money(amt * rate)

        import uuid
        tx_number = f"ODEME-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO transactions (tx_number, tx_date, tx_type, reference_no, description, status)
            VALUES (?, ?, 'PAYMENT_RECEIVED', ?, ?, 'CONFIRMED')
            """,
            (tx_number, date, tx_number, description.strip() if description else f"{amt} {currency_code} Ödeme Alındı")
        )
        tx_id = cur.lastrowid

        cur.execute(
            """
            INSERT INTO transaction_lines (
                transaction_id, line_no, account_id, direction,
                currency_code, amount, exchange_rate, base_amount, description
            ) VALUES (?, 1, ?, 'CREDIT', ?, ?, ?, ?, ?)
            """,
            (tx_id, person_id, currency_code, str(amt), str(rate), str(base_amt), description)
        )

        self.log_audit("RECEIVE_PAYMENT", "transaction", tx_id, {
            "person_id": person_id, "amount": str(amt), "currency": currency_code, "date": date
        })
        return tx_id

    def cancel_transaction(self, tx_id: int, reason: str = "") -> None:
        """İşlemi kontrollü olarak iptal eder (Silinmez, statüsü CANCELLED olur, bakiye hesaplarından düşer)."""
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE transactions 
            SET status = 'CANCELLED', cancelled_at = CURRENT_TIMESTAMP, cancellation_reason = ?
            WHERE id = ?
            """,
            (reason, tx_id)
        )
        self.log_audit("CANCEL", "transaction", tx_id, {"reason": reason})

    # ------------------ DETAY & BAKİYE HESAPLARI ------------------
    def get_person_details(self, person_id: int) -> Optional[Dict[str, Any]]:
        """Kişinin genel bilgilerini, para birimlerine göre borç durumunu ve işlem geçmişini döner."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM accounts WHERE id = ?", (person_id,))
        row = cur.fetchone()
        if not row:
            return None

        person = dict(row)

        # 1. Para birimlerine göre borç, ödeme ve kalan bakiyeler
        cur.execute(
            """
            SELECT 
                l.currency_code,
                SUM(CASE WHEN l.direction = 'DEBIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) as total_given,
                SUM(CASE WHEN l.direction = 'CREDIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) as total_paid
            FROM transaction_lines l
            JOIN transactions t ON l.transaction_id = t.id
            WHERE l.account_id = ? AND t.status = 'CONFIRMED'
            GROUP BY l.currency_code
            """,
            (person_id,)
        )

        currency_balances = {}
        has_active_debt = False

        for r in cur.fetchall():
            curr = r["currency_code"]
            given = to_decimal(r["total_given"])
            paid = to_decimal(r["total_paid"])
            remaining = round_money(given - paid)
            
            # Kalan borç sıfırdan büyükse aktiftir
            is_closed = (remaining <= Decimal("0.00"))
            if not is_closed:
                has_active_debt = True

            currency_balances[curr] = {
                "currency_code": curr,
                "total_given": given,
                "total_paid": paid,
                "remaining": remaining,
                "is_closed": is_closed,
                "status": "Kapandı" if is_closed else "Aktif"
            }

        person["currency_balances"] = currency_balances
        person["has_active_debt"] = has_active_debt
        person["overall_status"] = "Aktif" if has_active_debt else ("Kapandı" if currency_balances else "İşlem Yok")

        # 2. Kronolojik İşlem Geçmişi
        cur.execute(
            """
            SELECT 
                t.id as tx_id,
                t.tx_number,
                t.tx_date,
                t.tx_type,
                t.status,
                l.direction,
                l.currency_code,
                l.amount,
                l.exchange_rate,
                l.base_amount,
                l.description
            FROM transactions t
            JOIN transaction_lines l ON t.id = l.transaction_id
            WHERE l.account_id = ? AND t.status = 'CONFIRMED'
            ORDER BY t.tx_date ASC, t.id ASC
            """,
            (person_id,)
        )

        history = []
        running_per_curr: Dict[str, Decimal] = {}

        for r in cur.fetchall():
            curr = r["currency_code"]
            amt = to_decimal(r["amount"])
            is_debt = (r["direction"] == "DEBIT")

            if curr not in running_per_curr:
                running_per_curr[curr] = Decimal("0.00")

            if is_debt:
                running_per_curr[curr] += amt
            else:
                running_per_curr[curr] -= amt

            # Bağlantı işlemleri için kullanıcı dostu etiket
            type_label = "Borç Verildi" if is_debt else "Ödeme Alındı"
            if r["tx_type"] == "CONTRACT_SELL":
                type_label = "Bağlantı: Döviz Çıkışı (Teslim Edilen)"
            elif r["tx_type"] == "CONTRACT_PAYMENT":
                type_label = "Bağlantı: Karşılık Tahsilatı"

            history.append({
                "tx_id": r["tx_id"],
                "tx_number": r["tx_number"],
                "tx_date": r["tx_date"],
                "tx_type": r["tx_type"],
                "type_label": type_label,
                "currency_code": curr,
                "amount": amt,
                "exchange_rate": to_decimal(r["exchange_rate"]),
                "base_amount": to_decimal(r["base_amount"]),
                "description": r["description"] or "",
                "running_balance": round_money(running_per_curr[curr]),
                "status": r["status"]
            })

        # Ekran için son işlemden eskiye veya kronolojik sıralı
        person["history"] = history
        return person

    # ------------------ DÖVİZ BAĞLANTI İŞLEMLERİ (FX CONTRACTS) ------------------
    def create_contract(
        self,
        person_id: int,
        sell_currency: str,
        sell_amount: Any,
        buy_currency: str,
        total_target_amount: Any,
        unit_price: Optional[Any] = None,
        contract_date: Optional[str] = None,
        description: str = ""
    ) -> int:
        """
        Yeni Döviz Bağlantı İşlemi oluşturur:
        Örnek: 1.000 EUR satılacak, karşılığında 50.000 TL alınacak.
        - Birim fiyat otomatik hesaplanır (veya girilen değer doğrulanır).
        - İlgili kişinin cari hareketlerine döviz çıkışı işlenir (1.000 EUR borç çıkışı).
        - Karşı taraftan alınması gereken 50.000 TL bağımsız bağlantı kaydında takip edilir.
        """
        s_amt = to_decimal(sell_amount)
        t_amt = to_decimal(total_target_amount)

        if s_amt <= Decimal("0"):
            raise ValueError("Satılacak döviz tutarı 0'dan büyük olmalıdır.")
        if t_amt <= Decimal("0"):
            raise ValueError("Alınacak karşılık tutarı 0'dan büyük olmalıdır.")
        if sell_currency == buy_currency:
            raise ValueError("Satılacak para birimi ile alınacak para birimi aynı olamaz.")

        # Birim fiyat (örn: 50.000 TL / 1.000 EUR = 50.00)
        calc_unit_price = round_rate(t_amt / s_amt)
        u_price = to_decimal(unit_price) if unit_price else calc_unit_price

        date_str = contract_date or datetime.now().strftime("%Y-%m-%d")

        import uuid
        contract_number = f"BGL-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

        cur = self.conn.cursor()

        # 1. Cari Hareket (Transactions): Verilen döviz çıkışı kaydı
        tx_desc = f"Bağlantı Çıkışı [{contract_number}]: {s_amt} {sell_currency} teslim edildi. Karşılığı: {t_amt} {buy_currency} (@{u_price})"
        if description:
            tx_desc += f" - {description.strip()}"

        cur.execute(
            """
            INSERT INTO transactions (tx_number, tx_date, tx_type, reference_no, description, status)
            VALUES (?, ?, 'CONTRACT_SELL', ?, ?, 'CONFIRMED')
            """,
            (contract_number, date_str, contract_number, tx_desc)
        )
        tx_id = cur.lastrowid

        # Kişiye borç çıkışı (DEBIT) yazılır
        cur.execute(
            """
            INSERT INTO transaction_lines (
                transaction_id, line_no, account_id, direction,
                currency_code, amount, exchange_rate, base_amount, description
            ) VALUES (?, 1, ?, 'DEBIT', ?, ?, ?, ?, ?)
            """,
            (tx_id, person_id, sell_currency, str(s_amt), str(u_price), str(round_money(s_amt * u_price)), tx_desc)
        )

        # 2. Döviz Bağlantı Sözleşmesi Kaydı (FX_CONTRACTS)
        cur.execute(
            """
            INSERT INTO fx_contracts (
                contract_number, account_id, sell_currency, sell_amount,
                buy_currency, unit_price, total_target_amount, collected_amount,
                remaining_amount, contract_date, description, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '0.00', ?, ?, ?, 'PENDING')
            """,
            (
                contract_number, person_id, sell_currency, str(s_amt),
                buy_currency, str(u_price), str(t_amt), str(t_amt),
                date_str, description.strip() if description else ""
            )
        )
        contract_id = cur.lastrowid

        self.log_audit("CREATE_CONTRACT", "fx_contract", contract_id, {
            "contract_number": contract_number,
            "person_id": person_id,
            "sell": f"{s_amt} {sell_currency}",
            "buy": f"{t_amt} {buy_currency}",
            "unit_price": str(u_price)
        })

        return contract_id

    def add_contract_payment(
        self,
        contract_id: int,
        amount: Any,
        payment_date: Optional[str] = None,
        description: str = ""
    ) -> int:
        """
        Bağlantıya karşılık kısmi veya tam ödeme/tahsilat ekler:
        - Tahsil edilen miktar toplam karşılığı geçemez.
        - Kalan miktar otomatik hesaplanır.
        - Durum 'PENDING', 'PARTIAL' veya 'COMPLETED' olarak güncellenir.
        - Cari hesap ödeme hareketi (CREDIT) eklenir.
        """
        pay_amt = to_decimal(amount)
        if pay_amt <= Decimal("0"):
            raise ValueError("Tahsilat tutarı 0'dan büyük olmalıdır.")

        cur = self.conn.cursor()
        cur.execute("SELECT * FROM fx_contracts WHERE id = ?", (contract_id,))
        c = cur.fetchone()
        if not c:
            raise ValueError("Bağlantı kaydı bulunamadı.")

        if c["status"] == "COMPLETED":
            raise ValueError("Bu bağlantının ödemesi zaten tamamen tamamlanmıştır.")

        target_total = to_decimal(c["total_target_amount"])
        already_collected = to_decimal(c["collected_amount"])
        remaining_before = to_decimal(c["remaining_amount"])
        buy_currency = c["buy_currency"]
        person_id = c["account_id"]
        contract_number = c["contract_number"]

        if pay_amt > remaining_before:
            raise ValueError(f"Girilen ödeme tutarı ({pay_amt} {buy_currency}) kalan borcu ({remaining_before} {buy_currency}) aşamaz.")

        new_collected = round_money(already_collected + pay_amt)
        new_remaining = round_money(target_total - new_collected)

        new_status = "COMPLETED" if new_remaining <= Decimal("0.00") else "PARTIAL"
        date_str = payment_date or datetime.now().strftime("%Y-%m-%d")

        import uuid
        payment_tx_no = f"BGL-ODM-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

        # 1. Cari Hareket (Transactions): Tahsilat kaydı
        tx_desc = f"Bağlantı Tahsilatı [{contract_number}]: {pay_amt} {buy_currency}"
        if description:
            tx_desc += f" - {description.strip()}"

        cur.execute(
            """
            INSERT INTO transactions (tx_number, tx_date, tx_type, reference_no, description, status)
            VALUES (?, ?, 'CONTRACT_PAYMENT', ?, ?, 'CONFIRMED')
            """,
            (payment_tx_no, date_str, contract_number, tx_desc)
        )
        tx_id = cur.lastrowid

        # Kişiden alacağımız tahsil edildi (CREDIT)
        cur.execute(
            """
            INSERT INTO transaction_lines (
                transaction_id, line_no, account_id, direction,
                currency_code, amount, exchange_rate, base_amount, description
            ) VALUES (?, 1, ?, 'CREDIT', ?, ?, '1.0000', ?, ?)
            """,
            (tx_id, person_id, buy_currency, str(pay_amt), str(pay_amt), tx_desc)
        )

        # 2. Bağlantı Ödeme Tablosu Kaydı (FX_CONTRACT_PAYMENTS)
        cur.execute(
            """
            INSERT INTO fx_contract_payments (
                contract_id, transaction_id, amount, currency_code, payment_date, description
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (contract_id, tx_id, str(pay_amt), buy_currency, date_str, description.strip() if description else "")
        )
        payment_record_id = cur.lastrowid

        # 3. Bağlantı Ana Tablosunu Güncelle (Kalan ve Durum)
        cur.execute(
            """
            UPDATE fx_contracts
            SET collected_amount = ?, remaining_amount = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (str(new_collected), str(new_remaining), new_status, contract_id)
        )

        self.log_audit("ADD_CONTRACT_PAYMENT", "fx_contract_payment", payment_record_id, {
            "contract_id": contract_id,
            "contract_number": contract_number,
            "amount": f"{pay_amt} {buy_currency}",
            "remaining": str(new_remaining),
            "status": new_status
        })

        return payment_record_id

    def get_contract_details(self, contract_id: int) -> Optional[Dict[str, Any]]:
        """Bağlantının tüm detaylarını, kişi bilgilerini ve parçalı ödeme geçmişini döner."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT 
                c.*,
                a.name as person_name,
                a.phone as person_phone
            FROM fx_contracts c
            JOIN accounts a ON c.account_id = a.id
            WHERE c.id = ?
            """,
            (contract_id,)
        )
        row = cur.fetchone()
        if not row:
            return None

        contract = dict(row)

        # Decimal formatları
        contract["sell_amount"] = to_decimal(contract["sell_amount"])
        contract["unit_price"] = to_decimal(contract["unit_price"])
        contract["total_target_amount"] = to_decimal(contract["total_target_amount"])
        contract["collected_amount"] = to_decimal(contract["collected_amount"])
        contract["remaining_amount"] = to_decimal(contract["remaining_amount"])

        # Durum Türkçe etiketi
        status_labels = {
            "PENDING": "Bekliyor",
            "PARTIAL": "Kısmi Ödendi",
            "COMPLETED": "Tamamlandı"
        }
        contract["status_label"] = status_labels.get(contract["status"], contract["status"])

        # Ödeme geçmişi
        cur.execute(
            """
            SELECT 
                p.id,
                p.amount,
                p.currency_code,
                p.payment_date,
                p.description,
                p.created_at,
                t.tx_number
            FROM fx_contract_payments p
            LEFT JOIN transactions t ON p.transaction_id = t.id
            WHERE p.contract_id = ?
            ORDER BY p.payment_date ASC, p.id ASC
            """,
            (contract_id,)
        )
        payments = []
        for r in cur.fetchall():
            payments.append({
                "id": r["id"],
                "amount": to_decimal(r["amount"]),
                "currency_code": r["currency_code"],
                "payment_date": r["payment_date"],
                "description": r["description"] or "",
                "created_at": r["created_at"],
                "tx_number": r["tx_number"] or ""
            })

        contract["payments"] = payments
        return contract

    def get_contracts_list(
        self,
        status_filter: str = "ALL",
        search: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Filtreler: ALL, PENDING, PARTIAL, COMPLETED
        Kişi adına, açıklamaya veya sözleşme numarasına göre arama yapılabilir.
        """
        cur = self.conn.cursor()
        sql = """
            SELECT 
                c.*,
                a.name as person_name,
                a.phone as person_phone
            FROM fx_contracts c
            JOIN accounts a ON c.account_id = a.id
            WHERE 1=1
        """
        params = []

        if status_filter != "ALL":
            sql += " AND c.status = ?"
            params.append(status_filter)

        if search:
            sql += " AND (a.name LIKE ? OR c.contract_number LIKE ? OR c.description LIKE ?)"
            term = f"%{search.strip()}%"
            params.extend([term, term, term])

        sql += " ORDER BY c.contract_date DESC, c.id DESC"
        cur.execute(sql, params)

        status_labels = {
            "PENDING": "Bekliyor",
            "PARTIAL": "Kısmi Ödendi",
            "COMPLETED": "Tamamlandı"
        }

        results = []
        for row in cur.fetchall():
            d = dict(row)
            d["sell_amount"] = to_decimal(d["sell_amount"])
            d["unit_price"] = to_decimal(d["unit_price"])
            d["total_target_amount"] = to_decimal(d["total_target_amount"])
            d["collected_amount"] = to_decimal(d["collected_amount"])
            d["remaining_amount"] = to_decimal(d["remaining_amount"])
            d["status_label"] = status_labels.get(d["status"], d["status"])
            results.append(d)

        return results

