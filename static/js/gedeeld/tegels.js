// Tegels met een label en een grote waarde (Portfolio-home en Statistieken).

const TOON_TOTAAL_DEGIRO_TEGEL = true;

const UITLEG_WAARDE = "Waarde van je huidige posities tegen de laatste koers, zonder cash. DEGIRO toont soms een paar euro anders, bijvoorbeeld doordat de koers van een andere beurs wordt gebruikt voor posities op Tradegate.";
const UITLEG_TOTAAL_DEGIRO = "Waarde van posities plus cash, min wat je netto hebt gestort. Zo rekent DEGIRO 'Totaal Account'. Inclusief dividend, promotie en kosten; dit verschilt daarom van 'Totaal rendement', dat alleen de posities tegenover het geïnvesteerde bedrag zet.";
const UITLEG_STORTINGEN_ONTBREKEN = "Stortingen ontbreken in het rekeningoverzicht";

// Onbetrouwbaar: het overzicht is onvolledig (backend), of (vangnet zonder dat veld) geen stortingen terwijl er
// wel transacties zijn, of een negatief saldo (kan bij DEGIRO niet).
function kassaldoIsBetrouwbaar(totalen) {
    if (totalen.kassaldo_eur === null || totalen.kassaldo_eur === undefined) return false;
    const status = totalen.rekeningoverzicht_status;
    if (status && typeof status.volledig === "boolean") return status.volledig;
    if (totalen.kassaldo_eur < 0) return false;
    const heeftTransacties = Boolean(totalen.geinvesteerd) || Boolean(totalen.waarde);
    return !(heeftTransacties && !totalen.netto_gestort_eur);
}

// Voorkomt verwarring bij vergelijken met DEGIRO (gestort bedrag, totaal W/V).
const UITLEG_GEINVESTEERD = "Netto aankopen incl. kosten (verkopen afgetrokken). Niet gelijk aan het gestorte bedrag.";
const UITLEG_RENDEMENT = "Waarde van de posities min geïnvesteerd. Excl. dividend en cash; wijkt daarom af van het totaal W/V bij DEGIRO.";
const UITLEG_NETTO_GESTORT = "Wat je netto naar DEGIRO hebt overgemaakt (stortingen min opnames). Promotie, dividend en rente zitten hier niet in. DEGIRO's totaal W/V = totale waarde (posities + cash) min dit bedrag.";

// null zonder promotiekorting; anders hoe het netto bedrag op de tegel tot stand komt.
function transactiekostenSubtekst(totalen) {
    if (!(totalen.transactiekosten_korting > 0)) return null;
    return `betaald ${formatGetal(totalen.totale_transactiekosten_betaald, 2)} − promotie ${formatGetal(totalen.transactiekosten_korting, 2)}`;
}

// Label met, als er uitleg is, een (i) erachter.
function maakLabel(label, uitleg, klasse) {
    const labelDiv = document.createElement("div");
    labelDiv.textContent = label;
    labelDiv.className = klasse;
    if (uitleg) {
        labelDiv.className = `${klasse} statTegelLabel`;
        const tip = document.createElement("span");
        tip.className = "infoTip";
        tip.dataset.label = label;
        tip.textContent = uitleg;
        labelDiv.appendChild(tip);
        activeerInfoTip(tip);
    }
    return labelDiv;
}

