// Unit tests voor de rekenkern van het Diagnostiek-subtabblad
// (static/js/diagnostiek.js). Draait via Node's ingebouwde testrunner:
//   node --test tests/test_diagnostiek.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    voegMeldingenSamen, telPerNiveau, groepeerPerCategorie, diagnostiekTellerTekst,
    hoogsteNiveau, categorieStandaardOpen, diagnostiekTabelRijen,
    deelInBlokken, filterOpNiveau, wisselNiveauFilter, diagnostiekConclusie, categorieSamenvatting, meldingActie,
    formatUploadMoment, uploadBlokken, meldingenVoorTelling, uploadOokLiveTekst,
} = require("../static/js/diagnostiek.js");

const m = (categorie, niveau, tekst, sleutel = tekst) => ({ categorie, niveau, tekst, sleutel });

test("voegMeldingenSamen: lege of ontbrekende invoer geeft lege lijst", () => {
    assert.deepEqual(voegMeldingenSamen(null, undefined), []);
    assert.deepEqual(voegMeldingenSamen([], []), []);
});

test("voegMeldingenSamen: nieuwe meldingen achteraan", () => {
    const a = m("Wisselkoersen", "GOED", "a");
    const b = m("Wisselkoersen", "INFO", "b");
    assert.deepEqual(voegMeldingenSamen([a], [b]), [a, b]);
});

test("voegMeldingenSamen: zelfde categorie + sleutel -> nieuwste wint, op de oude plek", () => {
    const oud = m("Wisselkoersen", "GOED", "USD oud", "USDEUR=X");
    const ander = m("Wisselkoersen", "GOED", "GBP", "GBPEUR=X");
    const nieuw = m("Wisselkoersen", "FOUT", "USD nieuw", "USDEUR=X");
    assert.deepEqual(voegMeldingenSamen([oud, ander], [nieuw]), [nieuw, ander]);
});

test("voegMeldingenSamen: zelfde sleutel in andere categorie blijft apart", () => {
    const a = m("Wisselkoersen", "GOED", "a", "x");
    const b = m("Koersen", "GOED", "b", "x");
    assert.equal(voegMeldingenSamen([a], [b]).length, 2);
});

test("voegMeldingenSamen: wijzigt de invoer niet", () => {
    const bestaand = [m("Wisselkoersen", "GOED", "a", "k")];
    voegMeldingenSamen(bestaand, [m("Wisselkoersen", "FOUT", "b", "k")]);
    assert.equal(bestaand[0].tekst, "a");
});

test("telPerNiveau: telt per niveau, onbekend niveau telt als INFO", () => {
    const telling = telPerNiveau([
        m("W", "FOUT", "1"), m("W", "LET_OP", "2"), m("W", "LET_OP", "3"), m("W", "RAAR", "4"),
    ]);
    assert.deepEqual(telling, { FOUT: 1, LET_OP: 2, INFO: 1, GOED: 0 });
    assert.deepEqual(telPerNiveau(null), { FOUT: 0, LET_OP: 0, INFO: 0, GOED: 0 });
});

test("groepeerPerCategorie: binnen categorie ernstigste eerst", () => {
    const groepen = groepeerPerCategorie([
        m("Wisselkoersen", "GOED", "g"), m("Wisselkoersen", "FOUT", "f"), m("Wisselkoersen", "LET_OP", "l"),
    ]);
    assert.equal(groepen.length, 1);
    assert.deepEqual(groepen[0].meldingen.map(x => x.niveau), ["FOUT", "LET_OP", "GOED"]);
});

test("groepeerPerCategorie: categorie met ernstigste melding eerst, anders volgorde van voorkomen", () => {
    const groepen = groepeerPerCategorie([
        m("A", "GOED", "a"), m("B", "INFO", "b"), m("C", "FOUT", "c"), m("D", "INFO", "d"),
    ]);
    assert.deepEqual(groepen.map(g => g.categorie), ["C", "B", "D", "A"]);
});

test("groepeerPerCategorie: lege invoer", () => {
    assert.deepEqual(groepeerPerCategorie([]), []);
    assert.deepEqual(groepeerPerCategorie(undefined), []);
});

