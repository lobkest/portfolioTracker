// Unit tests voor bouwHomeIndeling() (static/js/gedeeld/home_indeling.js): indeling van Home, zonder DOM.
//   node --test tests/test_home_indeling.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function laad() {
    const context = {
        document: { createElement: () => ({}) },
        formatteerEuro: x => `EUR ${x}`,
        formatGetal: (x, d) => x.toFixed(d).replace(".", ","),
        formatPct: x => `${x}%`,
        formatDatum: x => x,
        klasseVoorRendement: x => (x === null || x === undefined ? "" : x >= 0 ? "positief" : "negatief"),
        aansluitingskostenTegel: require("../static/js/aansluitingskosten.js").aansluitingskostenTegel,
        activeerInfoTip: () => {},
    };
    vm.createContext(context);
    for (const bestand of ["tegels.js", "home_indeling.js"]) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "static", "js", "gedeeld", bestand), "utf8"), context);
    }
    return context;
}

// Een gewone kopie: arrays uit de vm-context zijn niet reference-equal aan die van deze realm.
function bouw(totalen, aansluitingskosten) {
    return JSON.parse(JSON.stringify(laad().bouwHomeIndeling(totalen, aansluitingskosten)));
}

// RNA-patroon: 13440 + 2,19 + 0 − 3,48 = 13438,71
const RNA = {
    geinvesteerd: 13438.71, waarde: 14000, rendement_eur: 561.29, rendement_pct: 4.18,
    dividend_netto: 2.19, kassaldo_eur: 3.48, netto_gestort_eur: 13440, overige_kassabewegingen_eur: 0,
    totaal_degiro_eur: 563.48, all_time_high: { waarde: 600, datum: "01-02-2025" },
    transactiekosten_beschikbaar: true, totale_transactiekosten: 7,
};
const AANSLUITING = {
    totaal_eur: 22.5, aantal_regels: 9, per_jaar: [{ jaar: 2025, bedrag_eur: 22.5 }],
    per_beurs: [{ code: "XET", naam: "Xetra", verdeling_naam: "Xetra", verdeling_naam_euronext: "Xetra",
                  totaal_eur: 22.5, aantal_regels: 9, per_jaar: [{ jaar: 2025, bedrag_eur: 22.5 }] }],
};

const ids = lijst => lijst.map(r => r.id);
const sectie = (indeling, id) => indeling.rekenblok.find(s => s.id === id);
const labels = lijst => lijst.map(r => r.label);

// Som van de regels van een sectie: + telt op, − trekt af (het eerste getal heeft geen teken).
function somTot(regels) {
    return regels.filter(r => !r.totaal).reduce((som, r) => som + (r.teken === "−" ? -r.bedrag : r.bedrag), 0);
}

test("rekenblok: drie secties in volgorde, met tekens", () => {
    const ind = bouw(RNA, null);
    assert.deepEqual(ids(ind.rekenblok), ["ingegaan", "opgeleverd", "degiro"]);
    assert.deepEqual(sectie(ind, "ingegaan").regels.map(r => [r.teken, r.label]), [
        ["", "Netto gestort"], ["+", "Dividend (netto)"], ["−", "Vrije ruimte (cash)"], ["=", "Totaal geïnvesteerd"],
    ]);
    assert.deepEqual(sectie(ind, "opgeleverd").regels.map(r => [r.teken, r.label]), [
        ["", "Totale huidige waarde"], ["−", "Totaal geïnvesteerd"], ["=", "Totaal rendement"],
    ]);
    assert.deepEqual(sectie(ind, "degiro").regels.map(r => [r.teken, r.label]), [
        ["", "Totaal rendement"], ["+", "Dividend (netto)"], ["=", "Totaal W/V (DEGIRO-definitie)"],
    ]);
});

