// Unit tests voor de koersmelding en de splitlabels (static/js/koersen.js).
//   node --test tests/test_koersen.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    koersMeldingTekst, tickerWaarschuwingTekst, splitLabel, splitLabelIndex,
    splitsAlternatieven, verborgenAlternatievenTekst,
} = require("../static/js/koersen.js");

test("alles compleet: geen melding", () => {
    assert.equal(koersMeldingTekst([], []), null);
    assert.equal(koersMeldingTekst(undefined, undefined), null);
});

test("onvolledig noemt de posities en dat heropenen helpt", () => {
    const tekst = koersMeldingTekst([{ ticker: "ASML.AS", naam: "ASML" }, { ticker: "X", naam: "X" }], []);
    assert.match(tekst, /^⚠️ Nog geen koersen voor 2 posities \(ASML \(ASML\.AS\), X\)/);
    assert.match(tekst, /opnieuw opent/);
    assert.match(tekst, /te laag/);
});

test("ontbrekend apart, enkelvoud", () => {
    const tekst = koersMeldingTekst([], [{ ticker: "WEG", naam: "Weg NV" }]);
    assert.equal(tekst, "⚠️ Yahoo Finance geeft geen koersen voor 1 positie (Weg NV (WEG)): die tellen niet mee in de waarde, de inleg wel.");
});

test("beide meldingen samen", () => {
    const tekst = koersMeldingTekst([{ ticker: "A", naam: "A" }], [{ ticker: "B", naam: "B" }]);
    assert.match(tekst, /Nog geen koersen voor 1 positie \(A\).*Yahoo Finance geeft geen koersen voor 1 positie \(B\)/);
});

test("splitlabels met Nederlandse komma", () => {
    assert.equal(splitLabel(4), "Split 4:1");
    assert.equal(splitLabel(3), "Split 3:1");
    assert.equal(splitLabel(37 / 14), "Split 2,64:1");
    assert.equal(splitLabel(1 / 3), "Reverse split 1:3");
    assert.equal(splitLabel(0.05), "Reverse split 1:20");
    assert.equal(splitLabel(0.005), "Reverse split 1:200");
});

test("splitdatum op of na een grafiekdatum", () => {
    const labels = ["2021-01-22", "2021-01-25", "2021-01-26", "2021-01-27"];
    assert.equal(splitLabelIndex(labels, "2021-01-26"), 2);
    assert.equal(splitLabelIndex(labels, "2021-01-23"), 1); // weekend: eerste handelsdag erna
    assert.equal(splitLabelIndex(labels, "2021-02-01"), -1); // buiten de grafiek
});

test("tickerwaarschuwing: niets zonder waarschuwingen", () => {
    assert.equal(tickerWaarschuwingTekst([]), null);
    assert.equal(tickerWaarschuwingTekst(undefined), null);
});

test("tickerwaarschuwing: alleen OpenFIGI geeft geen banner", () => {
    assert.equal(tickerWaarschuwingTekst([{ ticker: "VWCE.DE", naam: "VWCE", redenen: ["openfigi"] }]), null);
    const tekst = tickerWaarschuwingTekst([
        { ticker: "VWCE.DE", naam: "VWCE", redenen: ["openfigi"] },
        { ticker: "A", naam: "A", redenen: ["koers"] },
    ]);
    assert.equal(tekst, "⚠️ Bij 1 positie (A) wijkt de koers meer dan verwacht af van Yahoo Finance. " +
        "Controleer het Ticker-zekerheid-tabblad.");
});

test("tickerwaarschuwing: koers, OpenFIGI en beide apart gegroepeerd", () => {
    const tekst = tickerWaarschuwingTekst([
        { ticker: "A", naam: "A", redenen: ["koers"] },
        { ticker: "B", naam: "B", redenen: ["openfigi", "koers"] },
        { ticker: "C", naam: "C", redenen: ["koers"] },
    ]);
    assert.match(tekst, /^⚠️ Bij 2 posities \(A, C\) wijkt de koers meer dan verwacht af/);
    assert.match(tekst, /Bij 1 positie \(B\) wijkt de koers af van Yahoo Finance én staat de ticker niet bij OpenFIGI\./);
    assert.doesNotMatch(tekst, /Bij 1 positie \(B\) staat de ticker niet bij wat/);
});

test("tickerwaarschuwing: zonder redenen (oud antwoord) telt als koers", () => {
    assert.match(tickerWaarschuwingTekst([{ ticker: "X", naam: "X" }]), /Bij 1 positie \(X\) wijkt de koers/);
});

