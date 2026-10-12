// Indeling van de Home-cijfers (hero + groepen voor alle schermen, plus een uitklapbaar rekenoverzicht), zonder DOM.
// Alle bedragen komen uit de API (`statistieken.totalen`); hier wordt alleen gekozen en geordend.
// Leunt op tegels.js (UITLEG_*, kassaldoIsBetrouwbaar) en opmaak.js (klasseVoorRendement); laadt na tegels.js.

const UITLEG_OVERIGE_KASSABEWEGINGEN = "Kassabewegingen die geen aankoop, storting of dividend zijn, zoals aansluitingskosten en rente. Min = kosten.";

function _isGetal(x) {
    return x !== null && x !== undefined;
}

// bedrag blijft positief; het teken staat apart in `teken` (+, −, = of leeg).
function _regel(id, teken, label, bedrag, extra) {
    return { id, teken, label, bedrag, tekst: null, uitleg: null, klasse: "", pct: null, subtekst: null, totaal: false, ...extra };
}

function _kassaRegels(totalen, betrouwbaar) {
    return {
        gestort: betrouwbaar
            ? _regel("gestort", "", "Netto gestort", totalen.netto_gestort_eur, { uitleg: UITLEG_NETTO_GESTORT })
            : _regel("gestort", "", "Netto gestort", null, { tekst: "onbekend", uitleg: UITLEG_STORTINGEN_ONTBREKEN }),
        cash: betrouwbaar
            ? _regel("cash", "", "Vrije ruimte (cash)", totalen.kassaldo_eur)
            : _regel("cash", "", "Vrije ruimte (cash)", null, { tekst: "onbekend", uitleg: UITLEG_STORTINGEN_ONTBREKEN }),
    };
}

function _opbouwSecties(totalen, aansluitend) {
    const dividend = _isGetal(totalen.dividend_netto) ? totalen.dividend_netto : 0;
    const overig = totalen.overige_kassabewegingen_eur;
    const overigRegel = overig
        ? [_regel("overig", overig < 0 ? "−" : "+", "Overige kassabewegingen", Math.abs(overig), { uitleg: UITLEG_OVERIGE_KASSABEWEGINGEN })]
        : [];
    const rendementRegel = (teken) => _regel("rendement", teken, "Totaal rendement", totalen.rendement_eur, {
        klasse: klasseVoorRendement(totalen.rendement_eur), pct: totalen.rendement_pct, totaal: teken === "=",
        uitleg: teken === "=" ? UITLEG_RENDEMENT : null,
    });

    const secties = [];
    if (aansluitend) {
        secties.push({
            id: "ingegaan", titel: "Wat erin ging", regels: [
                _kassaRegels(totalen, true).gestort,
                _regel("dividend", "+", "Dividend (netto)", dividend),
                ...overigRegel,
                _regel("cash", "−", "Vrije ruimte (cash)", totalen.kassaldo_eur),
                _regel("geinvesteerd", "=", "Totaal geïnvesteerd", totalen.geinvesteerd, { totaal: true, uitleg: UITLEG_GEINVESTEERD }),
            ],
        });
    }
    secties.push({
        id: "opgeleverd", titel: "Wat het opleverde", regels: [
            _regel("waarde", "", "Totale huidige waarde", totalen.waarde, { uitleg: UITLEG_WAARDE }),
            _regel("geinvesteerd", "−", "Totaal geïnvesteerd", totalen.geinvesteerd, { uitleg: UITLEG_GEINVESTEERD }),
            rendementRegel("="),
        ],
    });
    if (aansluitend && TOON_TOTAAL_DEGIRO_TEGEL && _isGetal(totalen.totaal_degiro_eur)) {
        secties.push({
            id: "degiro", titel: "Zoals DEGIRO rekent", regels: [
                rendementRegel(""),
                _regel("dividend", "+", "Dividend (netto)", dividend),
                ...overigRegel,
                _regel("totaal_degiro", "=", "Totaal W/V (DEGIRO-definitie)", totalen.totaal_degiro_eur,
                    { totaal: true, klasse: klasseVoorRendement(totalen.totaal_degiro_eur), uitleg: UITLEG_TOTAAL_DEGIRO }),
            ],
        });
    }
    return secties;
}

