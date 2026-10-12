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
const sectie = (indeling, id) => indeling.rekenoverzicht.find(s => s.id === id);
const labels = lijst => lijst.map(r => r.label);

// Som van de regels van een sectie: + telt op, − trekt af (het eerste getal heeft geen teken).
function somTot(regels) {
    return regels.filter(r => !r.totaal).reduce((som, r) => som + (r.teken === "−" ? -r.bedrag : r.bedrag), 0);
}

test("rekenoverzicht: drie secties in volgorde, met tekens", () => {
    const ind = bouw(RNA, null);
    assert.deepEqual(ids(ind.rekenoverzicht), ["ingegaan", "opgeleverd", "degiro"]);
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

test("rekenoverzicht sluit: geïnvesteerd, rendement en W/V met de RNA-cijfers", () => {
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
        assert.deepEqual(ids(ind.rekenoverzicht), ["opgeleverd"]);
        const onbekend = ind.groepen[0].regels.filter(r => r.tekst === "onbekend");
        assert.deepEqual(labels(onbekend), ["Netto gestort", "Vrije ruimte (cash)"]);
        for (const r of onbekend) assert.match(r.uitleg, /Stortingen ontbreken/);
        assert.equal(ind.hero.totaalDegiro, null);
    }
});

test("zonder rekeningoverzicht: alleen sectie 2 en geen cash, netto gestort of dividend", () => {
    const totalen = { ...RNA, kassaldo_eur: null, netto_gestort_eur: null, dividend_netto: null,
                      overige_kassabewegingen_eur: null, totaal_degiro_eur: null };
    const ind = bouw(totalen, null);
    assert.deepEqual(ids(ind.rekenoverzicht), ["opgeleverd"]);
    assert.deepEqual(labels(ind.groepen.flatMap(g => g.regels)),
        ["Totaal geïnvesteerd", "Totale transactiekosten"]);
});

test("betrouwbaar kassaldo zonder overig-veld: geen opbouw, wel de losse cijfers", () => {
    const { overige_kassabewegingen_eur, ...totalen } = RNA;
    const ind = bouw(totalen, null);
    assert.deepEqual(ids(ind.rekenoverzicht), ["opgeleverd"]);
    assert.deepEqual(labels(ind.groepen.flatMap(g => g.regels)), [
        "Totaal geïnvesteerd", "Netto gestort", "Vrije ruimte (cash)", "Ontvangen dividend (netto)", "Totale transactiekosten",
    ]);
});

test("kosten: transactiekosten met korting-uitleg, aansluitingskosten alleen als aanwezig, geen losse hoogste-tegel", () => {
        const totalen = { ...RNA, totale_transactiekosten: 35, totale_transactiekosten_betaald: 42, transactiekosten_korting: 7 };
    const ind = bouw(totalen, AANSLUITING);
    const kosten = ind.groepen.find(g => g.id === "kosten").regels;
    assert.deepEqual(labels(kosten), ["Totale transactiekosten", "Aansluitingskosten"]);
    assert.equal(kosten[0].subtekst, "betaald 42,00 − promotie 7,00");
    assert.ok(!labels(bouw(RNA, null).groepen.flatMap(g => g.regels)).includes("Aansluitingskosten"));
});

test("zonder kostenkolom: notitie, geen transactiekostenregel", () => {
    const ind = bouw({ ...RNA, transactiekosten_beschikbaar: false }, null);
    assert.equal(ind.kostenNotitie, true);
    assert.ok(!labels(ind.groepen.flatMap(g => g.regels)).includes("Totale transactiekosten"));
});

test("(i) blijven bij de bestaande teksten", () => {
    const ind = bouw(RNA, null);
    const uitleg = Object.fromEntries(ind.rekenoverzicht.flatMap(s => s.regels).map(r => [r.label, r.uitleg]));
    assert.match(uitleg["Netto gestort"], /stortingen min opnames/);
    assert.match(uitleg["Totale huidige waarde"], /zonder cash/);
    assert.match(uitleg["Totaal geïnvesteerd"], /Netto aankopen/);
    assert.match(uitleg["Totaal W/V (DEGIRO-definitie)"], /Totaal Account/);
});

test("hero met W/V en groepen Inleg, Opbrengst, Kosten", () => {
    const ind = bouw(RNA, AANSLUITING);
    assert.equal(ind.hero.waarde.bedrag, 14000);
    assert.equal(ind.hero.rendement.pct, 4.18);
    assert.equal(ind.hero.totaalDegiro.bedrag, 563.48);
    assert.deepEqual(ind.groepen.map(g => g.titel), ["Inleg", "Opbrengst", "Kosten"]);
    assert.deepEqual(labels(ind.groepen[0].regels), ["Totaal geïnvesteerd", "Netto gestort", "Vrije ruimte (cash)"]);
    assert.deepEqual(labels(ind.groepen[1].regels), ["Ontvangen dividend (netto)"]);
    assert.deepEqual(labels(ind.groepen[2].regels), ["Totale transactiekosten", "Aansluitingskosten"]);
});

test("hoogsteRendementTekst: bedrag en datum, negatief, gelijk aan huidige, ontbrekend", () => {
    const { hoogsteRendementTekst } = laad();
    const tekst = (ath, extra) => hoogsteRendementTekst({ all_time_high: ath, ...extra });
    assert.equal(tekst({ waarde: 2773.8, datum: "06-10-2026" }), "Hoogste: EUR 2773.8 (06-10-2026)");
    assert.equal(tekst({ waarde: -50, datum: "01-02-2025" }), "Hoogste: EUR -50 (01-02-2025)");
    assert.equal(tekst({ waarde: 586.5, datum: "01-02-2025" }, { rendement_eur: 586.5 }), "Hoogste: EUR 586.5 (01-02-2025)");
    assert.equal(tekst({ waarde: 0, datum: "01-02-2025" }), "Hoogste: EUR 0 (01-02-2025)");
    for (const ath of [null, undefined, {}, { waarde: null, datum: "01-02-2025" }, { waarde: 5, datum: null }, { waarde: 5 }]) {
        assert.equal(tekst(ath), null);
    }
});

test("hoogste rendement: regel bij rendement in de hero, geen losse tegel", () => {
    const ind = bouw(RNA, AANSLUITING);
    const verwacht = "Hoogste: EUR 600 (01-02-2025)";
    assert.equal(ind.hero.rendement.subtekst, verwacht);
    assert.equal(ind.hero.waarde.subtekst, null);
    const alle = ind.groepen.flatMap(g => g.regels);
    assert.ok(!labels(alle).includes("Hoogste rendement"));
    const zonder = bouw({ ...RNA, all_time_high: null }, null);
    assert.equal(zonder.hero.rendement.subtekst, null);
});

test("lege groepen vervallen; onbetrouwbaar kassaldo toont onbekend en geen W/V in de hero", () => {
        const kaal = { geinvesteerd: 100, waarde: 110, rendement_eur: 10, rendement_pct: 10 };
    assert.deepEqual(bouw(kaal, null).groepen.map(g => g.titel), ["Inleg"]);
    const ind = bouw({ ...RNA, kassaldo_eur: -1 }, null);
    assert.equal(ind.hero.totaalDegiro, null);
    assert.deepEqual(ind.groepen[0].regels.map(r => r.tekst), [null, "onbekend", "onbekend"]);
});

test("zonder W/V-waarde geen sectie 3 en geen W/V in de hero", () => {
    const ind = bouw({ ...RNA, totaal_degiro_eur: null }, null);
    assert.deepEqual(ids(ind.rekenoverzicht), ["ingegaan", "opgeleverd"]);
    assert.equal(ind.hero.totaalDegiro, null);
});

test("regel-id's zijn uniek binnen een sectie of groep", () => {
    const ind = bouw(RNA, AANSLUITING);
    for (const s of ind.rekenoverzicht) assert.equal(new Set(ids(s.regels)).size, s.regels.length, s.id);
    for (const g of ind.groepen) assert.equal(new Set(ids(g.regels)).size, g.regels.length, g.id);
});

test("één indeling: hero en groepen hangen niet van het scherm af, het overzicht is een apart onderdeel", () => {
    const ind = bouw(RNA, AANSLUITING);
    assert.deepEqual(Object.keys(ind).sort(), ["groepen", "hero", "kassaldoBetrouwbaar", "kostenNotitie", "rekenoverzicht"]);
    assert.deepEqual(ids(ind.rekenoverzicht), ["ingegaan", "opgeleverd", "degiro"]);
});

test("knoptekst per stand", () => {
    const { berekeningKnopTekst } = laad();
    assert.equal(berekeningKnopTekst(false), "Bekijk berekening");
    assert.equal(berekeningKnopTekst(true), "Verberg berekening");
});

test("onbetrouwbaar kassaldo: overzicht met alleen sectie 2 (knop blijft)", () => {
    const ind = bouw({ ...RNA, kassaldo_eur: -5 }, null);
    assert.deepEqual(ids(ind.rekenoverzicht), ["opgeleverd"]);
});

test("hero: waarde, rendement met hoogste-regel en W/V", () => {
    const ind = bouw(RNA, null);
    assert.equal(ind.hero.waarde.bedrag, 14000);
    assert.equal(ind.hero.rendement.subtekst, "Hoogste: EUR 600 (01-02-2025)");
    assert.equal(ind.hero.totaalDegiro.bedrag, 563.48);
});

function maakDom() {
    const maak = () => {
        const el = { children: [], attrs: {}, listeners: {}, hidden: false, dataset: {}, className: "", textContent: "" };
        el.appendChild = c => { el.children.push(c); return c; };
        el.setAttribute = (k, v) => { el.attrs[k] = v; };
        el.addEventListener = (n, f) => { el.listeners[n] = f; };
        return el;
    };
    return maak;
}

function alleDelen(el) {
    return [el, ...el.children.flatMap(alleDelen)];
}

test("DOM: overzicht standaard dicht, knop wisselt tekst en aria-expanded, zonder overzicht geen knop", () => {
    const context = laad();
    context.document.createElement = maakDom();
    const heel = context.maakHomeSectie(RNA, null);
    const knop = alleDelen(heel).find(e => e.id === "home-berekening-knop");
    const overzicht = alleDelen(heel).find(e => e.id === "home-berekening-overzicht");
    assert.equal(overzicht.hidden, true);
    assert.deepEqual([knop.textContent, knop.attrs["aria-expanded"]], ["Bekijk berekening", "false"]);
    knop.listeners.click();
    assert.deepEqual([overzicht.hidden, knop.textContent, knop.attrs["aria-expanded"]], [false, "Verberg berekening", "true"]);
    knop.listeners.click();
    assert.equal(overzicht.hidden, true);

    context.bouwHomeIndeling = () => ({ rekenoverzicht: [], hero: { waarde: {}, rendement: {} }, groepen: [], kostenNotitie: false });
    context.formatteerEuro = () => "";
    const leeg = context.maakHomeSectie({}, null);
    assert.ok(!alleDelen(leeg).some(e => e.id === "home-berekening-knop"));
});
