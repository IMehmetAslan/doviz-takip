"""
Foreign exchange lot (parti) management and FIFO/LIFO/Manual allocation engine.
"""

from decimal import Decimal
import sqlite3
from typing import List, Dict, Any, Optional
from src.decimal_engine import to_decimal, round_money, round_rate, calc_base_amount

class LotEngine:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_lot(
        self,
        lot_number: str,
        entry_date: str,
        currency_code: str,
        amount: Decimal,
        exchange_rate: Decimal,
        account_id: Optional[int] = None,
        cash_bank_id: Optional[int] = None,
        tx_line_id: Optional[int] = None,
        description: str = "",
    ) -> int:
        """Yeni bir döviz partisi oluşturur."""
        amount_dec = round_money(amount)
        rate_dec = round_rate(exchange_rate)
        base_amt = calc_base_amount(amount_dec, rate_dec)

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO fx_lots (
                lot_number, entry_date, currency_code, initial_amount,
                remaining_amount, exchange_rate, base_amount,
                account_id, cash_bank_id, transaction_line_id, status, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                lot_number,
                entry_date,
                currency_code,
                str(amount_dec),
                str(amount_dec),
                str(rate_dec),
                str(base_amt),
                account_id,
                cash_bank_id,
                tx_line_id,
                description,
            ),
        )
        return cur.lastrowid

    def get_open_lots(self, currency_code: str, account_id: Optional[int] = None, order: str = "FIFO") -> List[Dict[str, Any]]:
        """Açık veya kısmi açık partileri sıralı döner (FIFO için en eski ilk, LIFO için en yeni ilk)."""
        sql = """
            SELECT * FROM fx_lots 
            WHERE currency_code = ? AND status IN ('OPEN', 'PARTIAL')
        """
        params = [currency_code]
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)

        if order.upper() == "FIFO":
            sql += " ORDER BY entry_date ASC, id ASC"
        else: # LIFO
            sql += " ORDER BY entry_date DESC, id DESC"

        cur = self.conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def allocate_lots(
        self,
        tx_line_id: int,
        currency_code: str,
        exit_amount: Decimal,
        exit_rate: Decimal,
        method: str = "FIFO",  # 'FIFO', 'LIFO', 'MANUAL'
        specific_lot_ids: Optional[List[int]] = None,
        account_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Döviz çıkışında partilerden tahsis (allocation) yapar ve gerçekleşen kur kâr/zararını hesaplar.
        """
        remaining_needed = round_money(exit_amount)
        exit_rate_dec = round_rate(exit_rate)
        allocations = []

        if method.upper() == "MANUAL" and specific_lot_ids:
            cur = self.conn.cursor()
            placeholders = ",".join("?" * len(specific_lot_ids))
            cur.execute(f"SELECT * FROM fx_lots WHERE id IN ({placeholders})", specific_lot_ids)
            available_lots = [dict(r) for r in cur.fetchall()]
        else:
            available_lots = self.get_open_lots(currency_code, account_id=account_id, order=method)

        for lot in available_lots:
            if remaining_needed <= Decimal("0"):
                break

            lot_rem = to_decimal(lot["remaining_amount"])
            if lot_rem <= Decimal("0"):
                continue

            entry_rate = to_decimal(lot["exchange_rate"])
            
            # Bu partiden ne kadar düşülecek
            alloc_amt = min(remaining_needed, lot_rem)
            new_remaining = lot_rem - alloc_amt
            remaining_needed -= alloc_amt

            new_status = "CLOSED" if new_remaining == Decimal("0") else "PARTIAL"

            # Kâr/Zarar hesabı (TL bazında): (Çıkış Kuru - Giriş Kuru) * Tutar
            # Örn: 1000$ giriş kuru 30 TL, çıkış kuru 34 TL -> (34 - 30) * 1000 = +4.000 TL Kâr
            profit_loss = round_money((exit_rate_dec - entry_rate) * alloc_amt)

            # Veritabanını güncelle
            cur = self.conn.cursor()
            cur.execute(
                """
                UPDATE fx_lots
                SET remaining_amount = ?, status = ?
                WHERE id = ?
                """,
                (str(new_remaining), new_status, lot["id"]),
            )

            cur.execute(
                """
                INSERT INTO fx_lot_allocations (
                    lot_id, transaction_line_id, allocated_amount,
                    exit_rate, entry_rate, profit_loss
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    lot["id"],
                    tx_line_id,
                    str(alloc_amt),
                    str(exit_rate_dec),
                    str(entry_rate),
                    str(profit_loss),
                ),
            )

            allocations.append({
                "lot_id": lot["id"],
                "lot_number": lot["lot_number"],
                "allocated_amount": alloc_amt,
                "entry_rate": entry_rate,
                "exit_rate": exit_rate_dec,
                "profit_loss": profit_loss,
                "new_remaining": new_remaining,
            })

        if remaining_needed > Decimal("0"):
            raise ValueError(
                f"Yetersiz döviz partisi! {currency_code} cinsinden {remaining_needed} açıkta kaldı."
            )

        return allocations

