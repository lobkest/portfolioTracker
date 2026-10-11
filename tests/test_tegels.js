// Unit tests voor maakTotalenSectie() (static/js/gedeeld/tegels.js); document en opmaakfuncties zijn gestubd.
//   node --test tests/test_tegels.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function maakElement() {
    return {
        children: [],
        className: "",
        textContent: "",
        dataset: {},
        appendChild(kind) { this.children.push(kind); return kind; },
    };
}

function laadTegels() {
    const code = fs.readFileSync(path.join(__dirname, "..", "static", "js", "gedeeld", "tegels.js"), "utf8");
    const context = {
        document: { createElement: maakElement },
        formatteerEuro: x => `EUR ${x}`,
        formatGetal: (x, d) => x.toFixed(d).replace(".", ","),
        formatPct: x => `${x}%`,
        formatDatum: x => x,
        klasseVoorRendement: () => "positief",
        aansluitingskostenTegel: require("../static/js/aansluitingskosten.js").aansluitingskostenTegel,
        geactiveerd: [],
    };
    context.activeerInfoTip = tip => context.geactiveerd.push(tip);
    vm.createContext(context);
    vm.runInContext(code, context);
    return context;
}

// label -> subtekst onder de waarde (null zonder subtekst)
function tegelSubtekst(container) {
    const rij = container.children[0];
    return Object.fromEntries(rij.children.map(tegel => [
        tegel.children[0].textContent,
        tegel.children[2] ? tegel.children[2].textContent : null,
    ]));
}

// label -> tekst van de (i) naast het label (null zonder)
function tegelInfoTip(container) {
    const rij = container.children[0];
    return Object.fromEntries(rij.children.map(tegel => {
        const tip = tegel.children[0].children.find(k => k.className === "infoTip");
        return [tegel.children[0].textContent, tip ? tip.textContent : null];
    }));
}

const TOTALEN = {
    geinvesteerd: 1000,
    waarde: 1200,
    rendement_eur: 200,
    rendement_pct: 20,
    dividend_netto: 15,
    kassaldo_eur: 50,
    netto_gestort_eur: 13440,
    all_time_high: { waarde: 250, datum: "01-02-2025" },
    transactiekosten_beschikbaar: true,
    totale_transactiekosten: 7,
};

test("maakTotalenSectie: uitleg van geïnvesteerd en rendement staat achter een (i), niet als subtekst", () => {
    const ctx = laadTegels();
    const container = ctx.maakTotalenSectie(TOTALEN);
    const tips = tegelInfoTip(container);
    const sub = tegelSubtekst(container);
    assert.match(tips["Totaal geïnvesteerd"], /Niet gelijk aan het gestorte bedrag/);
    assert.match(tips["Totaal rendement (€)"], /Excl\. dividend en cash/);
    assert.equal(tips["Totaal rendement (%)"], tips["Totaal rendement (€)"]);
    for (const label of ["Totaal geïnvesteerd", "Totaal rendement (€)", "Totaal rendement (%)"]) {
        assert.equal(sub[label], null, label);
    }
});

test("maakTotalenSectie: (i) wordt geactiveerd en heeft het label als naam", () => {
    const ctx = laadTegels();
    const tegel = ctx.maakTotalenSectie(TOTALEN).children[0].children[0];
    const tip = tegel.children[0].children[0];
    assert.equal(tegel.children[0].className, "kleinLabel statTegelLabel");
    assert.equal(tip.dataset.label, "Totaal geïnvesteerd");
    assert.ok(ctx.geactiveerd.includes(tip));
});

test("maakTotalenSectie: Netto gestort met (i), direct na Vrije ruimte (cash); andere tegels ongewijzigd", () => {
    const ctx = laadTegels();
    const container = ctx.maakTotalenSectie(TOTALEN);
    const sub = tegelSubtekst(container);
    const tips = tegelInfoTip(container);
    assert.deepEqual(Object.keys(sub), [
        "Totaal geïnvesteerd",
        "Totale huidige waarde",
        "Totaal rendement (€)",
        "Totaal rendement (%)",
        "Ontvangen dividend (netto)",
        "Vrije ruimte (cash)",
        "Netto gestort",
        "Hoogste rendement",
        "Totale transactiekosten",
    ]);
    const tegel = container.children[0].children.find(t => t.children[0].textContent === "Netto gestort");
    assert.equal(tegel.children[1].textContent, "EUR 13440");
    assert.match(tips["Netto gestort"], /stortingen min opnames/);
    for (const label of ["Totale huidige waarde", "Ontvangen dividend (netto)", "Vrije ruimte (cash)",
                         "Hoogste rendement", "Totale transactiekosten"]) {
        assert.equal(tips[label], null, label);
    }
    assert.equal(sub["Totale huidige waarde"], null);
    assert.equal(sub["Ontvangen dividend (netto)"], null);
    assert.equal(sub["Vrije ruimte (cash)"], null);
    assert.equal(sub["Netto gestort"], null);
    assert.equal(sub["Hoogste rendement"], "01-02-2025");
    assert.equal(sub["Totale transactiekosten"], null);
});