function _kostenItems(totalen, aansluitingskosten) {
    const items = [];
    if (totalen.transactiekosten_beschikbaar) {
        items.push(_regel("transactiekosten", "", "Totale transactiekosten", totalen.totale_transactiekosten,
            { subtekst: transactiekostenSubtekst(totalen) }));
    }
    const aansluiting = aansluitingskostenTegel(aansluitingskosten);
    if (aansluiting) {
        items.push(_regel("aansluitingskosten", "", aansluiting.label, null,
            { tekst: aansluiting.waardeTekst, uitleg: aansluiting.uitleg }));
    }
    return items;
}

// Kleine regel bij het totaalrendement ; null als waarde of datum ontbreekt.
function hoogsteRendementTekst(totalen) {
    const ath = totalen.all_time_high;
    if (!ath || !_isGetal(ath.waarde) || !ath.datum) return null;
    return `Hoogste: ${formatteerEuro(ath.waarde)} (${formatDatum(ath.datum)})`;
}

function berekeningKnopTekst(open) {
    return open ? "Verberg berekening" : "Bekijk berekening";
}

// hero + groepen: voor elk scherm; rekenoverzicht: [{id, titel, regels}], alleen op breed scherm en standaard dicht.
// Een regel heeft bedrag (getal) óf tekst (bv. "onbekend").
function bouwHomeIndeling(totalen, aansluitingskosten) {
    const heeftKassaldo = _isGetal(totalen.kassaldo_eur);
    const betrouwbaar = kassaldoIsBetrouwbaar(totalen);
    // Het rekenoverzicht sluit alleen met het backend-veld; zonder dat (bv. export niet vanaf de opening) geen opbouw.
    const aansluitend = betrouwbaar && _isGetal(totalen.overige_kassabewegingen_eur);
    const kosten = _kostenItems(totalen, aansluitingskosten);
    const dividendRegel = _isGetal(totalen.dividend_netto)
        ? _regel("dividend", "", "Ontvangen dividend (netto)", totalen.dividend_netto) : null;
    const kassa = heeftKassaldo ? _kassaRegels(totalen, betrouwbaar) : null;

    const toonDegiro = TOON_TOTAAL_DEGIRO_TEGEL && betrouwbaar && _isGetal(totalen.totaal_degiro_eur);
    const hero = {
        waarde: _regel("waarde", "", "Totale huidige waarde", totalen.waarde, { uitleg: UITLEG_WAARDE }),
        rendement: _regel("rendement", "", "Totaal rendement", totalen.rendement_eur, {
            klasse: klasseVoorRendement(totalen.rendement_eur), pct: totalen.rendement_pct, uitleg: UITLEG_RENDEMENT,
            subtekst: hoogsteRendementTekst(totalen),
        }),
        totaalDegiro: toonDegiro
            ? _regel("totaal_degiro", "", "Totaal W/V (DEGIRO-definitie)", totalen.totaal_degiro_eur,
                { klasse: klasseVoorRendement(totalen.totaal_degiro_eur), uitleg: UITLEG_TOTAAL_DEGIRO })
            : null,
    };

    const groepen = [
        { id: "inleg", titel: "Inleg", regels: [
            _regel("geinvesteerd", "", "Totaal geïnvesteerd", totalen.geinvesteerd, { uitleg: UITLEG_GEINVESTEERD }),
            ...(kassa ? [kassa.gestort, kassa.cash] : []),
        ] },
        { id: "opbrengst", titel: "Opbrengst", regels: [dividendRegel].filter(Boolean) },
        { id: "kosten", titel: "Kosten", regels: kosten },
    ].filter(groep => groep.regels.length > 0);

    return {
        kassaldoBetrouwbaar: betrouwbaar,
        rekenoverzicht: _opbouwSecties(totalen, aansluitend),
        kostenNotitie: !totalen.transactiekosten_beschikbaar,
        hero,
        groepen,
    };
}
