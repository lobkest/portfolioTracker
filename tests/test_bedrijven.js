// Unit tests voor de rekenkern van het "Top N bedrijven"-tabblad
// (static/js/bedrijven.js): bedrijfsnaam-opmaak, top-N-keuze en inkorten.
// Draait via Node's ingebouwde testrunner, geen extra dependency nodig:
//   node --test tests/test_bedrijven.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    maakBedrijfsnaamLeesbaar: leesbaar,
    maakUniekeWeergaveNamen,
    breekLabelAf,
    effectieveTopN,
    kiesTopN,
    snijTopBedrijven,
    gebruikHorizontaleStaven,
    bedrijvenTitel,
} = require("../static/js/bedrijven.js");

// --- maakBedrijfsnaamLeesbaar: de bekende voorbeelden uit de ETF-bestanden ---

test("leesbaar: bekende voorbeelden (HOOFDLETTERS uit ETF-bestanden)", () => {
    const voorbeelden = [
        ["NVIDIA CORP", "Nvidia"],
        ["APPLE INC", "Apple"],
        ["MICROSOFT CORP", "Microsoft"],
        ["AMAZON COM INC", "Amazon.com"],
        ["MICRON TECHNOLOGY INC", "Micron Technology"],
        ["ALPHABET INC CLASS A", "Alphabet Class A"],
        ["ALPHABET INC CLASS C", "Alphabet Class C"],
        ["META PLATFORMS INC CLASS A", "Meta Platforms Class A"],
        ["BROADCOM INC", "Broadcom"],
        ["ADVANCED MICRO DEVICES INC", "Advanced Micro Devices"],
    ];
    for (const [ruw, verwacht] of voorbeelden) {
        assert.equal(leesbaar(ruw), verwacht, ruw);
    }
});

test("leesbaar: Amazon.com behoudt de punt, ook als de bron hem al schrijft", () => {
    assert.equal(leesbaar("Amazon.com Inc"), "Amazon.com");
    assert.equal(leesbaar("AMAZON.COM, INC."), "Amazon.com");
});

test("leesbaar: Alphabet Class A en C blijven onderscheidbaar", () => {
    assert.notEqual(leesbaar("ALPHABET INC CLASS A"), leesbaar("ALPHABET INC CLASS C"));
});

test("leesbaar: Yahoo-schrijfwijze (gemengde hoofdletters) geeft hetzelfde als iShares-HOOFDLETTERS", () => {
    assert.equal(leesbaar("Apple Inc"), leesbaar("APPLE INC"));
    assert.equal(leesbaar("NVIDIA Corp"), leesbaar("NVIDIA CORP"));
    assert.equal(leesbaar("Meta Platforms Inc Class A"), "Meta Platforms Class A");
});

test("leesbaar: uitzonderingslijst en afkortingen blijven hoofdletters", () => {
    assert.equal(leesbaar("ASML HOLDING NV"), "ASML Holding");
    assert.equal(leesbaar("SAP SE"), "SAP");
    assert.equal(leesbaar("ADVANCED MICRO DEVICES"), "Advanced Micro Devices");
    assert.equal(leesbaar("TSMC"), "TSMC");
    assert.equal(leesbaar("AMD INC"), "AMD");
});

test("leesbaar: woorden van hooguit 4 letters zonder klinker blijven hoofdletters", () => {
    assert.equal(leesbaar("HSBC HOLDINGS PLC"), "HSBC Holdings");
    assert.equal(leesbaar("UBS GROUP AG"), "UBS Group");
    assert.equal(leesbaar("ST JAMES PLACE PLC"), "St James Place");
});

test("leesbaar: juridische achtervoegsels alleen aan het eind, ook meerdere en met leestekens", () => {
    assert.equal(leesbaar("BYD CO LTD"), "BYD");
    assert.equal(leesbaar("JPMORGAN CHASE & CO"), "JPMorgan Chase");
    assert.equal(leesbaar("Adyen N.V."), "Adyen");
    assert.equal(leesbaar("INCA HOLDING"), "Inca Holding"); // "inc" als deel van een woord blijft
    assert.equal(leesbaar("COMPANY BANK AG"), "Company Bank"); // "Company" vooraan blijft, "AG" achteraan niet
});