test("maakTotalenSectie: zonder kassaldo geen Netto gestort (ook niet als €0,00)", () => {
    const ctx = laadTegels();
    for (const waarde of [null, undefined]) {
        const totalen = { ...TOTALEN, kassaldo_eur: null, netto_gestort_eur: waarde };
        const labels = Object.keys(tegelSubtekst(ctx.maakTotalenSectie(totalen)));
        assert.ok(!labels.includes("Netto gestort"));
        assert.ok(!labels.includes("Vrije ruimte (cash)"));
    }
});

test("maakTotalenSectie: Netto gestort van 0 wordt wel getoond (kassaldo bekend)", () => {
    const ctx = laadTegels();
    const labels = Object.keys(tegelSubtekst(ctx.maakTotalenSectie({ ...TOTALEN, netto_gestort_eur: 0 })));
    assert.ok(labels.includes("Netto gestort"));
});

test("maakTotalenSectie: transactiekosten met promotiekorting tonen netto met betaald en promotie", () => {
    const ctx = laadTegels();
    const totalen = { ...TOTALEN, totale_transactiekosten: 35, totale_transactiekosten_betaald: 42, transactiekosten_korting: 7 };
    const rij = ctx.maakTotalenSectie(totalen).children[0];
    const tegel = rij.children.find(t => t.children[0].textContent === "Totale transactiekosten");
    assert.equal(tegel.children[1].textContent, "EUR 35");
    assert.equal(tegel.children[2].textContent, "betaald 42,00 − promotie 7,00");
});

test("maakTotalenSectie: transactiekosten zonder korting (0 of veld ontbreekt) zonder subtekst", () => {
    const ctx = laadTegels();
    for (const totalen of [TOTALEN, { ...TOTALEN, totale_transactiekosten_betaald: 7, transactiekosten_korting: 0 }]) {
        assert.equal(tegelSubtekst(ctx.maakTotalenSectie(totalen))["Totale transactiekosten"], null);
    }
});

const AANSLUITINGSKOSTEN = {
    totaal_eur: 7.5, aantal_regels: 3, per_jaar: [{ jaar: 2025, bedrag_eur: 7.5 }],
    per_beurs: [{ code: "XET", naam: "Xetra", verdeling_naam: "Xetra", verdeling_naam_euronext: "Xetra",
                  totaal_eur: 7.5, aantal_regels: 3, per_jaar: [{ jaar: 2025, bedrag_eur: 7.5 }] }],
};

test("maakTotalenSectie: Aansluitingskosten direct na Totale transactiekosten, met (i)", () => {
    const ctx = laadTegels();
    const container = ctx.maakTotalenSectie(TOTALEN, AANSLUITINGSKOSTEN);
    const labels = Object.keys(tegelSubtekst(container));
    assert.deepEqual(labels.slice(-2), ["Totale transactiekosten", "Aansluitingskosten"]);
    const tegel = container.children[0].children.at(-1);
    assert.equal(tegel.children[1].textContent, "€7,50");
    assert.match(tegelInfoTip(container)["Aansluitingskosten"], /Niet meegeteld in transactiekosten of rendement/);
});

test("maakTotalenSectie: Totale transactiekosten blijft gelijk met aansluitingskosten", () => {
    const ctx = laadTegels();
    const met = ctx.maakTotalenSectie(TOTALEN, AANSLUITINGSKOSTEN).children[0].children;
    const zonder = ctx.maakTotalenSectie(TOTALEN).children[0].children;
    const kosten = tegels => tegels.find(t => t.children[0].textContent === "Totale transactiekosten").children[1].textContent;
    assert.equal(kosten(met), kosten(zonder));
});

test("maakTotalenSectie: geen Aansluitingskosten zonder veld of zonder kosten (ook niet als €0,00)", () => {
    const ctx = laadTegels();
    const leeg = { totaal_eur: 0, aantal_regels: 0, per_jaar: [], per_beurs: [] };
    for (const samenvatting of [undefined, null, leeg]) {
        const labels = Object.keys(tegelSubtekst(ctx.maakTotalenSectie(TOTALEN, samenvatting)));
        assert.ok(!labels.includes("Aansluitingskosten"));
    }
});