test("rekenblok sluit: geïnvesteerd, rendement en W/V met de RNA-cijfers", () => {
    const ind = bouw(RNA, null);
    const ingegaan = sectie(ind, "ingegaan").regels;
    assert.equal(+somTot(ingegaan).toFixed(2), ingegaan.find(r => r.totaal).bedrag);
    assert.equal(+somTot(sectie(ind, "opgeleverd").regels).toFixed(2), RNA.rendement_eur);
    assert.equal(+somTot(sectie(ind, "degiro").regels).toFixed(2), RNA.totaal_degiro_eur);
});

test("overige kassabewegingen: alleen bij ≠ 0, met teken en positief bedrag; in sectie 1 en 3", () => {
        assert.ok(!ids(sectie(bouw(RNA, null), "ingegaan").regels).includes("overig"));
    const ind = bouw({ ...RNA, overige_kassabewegingen_eur: -22.5 }, AANSLUITING);
    for (const id of ["ingegaan", "degiro"]) {
        const regel = sectie(ind, id).regels.find(r => r.id === "overig");
        assert.deepEqual([regel.teken, regel.bedrag], ["−", 22.5], id);
    }
    const plus = bouw({ ...RNA, overige_kassabewegingen_eur: 0.4 }, null);
    assert.equal(sectie(plus, "ingegaan").regels.find(r => r.id === "overig").teken, "+");
});

test("sluiten met aansluitingskosten: 13500 + 2,19 − 22,50 − 3,48 = 13476,21", () => {
    const totalen = { ...RNA, netto_gestort_eur: 13500, overige_kassabewegingen_eur: -22.5, geinvesteerd: 13476.21 };
    const regels = sectie(bouw(totalen, null), "ingegaan").regels;
    assert.equal(+somTot(regels).toFixed(2), 13476.21);
});

test("onbetrouwbaar kassaldo: alleen sectie 2, onbekend voor cash en netto gestort, geen W/V", () => {
        for (const totalen of [{ ...RNA, kassaldo_eur: -5 }, { ...RNA, netto_gestort_eur: 0 }]) {
        const ind = bouw(totalen, null);
        assert.deepEqual(ids(ind.rekenblok), ["opgeleverd"]);
        const onbekend = ind.overige.filter(r => r.tekst === "onbekend");
        assert.deepEqual(labels(onbekend), ["Vrije ruimte (cash)", "Netto gestort"]);
        for (const r of onbekend) assert.match(r.uitleg, /Stortingen ontbreken/);
        assert.equal(ind.hero.totaalDegiro, null);
    }
});

test("zonder rekeningoverzicht: alleen sectie 2 en geen cash, netto gestort of dividend", () => {
    const totalen = { ...RNA, kassaldo_eur: null, netto_gestort_eur: null, dividend_netto: null,
                      overige_kassabewegingen_eur: null, totaal_degiro_eur: null };
    const ind = bouw(totalen, null);
    assert.deepEqual(ids(ind.rekenblok), ["opgeleverd"]);
    assert.deepEqual(labels(ind.overige), ["Totale transactiekosten", "Hoogste rendement"]);
    assert.deepEqual(labels(ind.groepen.flatMap(g => g.regels)),
        ["Totaal geïnvesteerd", "Hoogste rendement", "Totale transactiekosten"]);
});

test("betrouwbaar kassaldo zonder overig-veld: geen opbouw, wel de losse cijfers", () => {
    const { overige_kassabewegingen_eur, ...totalen } = RNA;
    const ind = bouw(totalen, null);
    assert.deepEqual(ids(ind.rekenblok), ["opgeleverd"]);
    assert.deepEqual(labels(ind.overige), [
        "Ontvangen dividend (netto)", "Vrije ruimte (cash)", "Netto gestort", "Totale transactiekosten", "Hoogste rendement",
    ]);
});

test("overige rij: transactiekosten met korting-uitleg, aansluitingskosten alleen als aanwezig, hoogste rendement met datum", () => {
        const totalen = { ...RNA, totale_transactiekosten: 35, totale_transactiekosten_betaald: 42, transactiekosten_korting: 7 };
    const ind = bouw(totalen, AANSLUITING);
    assert.deepEqual(labels(ind.overige), ["Totale transactiekosten", "Aansluitingskosten", "Hoogste rendement"]);
    assert.equal(ind.overige[0].subtekst, "betaald 42,00 − promotie 7,00");
    assert.equal(ind.overige[2].subtekst, "01-02-2025");
    assert.ok(!labels(bouw(RNA, null).overige).includes("Aansluitingskosten"));
});

