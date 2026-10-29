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
    assert.equal(tips["Totaal rendement (%)"], null);
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
    const container = ctx.maakTotalenSectie(MET_TOTAAL);
    const sub = tegelSubtekst(container);
    const tips = tegelInfoTip(container);
    assert.deepEqual(Object.keys(sub), [
        "Totaal geïnvesteerd",
        "Totale huidige waarde",
        "Totaal rendement (€)",
        "Totaal rendement (%)",
        "Totaal W/V (DEGIRO-definitie)",
        "Ontvangen dividend (netto)",
        "Vrije ruimte (cash)",
        "Netto gestort",
        "Hoogste rendement",
        "Totale transactiekosten",
    ]);
    const tegel = container.children[0].children.find(t => t.children[0].textContent === "Netto gestort");
    assert.equal(tegel.children[1].textContent, "EUR 13440");
    assert.match(tips["Netto gestort"], /stortingen min opnames/);
    for (const label of ["Ontvangen dividend (netto)", "Vrije ruimte (cash)",
                         "Hoogste rendement", "Totale transactiekosten"]) {
        assert.equal(tips[label], null, label);
    }
    assert.match(tips["Totale huidige waarde"], /zonder cash.*Tradegate/);
    assert.match(tips["Totaal W/V (DEGIRO-definitie)"], /Totaal Account/);
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

const TOTAAL_LABEL = "Totaal W/V (DEGIRO-definitie)";
const MET_TOTAAL = { ...TOTALEN, totaal_degiro_eur: 2691.36 };

function waardeVan(container, label) {
    return container.children[0].children.find(t => t.children[0].textContent === label).children[1].textContent;
}

test("Totaal W/V (DEGIRO): getoond met betrouwbaar kassaldo, direct na Totaal rendement (%)", () => {
    const ctx = laadTegels();
    const container = ctx.maakTotalenSectie(MET_TOTAAL);
    const labels = Object.keys(tegelSubtekst(container));
    assert.equal(labels[labels.indexOf("Totaal rendement (%)") + 1], TOTAAL_LABEL);
    assert.equal(waardeVan(container, TOTAAL_LABEL), "EUR 2691.36");
});

test("Totaal W/V (DEGIRO): verborgen zonder rekeningoverzicht, bij netto gestort 0 met transacties en bij negatief saldo", () => {
    const ctx = laadTegels();
    const onbetrouwbaar = [
        { ...MET_TOTAAL, kassaldo_eur: null, netto_gestort_eur: null, totaal_degiro_eur: null },
        { ...MET_TOTAAL, netto_gestort_eur: 0 },
        { ...MET_TOTAAL, netto_gestort_eur: null },
        { ...MET_TOTAAL, kassaldo_eur: -3389.75 },
    ];
    for (const totalen of onbetrouwbaar) {
        assert.ok(!Object.keys(tegelSubtekst(ctx.maakTotalenSectie(totalen))).includes(TOTAAL_LABEL));
    }
});

test("Onbetrouwbaar kassaldo: cash en netto gestort tonen 'onbekend' met (i), geen bedrag", () => {
    const ctx = laadTegels();
    const totalen = { ...MET_TOTAAL, netto_gestort_eur: 0, kassaldo_eur: -3389.75 };
    const container = ctx.maakTotalenSectie(totalen);
    const tips = tegelInfoTip(container);
    for (const label of ["Vrije ruimte (cash)", "Netto gestort"]) {
        assert.equal(waardeVan(container, label), "onbekend");
        assert.match(tips[label], /Stortingen ontbreken/);
    }
});

test("Netto gestort van 0 zonder transacties blijft een betrouwbaar kassaldo", () => {
    const ctx = laadTegels();
    const totalen = { ...MET_TOTAAL, geinvesteerd: 0, waarde: 0, netto_gestort_eur: 0 };
    const container = ctx.maakTotalenSectie(totalen);
    assert.equal(waardeVan(container, "Netto gestort"), "EUR 0");
    assert.ok(Object.keys(tegelSubtekst(container)).includes(TOTAAL_LABEL));
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
    per_beurs: [{ code: "XET", naam: "Xetra", verdeling_naam: "Xetra",
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

test("kassaldoIsBetrouwbaar: rekeningoverzicht_status.volledig wint van de heuristiek", () => {
    const ctx = laadTegels();
    assert.equal(ctx.kassaldoIsBetrouwbaar({ ...TOTALEN, rekeningoverzicht_status: { volledig: false } }), false);
    // Heuristiek zou onbetrouwbaar zeggen (netto gestort 0 met transacties); de backend zegt volledig.
    assert.equal(ctx.kassaldoIsBetrouwbaar({ ...TOTALEN, netto_gestort_eur: 0, rekeningoverzicht_status: { volledig: true } }), true);
});

test("kassaldoIsBetrouwbaar: zonder status-veld blijft de heuristiek als vangnet", () => {
    const ctx = laadTegels();
    assert.equal(ctx.kassaldoIsBetrouwbaar(TOTALEN), true);
    assert.equal(ctx.kassaldoIsBetrouwbaar({ ...TOTALEN, netto_gestort_eur: 0 }), false);
    assert.equal(ctx.kassaldoIsBetrouwbaar({ ...TOTALEN, kassaldo_eur: -1 }), false);
    assert.equal(ctx.kassaldoIsBetrouwbaar({ ...TOTALEN, rekeningoverzicht_status: null, netto_gestort_eur: 0 }), false);
});

test("onvolledig overzicht: cash en netto gestort tonen 'onbekend', geen Totaal W/V", () => {
    const ctx = laadTegels();
    const totalen = { ...TOTALEN, totaal_degiro_eur: 10, rekeningoverzicht_status: { volledig: false } };
    const rij = ctx.maakTotalenSectie(totalen).children[0];
    const waarden = Object.fromEntries(rij.children.map(t => [t.children[0].textContent, t.children[1].textContent]));
    assert.equal(waarden["Vrije ruimte (cash)"], "onbekend");
    assert.equal(waarden["Netto gestort"], "onbekend");
    assert.ok(!("Totaal W/V (DEGIRO-definitie)" in waarden));
});
