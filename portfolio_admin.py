"""Portfolio-codes genereren/valideren en een upload koppelen aan een bestaande portfolio."""
import random
import re
import string

from db import db_get_order_id_sets, db_portfolio_bestaat_met_cursor

CODE_LENGTH = 3


def is_geldige_code(code):
    return bool(re.fullmatch(rf"[A-Z]{{{CODE_LENGTH}}}", code or ""))


def generate_code(cur, length=CODE_LENGTH):
    chars = string.ascii_uppercase
    while True:
        code = "".join(random.choices(chars, k=length))
        if not db_portfolio_bestaat_met_cursor(cur, code):
            return code


def find_matching_code(cur, new_order_ids):
    """Match als de Order ID's van een portfolio en de upload een deelverzameling van elkaar zijn.
    Geeft (code, ontbrekende_order_ids) of (None, None)."""
    existing = db_get_order_id_sets(cur)
    for code, ids in existing.items():
        if ids <= new_order_ids:
            return code, new_order_ids - ids
        if new_order_ids <= ids:
            return code, set()
    return None, None
