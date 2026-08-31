"""
Tijdelijk diagnose-scriptje voor opdracht 4 (transactiekosten lijken te
laag): print voor een gegeven portfolio-code de transactiekosten-cijfers
zoals ze in de database staan, zodat je kunt zien of het een parsing-bug is
(waarde in de Excel-kolom, maar NULL in de database) of verwacht
DEGIRO-gedrag (kolom is legitiem leeg voor bepaalde rijen, bv. deeluitvoeringen
van dezelfde order).

Alleen lezend (geen schrijfacties) -- veilig om tegen de productiedatabase
te draaien.

Gebruik:
    python diagnose_transactiekosten.py <CODE>
"""
import sys

from db import get_db_connection
from analysis import bereken_totale_transactiekosten
import pandas as pd


def diagnose(code):
    code = code.strip().upper()
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM transacties WHERE code = %s", (code,))
    totaal_rijen = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM transacties WHERE code = %s AND transactiekosten IS NULL",
        (code,),
    )
    null_rijen = cur.fetchone()[0]

    cur.execute(
        "SELECT datum, product, order_id, transactiekosten FROM transacties "
        "WHERE code = %s ORDER BY datum",
        (code,),
    )
    rijen = cur.fetchall()
    cur.close()
    conn.close()

    if totaal_rijen == 0:
        print(f"Geen transacties gevonden voor code '{code}'.")
        return

    niet_null_rijen = totaal_rijen - null_rijen
    print(f"Portfolio '{code}': {totaal_rijen} transactie(s) totaal")
    print(f"  - transactiekosten IS NULL:     {null_rijen}")
    print(f"  - transactiekosten niet NULL:   {niet_null_rijen}")

    df = pd.DataFrame(rijen, columns=["datum", "product", "order_id", "transactiekosten"])
    resultaat = bereken_totale_transactiekosten(df)
    if resultaat["beschikbaar"]:
        print(f"  - som (bereken_totale_transactiekosten): €{resultaat['totaal']:.2f}")
    else:
        print("  - som (bereken_totale_transactiekosten): niet beschikbaar (kolom ontbreekt/alleen NaN)")

    if null_rijen:
        print(f"\nRijen zonder transactiekosten (eerste 20 van {null_rijen}):")
        getoond = 0
        for datum, product, order_id, kosten in rijen:
            if kosten is None:
                print(f"  {datum}  {product}  order_id={order_id}")
                getoond += 1
                if getoond >= 20:
                    print(f"  ... en {null_rijen - getoond} meer")
                    break

    print(
        "\nVergelijk dit met de ruwe Excel-kolom 'Transactiekosten en/of kosten van derden EUR' "
        "voor dezelfde Order ID's: staat daar bij deze rijen een waarde (parsing-bug) of is de "
        "cel ook daar leeg (verwacht DEGIRO-gedrag, bv. alleen de eerste deeluitvoering van een "
        "order met meerdere fills krijgt een kostenbedrag)?"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Gebruik: python diagnose_transactiekosten.py <CODE>")
        sys.exit(1)
    diagnose(sys.argv[1])
