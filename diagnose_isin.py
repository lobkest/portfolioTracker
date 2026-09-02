"""
Diagnosescript: is yfinance's Ticker(...).isin bruikbaar als extra
matchsignaal voor ticker-zekerheid?

Wegwerpscript, geen productiecode. Wijzigt niets aan de app of de database
(alleen lezen uit `transacties`). Draai met:

    python diagnose_isin.py

Vul eerst PORTFOLIO_CODE hieronder in.
"""
import time

import yfinance as yf

from db import get_db_connection

PORTFOLIO_CODE = "AAA"  # <-- vul hier je eigen 3-letter-code in

# Wordt gebruikt als PORTFOLIO_CODE niet (meer) in Neon staat, of als de
# DB-connectie/query faalt. Zelf met de hand aanvullen, bv. met de tickers
# van de huidige Ticker-zekerheid-pagina.
FALLBACK_TICKERS = [
    # (ticker, excel_isin)
    # ("VUSA.AS", "IE00B3XXRP09"),
]


def haal_unieke_tickers(code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (ticker) ticker, isin
        FROM transacties
        WHERE code = %s AND ticker IS NOT NULL
        ORDER BY ticker
    """, (code,))
    rijen = cur.fetchall()
    cur.close()
    conn.close()
    return rijen


def main():
    bron = "database"
    try:
        rijen = haal_unieke_tickers(PORTFOLIO_CODE)
    except Exception as e:
        print(f"DB-query voor code '{PORTFOLIO_CODE}' faalde ({e}), val terug op FALLBACK_TICKERS.\n")
        rijen = []
        bron = "fallback"

    if not rijen:
        if bron == "database":
            print(f"Geen tickers gevonden voor code '{PORTFOLIO_CODE}' in database, val terug op FALLBACK_TICKERS.\n")
        rijen = FALLBACK_TICKERS
        bron = "fallback"

    if not rijen:
        print("Geen tickers gevonden (database leeg/niet bereikbaar én FALLBACK_TICKERS is leeg).")
        return

    bron_label = "uit database" if bron == "database" else "fallback-lijst"
    print(f"{len(rijen)} unieke tickers gevonden ({bron_label}).\n")
    print(f"{'ticker':<12} | {'excel_isin':<14} | {'yahoo_isin':<20} | {'match':<5} | duur (s)")
    print("-" * 75)

    resultaten = []
    for ticker, excel_isin in rijen:
        start = time.perf_counter()
        try:
            yahoo_isin = yf.Ticker(ticker).isin
        except Exception as e:
            yahoo_isin = f"FOUT: {e}"
        duur = time.perf_counter() - start

        yahoo_isin_str = yahoo_isin if yahoo_isin else "(leeg)"
        match = bool(yahoo_isin) and yahoo_isin == excel_isin
        print(f"{ticker:<12} | {excel_isin:<14} | {yahoo_isin_str:<20} | {str(match):<5} | {duur:.2f}")

        resultaten.append({
            "ticker": ticker,
            "excel_isin": excel_isin,
            "yahoo_isin": yahoo_isin,
            "duur": duur,
        })

    # Samenvatting
    totaal = len(resultaten)
    hits = [r for r in resultaten if r["yahoo_isin"] and not str(r["yahoo_isin"]).startswith("FOUT:")]
    matches = [r for r in hits if r["yahoo_isin"] == r["excel_isin"]]
    duren = [r["duur"] for r in resultaten]

    print("\n" + "=" * 40)
    print("SAMENVATTING")
    print("=" * 40)
    print(f"Aantal tickers getest:        {totaal}")
    print(f"Niet-lege Yahoo-ISIN (hit rate):  {len(hits)}/{totaal} ({100 * len(hits) / totaal:.0f}%)")
    if hits:
        print(f"Matchte met Excel-ISIN:       {len(matches)}/{len(hits)} ({100 * len(matches) / len(hits):.0f}% van de hits)")
    else:
        print("Matchte met Excel-ISIN:       n.v.t. (geen hits)")
    print(f"Gemiddelde duur per call:     {sum(duren) / totaal:.2f} s")
    print(f"Maximale duur per call:       {max(duren):.2f} s")


if __name__ == "__main__":
    main()
