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
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    return context;
}

// label -> subtekst (null zonder subtekst)
function tegelUitleg(container) {
    const rij = container.children[0];
    return Object.fromEntries(rij.children.map(tegel => [
        tegel.children[0].textContent,
        tegel.children[2] ? tegel.children[2].textContent : null,
    ]));
}

const TOTALEN = {
    geinvesteerd: 1000,
    waarde: 1200,
    rendement_eur: 200,
    rendement_pct: 20,
    dividend_netto: 15,
    kassaldo_eur: 50,
    all_time_high: { waarde: 250, datum: "01-02-2025" },
    transactiekosten_beschikbaar: true,
    totale_transactiekosten: 7,
};

test("maakTotalenSectie: geïnvesteerd en rendement krijgen een uitleg", () => {
    const ctx = laadTegels();
    const uitleg = tegelUitleg(ctx.maakTotalenSectie(TOTALEN));
    assert.match(uitleg["Totaal geïnvesteerd"], /Niet gelijk aan het gestorte bedrag/);
    assert.match(uitleg["Totaal rendement (€)"], /Excl\. dividend en cash/);
    assert.equal(uitleg["Totaal rendement (%)"], uitleg["Totaal rendement (€)"]);
});

test("maakTotalenSectie: andere tegels ongewijzigd, volgorde gelijk", () => {
    const ctx = laadTegels();
    const uitleg = tegelUitleg(ctx.maakTotalenSectie(TOTALEN));
    assert.deepEqual(Object.keys(uitleg), [
        "Totaal geïnvesteerd",
        "Totale huidige waarde",
        "Totaal rendement (€)",
        "Totaal rendement (%)",
        "Ontvangen dividend (netto)",
        "Vrije ruimte (cash)",
        "Hoogste rendement",
        "Totale transactiekosten",
    ]);
    assert.equal(uitleg["Totale huidige waarde"], null);
    assert.equal(uitleg["Ontvangen dividend (netto)"], null);
    assert.equal(uitleg["Vrije ruimte (cash)"], null);
    assert.equal(uitleg["Hoogste rendement"], "01-02-2025");
    assert.equal(uitleg["Totale transactiekosten"], null);
});

test("maakTotalenSectie: uitleg staat in de subtekst-klasse", () => {
    const ctx = laadTegels();
    const tegel = ctx.maakTotalenSectie(TOTALEN).children[0].children[0];
    assert.equal(tegel.children[2].className, "kleineMelding");
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
        assert.equal(tegelUitleg(ctx.maakTotalenSectie(totalen))["Totale transactiekosten"], null);
    }
});
