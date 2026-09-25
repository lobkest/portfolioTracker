"""Portfolio-codes genereren/valideren en een upload koppelen aan een bestaande portfolio."""
import random
import re
import string


CODE_LENGTH = 3


def is_geldige_code(code):
    return bool(re.fullmatch(rf"[A-Z]{{{CODE_LENGTH}}}", code or ""))


def generate_code(cur, length=CODE_LENGTH):
    chars = string.ascii_uppercase
    while True:
        code = "".join(random.choices(chars, k=length))
        cur.execute("SELECT 1 FROM portfolios WHERE code = %s", (code,))
        if cur.fetchone() is None:
            return code


def get_order_id_sets(cur):
    cur.execute("SELECT code, order_id FROM transacties WHERE order_id IS NOT NULL")
    sets = {}
    for code, order_id in cur.fetchall():
        sets.setdefault(code, set()).add(order_id)
    return sets


def find_matching_code(cur, new_order_ids):
    """Match als de Order ID's van een portfolio en de upload een deelverzameling van elkaar zijn.
    Geeft (code, ontbrekende_order_ids) of (None, None)."""
    existing = get_order_id_sets(cur)
    for code, ids in existing.items():
        if ids <= new_order_ids:
            return code, new_order_ids - ids
        if new_order_ids <= ids:
            return code, set()
    return None, None
