// Unit tests voor static/js/vergelijkbare_etfs.js (fase B: vergelijkbare fondsen op een thuisbeurs). Verzonnen data.
//   node --test tests/test_vergelijkbare_etfs.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    VERGELIJK_GEEN_KANDIDATEN_TEKST, VERGELIJK_ONVOLLEDIG_TEKST, VERGELIJK_FOUT_TEKST,
    VERGELIJK_ONGEVAL_TEKST, VERGELIJK_TE_LANG_TEKST, vergelijkbaarPaneel, vergelijkFoutTekst, uitkeringsvormTekst,
} = require("../static/js/vergelijkbare_etfs.js");
const { alternatievenPaneel } = require("../static/js/beurs_alternatieven.js");

const BRON_TOP10 = [{ naam: "Apple", gewicht_pct: 4.5 }, { naam: "Shell", gewicht_pct: 1.25 }];

function alternatief(extra) {
    return {
        ticker: "XMWO.AS", naam: "Xtra World UCITS ETF", isin: "IE0000000020", beurs: "AMS", beurs_code: "EAM",
        beurs_naam: "Euronext Amsterdam", overlap_pct: 87.34, land_gelijkenis_pct: 91.2, sector_gelijkenis_pct: 80,
        aantal_holdings: 1523, uitkeringsvorm: "ACC", top10: [{ naam: "Apple", gewicht_pct: 4.1 }], ...extra,
    };
}

function antwoord(extra) {
    return {
        status: "ok", ticker: "IWDA.DE", naam: "World ETF", waarschuwing: null, fout: null, alternatieven: [alternatief()],
        aantal_kandidaten: 5, aantal_onderzocht: 3, onvolledig: false, min_overlap_pct: 70, kandidaten_via: "codes",
        bron: { aantal_holdings: 1600, top10: BRON_TOP10, uitkeringsvorm: "ACC" }, ...extra,
    };
}

test("kaart: naam, notering, overlap als hoofdgetal en de regels met percentages", () => {
    const [kaart] = vergelijkbaarPaneel(antwoord(), "World ETF").kaarten;
    assert.equal(kaart.titel, "Xtra World UCITS ETF");
    assert.equal(kaart.notering, "XMWO.AS op Euronext Amsterdam");
    assert.equal(kaart.hoofdgetal, "87,3%");
    assert.equal(kaart.hoofdLabel, "overlap");
    assert.deepEqual(kaart.regels, [
        { label: "Landen", tekst: "91,2% gelijk" },
        { label: "Sectoren", tekst: "80,0% gelijk" },
        { label: "Aantal holdings", tekst: "1.523" },
        { label: "Uitkeringsvorm", tekst: "accumulerend" },
    ]);
});

test("top-10 naast elkaar met bedrijf en gewicht", () => {
    const [kaart] = vergelijkbaarPaneel(antwoord(), "World ETF").kaarten;
    assert.deepEqual(kaart.top10.bron, ["Apple 4,50%", "Shell 1,25%"]);
    assert.deepEqual(kaart.top10.alternatief, ["Apple 4,10%"]);
});

test("onbekende sector en uitkeringsvorm worden 'onbekend'", () => {
    const [kaart] = vergelijkbaarPaneel(antwoord({ alternatieven: [alternatief({ sector_gelijkenis_pct: null, uitkeringsvorm: null })] }), "x").kaarten;
    assert.equal(kaart.regels[1].tekst, "onbekend");
    assert.equal(kaart.regels[3].tekst, "onbekend");
    assert.equal(uitkeringsvormTekst("DIS"), "uitkerend");
});

test("status geen_kandidaten: vaste tekst, geen kaarten", () => {
    const p = vergelijkbaarPaneel(antwoord({ status: "geen_kandidaten", alternatieven: [], aantal_kandidaten: 0 }), "x");
    assert.equal(p.melding, VERGELIJK_GEEN_KANDIDATEN_TEKST);
    assert.equal(VERGELIJK_GEEN_KANDIDATEN_TEKST, "Geen vergelijkbare fondsen automatisch gevonden.");
    assert.deepEqual(p.kaarten, []);
    assert.equal(p.telling, null);
});

test("status geen_alternatieven noemt de drempel en telt de kandidaten", () => {
    const p = vergelijkbaarPaneel(antwoord({ status: "geen_alternatieven", alternatieven: [] }), "x");
    assert.equal(p.melding, "Geen vergelijkbaar fonds op een thuisbeurs met minstens 70% overlap gevonden.");
    assert.equal(p.telling, "3 van 5 kandidaten hadden een notering op een thuisbeurs en volledige holdings.");
});

test("status fout toont de melding van de server, of een standaardtekst", () => {
    assert.equal(vergelijkbaarPaneel(antwoord({ status: "fout", fout: "Geen holdings." }), "x").melding, "Geen holdings.");
    assert.equal(vergelijkbaarPaneel(antwoord({ status: "fout", fout: null }), "x").melding, VERGELIJK_FOUT_TEKST);
});

