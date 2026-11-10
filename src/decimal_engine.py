"""
Financial calculations and Decimal helpers.
Guarantees precision with no floating-point rounding errors.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Union

MONEY_PLACES = Decimal("0.01")          # 2 ondalık basamak (Para tutarları)
RATE_PLACES = Decimal("0.0001")         # 4 ondalık basamak (Döviz kurları)
HIGH_PRECISION = Decimal("0.00000001")  # 8 basamak ara hesaplama

def to_decimal(val: Union[str, int, float, Decimal, None], default: str = "0.00") -> Decimal:
    """Her türlü girdiyi güvenli bir Decimal objesine çevirir."""
    if val is None or val == "":
        return Decimal(default)
    if isinstance(val, float):
        # Float'tan doğrudan Decimal çevrimindeki hassasiyet kayıplarını engellemek için string'e çevir
        return Decimal(str(val))
    return Decimal(str(val))

def round_money(val: Decimal) -> Decimal:
    """Standart muhasebe yuvarlaması (2 hane, HALF_UP)."""
    return val.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)

def round_rate(val: Decimal) -> Decimal:
    """Döviz kuru yuvarlaması (4 hane, HALF_UP)."""
    return val.quantize(RATE_PLACES, rounding=ROUND_HALF_UP)

def calc_base_amount(amount: Decimal, exchange_rate: Decimal) -> Decimal:
    """Tutar * Kur = TL / Ana Para Tutarı"""
    return round_money(amount * exchange_rate)

def calc_exchange_rate(base_amount: Decimal, foreign_amount: Decimal) -> Decimal:
    """TL Tutar / Döviz Tutar = Efektif Kur"""
    if foreign_amount == Decimal("0"):
        return Decimal("1.0000")
    return round_rate(base_amount / foreign_amount)

def format_currency(val: Decimal, symbol: str = "") -> str:
    """Türkçe formatında para gösterimi: 125.450,75 ₺"""
    val_rounded = round_money(val)
    parts = f"{val_rounded:,.2f}".split(".")
    # parts[0] virgüllerle binlik ayrılmış (ör. 125,450), parts[1] kuruş (75)
    integer_part = parts[0].replace(",", ".")
    decimal_part = parts[1]
    res = f"{integer_part},{decimal_part}"
    return f"{res} {symbol}".strip()