test("diagnostiekTellerTekst: alleen niveaus met meldingen, ernstigste eerst", () => {
    assert.equal(diagnostiekTellerTekst({ FOUT: 1, LET_OP: 2, INFO: 0, GOED: 3 }), "1 fout, 2 let op, 3 goed");
    assert.equal(diagnostiekTellerTekst({ FOUT: 0, LET_OP: 0, INFO: 0, GOED: 0 }), "");
});

test("hoogsteNiveau: ernstigste niveau, null bij lege invoer", () => {
    assert.equal(hoogsteNiveau([m("W", "GOED", "a"), m("W", "LET_OP", "b"), m("W", "INFO", "c")]), "LET_OP");
    assert.equal(hoogsteNiveau([m("W", "GOED", "a"), m("W", "FOUT", "b")]), "FOUT");
    assert.equal(hoogsteNiveau([m("W", "RAAR", "a"), m("W", "GOED", "b")]), "INFO");
    assert.equal(hoogsteNiveau([]), null);
    assert.equal(hoogsteNiveau(undefined), null);
});

test("categorieStandaardOpen: open bij LET_OP/FOUT, dicht bij alleen GOED/INFO", () => {
    assert.equal(categorieStandaardOpen([m("W", "GOED", "a"), m("W", "INFO", "b")]), false);
    assert.equal(categorieStandaardOpen([m("W", "GOED", "a"), m("W", "LET_OP", "b")]), true);
    assert.equal(categorieStandaardOpen([m("W", "FOUT", "a")]), true);
    assert.equal(categorieStandaardOpen([]), false);
});

test("diagnostiekTabelRijen: weging als % met 1 decimaal, rest als tekst", () => {
    const tabel = {
        kolommen: ["Bedrijf", "Weging", "Land", "Sector"],
        rijen: [["Apple", 4.567, "United States", "Technology"], ["Niet in holdingsdata", 76.25, "–", "–"]],
    };
    assert.deepEqual(diagnostiekTabelRijen(tabel), {
        kolommen: ["Bedrijf", "Weging", "Land", "Sector"],
        rijen: [["Apple", "4,6%", "United States", "Technology"], ["Niet in holdingsdata", "76,3%", "–", "–"]],
    });
});

test("diagnostiekTabelRijen: zonder tabel of rijen null; null-cel wordt leeg", () => {
    assert.equal(diagnostiekTabelRijen(undefined), null);
    assert.equal(diagnostiekTabelRijen({ kolommen: ["Bedrijf"], rijen: [] }), null);
    assert.deepEqual(diagnostiekTabelRijen({ kolommen: ["Bedrijf"], rijen: [[null]] }).rijen, [[""]]);
});

test("deelInBlokken: FOUT/LET_OP naar aandacht (ernstigste eerst), de rest naar in orde", () => {
    const blokken = deelInBlokken([
        m("Dividend", "GOED", "a"), m("Tickers", "LET_OP", "b"), m("Data", "INFO", "c"), m("Opslaan", "FOUT", "d"),
    ]);
    assert.deepEqual(blokken.aandacht.map(g => g.categorie), ["Opslaan", "Tickers"]);
    assert.deepEqual(blokken.inOrde.map(g => g.categorie), ["Data", "Dividend"]);
    assert.deepEqual(blokken.technisch, []);
});

test("deelInBlokken: Laadtijden en Yahoo-calls altijd technisch, ook met LET_OP; andere Koersen-meldingen niet", () => {
    const blokken = deelInBlokken([
        m("Laadtijden", "LET_OP", "Koersen ophalen: 12,0 s.", "koersen_ophalen_kern"),
        m("Koersen", "FOUT", "Yahoo-calls (upload): 5", "yahoo_kern"),
        m("Koersen", "LET_OP", "Geen koersdata voor X", "geen_koers:X"),
    ]);
    assert.deepEqual(blokken.aandacht.map(g => g.categorie), ["Koersen"]);
    assert.equal(blokken.aandacht[0].meldingen.length, 1);
    assert.deepEqual(blokken.technisch.map(g => g.categorie), ["Koersen (Yahoo-calls)", "Laadtijden"]);
    assert.deepEqual(blokken.inOrde, []);
});