test("waarschuwing bron_alleen_top10 met de naam van het fonds", () => {
    const p = vergelijkbaarPaneel(antwoord({ waarschuwing: "bron_alleen_top10" }), "World ETF");
    assert.equal(p.waarschuwing, "Van World ETF zijn alleen de 10 grootste posities bekend; de overlap is een onderschatting.");
    assert.equal(vergelijkbaarPaneel(antwoord(), "World ETF").waarschuwing, null);
});

test("onvolledig antwoord krijgt een opmerking", () => {
    assert.equal(vergelijkbaarPaneel(antwoord({ onvolledig: true }), "x").onvolledig, VERGELIJK_ONVOLLEDIG_TEKST);
    assert.equal(vergelijkbaarPaneel(antwoord(), "x").onvolledig, null);
});

test("fase B herhaalt de disclaimers niet: ze staan een keer onderaan het fase A-paneel", () => {
    const vergelijk = vergelijkbaarPaneel(antwoord(), "x");
    assert.equal(vergelijk.vasteRegels, undefined);
    assert.equal(JSON.stringify(vergelijk).includes("geen advies"), false);
    assert.equal(JSON.stringify(vergelijk).includes("Controleer in DEGIRO"), false);
});

test("onder_drempel: kopje met de drempel en kaarten met overlap, land en sector", () => {
    const eronder = [alternatief({ naam: "Net eronder", overlap_pct: 65.5 }), alternatief({ naam: "Verder eronder", overlap_pct: 40 })];
    const p = vergelijkbaarPaneel(antwoord({ status: "geen_alternatieven", alternatieven: [], onder_drempel: eronder }), "x");
    assert.equal(p.melding, "Geen vergelijkbaar fonds op een thuisbeurs met minstens 70% overlap gevonden.");
    assert.deepEqual(p.kaarten, []);
    assert.equal(p.onderDrempel.titel, "Minder vergelijkbaar (onder 70% overlap)");
    assert.deepEqual(p.onderDrempel.kaarten.map(k => k.hoofdgetal), ["65,5%", "40,0%"]);
    assert.equal(p.onderDrempel.kaarten[0].regels[0].label, "Landen");
    assert.equal(p.onderDrempel.kaarten[0].regels[1].label, "Sectoren");
});

test("zonder onder_drempel (leeg of afwezig) geen blok", () => {
    for (const extra of [{ onder_drempel: [] }, {}]) {
        const p = vergelijkbaarPaneel(antwoord({ status: "geen_alternatieven", alternatieven: [], ...extra }), "x");
        assert.equal(p.onderDrempel, null);
    }
    assert.equal(vergelijkbaarPaneel(antwoord(), "x").onderDrempel, null);
});

test("foutteksten per status", () => {
    assert.equal(vergelijkFoutTekst(500, { error: "x" }), VERGELIJK_ONGEVAL_TEKST);
    assert.equal(vergelijkFoutTekst(504, null), VERGELIJK_TE_LANG_TEKST);
    assert.equal(vergelijkFoutTekst(0, null), VERGELIJK_TE_LANG_TEKST);
    assert.equal(vergelijkFoutTekst(400, { error: "Alleen voor een ETF." }), "Alleen voor een ETF.");
    assert.equal(vergelijkFoutTekst(404, null), VERGELIJK_FOUT_TEKST);
});

test("fase A geeft de knop-gegevens per positie mee", () => {
    const voorkeur = { ticker: "IWDA.AS", beurs_naam: "Euronext Amsterdam" };
    const basis = {
        beurs: "TDG", beurs_naam: "Tradegate", jaarkosten_eur: 2.5, betaald_dit_jaar: false, blijft_betalen: false,
        besparing_per_jaar_eur: 2.5, overstapkosten_schatting_eur: 6, terugverdientijd_jaren: 2.4,
        posities: [
            { ticker: "IWDA.DE", naam: "World", echte_naam: "iShares World", isin: "IE00B4L5Y983", status: "gevonden", voorkeur,
              noteringen: [voorkeur], fase_b_mogelijk: true },
            { ticker: "AAPL", naam: "Apple", isin: null, status: "niet_gevonden", voorkeur: null, noteringen: [], fase_b_mogelijk: false },
        ],
    };
    const [etf, aandeel] = alternatievenPaneel(basis).posities;
    assert.deepEqual([etf.ticker, etf.isin, etf.echteNaam, etf.faseB, etf.alGevonden], ["IWDA.DE", "IE00B4L5Y983", "iShares World", true, true]);
    assert.deepEqual([aandeel.faseB, aandeel.alGevonden, aandeel.isin, aandeel.echteNaam], [false, false, null, null]);
});
