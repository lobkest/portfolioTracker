// Unit tests voor static/js/wat_als.js (teksten, invoer en opmaak van het tabblad Zoeken & wat als).
//   node --test tests/test_wat_als.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    WAT_ALS_MAX_AANTAL, WAT_ALS_BALK_MAX_PP, WAT_ALS_WAARSCHUWING_TEKSTEN, WAT_ALS_ONBEKENDE_WAARSCHUWING,
    isWatAlsIsin, watAlsAantal, watAlsGeschatteWaardeTekst, watAlsWaarschuwingTekst, watAlsTypeLabel, watAlsNoteringLabel,
    watAlsIsinTekst, watAlsTopRijen, watAlsMeldingZelfdeFonds, watAlsBalk, watAlsRij, watAlsAlInPortfolioTekst,
    watAlsAlInBezitRegels, watAlsFoutTekst, watAlsDividendRegel, watAlsDividendResultaatTekst,
    watAlsOverlapRegel, watAlsOverlapEtfTekst, watAlsBedrijvenOnvolledigTekst, watAlsProfielBedrijven,
} = require("../static/js/wat_als.js");

// De codes die effect_profiel.py kan teruggeven.
const BACKEND_WAARSCHUWINGEN = ["geen_koers", "geen_sector", "geen_holdings", "alleen_top10", "isin_gekozen", "land_via_proxy",
    "geen_dividend_data"];

const DIVIDEND = {
    status: "keert_uit", per_stuk_bruto_eur: 2, per_stuk_netto_eur: 1.7, yield_bruto: 0.02, belasting_fractie: 0.15,
    belasting_bron: "land", bron: "yahoo_reeks", uitkeringen_12m: 4,
};

test("dividendregel: bedragen, yield en infotip; geen uitkering en onbekend krijgen een vaste tekst", () => {
    const regel = watAlsDividendRegel(DIVIDEND);
    assert.match(regel.tekst, /€2,00 bruto \/ €1,70 netto per stuk per jaar \(bruto 2,00%\)/);
    assert.match(regel.tip.tekst, /15% bronbelasting/);
    assert.deepEqual(watAlsDividendRegel({ status: "keert_niet_uit" }),
        { tekst: "Keert geen dividend uit (vermoedelijk accumulerend)", tip: null });
    assert.deepEqual(watAlsDividendRegel({ status: "onbekend" }), { tekst: "Dividend onbekend", tip: null });
    assert.equal(watAlsDividendRegel(undefined).tekst, "Dividend onbekend");
});

test("dividend in het resultaat: bedragen van de backend, anders de status", () => {
    assert.match(watAlsDividendResultaatTekst({ dividend_status: "keert_uit", dividend_jaar_bruto_eur: 20, dividend_jaar_netto_eur: 17 }),
        /€20,00 bruto \/ €17,00 netto/);
    assert.equal(watAlsDividendResultaatTekst({ dividend_status: "onbekend", dividend_jaar_bruto_eur: null, dividend_jaar_netto_eur: null }),
        "Dividend onbekend");
    assert.match(watAlsDividendResultaatTekst({ dividend_status: "keert_niet_uit", dividend_jaar_bruto_eur: null }), /Keert geen dividend uit/);
});

test("elke waarschuwingscode van de backend heeft een eigen tekst", () => {
    for (const code of BACKEND_WAARSCHUWINGEN) {
        assert.ok(WAT_ALS_WAARSCHUWING_TEKSTEN[code], code);
        assert.notEqual(watAlsWaarschuwingTekst(code), WAT_ALS_ONBEKENDE_WAARSCHUWING, code);
    }
});

test("een onbekende waarschuwingscode geeft een algemene tekst en geen crash", () => {
    assert.equal(watAlsWaarschuwingTekst("nieuwe_code"), WAT_ALS_ONBEKENDE_WAARSCHUWING);
    assert.equal(watAlsWaarschuwingTekst(undefined), WAT_ALS_ONBEKENDE_WAARSCHUWING);
    assert.equal(watAlsWaarschuwingTekst("constructor"), WAT_ALS_ONBEKENDE_WAARSCHUWING);
});

test("ISIN-tekst voor zeker, kiezen en geen", () => {
    assert.match(watAlsIsinTekst("zeker", "eigen_data"), /eigen transacties/);
    assert.match(watAlsIsinTekst("zeker", "zoekopdracht"), /zoekopdracht/);
    assert.match(watAlsIsinTekst("zeker", "ishares_naam"), /naam van het fonds/);
    assert.match(watAlsIsinTekst("zeker", null), /bekend/);
    assert.match(watAlsIsinTekst("kiezen", "ishares_naam"), /meerdere varianten/);
    assert.match(watAlsIsinTekst("geen", null), /10 grootste holdings/);
});