test("alternatieven zonder koersdata en zonder valuta/land/sector worden verborgen en geteld", () => {
    const leeg = { ticker: "IE000A9G9R73.SG", aantal_gecontroleerd: 0, valuta: null, land: null, sector: null };
    const metData = { ticker: "ST4R.DE", aantal_gecontroleerd: 3, valuta: "EUR" };
    const alleenValuta = { ticker: "X.AS", aantal_gecontroleerd: 0, valuta: "USD" };
    const { zichtbaar, aantalVerborgen } = splitsAlternatieven([leeg, metData, alleenValuta]);
    assert.deepEqual(zichtbaar.map(a => a.ticker), ["ST4R.DE", "X.AS"]);
    assert.equal(aantalVerborgen, 1);
    assert.deepEqual(splitsAlternatieven(undefined), { zichtbaar: [], aantalVerborgen: 0 });
});

test("tekst voor verborgen alternatieven: enkelvoud, meervoud, niets", () => {
    assert.equal(verborgenAlternatievenTekst(1), "1 kandidaat zonder koersdata verborgen");
    assert.equal(verborgenAlternatievenTekst(2), "2 kandidaten zonder koersdata verborgen");
    assert.equal(verborgenAlternatievenTekst(0), null);
});

const {
    prijscontroleSubregel, prijscontroleSplitTekst, dagrangeOordeel,
    alternatiefDagrangeTekst, alternatiefSubregel, alternatiefControleTekst, alternatiefUitkeringsvormTekst,
} = require("../static/js/koersen.js");

test("prijscontrole-subregel zonder split-correctie", () => {
    const c = { bekende_koers: 28.32, yahoo_koers: 28.2 };
    assert.equal(prijscontroleSubregel(c), "Excel 28,320 · Yahoo 28,200");
    assert.equal(prijscontroleSplitTekst(c), null);
});

test("prijscontrole-subregel met split-correctie: gecorrigeerde koers met *, split in de details", () => {
    const c = { bekende_koers: 100, yahoo_koers: 25, yahoo_koers_gecorrigeerd: 100, split_factor: 4 };
    assert.equal(prijscontroleSubregel(c), "Excel 100,000 · Yahoo 100,000 *");
    assert.equal(prijscontroleSplitTekst(c), "×4, ruwe Yahoo-koers 25,000");
});

test("prijscontrole-subregel zonder koersen", () => {
    assert.equal(prijscontroleSubregel({ bekende_koers: null, yahoo_koers: null }), "Excel - · Yahoo onbekend");
});

test("dagrangeOordeel: ✓ / ✗ / – met de bestaande klassen", () => {
    assert.deepEqual([true, false, null, undefined].map(b => [dagrangeOordeel(b).tekst, dagrangeOordeel(b).klasse]), [
        ["✓", "positief"], ["✗", "negatief"], ["–", "gedempt"], ["–", "gedempt"],
    ]);
});

test("alternatief: subregel bij ETF alleen beurs en valuta, bij aandeel ook land en sector", () => {
    const alt = { beurs: "AMS", valuta: "EUR", land: "Nederland", sector: null };
    assert.equal(alternatiefSubregel(alt, true), "AMS · EUR");
    assert.equal(alternatiefSubregel(alt, false), "AMS · EUR · Nederland · onbekend");
    assert.equal(alternatiefSubregel({}, true), "onbekend · onbekend");
});

test("alternatief: dagrangetekst en details", () => {
    const goed = { aantal_matches: 1, aantal_gecontroleerd: 1 };
    assert.equal(alternatiefDagrangeTekst(goed), "1/1");
    assert.equal(alternatiefControleTekst(goed), "1 van 1 datums binnen de dagrange");
    assert.equal(alternatiefUitkeringsvormTekst(goed), null);

    const strijdig = { aantal_matches: 2, aantal_gecontroleerd: 3, uitkeringsvorm_strijdig: true };
    assert.equal(alternatiefDagrangeTekst(strijdig), "2/3 (DIS/ACC wijkt af)");
    assert.match(alternatiefUitkeringsvormTekst(strijdig), /DIS\/ACC/);

    const leeg = { aantal_matches: 0, aantal_gecontroleerd: 0 };
    assert.equal(alternatiefDagrangeTekst(leeg), "geen prijsdata");
    assert.equal(alternatiefControleTekst(leeg), "geen koersdata om te controleren");
});
