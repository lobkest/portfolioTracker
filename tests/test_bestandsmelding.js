// Unit tests voor de ene melding over rekeningoverzicht en transactiebestand (static/js/bestandsmelding.js).
//   node --test tests/test_bestandsmelding.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { bestandsmelding, bestandNiveaus } = require("../static/js/bestandsmelding.js");

const REKENING = {
    volledig: false,
    redenen: ["beginsaldo_niet_nul", "gestort_kleiner_dan_geinvesteerd", "saldo_negatief"],
    redenen_tekst: ["tekst-a", "tekst-b", "tekst-c"],
    eerste_regel_datum: "2026-09-14",
    laatste_regel_datum: "2026-10-10",
    eerste_transactie_datum: "2026-09-14",
    beginsaldo_eur: 1234.5,
    netto_gestort_eur: 500,
    geinvesteerd_eur: 2000,
    saldo_eur: -3389.75,
};
const REKENING_VOLLEDIG = { ...REKENING, volledig: true, redenen: [], redenen_tekst: [] };

const TRANSACTIES = {
    volledig: true,
    controle: "niet_te_controleren",
    redenen: [],
    eerste_transactie_datum: "2026-09-14",
    laatste_transactie_datum: "2026-10-10",
};
const AANWIJZING = {
    ...TRANSACTIES,
    volledig: false,
    controle: "aanwijzingen",
    redenen: [{ code: "negatief_aantal", tekst: "Er is een verkoop van ACME op 12-03-2025 zonder voldoende eerdere aankopen; eerdere transacties lijken te ontbreken." }],
};

test("volledig overzicht en geen aanwijzingen: geen melding", () => {
    assert.equal(bestandsmelding(REKENING_VOLLEDIG, { ...TRANSACTIES, controle: "geen_aanwijzingen" }, true), null);
    assert.equal(bestandsmelding(undefined, undefined, true), null);
    assert.equal(bestandsmelding(null, null, false), null);
});

test("volledig overzicht en 'niet te controleren' zonder onvolledig overzicht: geen melding", () => {
    assert.equal(bestandsmelding(REKENING_VOLLEDIG, TRANSACTIES, true), null);
});

test("één banner voor beide bestanden, met een blok per bestand en datums als dd-mm-jjjj", () => {
    const m = bestandsmelding(REKENING, AANWIJZING, true);
    assert.equal(m.kop, "Je bestanden beslaan mogelijk niet de hele periode.");
    assert.match(m.gevolg, /Netto gestort, vrije ruimte \(cash\) en Totaal W\/V/);
    assert.match(m.gevolg, /Posities, GAK en rendement/);
    assert.equal(m.blokken.length, 2);
    assert.equal(m.blokken[0].periode, "14-09-2026 t/m 10-10-2026");
    assert.equal(m.blokken[1].periode, "14-09-2026 t/m 10-10-2026");
});

test("rekeningredenen met bedragen", () => {
    const [rekening] = bestandsmelding(REKENING, TRANSACTIES, true).blokken;
    assert.deepEqual(rekening.redenen, [
        "Het overzicht begint met een saldo van €1.234,50; er stond dus al geld op de rekening vóór 14-09-2026.",
        "Netto gestort is €500,00, terwijl €2.000,00 is geïnvesteerd.",
        "Het kassaldo is −€3.389,75; dat kan bij DEGIRO niet.",
    ]);
});

test("zonder bedragen valt een reden terug op de backend-tekst", () => {
    const kaal = { volledig: false, redenen: ["saldo_negatief", "begint_na_eerste_transactie"], redenen_tekst: ["neg", "laat"] };
    assert.deepEqual(bestandsmelding(kaal, null, true).blokken[0].redenen, ["neg", "laat"]);
});

test("transactiebestand: aanwijzingen tonen de redenen uit de backend", () => {
    const [, transacties] = bestandsmelding(REKENING_VOLLEDIG, AANWIJZING, true).blokken;
    assert.deepEqual(transacties.redenen, [AANWIJZING.redenen[0].tekst]);
    assert.equal(transacties.tekst, null);
});

test("transactiebestand: niet te controleren, tweede zin alleen bij beginsaldo of te laag gestort", () => {
    const met = bestandsmelding(REKENING, TRANSACTIES, true).blokken[1].tekst;
    assert.match(met, /niet vast te stellen omdat het rekeningoverzicht onvolledig is/);
    assert.match(met, /waarschijnlijk ook eerdere transacties/);
    const zonder = bestandsmelding({ ...REKENING, redenen: ["saldo_negatief"], redenen_tekst: ["x"] }, TRANSACTIES, true).blokken[1].tekst;
    assert.match(zonder, /niet vast te stellen/);
    assert.doesNotMatch(zonder, /eerdere transacties/);
});

test("transactiebestand: geen aanwijzingen wordt genoemd als de banner er al is", () => {
    const m = bestandsmelding(REKENING, { ...TRANSACTIES, controle: "geen_aanwijzingen" }, true);
    assert.equal(m.blokken[1].tekst, "Geen aanwijzingen dat er transacties ontbreken.");
});

test("alleen transactiebestand met aanwijzingen (overzicht volledig)", () => {
    const m = bestandsmelding(REKENING_VOLLEDIG, AANWIJZING, true);
    assert.equal(m.kop, "Je bestanden beslaan mogelijk niet de hele periode.");
    assert.equal(m.gevolg, "Posities, GAK en rendement kunnen daardoor niet kloppen.");
    assert.deepEqual(m.blokken[0].redenen, []);
});