test("deelInBlokken: lege invoer", () => {
    assert.deepEqual(deelInBlokken(undefined), { aandacht: [], inOrde: [], technisch: [] });
});

test("filterOpNiveau: alleen dat niveau, onbekend telt als INFO; zonder filter alles", () => {
    const lijst = [m("A", "FOUT", "1"), m("A", "INFO", "2"), m("B", "RAAR", "3"), m("B", "GOED", "4")];
    assert.deepEqual(filterOpNiveau(lijst, "INFO").map(x => x.tekst), ["2", "3"]);
    assert.deepEqual(filterOpNiveau(lijst, "FOUT").map(x => x.tekst), ["1"]);
    assert.equal(filterOpNiveau(lijst, null).length, 4);
    assert.deepEqual(filterOpNiveau(undefined, "FOUT"), []);
});

test("wisselNiveauFilter: aan, nog een keer = uit, andere chip = wisselen", () => {
    assert.equal(wisselNiveauFilter(null, "FOUT"), "FOUT");
    assert.equal(wisselNiveauFilter("FOUT", "FOUT"), null);
    assert.equal(wisselNiveauFilter("FOUT", "INFO"), "INFO");
});

test("diagnostiekConclusie: per combinatie van niveaus", () => {
    assert.equal(diagnostiekConclusie([m("A", "GOED", "1"), m("A", "INFO", "2")]), "Alles in orde");
    assert.equal(diagnostiekConclusie([]), "Alles in orde");
    assert.equal(diagnostiekConclusie([m("A", "LET_OP", "1")]), "1 punt om naar te kijken");
    assert.equal(diagnostiekConclusie([m("A", "LET_OP", "1"), m("B", "LET_OP", "2")]), "2 punten om naar te kijken");
    assert.equal(diagnostiekConclusie([m("A", "FOUT", "1"), m("A", "GOED", "2")]), "1 fout gevonden");
    assert.equal(diagnostiekConclusie([m("A", "FOUT", "1"), m("B", "FOUT", "2")]), "2 fouten gevonden");
    assert.equal(diagnostiekConclusie([m("A", "FOUT", "1"), m("B", "LET_OP", "2"), m("B", "LET_OP", "3")]),
        "1 fout gevonden, 2 punten om naar te kijken");
});

test("diagnostiekConclusie: technische meldingen tellen niet mee", () => {
    assert.equal(diagnostiekConclusie([
        m("Laadtijden", "LET_OP", "traag", "basis_koersen_ophalen"), m("Koersen", "FOUT", "Yahoo", "yahoo_kern"),
    ]), "Alles in orde");
});

test("categorieSamenvatting: tekst van de ernstigste melding, bij gelijk niveau de eerste", () => {
    assert.equal(categorieSamenvatting([m("A", "INFO", "info"), m("A", "LET_OP", "eerste"), m("A", "LET_OP", "tweede")]),
        "eerste");
    assert.equal(categorieSamenvatting([]), "");
});

test("categorieSamenvatting: lange tekst ingekort met ellips", () => {
    assert.equal(categorieSamenvatting([m("A", "FOUT", "abcdefghij")], 6), "abcde…");
    assert.equal(categorieSamenvatting([m("A", "FOUT", "abcdef")], 6), "abcdef");
    assert.equal(categorieSamenvatting([m("A", "FOUT", "abc   defgh")], 7), "abc…");
});

test("meldingActie: tekst met pijl; null zonder label of tab", () => {
    const melding = Object.assign(m("Tickers", "LET_OP", "x"), { actie: { label: "Ticker-zekerheid", tab: "instellingen-ticker" } });
    assert.deepEqual(meldingActie(melding),
        { label: "Ticker-zekerheid", tab: "instellingen-ticker", tekst: "→ Naar Ticker-zekerheid" });
    assert.equal(meldingActie(m("Tickers", "LET_OP", "x")), null);
    assert.equal(meldingActie(Object.assign(m("T", "INFO", "x"), { actie: { label: "L" } })), null);
});

