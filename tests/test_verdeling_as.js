// Unit tests voor de as-keuze op Verdeling (static/js/verdeling_as.js).
//   node --test tests/test_verdeling_as.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    STANDAARD_AS, VERDELING_AS_OPSLAG_SLEUTEL, VERDELING_ASSEN, geldigeAs, domViewVoorAs, fondsElementenZichtbaar,
    heeftTijdreeks, effectieveModus, asVoorDimensie, leesOpgeslagenAs, bewaarAs,
} = require("../static/js/verdeling_as.js");

function nepOpslag(begin = {}) {
    const data = { ...begin };
    return { data, getItem: k => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = v; } };
}

test("standaard-as is Fonds en de knoppen staan in de vaste volgorde", () => {
    assert.equal(STANDAARD_AS, "fonds");
    assert.deepEqual(VERDELING_ASSEN.map(a => a.label), ["Fonds", "Land", "Sector", "Valuta", "Beurs"]);
});

test("geldigeAs: onbekende of lege waarde valt terug op Fonds", () => {
    assert.equal(geldigeAs("sector"), "sector");
    for (const slecht of ["bedrijven", "", null, undefined, 3]) assert.equal(geldigeAs(slecht), "fonds");
});

test("opgeslagen as wordt hersteld; ongeldig, ontbrekend of kapotte opslag geeft Fonds", () => {
    const opslag = nepOpslag();
    assert.equal(leesOpgeslagenAs(opslag), "fonds");
    bewaarAs(opslag, "valuta");
    assert.equal(opslag.data[VERDELING_AS_OPSLAG_SLEUTEL], "valuta");
    assert.equal(leesOpgeslagenAs(opslag), "valuta");
    assert.equal(leesOpgeslagenAs(nepOpslag({ [VERDELING_AS_OPSLAG_SLEUTEL]: "rommel" })), "fonds");
    const kapot = { getItem() { throw new Error("geblokkeerd"); }, setItem() { throw new Error("geblokkeerd"); } };
    assert.equal(leesOpgeslagenAs(kapot), "fonds");
    assert.doesNotThrow(() => bewaarAs(kapot, "land"));
    assert.equal(leesOpgeslagenAs(null), "fonds");
    assert.doesNotThrow(() => bewaarAs(null, "land"));
});

test("bewaarAs slaat een ongeldige as als Fonds op", () => {
    const opslag = nepOpslag();
    bewaarAs(opslag, "onzin");
    assert.equal(opslag.data[VERDELING_AS_OPSLAG_SLEUTEL], "fonds");
});

test("domViewVoorAs: Fonds is het verdeling-blok, de rest het eigen blok", () => {
    assert.equal(domViewVoorAs("fonds"), "verdeling");
    assert.equal(domViewVoorAs("land"), "land");
    assert.equal(domViewVoorAs("onzin"), "verdeling");
});

test("fonds-specifieke elementen alleen bij as Fonds", () => {
    assert.equal(fondsElementenZichtbaar("fonds"), true);
    for (const as of ["land", "sector", "valuta", "beurs"]) assert.equal(fondsElementenZichtbaar(as), false, as);
});

test("alle vijf assen hebben een tijdreeks, dus Over tijd blijft gekozen", () => {
    for (const { as } of VERDELING_ASSEN) {
        assert.equal(heeftTijdreeks(as), true, as);
        assert.equal(effectieveModus(as, "tijd"), "tijd", as);
    }
});

test("as zonder tijdreeks: Over tijd valt terug op Nu", () => {
    const assen = [{ as: "fonds", dimensie: "positie" }, { as: "kaal", dimensie: null }];
    assert.equal(heeftTijdreeks("kaal", assen), false);
    assert.equal(effectieveModus("kaal", "tijd", assen), "nu");
    assert.equal(effectieveModus("fonds", "tijd", assen), "tijd");
    assert.equal(effectieveModus("fonds", "nu", assen), "nu");
    assert.equal(heeftTijdreeks("onbekend", assen), false);
});

test("asVoorDimensie: positie hoort bij Fonds", () => {
    assert.equal(asVoorDimensie("positie"), "fonds");
    assert.equal(asVoorDimensie("beurs"), "beurs");
    assert.equal(asVoorDimensie("onzin"), "fonds");
});
