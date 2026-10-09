// Unit tests voor maakSorteerbareTabel() en de mobiele indeling (static/js/gedeeld/tabel.js), met een minimale nep-DOM.
//   node --test tests/test_tabel.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

class NepElement {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.attributen = {};
        this.listeners = {};
        this.className = "";
        this.textContent = "";
        this.value = "";
        this.hidden = false;
        this.classList = {
            add: klasse => { this.className = `${this.className} ${klasse}`.trim(); },
            contains: klasse => this.className.split(" ").includes(klasse),
        };
    }
    // Zoals in de browser: tekst via textContent is één tekstknoop.
    get childNodes() { return this.children.length || !this.textContent ? this.children : [this.textContent]; }
    appendChild(kind) { this.children.push(kind); return kind; }
    append(...kinderen) { this.children.push(...kinderen); }
    replaceChildren(...kinderen) { this.children = kinderen; }
    setAttribute(naam, waarde) { this.attributen[naam] = String(waarde); }
    getAttribute(naam) { return naam in this.attributen ? this.attributen[naam] : null; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    dispatch(type) { (this.listeners[type] || []).forEach(fn => fn()); }
    zoek(tag) {
        const gevonden = [];
        for (const kind of this.children) {
            if (!(kind instanceof NepElement)) continue;
            if (kind.tagName === tag.toUpperCase()) gevonden.push(kind);
            gevonden.push(...kind.zoek(tag));
        }
        return gevonden;
    }
}

globalThis.document = { createElement: tag => new NepElement(tag) };
globalThis.Option = function (tekst, waarde) {
    const optie = new NepElement("option");
    optie.textContent = tekst;
    optie.value = waarde;
    return optie;
};

const { maakSorteerbareTabel, maakCel, mobielIndeling, voegSubregelSamen } = require("../static/js/gedeeld/tabel.js");

const KOLOMMEN = [
    { label: "Naam", mobielRol: "titel", renderTd: r => maakCel(r.naam) },
    { label: "Waarde", mobielRol: "waarde", waarde: r => r.waarde, renderTd: r => maakCel(String(r.waarde)) },
    { label: "Aantal", mobielRol: "subregel", mobielTekst: r => `${r.aantal} st`, waarde: r => r.aantal, renderTd: r => maakCel(String(r.aantal)) },
    { label: "Code", alleenMobiel: true, renderTd: r => maakCel(`c-${r.naam}`) },
];
const RIJEN = [
    { naam: "A", waarde: 20, aantal: 3 },
    { naam: "B", waarde: null, aantal: 1 },
    { naam: "C", waarde: 50, aantal: 2 },
    { naam: "D", waarde: 10, aantal: 4 },
];

function tekst(el) {
    return typeof el === "string" ? el : el.textContent + el.children.map(tekst).join("");
}

function namen(element) {
    return element.zoek("tbody")[0].zoek("tr").map(tr => tr.children[0].textContent);
}

test("mobielIndeling: kolommen per rol in kolomvolgorde, zonder rol is detail", () => {
    const k = [
        { label: "Naam", mobielRol: "titel" },
        { label: "Aantal", mobielRol: "subregel" },
        { label: "Koers" },
        { label: "Waarde", mobielRol: "waarde" },
        { label: "GAK", mobielRol: "subregel" },
        { label: "Rendement", mobielRol: "subwaarde" },
        { label: "Dividend", mobielRol: "detail" },
    ];
    const indeling = mobielIndeling(k);
    const labels = rol => indeling[rol].map(kol => kol.label);
    assert.deepEqual(labels("titel"), ["Naam"]);
    assert.deepEqual(labels("subregel"), ["Aantal", "GAK"]);
    assert.deepEqual(labels("waarde"), ["Waarde"]);
    assert.deepEqual(labels("subwaarde"), ["Rendement"]);
    assert.deepEqual(labels("detail"), ["Koers", "Dividend"]);
});

test("mobielIndeling weigert een onbekende rol", () => {
    assert.throws(() => mobielIndeling([{ label: "Naam", mobielRol: "kop" }]), /Onbekende mobielRol: kop/);
});

test("voegSubregelSamen: met ' · ', lege delen vallen weg", () => {
    assert.equal(voegSubregelSamen(["60 st", "GAK €46,71"]), "60 st · GAK €46,71");
    assert.equal(voegSubregelSamen(["Gesloten", "", null, "12 st"]), "Gesloten · 12 st");
    assert.equal(voegSubregelSamen([]), "");
});

