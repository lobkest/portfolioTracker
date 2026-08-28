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
