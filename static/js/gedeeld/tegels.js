// Tegels met een label en een grote waarde (Portfolio-home en Statistieken).

// Tijdelijk uit: de DeGiro-totaaltegel moet nog nagekeken worden; op true zetten om hem terug te zien.
const TOON_TOTAAL_DEGIRO_TEGEL = false;

// Voorkomt verwarring bij vergelijken met DEGIRO (gestort bedrag, totaal W/V).
const UITLEG_GEINVESTEERD = "Netto aankopen incl. kosten (verkopen afgetrokken). Niet gelijk aan het gestorte bedrag.";
const UITLEG_RENDEMENT = "Waarde van de posities min geïnvesteerd. Excl. dividend en cash; wijkt daarom af van het totaal W/V bij DEGIRO.";
const UITLEG_NETTO_GESTORT = "Wat je netto naar DEGIRO hebt overgemaakt (stortingen min opnames). Promotie, dividend en rente zitten hier niet in. DEGIRO's totaal W/V = totale waarde (posities + cash) min dit bedrag.";

// null zonder promotiekorting; anders hoe het netto bedrag op de tegel tot stand komt.
function transactiekostenSubtekst(totalen) {
    if (!(totalen.transactiekosten_korting > 0)) return null;
    return `betaald ${formatGetal(totalen.totale_transactiekosten_betaald, 2)} − promotie ${formatGetal(totalen.transactiekosten_korting, 2)}`;
}

// `uitleg` komt achter een (i) naast het label; `subtekst` staat zichtbaar onder de waarde.
function maakStatTegel(label, waardeTekst, klasse, subtekst, uitleg) {
    const tegel = document.createElement("div");
    tegel.className = "statTegel";

    const labelDiv = document.createElement("div");
    labelDiv.textContent = label;
    labelDiv.className = "kleinLabel";
    tegel.appendChild(labelDiv);

    if (uitleg) {
        labelDiv.className = "kleinLabel statTegelLabel";
        const tip = document.createElement("span");
        tip.className = "infoTip";
        tip.dataset.label = label;
        tip.textContent = uitleg;
        labelDiv.appendChild(tip);
        activeerInfoTip(tip);
    }

    const waardeDiv = document.createElement("div");
    waardeDiv.textContent = waardeTekst;
    waardeDiv.className = `statWaarde ${klasse || ""}`;
    tegel.appendChild(waardeDiv);

    if (subtekst) {
        const subDiv = document.createElement("div");
        subDiv.textContent = subtekst;
        subDiv.className = "kleineMelding";
        tegel.appendChild(subDiv);
    }

    return tegel;
}

// aansluitingskosten: alleen op Home meegegeven (veld uit de API, kan ontbreken).
function maakTotalenSectie(totalen, aansluitingskosten) {
    const rij = document.createElement("div");
    rij.className = "tegelRij";

    rij.appendChild(maakStatTegel("Totaal geïnvesteerd", formatteerEuro(totalen.geinvesteerd), "", null, UITLEG_GEINVESTEERD));
    rij.appendChild(maakStatTegel("Totale huidige waarde", formatteerEuro(totalen.waarde)));
    rij.appendChild(maakStatTegel("Totaal rendement (€)", formatteerEuro(totalen.rendement_eur), klasseVoorRendement(totalen.rendement_eur), null, UITLEG_RENDEMENT));
    rij.appendChild(maakStatTegel("Totaal rendement (%)", formatPct(totalen.rendement_pct), klasseVoorRendement(totalen.rendement_pct), null, UITLEG_RENDEMENT));

    if (totalen.dividend_netto !== null && totalen.dividend_netto !== undefined) {
        rij.appendChild(maakStatTegel("Ontvangen dividend (netto)", formatteerEuro(totalen.dividend_netto)));
    }

    if (totalen.kassaldo_eur !== null && totalen.kassaldo_eur !== undefined) {
        rij.appendChild(maakStatTegel(
            "Vrije ruimte (cash)",
            formatteerEuro(totalen.kassaldo_eur)
        ));
    }

    if (totalen.netto_gestort_eur !== null && totalen.netto_gestort_eur !== undefined) {
        rij.appendChild(maakStatTegel(
            "Netto gestort", formatteerEuro(totalen.netto_gestort_eur), "", null, UITLEG_NETTO_GESTORT
        ));
    }

    if (TOON_TOTAAL_DEGIRO_TEGEL && totalen.totaal_degiro_eur !== null && totalen.totaal_degiro_eur !== undefined) {
        rij.appendChild(maakStatTegel(
            "Totaal (rendement + dividend + cash) (wat DeGiro laat zien)",
            formatteerEuro(totalen.totaal_degiro_eur),
            klasseVoorRendement(totalen.totaal_degiro_eur)
        ));
    }

    if (totalen.all_time_high && totalen.all_time_high.waarde !== null) {
        rij.appendChild(maakStatTegel(
            "Hoogste rendement",
            formatteerEuro(totalen.all_time_high.waarde),
            "",
            formatDatum(totalen.all_time_high.datum)
        ));
    }

    if (totalen.transactiekosten_beschikbaar) {
        rij.appendChild(maakStatTegel(
            "Totale transactiekosten", formatteerEuro(totalen.totale_transactiekosten), "", transactiekostenSubtekst(totalen)
        ));
    }

    const aansluiting = aansluitingskostenTegel(aansluitingskosten);
    if (aansluiting) {
        rij.appendChild(maakStatTegel(aansluiting.label, aansluiting.waardeTekst, "", null, aansluiting.uitleg));
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