// `uitleg` komt achter een (i) naast het label; `subtekst` staat zichtbaar onder de waarde.
function maakStatTegel(label, waardeTekst, klasse, subtekst, uitleg) {
    const tegel = document.createElement("div");
    tegel.className = "statTegel";
    tegel.appendChild(maakLabel(label, uitleg, "kleinLabel"));

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
    rij.appendChild(maakStatTegel("Totale huidige waarde", formatteerEuro(totalen.waarde), "", null, UITLEG_WAARDE));
    rij.appendChild(maakStatTegel("Totaal rendement (€)", formatteerEuro(totalen.rendement_eur), klasseVoorRendement(totalen.rendement_eur), null, UITLEG_RENDEMENT));
    rij.appendChild(maakStatTegel("Totaal rendement (%)", formatPct(totalen.rendement_pct), klasseVoorRendement(totalen.rendement_pct)));

    const heeftKassaldo = totalen.kassaldo_eur !== null && totalen.kassaldo_eur !== undefined;
    const kassaldoBetrouwbaar = kassaldoIsBetrouwbaar(totalen);

    if (TOON_TOTAAL_DEGIRO_TEGEL && kassaldoBetrouwbaar && totalen.totaal_degiro_eur !== null && totalen.totaal_degiro_eur !== undefined) {
        rij.appendChild(maakStatTegel(
            "Totaal W/V (DEGIRO-definitie)",
            formatteerEuro(totalen.totaal_degiro_eur),
            klasseVoorRendement(totalen.totaal_degiro_eur),
            null,
            UITLEG_TOTAAL_DEGIRO
        ));
    }

    if (totalen.dividend_netto !== null && totalen.dividend_netto !== undefined) {
        rij.appendChild(maakStatTegel("Ontvangen dividend (netto)", formatteerEuro(totalen.dividend_netto)));
    }

    if (heeftKassaldo) {
        rij.appendChild(kassaldoBetrouwbaar
            ? maakStatTegel("Vrije ruimte (cash)", formatteerEuro(totalen.kassaldo_eur))
            : maakStatTegel("Vrije ruimte (cash)", "onbekend", "", null, UITLEG_STORTINGEN_ONTBREKEN));
        rij.appendChild(kassaldoBetrouwbaar
            ? maakStatTegel("Netto gestort", formatteerEuro(totalen.netto_gestort_eur), "", null, UITLEG_NETTO_GESTORT)
            : maakStatTegel("Netto gestort", "onbekend", "", null, UITLEG_STORTINGEN_ONTBREKEN));
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

// Home: bedrag of tekst ("onbekend") van een regel uit bouwHomeIndeling(), met % erachter bij rendement.
function homeRegelTekst(regel) {
    if (regel.tekst !== null) return regel.tekst;
    const bedrag = formatteerEuro(regel.bedrag);
    return regel.pct === null ? bedrag : `${bedrag} (${formatPct(regel.pct)})`;
}

function maakHomeTegel(regel, id) {
    const tegel = maakStatTegel(regel.label, homeRegelTekst(regel), regel.klasse, regel.subtekst, regel.uitleg);
    tegel.id = id;
    return tegel;
}

function maakHomeRekenRegel(regel, id) {
    const rij = document.createElement("div");
    rij.className = regel.totaal ? "rekenRegel rekenTotaal" : "rekenRegel";
    rij.id = id;

    const teken = document.createElement("span");
    teken.className = "rekenTeken";
    teken.textContent = regel.teken;
    rij.appendChild(teken);

    rij.appendChild(maakLabel(regel.label, regel.uitleg, "rekenLabel"));

    const bedrag = document.createElement("span");
    bedrag.className = `rekenBedrag ${regel.klasse}`;
    bedrag.textContent = homeRegelTekst(regel);
    rij.appendChild(bedrag);
    return rij;
}

function maakHomeLaptop(indeling) {
    const laptop = document.createElement("div");
    laptop.className = "homeLaptop";

    const kop = document.createElement("div");
    kop.className = "homeKopcijfers";
    for (const regel of indeling.kopcijfers) {
        const cijfer = document.createElement("div");
        cijfer.id = `home-laptop-kop-${regel.id}`;
        cijfer.className = "homeKopcijfer";
        cijfer.appendChild(maakLabel(regel.label, regel.uitleg, "kleinLabel"));
        const waarde = document.createElement("div");
        waarde.className = `homeKopWaarde ${regel.klasse}`;
        waarde.textContent = formatteerEuro(regel.bedrag);
        cijfer.appendChild(waarde);
        if (regel.pct !== null) {
            const pct = document.createElement("div");
            pct.className = `homeKopPct ${regel.klasse}`;
            pct.textContent = formatPct(regel.pct);
            cijfer.appendChild(pct);
        }
        kop.appendChild(cijfer);
    }
    laptop.appendChild(kop);

    for (const sectie of indeling.rekenblok) {
        const blok = document.createElement("div");
        blok.className = "rekenSectie";
        blok.id = `home-laptop-${sectie.id}`;
        const titel = document.createElement("div");
        titel.className = "rekenTitel";
        titel.textContent = sectie.titel;
        blok.appendChild(titel);
        for (const regel of sectie.regels) {
            blok.appendChild(maakHomeRekenRegel(regel, `home-laptop-${sectie.id}-${regel.id}`));
        }
        laptop.appendChild(blok);
    }

    const overige = document.createElement("div");
    overige.className = "tegelRij";
    for (const regel of indeling.overige) overige.appendChild(maakHomeTegel(regel, `home-laptop-overige-${regel.id}`));
    laptop.appendChild(overige);
    return laptop;
}

function maakHomeMobiel(indeling) {
    const mobiel = document.createElement("div");
    mobiel.className = "homeMobiel";
    const { waarde, rendement, totaalDegiro } = indeling.hero;

    const hero = document.createElement("div");
    hero.className = "heroTegel";
    hero.id = "home-mobiel-hero";
    hero.appendChild(maakLabel(waarde.label, waarde.uitleg, "kleinLabel"));
    const grootCijfer = document.createElement("div");
    grootCijfer.className = "heroWaarde";
    grootCijfer.textContent = formatteerEuro(waarde.bedrag);
    hero.appendChild(grootCijfer);

    hero.appendChild(maakLabel(rendement.label, rendement.uitleg, "kleinLabel"));
    const rendementTekst = document.createElement("div");
    rendementTekst.className = `heroRendement ${rendement.klasse}`;
    rendementTekst.textContent = homeRegelTekst(rendement);
    hero.appendChild(rendementTekst);

    if (totaalDegiro) {
        const degiro = maakLabel(`${totaalDegiro.label}: ${formatteerEuro(totaalDegiro.bedrag)}`, totaalDegiro.uitleg, "heroDegiro");
        hero.appendChild(degiro);
    }
    mobiel.appendChild(hero);

    for (const groep of indeling.groepen) {
        const blok = document.createElement("div");
        blok.id = `home-mobiel-${groep.id}`;
        const titel = document.createElement("div");
        titel.className = "groepTitel";
        titel.textContent = groep.titel;
        blok.appendChild(titel);
        const rij = document.createElement("div");
        rij.className = "tegelRij";
        for (const regel of groep.regels) rij.appendChild(maakHomeTegel(regel, `home-mobiel-${groep.id}-${regel.id}`));
        blok.appendChild(rij);
        mobiel.appendChild(blok);
    }
    return mobiel;
}

// Beide weergaven staan in de DOM; style.css toont er één (zelfde breakpoint als de rest van de site).
function maakHomeSectie(totalen, aansluitingskosten) {
    const indeling = bouwHomeIndeling(totalen, aansluitingskosten);
    const container = document.createElement("div");
    container.appendChild(maakHomeLaptop(indeling));
    container.appendChild(maakHomeMobiel(indeling));
    if (indeling.kostenNotitie) {
        const kostenNotitie = document.createElement("p");
        kostenNotitie.className = "kleineMelding";
        kostenNotitie.textContent = "Totale transactiekosten: data ontbreekt — het geüploade transactiebestand bevat geen aparte kostenkolom.";
        container.appendChild(kostenNotitie);
    }
    return container;
}
