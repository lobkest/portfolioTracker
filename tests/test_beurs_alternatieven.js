// Unit tests voor static/js/beurs_alternatieven.js (paneel "Alternatieven op een thuisbeurs"). Verzonnen data.
//   node --test tests/test_beurs_alternatieven.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    KNOP_TEKST, VASTE_REGELS, DIT_JAAR_AL_BETAALD, FOUT_TEKST, ONGEVAL_TEKST, TE_LANG_TEKST, NIET_OP_ISIN_TEKST, alternatievenPaneel,
    alternatievenFoutTekst,
} = require("../static/js/beurs_alternatieven.js");

const VOORKEUR = { ticker: "IWDA.AS", beurs: "AMS", beurs_code: "EAM", beurs_naam: "Euronext Amsterdam", koers_eur: 100, afwijking_pct: 0.5 };

function positie(extra) {
    return {
        ticker: "IWDA.DE", naam: "World ETF", isin: "IE00B4L5Y983", status: "gevonden", voorkeur: VOORKEUR,
        noteringen: [VOORKEUR], waarschuwing: null, fout: null, al_in_bezit_op_thuisbeurs: false, fase_b_mogelijk: false,
        ...extra,
    };
}

function antwoord(extra) {
    return {
        beurs: "TDG", beurs_naam: "Tradegate", jaarkosten_eur: 2.5, betaald_dit_jaar: false, posities: [positie()],
        blijft_betalen: false, besparing_per_jaar_eur: 2.5, overstapkosten_schatting_eur: 6, terugverdientijd_jaren: 2.4,
        ...extra,
    };
}

test("samenvatting met beurs en jaarbedrag uit de API", () => {
    assert.equal(alternatievenPaneel(antwoord()).samenvatting, "Zolang er een positie op Tradegate staat, betaal je elk jaar €2,50.");
    assert.equal(alternatievenPaneel(antwoord({ jaarkosten_eur: null })).samenvatting,
                 "Zolang er een positie op Tradegate staat, betaal je elk jaar aansluitingskosten.");
});

test("status gevonden: ticker en thuisbeurs", () => {
    const [p] = alternatievenPaneel(antwoord()).posities;
    assert.equal(p.naam, "World ETF");
    assert.equal(p.tekst, "Gevonden op een thuisbeurs: IWDA.AS op Euronext Amsterdam.");
    assert.equal(p.waarschuwing, null);
});

test("notering alleen op naam gevonden: waarschuwing met controle-advies over de ISIN", () => {
    const opNaam = { ...VOORKEUR, waarschuwing: "niet_op_isin_bevestigd" };
    const [p] = alternatievenPaneel(antwoord({ posities: [positie({ voorkeur: opNaam, noteringen: [opNaam] })] })).posities;
    assert.equal(p.waarschuwing, NIET_OP_ISIN_TEKST);
    assert.equal(NIET_OP_ISIN_TEKST,
                 "Gevonden op naam, niet op ISIN. Controleer in DEGIRO of de ISIN gelijk is aan die van je positie (bijv. niet Acc i.p.v. Dist).");
    assert.equal(alternatievenPaneel(antwoord()).posities[0].waarschuwing, null);
});

test("status niet gevonden", () => {
    const [p] = alternatievenPaneel(antwoord({ posities: [positie({ status: "niet_gevonden", voorkeur: null, noteringen: [] })] })).posities;
    assert.equal(p.tekst, "Niet gevonden op een thuisbeurs.");
});

test("status waarschuwing: koers wijkt af, met percentage", () => {
    const afwijkend = { ...VOORKEUR, afwijking_pct: 6, waarschuwing: "koers wijkt af" };
    const [p] = alternatievenPaneel(antwoord({ posities: [positie({
        status: "waarschuwing", voorkeur: null, noteringen: [afwijkend], waarschuwing: "koers wijkt af" })] })).posities;
    assert.match(p.tekst, /IWDA\.AS op Euronext Amsterdam/);
    assert.match(p.waarschuwing, /koers wijkt af \(\+6,0%\).*niet gekozen/);
});

test("status waarschuwing: geen koers om te vergelijken", () => {
    const zonder = { ...VOORKEUR, koers_eur: null, afwijking_pct: null };
    const [p] = alternatievenPaneel(antwoord({ posities: [positie({
        status: "waarschuwing", voorkeur: null, noteringen: [zonder], waarschuwing: "geen koers om mee te vergelijken" })] })).posities;
    assert.match(p.waarschuwing, /niet vergeleken/);
});

