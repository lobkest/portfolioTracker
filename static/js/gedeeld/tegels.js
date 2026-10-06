// Tegels met een label en een grote waarde (Portfolio-home en Statistieken).

function maakStatTegel(label, waardeTekst, klasse) {
    const tegel = document.createElement("div");
    tegel.className = "statTegel";

    const labelDiv = document.createElement("div");
    labelDiv.textContent = label;
    labelDiv.className = "kleinLabel";
    tegel.appendChild(labelDiv);

    const waardeDiv = document.createElement("div");
    waardeDiv.textContent = waardeTekst;
    waardeDiv.className = `statWaarde ${klasse || ""}`;
    tegel.appendChild(waardeDiv);

    return tegel;
}

function maakTotalenSectie(totalen) {
    const rij = document.createElement("div");
    rij.className = "tegelRij";

    rij.appendChild(maakStatTegel("Totaal geïnvesteerd", formatteerEuro(totalen.geinvesteerd)));
    rij.appendChild(maakStatTegel("Totale huidige waarde", formatteerEuro(totalen.waarde)));
    rij.appendChild(maakStatTegel("Totaal rendement (€)", formatteerEuro(totalen.rendement_eur), klasseVoorRendement(totalen.rendement_eur)));
    rij.appendChild(maakStatTegel("Totaal rendement (%)", formatPct(totalen.rendement_pct), klasseVoorRendement(totalen.rendement_pct)));

    if (totalen.dividend_netto !== null && totalen.dividend_netto !== undefined) {
        rij.appendChild(maakStatTegel("Ontvangen dividend (netto)", formatteerEuro(totalen.dividend_netto)));
    }

    if (totalen.kassaldo_eur !== null && totalen.kassaldo_eur !== undefined) {
        rij.appendChild(maakStatTegel(
            "Vrije ruimte (cash)",
            formatteerEuro(totalen.kassaldo_eur)
        ));
    }

    if (totalen.totaal_degiro_eur !== null && totalen.totaal_degiro_eur !== undefined) {
        rij.appendChild(maakStatTegel(
            "Totaal (rendement + dividend + cash) (wat DeGiro laat zien)",
            formatteerEuro(totalen.totaal_degiro_eur),
            klasseVoorRendement(totalen.totaal_degiro_eur)
        ));
    }

    if (totalen.all_time_high && totalen.all_time_high.waarde !== null) {
        rij.appendChild(maakStatTegel(
            "Hoogste rendement",
            `${formatteerEuro(totalen.all_time_high.waarde)} (${formatDatum(totalen.all_time_high.datum)})`
        ));
    }

    if (totalen.transactiekosten_beschikbaar) {
        rij.appendChild(maakStatTegel("Totale transactiekosten", formatteerEuro(totalen.totale_transactiekosten)));
    }

    const container = document.createElement("div");
    container.appendChild(rij);

    if (!totalen.transactiekosten_beschikbaar) {
        const kostenNotitie = document.createElement("p");
        kostenNotitie.className = "kleineMelding";
        kostenNotitie.textContent = "Totale transactiekosten: data ontbreekt — het geüploade transactiebestand bevat geen aparte kostenkolom.";
        container.appendChild(kostenNotitie);
    }

    return container;
}
