// Unit tests voor static/js/aansluitingskosten.js (Home-tegel en blok op Verdeling → Beurs). Verzonnen data.
//   node --test tests/test_aansluitingskosten.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    UITLEG_AANSLUITINGSKOSTEN, heeftAansluitingskosten, aansluitingskostenTegel, aansluitingskostenRijen,
    aansluitingskostenTotaalTekst,
} =require("../static/js/aansluitingskosten.js");

function beurs(code, naam, verdelingNaam, verdelingNaamEuronext, perJaar) {
    const totaal = perJaar.reduce((s, [, b]) => s + b, 0);
    return {
        code, naam, verdeling_naam: verdelingNaam, verdeling_naam_euronext: verdelingNaamEuronext,
        totaal_eur: totaal, aantal_regels: perJaar.length,
        per_jaar: perJaar.map(([jaar, bedrag_eur]) => ({ jaar, bedrag_eur })),
    };
}

const SAMENVATTING = {
    totaal_eur: 12.5,
    aantal_regels: 5,
    per_jaar: [{ jaar: 2024, bedrag_eur: 5 }, { jaar: 2025, bedrag_eur: 7.5 }],
    per_beurs: [
        beurs("XET", "Xetra", "Xetra", "Xetra", [[2024, 2.5], [2025, 2.5]]),
        beurs("EAM", "Euronext Amsterdam", "Euronext Amsterdam", "Euronext", [[2025, 2.5]]),
        beurs("ZZZ", "Voorbeeldbeurs AG", "ZZZ", "ZZZ", [[2024, 2.5], [2025, 2.5]]),
    ],
};
const BEURS_VERDELING = {
    beurs: { "Xetra": 1000, "Euronext Amsterdam": 500 },
    beurs_euronext: { "Xetra": 1000, "Euronext": 500 },
};

test("heeftAansluitingskosten: alleen met kostenregels", () => {
    assert.equal(heeftAansluitingskosten(SAMENVATTING), true);
    for (const s of [undefined, null, { totaal_eur: 0, aantal_regels: 0, per_jaar: [], per_beurs: [] }]) {
        assert.equal(heeftAansluitingskosten(s), false);
    }
});

test("aansluitingskostenTegel: totaal op 2 decimalen met de uitleg", () => {
    assert.deepEqual(aansluitingskostenTegel(SAMENVATTING),
                     { label: "Aansluitingskosten", waardeTekst: "€12,50", uitleg: UITLEG_AANSLUITINGSKOSTEN });
    assert.match(UITLEG_AANSLUITINGSKOSTEN, /Niet meegeteld in transactiekosten of rendement/);
    assert.equal(aansluitingskostenTegel({ ...SAMENVATTING, totaal_eur: 2.499 }).waardeTekst, "€2,50");
});

test("aansluitingskostenTegel: zonder kosten of zonder veld geen tegel", () => {
    assert.equal(aansluitingskostenTegel(undefined), null);
    assert.equal(aansluitingskostenTegel({ totaal_eur: 0, aantal_regels: 0, per_jaar: [], per_beurs: [] }), null);
});

test("aansluitingskostenRijen: meerdere beurzen en jaren, hoogste totaal eerst", () => {
    const rijen = aansluitingskostenRijen(SAMENVATTING, BEURS_VERDELING, false);
    assert.deepEqual(rijen.map(r => r.beurs), ["Voorbeeldbeurs AG (ZZZ)", "Xetra (XET)", "Euronext Amsterdam (EAM)"]);
    const xet = rijen.find(r => r.code === "XET");
    assert.equal(xet.totaalTekst, "€5,00");
    assert.equal(xet.perJaar, "2024 €2,50 · 2025 €2,50");
});

test("aansluitingskostenRijen: beurs zonder huidige positie, ook met Euronext samengevoegd", () => {
    for (const samenvoegen of [false, true]) {
        const inBezit = Object.fromEntries(
            aansluitingskostenRijen(SAMENVATTING, BEURS_VERDELING, samenvoegen).map(r => [r.code, r.inBezit]));
        assert.deepEqual(inBezit, { XET: true, EAM: true, ZZZ: false }, String(samenvoegen));
    }
});

test("aansluitingskostenRijen: zolang de verrijking laadt is het bezit onbekend", () => {
    assert.ok(aansluitingskostenRijen(SAMENVATTING, undefined, false).every(r => r.inBezit === null));
});

test("aansluitingskostenRijen: onbekende beurs onderaan, zonder positie-oordeel", () => {
    const s = {
        ...SAMENVATTING,
        per_beurs: [beurs("onbekend", "onbekend", null, null, [[null, 9]]), ...SAMENVATTING.per_beurs],
    };
    const rijen = aansluitingskostenRijen(s, BEURS_VERDELING, false);
    const laatste = rijen.at(-1);
    assert.equal(laatste.beurs, "Onbekende beurs");
    assert.equal(laatste.perJaar, "jaar onbekend €9,00");
    assert.equal(laatste.inBezit, null);
});

test("aansluitingskostenRijen: afronding op 2 decimalen, geen eigen berekening", () => {
    const s = { ...SAMENVATTING, per_beurs: [{ ...beurs("XET", "Xetra", "Xetra", "Xetra", [[2024, 1.005]]), totaal_eur: 1.004 }] };
    const [rij] = aansluitingskostenRijen(s, BEURS_VERDELING, false);
    assert.equal(rij.totaalTekst, "€1,00");
    assert.equal(rij.totaal_eur, 1.004);
});

test("aansluitingskostenTotaalTekst: alleen bij meer dan één beurs, bedrag uit de API", () => {
    assert.equal(aansluitingskostenTotaalTekst(SAMENVATTING, 3), "€12,50");
    assert.equal(aansluitingskostenTotaalTekst(SAMENVATTING, 1), null);
    assert.equal(aansluitingskostenTotaalTekst(undefined, 3), null);
});

test("aansluitingskostenRijen: zonder kosten een lege lijst", () => {
    assert.deepEqual(aansluitingskostenRijen(undefined, BEURS_VERDELING, false), []);
    assert.deepEqual(aansluitingskostenRijen({ totaal_eur: 0, aantal_regels: 0, per_jaar: [], per_beurs: [] },
                                             BEURS_VERDELING, false), []);
});
