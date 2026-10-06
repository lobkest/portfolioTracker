"""Portfolio-codes genereren/valideren en een upload koppelen aan een bestaande portfolio."""
import random
import re
import string

from db import db_get_order_id_sets_met_overlap, db_portfolio_bestaat_met_cursor

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
    existing = db_get_order_id_sets_met_overlap(cur, new_order_ids)
    for code, ids in existing.items():
        if ids <= new_order_ids:
            return code, new_order_ids - ids
        if new_order_ids <= ids:
            return code, set()
    return None, None


FOUT_ANDERE_PORTFOLIO = "andere_portfolio"
FOUT_TRANSACTIES_ONTBREKEN = "transacties_ontbreken"
FOUT_GEEN_ORDER_IDS = "geen_order_ids"
FOUT_ONBEKENDE_TRANSACTIES = "onbekende_transacties"


def controleer_eigen_transactiebestand(opgeslagen, nieuw, ids_andere_portfolios):
    """Strenger dan find_matching_code(): het bestand moet alle opgeslagen Order ID's bevatten.
    Geeft (foutcode, None) of (None, toe_te_voegen_order_ids)."""
    if nieuw & ids_andere_portfolios or (opgeslagen and not nieuw & opgeslagen):
        return FOUT_ANDERE_PORTFOLIO, None
    if not opgeslagen <= nieuw:
        return FOUT_TRANSACTIES_ONTBREKEN, None
    return None, nieuw - opgeslagen


def controleer_eigen_rekeningoverzicht(rekening_ids, bekende_ids):
    """Foutcode, of None als elke Order ID uit het rekeningoverzicht bij deze portfolio hoort."""
    if not rekening_ids:
        return FOUT_GEEN_ORDER_IDS
    if not rekening_ids <= bekende_ids:
        return FOUT_ONBEKENDE_TRANSACTIES
    return None
