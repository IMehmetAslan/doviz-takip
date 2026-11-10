"""
Reporting Engine specifically designed for Personal Currency Debt & Receivable Tracking.
Provides:
- Dashboard Metrics (Active Debts, Closed Debts, Total Given, Total Received, Total Remaining by Currency)
- Debtors List with Filtering (Active vs Closed / Archive)
- Recent Activities and Latest Payers
"""

from decimal import Decimal
import sqlite3
from typing import List, Dict, Any, Optional
from src.decimal_engine import to_decimal, round_money

class ReportingEngine:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def get_dashboard_summary(self) -> Dict[str, Any]:
        """
        Dashboard Özeti:
        - Toplam kaç kişiye/firmaya borç verildiği
        - Aktif borç sayısı
        - Kapanan borç sayısı
        - Toplam verilen borçlar (Döviz bazında)
        - Toplam tahsil edilen ödemeler (Döviz bazında)
        - Kalan alacaklar (Döviz bazında)
        - Son yapılan işlemler (Son 8 işlem)
        - En son ödeme yapan kişiler (Son 5 ödeme)
        """
        cur = self.conn.cursor()

        # 1. Kişi bazında aktif / kapanmış borç sayısı ve bakiyeler
        cur.execute(
            """
            SELECT 
                l.account_id,
                l.currency_code,
                SUM(CASE WHEN l.direction = 'DEBIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) -
                SUM(CASE WHEN l.direction = 'CREDIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) as net_balance
            FROM transaction_lines l
            JOIN transactions t ON l.transaction_id = t.id
            WHERE l.account_id IS NOT NULL AND t.status = 'CONFIRMED'
            GROUP BY l.account_id, l.currency_code
            """
        )
        person_balances: Dict[int, Dict[str, Decimal]] = {}
        for r in cur.fetchall():
            acc_id = r["account_id"]
            if acc_id not in person_balances:
                person_balances[acc_id] = {}
            person_balances[acc_id][r["currency_code"]] = to_decimal(r["net_balance"])

        total_debtors = len(person_balances)
        active_debtors_count = 0
        closed_debtors_count = 0

        for acc_id, currs in person_balances.items():
            has_positive = any(bal > Decimal("0.00") for bal in currs.values())
            if has_positive:
                active_debtors_count += 1
            else:
                closed_debtors_count += 1

        # 2. Döviz bazında: Toplam Verilen, Toplam Alınan, Kalan Alacak
        cur.execute(
            """
            SELECT 
                l.currency_code,
                SUM(CASE WHEN l.direction = 'DEBIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) as total_given,
                SUM(CASE WHEN l.direction = 'CREDIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) as total_received
            FROM transaction_lines l
            JOIN transactions t ON l.transaction_id = t.id
            WHERE l.account_id IS NOT NULL AND t.status = 'CONFIRMED'
            GROUP BY l.currency_code
            ORDER BY total_given DESC
            """
        )

        currency_summary: Dict[str, Dict[str, Decimal]] = {}
        for r in cur.fetchall():
            curr = r["currency_code"]
            given = to_decimal(r["total_given"])
            received = to_decimal(r["total_received"])
            remaining = round_money(given - received)

            currency_summary[curr] = {
                "currency_code": curr,
                "total_given": given,
                "total_received": received,
                "total_remaining": remaining
            }

        # 3. Son Yapılan İşlemler (Son 8)
        cur.execute(
            """
            SELECT 
                t.id as tx_id,
                t.tx_number,
                t.tx_date,
                t.tx_type,
                a.id as person_id,
                a.name as person_name,
                l.currency_code,
                l.amount,
                l.direction,
                l.description
            FROM transactions t
            JOIN transaction_lines l ON t.id = l.transaction_id
            JOIN accounts a ON l.account_id = a.id
            WHERE t.status = 'CONFIRMED'
            ORDER BY t.tx_date DESC, t.id DESC
            LIMIT 8
            """
        )
        recent_transactions = []
        for r in cur.fetchall():
            is_debt = (r["direction"] == "DEBIT")
            recent_transactions.append({
                "tx_id": r["tx_id"],
                "tx_number": r["tx_number"],
                "tx_date": r["tx_date"],
                "tx_type": r["tx_type"],
                "type_label": "Borç Verildi" if is_debt else "Ödeme Alındı",
                "person_id": r["person_id"],
                "person_name": r["person_name"],
                "currency_code": r["currency_code"],
                "amount": to_decimal(r["amount"]),
                "is_debt": is_debt,
                "description": r["description"] or ""
            })

        # 4. En Son Ödeme Yapan Kişiler (Son 5 tahsilat)
        cur.execute(
            """
            SELECT 
                t.id as tx_id,
                t.tx_date,
                a.id as person_id,
                a.name as person_name,
                l.currency_code,
                l.amount,
                l.description
            FROM transactions t
            JOIN transaction_lines l ON t.id = l.transaction_id
            JOIN accounts a ON l.account_id = a.id
            WHERE t.status = 'CONFIRMED' AND l.direction = 'CREDIT'
            ORDER BY t.tx_date DESC, t.id DESC
            LIMIT 5
            """
        )
        latest_payers = []
        for r in cur.fetchall():
            latest_payers.append({
                "person_id": r["person_id"],
                "person_name": r["person_name"],
                "tx_date": r["tx_date"],
                "currency_code": r["currency_code"],
                "amount": to_decimal(r["amount"]),
                "description": r["description"] or ""
            })

        # Toplam kişi sayısı (rehber)
        cur.execute("SELECT COUNT(*) as count FROM accounts WHERE active = 1")
        total_persons_registered = cur.fetchone()["count"]

        # 5. Aktif Döviz Bağlantıları Özeti
        cur.execute(
            """
            SELECT 
                COUNT(*) as total_contracts,
                SUM(CASE WHEN status != 'COMPLETED' THEN 1 ELSE 0 END) as active_contracts,
                SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as completed_contracts
            FROM fx_contracts
            """
        )
        c_row = cur.fetchone()
        contract_stats = {
            "total_contracts": c_row["total_contracts"] if c_row else 0,
            "active_contracts": c_row["active_contracts"] if (c_row and c_row["active_contracts"]) else 0,
            "completed_contracts": c_row["completed_contracts"] if (c_row and c_row["completed_contracts"]) else 0
        }

        return {
            "total_debtors": total_debtors,
            "active_debts_count": active_debtors_count,
            "closed_debts_count": closed_debtors_count,
            "total_persons_registered": total_persons_registered,
            "currency_summary": currency_summary,
            "recent_transactions": recent_transactions,
            "latest_payers": latest_payers,
            "contract_stats": contract_stats
        }

    def get_debtors_list(self, filter_status: str = "ALL", search: str = "") -> List[Dict[str, Any]]:
        """
        Borçlu Listesi:
        Her kayıt için:
        - Kişi / Firma Adı
        - Telefon, Not
        - Toplam Verdiğim Borç (Para birimi bazında)
        - Toplam Yaptığı Ödeme (Para birimi bazında)
        - Kalan Borç (Para birimi bazında)
        - Borcun Döviz Cinsi
        - Son İşlem Tarihi
        - Borç Durumu (Aktif / Kapandı)
        Filtreler: ALL, ACTIVE, CLOSED
        """
        cur = self.conn.cursor()

        sql = """
            SELECT a.id, a.code, a.name, a.phone, a.description, a.created_at
            FROM accounts a
            WHERE a.active = 1
        """
        params = []
        if search:
            sql += " AND (a.name LIKE ? OR a.phone LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term])

        sql += " ORDER BY a.name ASC"
        cur.execute(sql, params)
        accounts = [dict(r) for r in cur.fetchall()]

        # İşlem özetleri ve bakiyeleri getir
        bal_sql = """
            SELECT 
                l.account_id,
                l.currency_code,
                SUM(CASE WHEN l.direction = 'DEBIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) as total_given,
                SUM(CASE WHEN l.direction = 'CREDIT' THEN CAST(l.amount AS NUMERIC) ELSE 0 END) as total_paid,
                MAX(t.tx_date) as last_tx_date
            FROM transaction_lines l
            JOIN transactions t ON l.transaction_id = t.id
            WHERE l.account_id IS NOT NULL AND t.status = 'CONFIRMED'
            GROUP BY l.account_id, l.currency_code
        """
        cur.execute(bal_sql)

        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for r in cur.fetchall():
            acc_id = r["account_id"]
            if acc_id not in grouped:
                grouped[acc_id] = []

            given = to_decimal(r["total_given"])
            paid = to_decimal(r["total_paid"])
            remaining = round_money(given - paid)
            is_closed = (remaining <= Decimal("0.00"))

            grouped[acc_id].append({
                "currency_code": r["currency_code"],
                "total_given": given,
                "total_paid": paid,
                "remaining": remaining,
                "is_closed": is_closed,
                "status": "Kapandı" if is_closed else "Aktif",
                "last_tx_date": r["last_tx_date"]
            })

        result = []
        for acc in accounts:
            acc_id = acc["id"]
            currencies = grouped.get(acc_id, [])

            if not currencies:
                # Henüz işlem yapılmamış kişi
                has_active = False
                status = "İşlem Yok"
                last_date = str(acc["created_at"])[:10] if acc["created_at"] else "-"
            else:
                has_active = any(not c["is_closed"] for c in currencies)
                status = "Aktif" if has_active else "Kapandı"
                # En son işlem tarihi
                dates = [c["last_tx_date"] for c in currencies if c["last_tx_date"]]
                last_date = str(max(dates))[:10] if dates else "-"

            acc["currencies"] = currencies
            acc["status"] = status
            acc["last_tx_date"] = last_date
            acc["has_active_debt"] = has_active

            # Filtreleme kontrolü
            if filter_status == "ACTIVE" and status != "Aktif":
                continue
            if filter_status == "CLOSED" and status != "Kapandı":
                continue

            result.append(acc)

        return result