test("aantal stuks: alleen een geheel getal van 1 tot het maximum", () => {
    for (const ongeldig of ["0", "-1", "1.5", "1,5", "abc", "", "  ", null, undefined, "1e3", String(WAT_ALS_MAX_AANTAL + 1)]) {
        assert.equal(watAlsAantal(ongeldig).geldig, false, String(ongeldig));
    }
    assert.deepEqual(watAlsAantal("10"), { geldig: true, aantal: 10 });
    assert.deepEqual(watAlsAantal(" 7 "), { geldig: true, aantal: 7 });
    assert.equal(watAlsAantal(String(WAT_ALS_MAX_AANTAL)).geldig, true);
});

test("ISIN-herkenning gelijk aan de backend", () => {
    assert.equal(isWatAlsIsin("IE00B4L5Y983"), true);
    assert.equal(isWatAlsIsin(" ie00b4l5y983 "), true);
    assert.equal(isWatAlsIsin("IWDA.AS"), false);
    assert.equal(isWatAlsIsin("ASML"), false);
    assert.equal(isWatAlsIsin("IE00B4L5Y98"), false);
    assert.equal(isWatAlsIsin(null), false);
});

test("geschatte waarde: aantal x koers, leeg zonder koers", () => {
    assert.equal(watAlsGeschatteWaardeTekst(10, 46.5), "≈ €465,00");
    assert.equal(watAlsGeschatteWaardeTekst(10, null), "");
    assert.equal(watAlsGeschatteWaardeTekst(NaN, 5), "");
});

test("rij-opmaak: vóór, ná en het verschil met teken", () => {
    const plus = watAlsRij({ naam: "Land A", voor_pct: 70, na_pct: 75.25, verschil_pp: 5.25 });
    assert.equal(plus.voorTekst, "70,0%");
    assert.equal(plus.naTekst, "75,3%");
    assert.equal(plus.verschilTekst, "+5,3 pp");
    assert.equal(plus.verschilKlasse, "positief");
    const min = watAlsRij({ naam: "Land B", voor_pct: 30, na_pct: 15, verschil_pp: -15 });
    assert.equal(min.verschilTekst, "−15,0 pp");
    assert.equal(min.verschilKlasse, "negatief");
    const geen = watAlsRij({ naam: "Land C", voor_pct: 10, na_pct: 10, verschil_pp: 0 });
    assert.equal(geen.verschilTekst, "0,0 pp");
    assert.equal(geen.verschilKlasse, "gedempt");
    assert.equal(watAlsRij({ naam: "X", voor_pct: 1, na_pct: 1, verschil_pp: null }).verschilTekst, "onbekend");
});

test("een verschil dat naar nul afrondt krijgt geen teken", () => {
    assert.equal(watAlsRij({ naam: "X", voor_pct: 1, na_pct: 1.01, verschil_pp: 0.01 }).verschilTekst, "0,0 pp");
    assert.equal(watAlsRij({ naam: "X", voor_pct: 1, na_pct: 0.99, verschil_pp: -0.01 }).verschilKlasse, "gedempt");
});

test("balkbreedte: begrensd op de schaal, 0 bij geen verschil", () => {
    assert.deepEqual(watAlsBalk(0), { richting: "geen", breedtePct: 0 });
    assert.deepEqual(watAlsBalk(null), { richting: "geen", breedtePct: 0 });
    assert.deepEqual(watAlsBalk(WAT_ALS_BALK_MAX_PP / 2), { richting: "plus", breedtePct: 50 });
    assert.deepEqual(watAlsBalk(-WAT_ALS_BALK_MAX_PP / 4), { richting: "min", breedtePct: 25 });
    assert.deepEqual(watAlsBalk(WAT_ALS_BALK_MAX_PP), { richting: "plus", breedtePct: 100 });
    assert.deepEqual(watAlsBalk(5000), { richting: "plus", breedtePct: 100 });
    assert.deepEqual(watAlsBalk(-5000), { richting: "min", breedtePct: 100 });
});

test("melding zelfde fonds: geen bij een lege lijst of null", () => {
    assert.equal(watAlsMeldingZelfdeFonds([]), null);
    assert.equal(watAlsMeldingZelfdeFonds(null), null);
    assert.equal(watAlsMeldingZelfdeFonds(undefined), null);
});

test("melding zelfde fonds: bij een en bij twee tickers", () => {
    const een = watAlsMeldingZelfdeFonds(["CSPX.AS"]);
    assert.match(een, /je positie CSPX\.AS/);
    assert.match(een, /nieuwe positie/);
    assert.match(een, /die positie/);
    const twee = watAlsMeldingZelfdeFonds(["CSPX.AS", "SXR8.DE"]);
    assert.match(twee, /je posities CSPX\.AS en SXR8\.DE/);
    assert.match(twee, /die posities/);
    assert.match(watAlsMeldingZelfdeFonds(["A", "B", "C"]), /A, B en C/);
});

test("top-rijen: aflopend, begrensd en als percentage", () => {
    const rijen = watAlsTopRijen({ NL: 0.1, US: 0.6, JP: 0.3, DE: 0.05 }, 3);
    assert.deepEqual(rijen.map(r => r.naam), ["US", "JP", "NL"]);
    assert.equal(rijen[0].pct, 60);
    assert.deepEqual(watAlsTopRijen(null, 5), []);
    assert.deepEqual(watAlsTopRijen({ A: NaN, B: 0.5 }, 5).map(r => r.naam), ["B"]);
});