// Zonder "Z" leest Date de tijd als lokaal: dan hangt de verwachte tekst niet af van de tijdzone van de testmachine.
test("formatUploadMoment: dd-mm-jjjj hh:mm, leeg bij een ongeldige datum", () => {
    assert.equal(formatUploadMoment("2026-10-10T09:05:00"), "10-10-2026 09:05");
    assert.match(formatUploadMoment("2026-10-10T12:05:00Z"), /^\d{2}-\d{2}-\d{4} \d{2}:\d{2}$/);
    assert.equal(formatUploadMoment("geen datum"), "");
});

const upload = (moment, meldingen, soort = "upload") => ({ soort, geupload_op: moment, meldingen });

test("uploadBlokken: zonder uploads geen blok", () => {
    assert.deepEqual(uploadBlokken(null, []), { laatste: null, ouder: [] });
    assert.deepEqual(uploadBlokken([], []), { laatste: null, ouder: [] });
});

test("uploadBlokken: wat ook live bestaat valt weg uit de laatste upload, de rest blijft", () => {
    const opgeslagen = m("Opslaan", "GOED", "3 opgeslagen", "insert");
    const ookLive = m("Tickers", "LET_OP", "oud", "tickers:dis_acc:X");
    const blokken = uploadBlokken([upload("2026-10-10T09:05:00", [opgeslagen, ookLive])],
                                  [m("Tickers", "INFO", "nu", "tickers:dis_acc:X")]);
    assert.equal(blokken.laatste.titel, "Laatste upload (10-10-2026 09:05)");
    assert.deepEqual(blokken.laatste.meldingen, [opgeslagen]);
    assert.equal(blokken.laatste.aantalOokLive, 1);
});

test("uploadBlokken: hooguit 4 eerdere uploads, bijwerken in de titel", () => {
    const lijst = [upload("2026-10-10T09:05:00", [], "bijwerken")];
    for (let dag = 9; dag >= 4; dag--) lijst.push(upload(`2026-10-0${dag}T08:00:00`, [m("Opslaan", "INFO", `d${dag}`)]));
    const blokken = uploadBlokken(lijst, []);
    assert.equal(blokken.laatste.titel, "Laatste upload (10-10-2026 09:05, bestanden bijgewerkt)");
    assert.deepEqual(blokken.ouder.map(b => b.titel), [
        "Eerdere upload (09-10-2026 08:00)", "Eerdere upload (08-10-2026 08:00)",
        "Eerdere upload (07-10-2026 08:00)", "Eerdere upload (06-10-2026 08:00)",
    ]);
    // Eerdere uploads blijven compleet: daar wordt niets weggefilterd.
    assert.equal(blokken.ouder[0].meldingen[0].tekst, "d9");
});

test("meldingenVoorTelling: live plus de laatste upload, live wint bij dezelfde sleutel", () => {
    const live = [m("Tickers", "INFO", "nu", "x"), m("Data", "LET_OP", "data", "d")];
    const uploads = [upload("2026-10-10T09:05:00", [m("Tickers", "FOUT", "oud", "x"), m("Opslaan", "LET_OP", "o", "o")]),
                     upload("2026-10-01T09:05:00", [m("Opslaan", "FOUT", "ouder", "z")])];
    const samen = meldingenVoorTelling(live, uploads);
    assert.deepEqual(samen.map(x => x.tekst), ["nu", "o", "data"]);
    assert.equal(diagnostiekConclusie(samen), "2 punten om naar te kijken");
    assert.deepEqual(meldingenVoorTelling(live, null), live);
});

test("uploadOokLiveTekst: enkelvoud en meervoud", () => {
    assert.equal(uploadOokLiveTekst(1), "1 melding van deze upload staat hierboven, met de stand van nu.");
    assert.equal(uploadOokLiveTekst(3), "3 meldingen van deze upload staan hierboven, met de stand van nu.");
});