test("zonder kostenkolom: notitie, geen transactiekostenregel", () => {
    const ind = bouw({ ...RNA, transactiekosten_beschikbaar: false }, null);
    assert.equal(ind.kostenNotitie, true);
    assert.ok(!labels(ind.overige).includes("Totale transactiekosten"));
});

test("(i) blijven bij de bestaande teksten", () => {
    const ind = bouw(RNA, null);
    const uitleg = Object.fromEntries(ind.rekenblok.flatMap(s => s.regels).map(r => [r.label, r.uitleg]));
    assert.match(uitleg["Netto gestort"], /stortingen min opnames/);
    assert.match(uitleg["Totale huidige waarde"], /zonder cash/);
    assert.match(uitleg["Totaal geïnvesteerd"], /Netto aankopen/);
    assert.match(uitleg["Totaal W/V (DEGIRO-definitie)"], /Totaal Account/);
});

test("kopcijfers: waarde en rendement (€ en %), rendement gekleurd", () => {
    const ind = bouw({ ...RNA, rendement_eur: -10, rendement_pct: -1 }, null);
    assert.deepEqual(labels(ind.kopcijfers), ["Totale huidige waarde", "Totaal rendement"]);
    const rendement = ind.kopcijfers[1];
    assert.deepEqual([rendement.bedrag, rendement.pct, rendement.klasse], [-10, -1, "negatief"]);
});

test("mobiel: hero met W/V en groepen Inleg, Opbrengst, Kosten", () => {
    const ind = bouw(RNA, AANSLUITING);
    assert.equal(ind.hero.waarde.bedrag, 14000);
    assert.equal(ind.hero.rendement.pct, 4.18);
    assert.equal(ind.hero.totaalDegiro.bedrag, 563.48);
    assert.deepEqual(ind.groepen.map(g => g.titel), ["Inleg", "Opbrengst", "Kosten"]);
    assert.deepEqual(labels(ind.groepen[0].regels), ["Totaal geïnvesteerd", "Netto gestort", "Vrije ruimte (cash)"]);
    assert.deepEqual(labels(ind.groepen[1].regels), ["Ontvangen dividend (netto)", "Hoogste rendement"]);
    assert.deepEqual(labels(ind.groepen[2].regels), ["Totale transactiekosten", "Aansluitingskosten"]);
});

test("mobiel: lege groepen vervallen; onbetrouwbaar kassaldo toont onbekend en geen W/V in de hero", () => {
        const kaal = { geinvesteerd: 100, waarde: 110, rendement_eur: 10, rendement_pct: 10 };
    assert.deepEqual(bouw(kaal, null).groepen.map(g => g.titel), ["Inleg"]);
    const ind = bouw({ ...RNA, kassaldo_eur: -1 }, null);
    assert.equal(ind.hero.totaalDegiro, null);
    assert.deepEqual(ind.groepen[0].regels.map(r => r.tekst), [null, "onbekend", "onbekend"]);
});

test("zonder W/V-waarde geen sectie 3 en geen W/V in de hero", () => {
    const ind = bouw({ ...RNA, totaal_degiro_eur: null }, null);
    assert.deepEqual(ids(ind.rekenblok), ["ingegaan", "opgeleverd"]);
    assert.equal(ind.hero.totaalDegiro, null);
});

test("regel-id's zijn uniek binnen een sectie of groep", () => {
    const ind = bouw(RNA, AANSLUITING);
    for (const s of ind.rekenblok) assert.equal(new Set(ids(s.regels)).size, s.regels.length, s.id);
    for (const g of ind.groepen) assert.equal(new Set(ids(g.regels)).size, g.regels.length, g.id);
});