test("labels voor type en notering", () => {
    assert.equal(watAlsTypeLabel("ETF"), "ETF");
    assert.equal(watAlsTypeLabel("EQUITY"), "Aandeel");
    assert.equal(watAlsNoteringLabel({ ticker: "IWDA.AS", beurs: "AMS", beurs_naam: "Amsterdam" }), "Amsterdam · IWDA.AS");
    assert.equal(watAlsNoteringLabel({ ticker: "IWDA.AS", beurs: "AMS" }), "AMS · IWDA.AS");
    assert.equal(watAlsNoteringLabel({ ticker: "X" }), "X");
});

test("al in je portfolio en al in bezit", () => {
    assert.equal(watAlsAlInPortfolioTekst(44.74), "44,7% van deze aankoop zit al in je portfolio");
    const regels = watAlsAlInBezitRegels({
        direct_eur: 300, via_etf_eur: 500, per_etf: { ETF_B: 100, ETF_A: 400 }, gematchte_naam: "Apple Inc",
    });
    assert.match(regels[0], /Direct: €300,00\. Via je ETF's: €500,00\./);
    assert.deepEqual(regels.slice(1, 3), ["ETF_A: €400,00", "ETF_B: €100,00"]);
    assert.match(regels.at(-1), /naam van het bedrijf/);
    const nergens = watAlsAlInBezitRegels({ direct_eur: 0, via_etf_eur: 0, per_etf: {}, gematchte_naam: null });
    assert.equal(nergens.length, 1);
    assert.match(nergens[0], /niet in je portfolio/);
});

test("overlapregel: onbekend, top10 met ondergrens en volledig", () => {
    assert.equal(watAlsOverlapRegel({ holdings_status: "onbekend", al_in_portfolio_pct: null, gedekt_pct: null }),
        "Overlap onbekend: de bedrijven in dit fonds zijn niet beschikbaar");
    assert.equal(watAlsOverlapRegel({ holdings_status: "top10", al_in_portfolio_pct: 12.34, gedekt_pct: 25 }),
        "Minstens 12,3% van deze aankoop zit al in je portfolio (op basis van de top 10, 25,0% van het fonds)");
    assert.equal(watAlsOverlapRegel({ holdings_status: "volledig", al_in_portfolio_pct: 44.74, gedekt_pct: 99 }),
        "44,7% van deze aankoop zit al in je portfolio");
});

test("overlap per ETF: null is onbekend, geen 0, en top10 is een ondergrens", () => {
    assert.equal(watAlsOverlapEtfTekst(null, "onbekend"), "onbekend");
    assert.equal(watAlsOverlapEtfTekst(12.34, "top10"), "minstens 12,3%");
    assert.equal(watAlsOverlapEtfTekst(0, "volledig"), "0,0%");
});

test("bedrijven ná de aankoop: melding alleen bij een ETF zonder holdings", () => {
    assert.equal(watAlsBedrijvenOnvolledigTekst({ ticker: "TDIV.AS", bedrijven_onvolledig: true }),
        "TDIV.AS is hier niet meegeteld: de bedrijven in dit fonds zijn onbekend");
    assert.equal(watAlsBedrijvenOnvolledigTekst({ ticker: "AAPL", bedrijven_onvolledig: false }), null);
});

test("profiel: top-5-bedrijven met percentage, bronregel bij top10, leeg zonder holdings", () => {
    const top5 = [["A", 0.3], ["B", 0.2], ["C", 0.1], ["D", 0.05], ["E", 0.04]].map(([naam, gewicht]) => ({ naam, gewicht }));
    const volledig = watAlsProfielBedrijven({ bron: "provider_csv", gedekt_gewicht: 1, top5 });
    assert.deepEqual(volledig.rijen.map(r => `${r.naam} ${r.pct}`), ["A 30", "B 20", "C 10", "D 5", "E 4"]);
    assert.equal(volledig.bronTekst, null);
    const top10 = watAlsProfielBedrijven({ bron: "yfinance_top10", gedekt_gewicht: 0.254, top5 });
    assert.equal(top10.bronTekst, "op basis van de 10 grootste holdings (25,4% van het fonds)");
    assert.deepEqual(watAlsProfielBedrijven(null), { rijen: [], bronTekst: null });
});

test("foutteksten per statuscode", () => {
    assert.equal(watAlsFoutTekst(400, "Het aantal moet een geheel getal zijn."), "Het aantal moet een geheel getal zijn.");
    assert.match(watAlsFoutTekst(400, null), /niet toegestaan/);
    assert.equal(watAlsFoutTekst(404, "iets"), "Niet gevonden.");
    assert.match(watAlsFoutTekst(422, null), /geen actuele koers/);
    assert.match(watAlsFoutTekst(429, "Je hebt te veel aanvragen gedaan. Probeer het over 5 minuten opnieuw."), /over 5 minuten/);
    for (const status of [500, 504, 0]) assert.match(watAlsFoutTekst(status, "interne details"), /Er ging iets mis/);
});
