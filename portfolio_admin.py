"""
Portfolio-code-beheer en upload-matching: genereren/valideren van de
3-letter-portfoliocode, en het herkennen of een nieuwe upload bij een
bestaande portfolio hoort (via Order ID-overlap).

Losgetrokken uit analysis.py.
"""
import random
import re
import string


CODE_LENGTH = 3


def is_geldige_code(code):
    """Zelfde regels als een gegenereerde code (zie generate_code): exact
    CODE_LENGTH hoofdletters A-Z. Wordt hergebruikt bij het valideren van
    een door de gebruiker zelf gekozen nieuwe code (code-wijzigen)."""
    return bool(re.fullmatch(rf"[A-Z]{{{CODE_LENGTH}}}", code or ""))


def generate_code(cur, length=CODE_LENGTH):
    """Genereert een unieke portfolio-code die nog niet in gebruik is."""
    chars = string.ascii_uppercase
    while True:
        code = "".join(random.choices(chars, k=length))
        cur.execute("SELECT 1 FROM portfolios WHERE code = %s", (code,))
        if cur.fetchone() is None:
            return code


def get_order_id_sets(cur):
    """Geeft per portfolio-code de set van al opgeslagen Order ID's terug."""
    cur.execute("SELECT code, order_id FROM transacties WHERE order_id IS NOT NULL")
    sets = {}
    for code, order_id in cur.fetchall():
        sets.setdefault(code, set()).add(order_id)
    return sets


def find_matching_code(cur, new_order_ids):
    """
    Zoekt een bestaande portfolio die dezelfde persoon vertegenwoordigt:
    - bestaande data zit volledig in de nieuwe upload (update met extra transacties), of
    - de nieuwe upload zit volledig in de bestaande data (niets nieuws)
    Geeft (code, ontbrekende_order_ids) terug, of (None, None) als er geen match is.
    """
    existing = get_order_id_sets(cur)
    for code, ids in existing.items():
        if ids <= new_order_ids:
            return code, new_order_ids - ids
        if new_order_ids <= ids:
            return code, set()
    return None, None