test("status fout: de tekst van de server", () => {
    const [p] = alternatievenPaneel(antwoord({ posities: [positie({ status: "fout", voorkeur: null, fout: "Yahoo kapot." })] })).posities;
    assert.equal(p.tekst, "Yahoo kapot.");
});

test("al in bezit op een thuisbeurs", () => {
    const [p] = alternatievenPaneel(antwoord({ posities: [positie({ al_in_bezit_op_thuisbeurs: true })] })).posities;
    assert.equal(p.extra, "Je hebt dit product al op een thuisbeurs.");
    assert.equal(alternatievenPaneel(antwoord()).posities[0].extra, null);
});

test("regels per beurs: besparing, overstapkosten met toelichting, terugverdientijd", () => {
    const regels = Object.fromEntries(alternatievenPaneel(antwoord()).regels.map(r => [r.label, r.tekst]));
    assert.equal(regels["Besparing per jaar"], "€2,50");
    assert.equal(regels["Geschatte overstapkosten"], "€6,00 (schatting op basis van je eigen transactiekosten)");
    assert.equal(regels["Terugverdientijd"], "2,4 jaar");
});

test("onbekend bij None", () => {
    const regels = Object.fromEntries(alternatievenPaneel(antwoord({
        besparing_per_jaar_eur: null, overstapkosten_schatting_eur: null, terugverdientijd_jaren: null,
    })).regels.map(r => [r.label, r.tekst]));
    assert.deepEqual(regels, { "Besparing per jaar": "onbekend", "Geschatte overstapkosten": "onbekend", "Terugverdientijd": "onbekend" });
});

test("blijft betalen: regel en geen terugverdientijd", () => {
    const paneel = alternatievenPaneel(antwoord({ blijft_betalen: true, besparing_per_jaar_eur: 0, terugverdientijd_jaren: null }));
    assert.equal(paneel.blijftBetalen,
                 "Niet alle posities hebben een alternatief op een thuisbeurs; je blijft dan €2,50 per jaar betalen.");
    assert.equal(paneel.regels[2].tekst, "niet van toepassing");
    assert.equal(alternatievenPaneel(antwoord()).blijftBetalen, null);
});

test("dit jaar al betaald en de vaste regels", () => {
    assert.equal(alternatievenPaneel(antwoord({ betaald_dit_jaar: true })).ditJaarAlBetaald, DIT_JAAR_AL_BETAALD);
    assert.match(DIT_JAAR_AL_BETAALD, /vanaf het jaar na je laatste transactie of positie op deze beurs betaal je niet meer/);
    assert.equal(alternatievenPaneel(antwoord()).ditJaarAlBetaald, null);
    assert.deepEqual(alternatievenPaneel(antwoord()).vasteRegels, VASTE_REGELS);
    assert.equal(VASTE_REGELS.length, 1);
    assert.equal(VASTE_REGELS[0], "Dit is geen advies. Controleer in DEGIRO of het product op deze beurs te koop is; DEGIRO biedt niet elke notering aan. "
        + "Een vergelijkbaar fonds is niet hetzelfde: index, kosten en risico kunnen verschillen.");
    assert.equal(KNOP_TEKST, "Alternatieven op een thuisbeurs");
});

test("foutteksten per soort fout", () => {
    assert.equal(alternatievenFoutTekst(400, { error: "Geef een geldige beurscode." }), "Geef een geldige beurscode.");
    assert.equal(alternatievenFoutTekst(500, { error: "Alternatieven opzoeken is mislukt." }), ONGEVAL_TEKST);
    assert.equal(ONGEVAL_TEKST, "Er ging iets mis bij het opzoeken van alternatieven.");
    assert.equal(alternatievenFoutTekst(500, null), ONGEVAL_TEKST);
    for (const status of [504, 0]) assert.equal(alternatievenFoutTekst(status, null), TE_LANG_TEKST);
    assert.match(TE_LANG_TEKST, /duurde te lang/);
    assert.equal(alternatievenFoutTekst(404, null), FOUT_TEKST);
    assert.equal(alternatievenFoutTekst(404, { error: "Geen portfolio." }), "Geen portfolio.");
});
