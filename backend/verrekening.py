"""'Verrekening van Aandelen' (contant geld voor fracties bij een corporate action) als opbrengst van de positie.
Pure functies. De lijst hangt aan transacties_df.attrs: het geld is geen transactie, dus geen rij die elke andere
berekening of weergave van transacties zou meekrijgen."""
import pandas as pd

from backend.rekening_regelsoorten import REGELSOORT_VERREKENING_AANDELEN, regelsoort
from backend.split_correctie import isin_ketens, vind_wisselparen

VERREKENINGEN_ATTR = "verrekeningen"


def zet_verrekeningen(transacties_df, verrekeningen):
    transacties_df.attrs[VERREKENINGEN_ATTR] = verrekeningen
    return transacties_df


def verrekeningen_van(transacties_df):
    """[{datum (Timestamp), ticker, isin, product, bedrag}]; leeg als ze niet zijn gezet."""
    return transacties_df.attrs.get(VERREKENINGEN_ATTR) or []


def _ticker_per_keten(transacties_df):
    eind_van = isin_ketens(vind_wisselparen(transacties_df)[0])
    met_ticker = transacties_df.dropna(subset=["ticker", "isin"])
    ticker_per_eind = {}
    for isin, ticker in zip(met_ticker["isin"], met_ticker["ticker"]):
        eind = eind_van.get(isin, isin)
        # De eigen ticker van de eind-ISIN gaat voor, anders die van een eerdere ISIN in de keten.
        if isin == eind or eind not in ticker_per_eind:
            ticker_per_eind[eind] = ticker
    return eind_van, ticker_per_eind


def bouw_verrekeningen(rekening_regels, transacties_df):
    """(toegekend, niet_toegekend). toegekend: de verrekeningen met een ISIN waarvoor een positie bestaat (een ISIN-keten
    is één positie). niet_toegekend: de rekening-regels zonder ISIN of zonder positie."""
    regels = [r for r in rekening_regels or []
              if regelsoort(r["omschrijving"]) == REGELSOORT_VERREKENING_AANDELEN
              and r["mutatie_valuta"] == "EUR" and r["mutatie"] is not None]
    if not regels:
        return [], []
    eind_van, ticker_per_eind = _ticker_per_keten(transacties_df)
    toegekend, niet_toegekend = [], []
    for regel in regels:
        isin = regel.get("isin")
        ticker = ticker_per_eind.get(eind_van.get(isin, isin)) if isin else None
        if ticker is None:
            niet_toegekend.append(regel)
            continue
        toegekend.append({
            "datum": pd.Timestamp(regel["datum"]).normalize(), "ticker": ticker, "isin": isin,
            "product": regel.get("product"), "bedrag": float(regel["mutatie"]),
        })
    return toegekend, niet_toegekend