test("alleen rekeningoverzicht onvolledig, zonder transactiestatus: de bestaande kop", () => {
    const m = bestandsmelding(REKENING, null, true);
    assert.match(m.kop, /Je rekeningoverzicht \(Account\.xlsx\) beslaat niet de hele periode/);
    assert.equal(m.blokken.length, 1);
});

test("zonder rekeningoverzicht: één regel in plaats van een blok met redenen", () => {
    const m = bestandsmelding(null, AANWIJZING, true);
    assert.equal(m.blokken[0].tekst, "Geen rekeningoverzicht geüpload.");
    assert.deepEqual(m.blokken[0].redenen, []);
});

test("met code: link naar Instellingen", () => {
    const m = bestandsmelding(REKENING, TRANSACTIES, true);
    assert.equal(m.linkTekst, "Upload de volledige bestanden");
    assert.equal(m.linkNaarInstellingen, true);
});

test("niet opslaan: alleen tekst, geen link", () => {
    const m = bestandsmelding(REKENING, TRANSACTIES, false);
    assert.equal(m.linkTekst, "Upload bij de volgende analyse de volledige bestanden");
    assert.equal(m.linkNaarInstellingen, false);
});

test("geen sluit- of opslaglogica meer in de melding", () => {
    const root = path.join(__dirname, "..");
    const bronnen = ["static/js/bestandsmelding.js", "static/js/tabs/portfolio.js", "templates/portfolio.html"]
        .map(p => fs.readFileSync(path.join(root, p), "utf8")).join("\n");
    assert.doesNotMatch(bronnen, /bestandsmeldingGesloten|bannerSluit|Melding sluiten/);
    assert.doesNotMatch(fs.readFileSync(path.join(root, "static/js/tabs/portfolio.js"), "utf8"), /sessionStorage/);
});

test("bestandNiveaus: alleen rekeningoverzicht onvolledig", () => {
    const { velden, eerste } = bestandNiveaus(REKENING, { ...TRANSACTIES, controle: "geen_aanwijzingen" });
    assert.equal(velden.bestand2.niveau, "opnieuw");
    assert.equal(velden.bestand2.label, "Opnieuw uploaden");
    assert.equal(velden.bestand2.tekst, "Dit overzicht begint op 14-09-2026; upload het volledige bestand.");
    assert.equal(velden.bestand2.periode, "14-09-2026 t/m 10-10-2026");
    assert.equal(velden.bestand1.niveau, "geen");
    assert.equal(eerste, "bestand2");
});

test("bestandNiveaus: alleen transactiebestand met aanwijzingen", () => {
    const { velden, eerste } = bestandNiveaus(REKENING_VOLLEDIG, AANWIJZING);
    assert.equal(velden.bestand1.niveau, "opnieuw");
    assert.match(velden.bestand1.tekst, /transacties te ontbreken/);
    assert.equal(velden.bestand1.periode, "14-09-2026 t/m 10-10-2026");
    assert.equal(velden.bestand2.niveau, "geen");
    assert.equal(eerste, "bestand1");
});

test("bestandNiveaus: transactiebestand niet te controleren is aanbevolen, met de zin uit de melding", () => {
    const { velden, eerste } = bestandNiveaus(REKENING, TRANSACTIES);
    assert.equal(velden.bestand1.niveau, "aanbevolen");
    assert.equal(velden.bestand1.label, "Aanbevolen");
    assert.match(velden.bestand1.tekst, /Of dit bestand compleet is, is niet vast te stellen/);
    assert.match(velden.bestand1.tekst, /waarschijnlijk ook eerdere transacties/);
    assert.equal(velden.bestand2.niveau, "opnieuw");
    assert.equal(eerste, "bestand2");
});

test("bestandNiveaus: beide opnieuw, eerste is het transactiebestand (veldvolgorde)", () => {
    const { velden, eerste } = bestandNiveaus(REKENING, AANWIJZING);
    assert.equal(velden.bestand1.niveau, "opnieuw");
    assert.equal(velden.bestand2.niveau, "opnieuw");
    assert.equal(eerste, "bestand1");
});

test("bestandNiveaus: sterk gaat voor zacht, ook als het zachte veld eerder staat", () => {
    assert.equal(bestandNiveaus(REKENING, TRANSACTIES).eerste, "bestand2");
});

test("bestandNiveaus: alles in orde of status ontbreekt: geen markering", () => {
    for (const [rek, tr] of [[REKENING_VOLLEDIG, { ...TRANSACTIES, controle: "geen_aanwijzingen" }], [undefined, undefined], [null, null], [REKENING_VOLLEDIG, null]]) {
        const { velden, eerste } = bestandNiveaus(rek, tr);
        assert.equal(velden.bestand1.niveau, "geen");
        assert.equal(velden.bestand2.niveau, "geen");
        assert.equal(velden.bestand1.label, null);
        assert.equal(eerste, null);
    }
});

test("bestandNiveaus: geen rekeningoverzicht geüpload is geen 'opnieuw'", () => {
    assert.equal(bestandNiveaus(null, { ...TRANSACTIES, controle: "geen_aanwijzingen" }).velden.bestand2.niveau, "geen");
});
