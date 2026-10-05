// Unit tests voor de koersmelding en de splitlabels (static/js/koersen.js).
//   node --test tests/test_koersen.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { koersMeldingTekst, tickerWaarschuwingTekst, splitLabel, splitLabelIndex } = require("../static/js/koersen.js");

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

test("tickerwaarschuwing: alleen OpenFIGI noemt geen koersafwijking", () => {
    const tekst = tickerWaarschuwingTekst([{ ticker: "VWCE.DE", naam: "VWCE", redenen: ["openfigi"] }]);
    assert.equal(tekst, "⚠️ Bij 1 positie (VWCE) staat de ticker niet bij wat OpenFIGI voor de ISIN kent. " +
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