test("kaartTitel bestaat niet meer in de tabelbouwer en de tabbladen", () => {
    const js = path.join(__dirname, "..", "static", "js");
    for (const bestand of ["gedeeld/tabel.js", "tabs/statistieken.js"]) {
        assert.ok(!fs.readFileSync(path.join(js, bestand), "utf8").includes("kaartTitel"), bestand);
    }
});

test("compactOpMobiel: per rij een knop met titel, subregel en waarde; details met de overige kolommen", () => {
    const element = maakSorteerbareTabel(KOLOMMEN, RIJEN, { compactOpMobiel: true });
    assert.ok(element.zoek("table")[0].classList.contains("alleenBreed"));
    const li = element.zoek("ul")[0].children[0];
    const [knop, details] = li.children;
    assert.equal(knop.tagName, "BUTTON");
    assert.deepEqual(knop.children.map(c => [c.className, tekst(c)]), [
        ["mobielTitel", "A"],
        ["mobielSubregel", "3 st"],
        ["mobielWaarde", "20"],
    ]);
    assert.deepEqual(details.children.map(tekst), ["Code", "c-A"]);
});

test("compactOpMobiel: alleenMobiel-kolom niet in de tabel, wel sorteren op de oorspronkelijke index", () => {
    const element = maakSorteerbareTabel(KOLOMMEN, RIJEN, { compactOpMobiel: true });
    assert.deepEqual(element.zoek("th").map(th => th.textContent), ["Naam", "Waarde", "Aantal"]);
    assert.equal(element.zoek("tbody")[0].zoek("tr")[0].children.length, 3);
});

test("tik op een rij klapt de details uit en weer in", () => {
    const li = maakSorteerbareTabel(KOLOMMEN, RIJEN, { compactOpMobiel: true }).zoek("ul")[0].children[0];
    const [knop, details] = li.children;
    assert.equal(knop.getAttribute("aria-expanded"), "false");
    assert.equal(details.hidden, true);
    knop.dispatch("click");
    assert.equal(knop.getAttribute("aria-expanded"), "true");
    assert.equal(details.hidden, false);
    knop.dispatch("click");
    assert.equal(knop.getAttribute("aria-expanded"), "false");
    assert.equal(details.hidden, true);
});

test("zonder compactOpMobiel: geen mobiele lijst, geen extra klasse en geen sorteerkeuze", () => {
    const element = maakSorteerbareTabel(KOLOMMEN.slice(0, 3), RIJEN);
    const tabel = element.zoek("table")[0];
    assert.equal(tabel.className, "dataTabel");
    assert.equal(element.zoek("ul").length, 0);
    assert.equal(element.zoek("select").length, 0);
});

test("sorteerPlek: de sorteerkeuze komt daar, niet boven de rijen; leeg bij geen rijen", () => {
    const plek = document.createElement("div");
    const element = maakSorteerbareTabel(KOLOMMEN, RIJEN, { compactOpMobiel: true, sorteerPlek: plek });
    assert.equal(element.zoek("select").length, 0);
    assert.equal(plek.zoek("select").length, 1);
    maakSorteerbareTabel(KOLOMMEN, [], { compactOpMobiel: true, sorteerPlek: plek });
    assert.equal(plek.children.length, 0);
});

test("sorteerkeuze: standaardvolgorde plus beide richtingen per sorteerbare kolom", () => {
    const select = maakSorteerbareTabel(KOLOMMEN, RIJEN, { compactOpMobiel: true }).zoek("select")[0];
    assert.deepEqual(select.children.map(o => o.value), ["", "1|desc", "1|asc", "2|desc", "2|asc"]);
});

