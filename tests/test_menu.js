// Unit tests voor de rekenkern van het hamburger-menu (static/js/menu.js).
// Draait via Node's ingebouwde testrunner, geen extra dependency nodig:
//   node --test tests/test_menu.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { volgendeMenuOpenStatus, menuOpenStatusNaViewKeuze } = require("../static/js/menu.js");

test("volgendeMenuOpenStatus: dicht -> open", () => {
    assert.equal(volgendeMenuOpenStatus(false), true);
});

test("volgendeMenuOpenStatus: open -> dicht", () => {
    assert.equal(volgendeMenuOpenStatus(true), false);
});

test("menuOpenStatusNaViewKeuze: sluit het menu ongeacht de status ervoor", () => {
    assert.equal(menuOpenStatusNaViewKeuze(true), false);
    assert.equal(menuOpenStatusNaViewKeuze(false), false);
});

// --- Regressiebewaking mobiele CSS/viewport (geen DOM nodig: leest de bronbestanden) ---

const fs = require("node:fs");
const path = require("node:path");

const CSS = fs.readFileSync(path.join(__dirname, "..", "static", "css", "style.css"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "..", "templates", "index.html"), "utf8");

// Geeft de inhoud van het eerste @media-blok waarvan de voorwaarde met
// `begin` start (accolades geteld, dus geneste regels blijven binnen het blok).
function mediaBlok(begin) {
    const start = CSS.indexOf(`@media ${begin}`);
    assert.notEqual(start, -1, `@media ${begin} niet gevonden`);
    const open = CSS.indexOf("{", start);
    let diepte = 0;
    for (let i = open; i < CSS.length; i++) {
        if (CSS[i] === "{") diepte++;
        if (CSS[i] === "}") diepte--;
        if (diepte === 0) return CSS.slice(open + 1, i);
    }
    throw new Error("onafgesloten @media-blok");
}

const MOBIEL = mediaBlok("(max-width: 768px)");

test("mobiel: invoervelden hebben font-size 16px (voorkomt iOS-autozoom bij focus)", () => {
    assert.match(MOBIEL, /input,\s*select,\s*textarea\s*\{[^}]*font-size:\s*16px/);
});

test("desktop: font-size van invoervelden is buiten de media query niet gezet", () => {
    const buitenMedia = CSS.replace(MOBIEL, "");
    assert.doesNotMatch(buitenMedia, /(^|\n)(input|select|textarea)[^{]*\{[^}]*font-size/);
});

test("mobiel: sidebar is zelf scrollbaar, begrensd (dvh + vh-fallback) en lekt niet door", () => {
    const sidebar = MOBIEL.match(/\.sidebar\s*\{([^}]*)\}/)[1];
    assert.match(sidebar, /overflow-y:\s*auto/);
    assert.match(sidebar, /overscroll-behavior:\s*contain/);
    assert.match(sidebar, /height:\s*100vh;\s*[^}]*height:\s*100dvh/);
});

test("mobiel: achtergrond wordt vastgezet zolang het menu open is (body.menuOpen)", () => {
    assert.match(MOBIEL, /body\.menuOpen,\s*body\.menuOpen \.content\s*\{[^}]*overflow:\s*hidden/);
});

test("viewport-meta blokkeert zoomen niet (toegankelijkheid)", () => {
    const meta = HTML.match(/<meta name="viewport"[^>]*>/)[0];
    assert.match(meta, /width=device-width/);
    assert.match(meta, /initial-scale=1/);
    assert.doesNotMatch(meta, /maximum-scale|user-scalable/);
});