test("leesbaar: strip nooit de héle naam weg", () => {
    assert.equal(leesbaar("COMPANY"), "Company");
    assert.equal(leesbaar("AG"), "Ag");
});

test("leesbaar: dashes zoals DeGiro ze schrijft", () => {
    assert.equal(leesbaar("META PLATFORMS INC- CLASS A"), "Meta Platforms Class A");
    assert.equal(leesbaar("ALPHABET INC. - CLASS A"), "Alphabet Class A");
    assert.equal(leesbaar("COCA-COLA CO"), "Coca-Cola");
});

test("leesbaar: verbindingswoorden blijven klein, & blijft staan", () => {
    assert.equal(leesbaar("ROYAL BANK OF CANADA"), "Royal Bank of Canada");
    assert.equal(leesbaar("JOHNSON & JOHNSON"), "Johnson & Johnson");
    assert.equal(leesbaar("PROCTER & GAMBLE CO"), "Procter & Gamble");
});

test("leesbaar: lege of onbekende invoer blijft ongewijzigd, geen crash", () => {
    assert.equal(leesbaar(""), "");
    assert.equal(leesbaar("   "), "   ");
    assert.equal(leesbaar(null), null);
    assert.equal(leesbaar(undefined), undefined);
    assert.equal(leesbaar(42), 42);
});

// --- maakUniekeWeergaveNamen: verschillende bedrijven vallen nooit samen ---

test("uniek: namen zonder botsing krijgen gewoon de nette naam", () => {
    assert.deepEqual(
        maakUniekeWeergaveNamen(["APPLE INC", "MICROSOFT CORP"]),
        ["Apple", "Microsoft"],
    );
});

test("uniek: botsing na opmaak (Rio Tinto PLC/Ltd) houdt het achtervoegsel", () => {
    assert.deepEqual(
        maakUniekeWeergaveNamen(["RIO TINTO PLC", "RIO TINTO LTD", "APPLE INC"]),
        ["Rio Tinto PLC", "Rio Tinto Ltd", "Apple"],
    );
});

test("uniek: als ook het achtervoegsel niet onderscheidt, blijft de ruwe naam staan", () => {
    // Beide worden "Foo" zonder achtervoegsel en ook beide "Foo Inc" mét.
    const ruw = ["FOO INC", "Foo Inc."];
    const uit = maakUniekeWeergaveNamen(ruw);
    assert.notEqual(uit[0], uit[1]);
    assert.deepEqual(uit, ruw);
});

test("uniek: dezelfde ruwe naam twee keer is geen botsing", () => {
    assert.deepEqual(maakUniekeWeergaveNamen(["APPLE INC", "APPLE INC"]), ["Apple", "Apple"]);
});

// --- breekLabelAf ---

test("breekLabelAf: korte tekst blijft één regel", () => {
    assert.deepEqual(breekLabelAf("Apple", 20), ["Apple"]);
});

test("breekLabelAf: breekt op woordgrenzen", () => {
    assert.deepEqual(breekLabelAf("Advanced Micro Devices", 16), ["Advanced Micro", "Devices"]);
    assert.deepEqual(breekLabelAf("Meta Platforms Class A", 16), ["Meta Platforms", "Class A"]);
});

test("breekLabelAf: 'Class A' wordt niet uit elkaar gebroken", () => {
    assert.deepEqual(breekLabelAf("Alphabet Class A", 12), ["Alphabet", "Class A"]);
});

test("breekLabelAf: een woord langer dan de limiet blijft heel", () => {
    assert.deepEqual(breekLabelAf("Supercalifragilistic Corp", 10), ["Supercalifragilistic", "Corp"]);
});

// --- top-N-keuze ---

test("kiesTopN: geldig aantal wordt overgenomen", () => {
    assert.equal(kiesTopN("15", 50, 10), 15);
    assert.equal(kiesTopN(" 7 ", 50, 10), 7);
});

