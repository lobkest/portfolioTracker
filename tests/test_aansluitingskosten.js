// Unit tests voor static/js/aansluitingskosten.js (Home-tegel en blok op Verdeling → Beurs). Verzonnen data.
//   node --test tests/test_aansluitingskosten.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    UITLEG_AANSLUITINGSKOSTEN, heeftAansluitingskosten, aansluitingskostenTegel, aansluitingskostenRijen,
    aansluitingskostenTotalen, ONVOLLEDIG_TEKST, DIT_JAAR_BETAALD_TEKST, POSITIE_AANGEHOUDEN_TEKST,
} = require("../static/js/aansluitingskosten.js");

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

const MET_RESULTAAT = {
    ...SAMENVATTING,
    totaal_wv_eur: 300,
    totaal_wv_na_aansluitingskosten_eur: 287.5,
    totaal_transactiekosten_eur: 6,
    totaal_wv_volledig: true,
    per_beurs: [
        {
            ...beurs("XET", "Xetra", "Xetra", "Xetra", [[2024, 2.5], [2025, 2.5]]),
            transacties_per_jaar: [{ jaar: 2024, aantal: 3 }, { jaar: 2025, aantal: 1 }],
            wv_eur: 250, wv_volledig: true, wv_na_aansluitingskosten_eur: 245, transactiekosten_eur: 4.5,
            betaald_dit_jaar: false, kosten_zonder_positie_of_transacties: [], activiteit_zonder_kosten: [],
        },
        {
            ...beurs("ZZZ", "Voorbeeldbeurs AG", "ZZZ", "ZZZ", [[2024, 2.5], [2026, 2.5]]),
            transacties_per_jaar: [{ jaar: 2025, aantal: 2 }, { jaar: 2026, aantal: 1 }],
            wv_eur: 50, wv_volledig: false, wv_na_aansluitingskosten_eur: 45, transactiekosten_eur: null,
            betaald_dit_jaar: true, kosten_zonder_positie_of_transacties: [2024], activiteit_zonder_kosten: [2025], jaren_met_positie: [2025, 2026],
        },
        {
            ...beurs("TDG", "Tradegate", "Tradegate", "Tradegate", [[2023, 2.5], [2024, 2.5]]),
            transacties_per_jaar: [],
            wv_eur: null, wv_volledig: true, wv_na_aansluitingskosten_eur: null, transactiekosten_eur: null,
            betaald_dit_jaar: false, kosten_zonder_positie_of_transacties: [2023, 2024], activiteit_zonder_kosten: [],
        },
    ],
};

function rijVoor(code) {
    return aansluitingskostenRijen(MET_RESULTAAT, BEURS_VERDELING, false).find(r => r.code === code);
}

test("aansluitingskostenRijen: kosten en aantal transacties per jaar", () => {
    assert.deepEqual(rijVoor("XET").jaarRegels, ["2024 €2,50 · 3 transacties", "2025 €2,50 · 1 transactie"]);
    assert.deepEqual(rijVoor("ZZZ").jaarRegels,
                     ["2024 €2,50 · geen transacties", "2025 geen kosten · 2 transacties", "2026 €2,50 · 1 transactie"]);
});

test("aansluitingskostenRijen: dit jaar al betaald", () => {
    assert.equal(rijVoor("ZZZ").betaaldDitJaar, true);
    assert.equal(rijVoor("XET").betaaldDitJaar, false);
    assert.match(DIT_JAAR_BETAALD_TEKST, /dit jaar al betaald/);
});

test("aansluitingskostenRijen: W/V, W/V na kosten en transactiekosten, streepje bij ontbreken", () => {
    const xet = rijVoor("XET");
    assert.deepEqual([xet.wvTekst, xet.wvNaTekst, xet.transactiekostenTekst], ["€250,00", "€245,00", "€4,50"]);
    const tdg = rijVoor("TDG");
    assert.deepEqual([tdg.wvTekst, tdg.wvNaTekst, tdg.transactiekostenTekst], ["–", "–", "–"]);
    assert.equal(tdg.wv_na_eur, null);
});

test("aansluitingskostenRijen: onvolledig als een koers ontbreekt", () => {
    const zzz = rijVoor("ZZZ");
    assert.equal(zzz.wvTekst, `€50,00 · ${ONVOLLEDIG_TEKST}`);
    assert.equal(zzz.wvNaTekst, `€45,00 · ${ONVOLLEDIG_TEKST}`);
    assert.equal(ONVOLLEDIG_TEKST, "onvolledig (koers ontbreekt)");
});

test("aansluitingskostenRijen: negatieve W/V met min", () => {
    const s = { ...MET_RESULTAAT, per_beurs: [{ ...MET_RESULTAAT.per_beurs[0], wv_eur: -10, wv_na_aansluitingskosten_eur: -15 }] };
    assert.equal(aansluitingskostenRijen(s, BEURS_VERDELING, false)[0].wvNaTekst, "-€15,00");
});

