// Unit tests voor maakSorteerbareTabel() (static/js/gedeeld/tabel.js), met een minimale nep-DOM.
//   node --test tests/test_tabel.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

class NepElement {
    constructor(tag) {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.attributen = {};
        this.listeners = {};
        this.className = "";
        this.textContent = "";
        this.value = "";
        this.classList = {
            add: klasse => { this.className = `${this.className} ${klasse}`.trim(); },
            contains: klasse => this.className.split(" ").includes(klasse),
        };
    }
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

const { maakSorteerbareTabel, maakCel } = require("../static/js/gedeeld/tabel.js");

const KOLOMMEN = [
    { label: "Naam", kaartTitel: true, renderTd: r => maakCel(r.naam) },
    { label: "Waarde", waarde: r => r.waarde, renderTd: r => maakCel(String(r.waarde)) },
    { label: "Aantal", waarde: r => r.aantal, renderTd: r => maakCel(String(r.aantal)) },
];
const RIJEN = [
    { naam: "A", waarde: 20, aantal: 3 },
    { naam: "B", waarde: null, aantal: 1 },
    { naam: "C", waarde: 50, aantal: 2 },
    { naam: "D", waarde: 10, aantal: 4 },
];

function namen(element) {
    return element.zoek("tbody")[0].zoek("tr").map(tr => tr.children[0].textContent);
}

test("met kaartenOpMobiel: elke td krijgt de kolomnaam als data-label, de tabel de klasse kaartTabel", () => {
    const element = maakSorteerbareTabel(KOLOMMEN, RIJEN, { kaartenOpMobiel: true });
    const tabel = element.zoek("table")[0];
    assert.ok(tabel.classList.contains("kaartTabel"));
    const rij = tabel.zoek("tbody")[0].zoek("tr")[0];
    assert.deepEqual(rij.children.map(td => td.getAttribute("data-label")), ["Naam", "Waarde", "Aantal"]);
    assert.ok(rij.children[0].classList.contains("kaartTitel"));
    assert.ok(!rij.children[1].classList.contains("kaartTitel"));
});

test("zonder kaartenOpMobiel: geen data-label, geen kaartklasse en geen sorteerkeuze", () => {
    const element = maakSorteerbareTabel(KOLOMMEN, RIJEN);
    const tabel = element.zoek("table")[0];
    assert.equal(tabel.className, "dataTabel");
    for (const td of tabel.zoek("td")) {
        assert.equal(td.getAttribute("data-label"), null);
        assert.equal(td.className, "");
    }
    assert.equal(element.zoek("select").length, 0);
});

test("sorteerkeuze: standaardvolgorde plus beide richtingen per sorteerbare kolom", () => {
    const select = maakSorteerbareTabel(KOLOMMEN, RIJEN, { kaartenOpMobiel: true }).zoek("select")[0];
    assert.deepEqual(select.children.map(o => o.value), ["", "1|desc", "1|asc", "2|desc", "2|asc"]);
});

test("sorteerkeuze geeft dezelfde volgorde als klikken op de kolomkop", () => {
    const viaKop = maakSorteerbareTabel(KOLOMMEN, RIJEN, { kaartenOpMobiel: true });
    const kopWaarde = viaKop.zoek("th")[1];
    kopWaarde.dispatch("click");
    assert.deepEqual(namen(viaKop), ["C", "A", "D", "B"]);
    assert.equal(viaKop.zoek("select")[0].value, "1|desc");
    kopWaarde.dispatch("click");
    const opgaandViaKop = namen(viaKop);
    assert.deepEqual(opgaandViaKop, ["D", "A", "C", "B"]);

    const viaSelect = maakSorteerbareTabel(KOLOMMEN, RIJEN, { kaartenOpMobiel: true });
    const select = viaSelect.zoek("select")[0];
    select.value = "1|asc";
    select.dispatch("change");
    assert.deepEqual(namen(viaSelect), opgaandViaKop);
    assert.equal(viaSelect.zoek("th")[1].textContent, "Waarde ▲");
});

test("sorteerkeuze schrijft in de gedeelde staat en kan terug naar de standaardvolgorde", () => {
    const staat = { sorteerKolom: null, sorteerRichting: "desc" };
    const element = maakSorteerbareTabel(KOLOMMEN, RIJEN, { kaartenOpMobiel: true, staat });
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
