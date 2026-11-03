// Unit tests voor static/js/per_aandeel.js.
//   node --test tests/test_per_aandeel.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { aandeelLandSectorRegels, etfPositieWaarde, etfHoldingsBijschrift } = require("../static/js/per_aandeel.js");

test("bekend land en sector", () => {
    const regels = aandeelLandSectorRegels({ SHOP: { land: "Canada", sector: "Technology" } }, "SHOP");
    assert.deepEqual(regels, [
        { label: "Land", tekst: "Canada", onbekend: false },
        { label: "Sector", tekst: "Technology", onbekend: false },
    ]);
});

test("Unknown en ontbrekend worden 'onbekend'", () => {
    const regels = aandeelLandSectorRegels({ X: { land: "Unknown", sector: null } }, "X");
    assert.deepEqual(regels, [
        { label: "Land", tekst: "onbekend", onbekend: true },
        { label: "Sector", tekst: "onbekend", onbekend: true },
    ]);
});

test("geen entry (ETF) of nog geen verrijking geeft null", () => {
    assert.equal(aandeelLandSectorRegels({ SHOP: { land: "Canada", sector: "Technology" } }, "VWRL.AS"), null);
    assert.equal(aandeelLandSectorRegels(undefined, "SHOP"), null);
});

test("etfPositieWaarde: waarde van een positie in bezit, anders null", () => {
    const stats = { posities: [{ ticker: "VWCE", huidige_waarde: 1234.5 }, { ticker: "NUL", huidige_waarde: 0 }] };
    assert.equal(etfPositieWaarde(stats, "VWCE"), 1234.5);
    assert.equal(etfPositieWaarde(stats, "NUL"), 0);
    assert.equal(etfPositieWaarde(stats, "GESLOTEN"), null);
    assert.equal(etfPositieWaarde(null, "VWCE"), null);
});

test("etfHoldingsBijschrift: volledige lijst van de aanbieder", () => {
    const data = { bron: "provider_csv", aantal: 1500, gedekt_gewicht: 0.99, holdings: [{}] };
    assert.deepEqual(etfHoldingsBijschrift(data),
        { tekst: "Volledige lijst van de fondsaanbieder: 1500 bedrijven", grijs: false });
});

test("etfHoldingsBijschrift: alleen top 10 is grijs en noemt de dekking", () => {
    const data = { bron: "yfinance_top10", aantal: 10, gedekt_gewicht: 0.2345, holdings: [{}] };
    const bijschrift = etfHoldingsBijschrift(data);
    assert.equal(bijschrift.grijs, true);
    assert.match(bijschrift.tekst, /^Alleen de top 10 via Yahoo, samen 23[,.]5\s?% van het fonds$/);
});

test("etfHoldingsBijschrift: zonder holdings null", () => {
    assert.equal(etfHoldingsBijschrift({ bron: null, aantal: 0, gedekt_gewicht: 0, holdings: [] }), null);
    assert.equal(etfHoldingsBijschrift(null), null);
});

const { aandeelKostenTekst, transactiesVoorTicker, boekingsrijenTekst } = require("../static/js/per_aandeel.js");

const KOSTEN = { transactiekosten: 3.5, autofx_kosten: null, kostenkorting: 0, totaal: 3.5, aantal_transacties: 2 };

test("aandeelKostenTekst: alleen transactiekosten", () => {
    assert.deepEqual(aandeelKostenTekst(KOSTEN), { tekst: "Transactiekosten €3,50 (2 transacties)", onbekend: false });
    assert.equal(aandeelKostenTekst({ ...KOSTEN, aantal_transacties: 1 }).tekst, "Transactiekosten €3,50 (1 transactie)");
});

test("aandeelKostenTekst: met AutoFX en met promotie", () => {
    assert.equal(aandeelKostenTekst({ ...KOSTEN, autofx_kosten: 0.5 }).tekst,
        "Transactiekosten €3,50 (2 transacties) · AutoFX €0,50");
    assert.equal(aandeelKostenTekst({ ...KOSTEN, autofx_kosten: 0.5, kostenkorting: 1 }).tekst,
        "Transactiekosten €3,50 (2 transacties) · AutoFX €0,50 · promotie −€1,00");
    assert.equal(aandeelKostenTekst({ ...KOSTEN, autofx_kosten: 0 }).tekst, "Transactiekosten €3,50 (2 transacties)");
});

test("aandeelKostenTekst: alles None of geen info is onbekend, nooit €0,00", () => {
    const onbekend = { tekst: "Transactiekosten onbekend", onbekend: true };
    assert.deepEqual(aandeelKostenTekst({ transactiekosten: null, autofx_kosten: null, kostenkorting: 0, totaal: null, aantal_transacties: 1 }), onbekend);
    assert.deepEqual(aandeelKostenTekst(undefined), onbekend);
});

test("transactiesVoorTicker: filtert, oud naar nieuw, zonder ticker valt weg, invoer ongewijzigd", () => {
    const lijst = [
        { ticker: "A", datum: "2024-03-01", tijd: "10:00" },
        { ticker: "B", datum: "2024-02-01", tijd: null },
        { ticker: null, datum: "2024-01-01", tijd: null },
        { ticker: "A", datum: "2024-01-05", tijd: "09:00" },
        { ticker: "A", datum: "2024-01-05", tijd: null },
    ];
    const kopie = JSON.parse(JSON.stringify(lijst));
    const rijen = transactiesVoorTicker(lijst, "A");
    assert.deepEqual(rijen.map(r => `${r.datum} ${r.tijd}`), ["2024-01-05 null", "2024-01-05 09:00", "2024-03-01 10:00"]);
    assert.deepEqual(lijst, kopie);
    assert.deepEqual(transactiesVoorTicker(lijst, "ONBEKEND"), []);
});

test("boekingsrijenTekst: alleen bij meer rijen dan aantal_transacties", () => {
    assert.equal(boekingsrijenTekst(2, KOSTEN), null);
    assert.equal(boekingsrijenTekst(3, KOSTEN), "Inclusief 1 boekingsrij van DEGIRO (split/productwijziging), zonder kosten.");
    assert.equal(boekingsrijenTekst(4, KOSTEN), "Inclusief 2 boekingsrijen van DEGIRO (split/productwijziging), zonder kosten.");
    assert.equal(boekingsrijenTekst(4, undefined), null);
});