test("kiesTopN: boven het beschikbare aantal wordt het maximum", () => {
    assert.equal(kiesTopN("80", 50, 10), 50);
    assert.equal(kiesTopN("30", 12, 10), 12);
});

test("kiesTopN: leeg, 0, negatief, decimaal of tekst valt terug op het laatst geldige aantal", () => {
    for (const ongeldig of ["", "   ", "0", "-5", "2.5", "2,5", "abc", "1e3", null, undefined]) {
        assert.equal(kiesTopN(ongeldig, 50, 20), 20, String(ongeldig));
    }
});

test("effectieveTopN: klemt tussen 1 en het beschikbare aantal", () => {
    assert.equal(effectieveTopN(10, 50), 10);
    assert.equal(effectieveTopN(50, 14), 14);
    assert.equal(effectieveTopN(0, 50), 1);
});

test("bedrijvenTitel: enkelvoud en meervoud", () => {
    assert.equal(bedrijvenTitel(10), "Top 10 bedrijven");
    assert.equal(bedrijvenTitel(1), "Top 1 bedrijf");
});

// --- snijTopBedrijven: restant opnieuw berekend bij een andere N ---

// Totaal 1000: vijf bedrijven van 300/200/100/50/50 (=700 gedekt in de lijst),
// backend-overig 300 (= niet-gedekt/buiten de meegeleverde lijst).
const DATA = {
    top: [
        { bedrijf: "A", waarde: 300 },
        { bedrijf: "B", waarde: 200 },
        { bedrijf: "C", waarde: 100 },
        { bedrijf: "D", waarde: 50 },
        { bedrijf: "E", waarde: 50 },
    ],
    overig: 300,
    totaal_waarde: 1000,
};

test("snijTopBedrijven: top 2 -> restant = backend-overig + C + D + E", () => {
    const r = snijTopBedrijven(DATA, 2);
    assert.deepEqual(r.getoond.map(e => e.bedrijf), ["A", "B"]);
    assert.equal(r.overigWaarde, 300 + 100 + 50 + 50); // 500
    assert.equal(r.overigPct, 50);
});

test("snijTopBedrijven: N gelijk aan het beschikbare aantal -> restant is alleen backend-overig", () => {
    const r = snijTopBedrijven(DATA, 5);
    assert.equal(r.getoond.length, 5);
    assert.equal(r.overigWaarde, 300);
    assert.equal(r.overigPct, 30);
});

test("snijTopBedrijven: N groter dan beschikbaar toont alles", () => {
    const r = snijTopBedrijven(DATA, 50);
    assert.equal(r.getoond.length, 5);
    assert.equal(r.overigWaarde, 300);
});

test("snijTopBedrijven: getoond + restant = totaal, bij elke N", () => {
    for (let n = 1; n <= 5; n++) {
        const r = snijTopBedrijven(DATA, n);
        const getoondWaarde = r.getoond.reduce((s, e) => s + e.waarde, 0);
        assert.equal(getoondWaarde + r.overigWaarde, 1000, `N=${n}`);
    }
});

test("snijTopBedrijven: lege data of totaal 0 crasht niet", () => {
    assert.deepEqual(snijTopBedrijven({ top: [] }, 10), { getoond: [], overigWaarde: 0, overigPct: 0 });
    assert.equal(snijTopBedrijven(null, 10).overigPct, 0);
});

// --- grafiekkeuze ---

test("gebruikHorizontaleStaven: >15 bedrijven of smal scherm -> horizontaal", () => {
    assert.equal(gebruikHorizontaleStaven(10, 1200), false);
    assert.equal(gebruikHorizontaleStaven(15, 1200), false);
    assert.equal(gebruikHorizontaleStaven(16, 1200), true);
    assert.equal(gebruikHorizontaleStaven(10, 375), true);
    assert.equal(gebruikHorizontaleStaven(10, 768), true);
    assert.equal(gebruikHorizontaleStaven(10, 769), false);
});