test("sorteerkeuze geeft dezelfde volgorde als klikken op de kolomkop", () => {
    const viaKop = maakSorteerbareTabel(KOLOMMEN, RIJEN, { compactOpMobiel: true });
    const kopWaarde = viaKop.zoek("th")[1];
    kopWaarde.dispatch("click");
    assert.deepEqual(namen(viaKop), ["C", "A", "D", "B"]);
    assert.equal(viaKop.zoek("select")[0].value, "1|desc");
    kopWaarde.dispatch("click");
    const opgaandViaKop = namen(viaKop);
    assert.deepEqual(opgaandViaKop, ["D", "A", "C", "B"]);

    const viaSelect = maakSorteerbareTabel(KOLOMMEN, RIJEN, { compactOpMobiel: true });
    const select = viaSelect.zoek("select")[0];
    select.value = "1|asc";
    select.dispatch("change");
    assert.deepEqual(namen(viaSelect), opgaandViaKop);
    const mobieleTitels = viaSelect.zoek("ul")[0].children.map(li => li.children[0].children[0].textContent);
    assert.deepEqual(mobieleTitels, opgaandViaKop);
    assert.equal(viaSelect.zoek("th")[1].textContent, "Waarde ▲");
});

test("sorteerkeuze schrijft in de gedeelde staat en kan terug naar de standaardvolgorde", () => {
    const staat = { sorteerKolom: null, sorteerRichting: "desc" };
    const element = maakSorteerbareTabel(KOLOMMEN, RIJEN, { compactOpMobiel: true, staat });
    const select = element.zoek("select")[0];
    select.value = "2|desc";
    select.dispatch("change");
    assert.deepEqual(staat, { sorteerKolom: 2, sorteerRichting: "desc" });
    assert.deepEqual(namen(element), ["D", "A", "C", "B"]);
    select.value = "";
    select.dispatch("change");
    assert.equal(staat.sorteerKolom, null);
    assert.deepEqual(namen(element), ["A", "B", "C", "D"]);
});

test("subwaarde houdt de klassen van de cel (groen/rood); zonder waarde-kolom een eigen rijklasse", () => {
    const kolommen = [
        { label: "Naam", mobielRol: "titel", renderTd: r => maakCel(r.naam) },
        {
            label: "Rendement",
            mobielRol: "subwaarde",
            renderTd: r => { const td = maakCel(String(r.waarde)); td.className = "rendementCel positief"; return td; },
        },
    ];
    const knop = maakSorteerbareTabel(kolommen, RIJEN, { compactOpMobiel: true }).zoek("button")[0];
    assert.equal(knop.className, "mobielRij mobielRijZonderWaarde");
    assert.equal(knop.children[2].className, "mobielSubwaarde rendementCel positief");
});

test("compactOpMobiel zonder sorteerbare kolommen: geen sorteerkeuze, wel de mobiele lijst", () => {
    const kolommen = [
        { label: "Naam", mobielRol: "titel", renderTd: r => maakCel(r.naam) },
        { label: "Aantal", mobielRol: "waarde", renderTd: r => maakCel(String(r.aantal)) },
    ];
    const element = maakSorteerbareTabel(kolommen, RIJEN, { compactOpMobiel: true });
    assert.equal(element.zoek("select").length, 0);
    assert.equal(element.zoek("ul")[0].children.length, 4);
});

test("rijKlasse: op de <tr> én op de mobiele rij", () => {
    const element = maakSorteerbareTabel(KOLOMMEN, RIJEN, {
        compactOpMobiel: true,
        rijKlasse: r => (r.naam === "C" ? "aanbevolen" : null),
    });
    assert.deepEqual(element.zoek("tbody")[0].zoek("tr").map(tr => tr.className), ["", "", "aanbevolen", ""]);
    assert.deepEqual(element.zoek("ul")[0].children.map(li => li.className), ["", "", "aanbevolen", ""]);
});

test("alleenTabel niet in de mobiele rij; detail zonder inhoud valt weg; uitleg als tooltip op de kop", () => {
    const kolommen = [
        { label: "Naam", mobielRol: "titel", renderTd: r => maakCel(r.naam) },
        { label: "Label", alleenTabel: true, uitleg: "Uitleg", renderTd: r => maakCel(`label-${r.naam}`) },
        { label: "Leeg", alleenMobiel: true, renderTd: () => maakCel("") },
        { label: "Aantal", renderTd: r => maakCel(String(r.aantal)) },
    ];
    const element = maakSorteerbareTabel(kolommen, RIJEN, { compactOpMobiel: true });
    assert.deepEqual(element.zoek("th").map(th => [th.textContent, th.title]), [["Naam", undefined], ["Label", "Uitleg"], ["Aantal", undefined]]);
    const details = element.zoek("ul")[0].children[0].children[1];
    assert.deepEqual(details.children.map(tekst), ["Aantal", "3"]);
});