test("aansluitingskostenRijen: meldingen voor de twee controlelijsten, neutraal", () => {
    assert.deepEqual(rijVoor("XET").meldingen, []);
    assert.deepEqual(rijVoor("ZZZ").meldingen, [
        "Voorbeeldbeurs AG: kosten in 2024 zonder positie of transacties op deze beurs.",
        "Voorbeeldbeurs AG: positie of transacties in 2025 zonder aansluitingskosten.",
    ]);
    assert.deepEqual(rijVoor("TDG").meldingen, ["Tradegate: kosten in 2023 en 2024 zonder positie of transacties op deze beurs."]);
});

test("aansluitingskostenRijen: zonder de nieuwe velden (oud antwoord) geen fouten", () => {
    const [rij] = aansluitingskostenRijen(SAMENVATTING, BEURS_VERDELING, false);
    assert.equal(rij.wvTekst, "–");
    assert.deepEqual(rij.meldingen, []);
    assert.equal(rij.betaaldDitJaar, false);
});

test("aansluitingskostenTotalen: alleen bij meer dan één beurs, bedragen uit de API", () => {
    assert.deepEqual(aansluitingskostenTotalen(MET_RESULTAAT, 3), {
        kostenTekst: "€12,50", wvTekst: "€300,00", wvNaTekst: "€287,50", wv_na_eur: 287.5, transactiekostenTekst: "€6,00",
    });
    assert.equal(aansluitingskostenTotalen(MET_RESULTAAT, 1), null);
    assert.equal(aansluitingskostenTotalen(undefined, 3), null);
});

test("aansluitingskostenTotalen: onvolledig totaal en ontbrekend totaal", () => {
    const t = aansluitingskostenTotalen({ ...MET_RESULTAAT, totaal_wv_volledig: false, totaal_transactiekosten_eur: null }, 3);
    assert.equal(t.wvTekst, `€300,00 · ${ONVOLLEDIG_TEKST}`);
    assert.equal(t.transactiekostenTekst, "–");
});

function pos(naam, nogInBezit = true, gewicht = 1) {
    return { ticker: naam, naam, nog_in_bezit: nogInBezit, wv_eur: 1, gewicht };
}

function rijMetPosities(posities, extra = {}) {
    const b = { ...beurs("XET", "Xetra", "Xetra", "Xetra", [[2024, 2.5]]), posities, ...extra };
    return aansluitingskostenRijen({ ...SAMENVATTING, per_beurs: [b] }, BEURS_VERDELING, false)[0];
}

test("jaarRegels: positie aangehouden in plaats van geen transacties", () => {
    const rij = rijMetPosities([pos("A")], {
        per_jaar: [{ jaar: 2024, bedrag_eur: 2.5 }, { jaar: 2025, bedrag_eur: 2.5 }, { jaar: 2026, bedrag_eur: 2.5 }],
        transacties_per_jaar: [{ jaar: 2024, aantal: 1 }], jaren_met_positie: [2024, 2025],
    });
    assert.deepEqual(rij.jaarRegels,
                     ["2024 €2,50 · 1 transactie", "2025 €2,50 · positie aangehouden", "2026 €2,50 · geen transacties"]);
    assert.equal(POSITIE_AANGEHOUDEN_TEKST, "positie aangehouden");
});

test("positieRegel: verkocht en deels herkenbaar", () => {
    const regel = rijMetPosities([pos("Naam A"), pos("Naam B", false), pos("Naam C", true, 0.25), pos("Naam D", false, 0.5)]).positieRegel;
    assert.deepEqual(regel.zichtbaar.map(i => i.tekst), ["Naam A", "Naam B (verkocht)", "Naam C (deels)", "Naam D (verkocht, deels)"]);
    assert.deepEqual(regel.zichtbaar.map(i => i.verkocht), [false, true, false, true]);
    assert.equal(regel.restLabel, null);
    assert.deepEqual(regel.rest, []);
});

test("positieRegel: na 4 posities afkappen met 'en N meer'", () => {
    const regel = rijMetPosities(["A", "B", "C", "D", "E", "F"].map(n => pos(n))).positieRegel;
    assert.equal(regel.zichtbaar.length, 4);
    assert.deepEqual(regel.rest.map(i => i.tekst), ["E", "F"]);
    assert.equal(regel.restLabel, "en 2 meer");
    assert.equal(regel.volledigeTekst, "A, B, C, D, E, F");
});

test("positieRegel: zonder posities geen regel", () => {
    assert.equal(rijMetPosities([]).positieRegel, null);
    assert.equal(aansluitingskostenRijen(SAMENVATTING, BEURS_VERDELING, false)[0].positieRegel, null);
});

test("aansluitingskostenRijen: zonder kosten een lege lijst", () => {
    assert.deepEqual(aansluitingskostenRijen(undefined, BEURS_VERDELING, false), []);
    assert.deepEqual(aansluitingskostenRijen({ totaal_eur: 0, aantal_regels: 0, per_jaar: [], per_beurs: [] },
                                             BEURS_VERDELING, false), []);
});
