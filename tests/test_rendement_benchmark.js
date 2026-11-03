// Tests voor de benchmark-constanten en -knoppen in static/js/tabs/rendement.js (geen pure module: constanten en functie worden uit de bron gehaald).
//   node --test tests/test_rendement_benchmark.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const bron = fs.readFileSync(path.join(__dirname, "..", "static", "js", "tabs", "rendement.js"), "utf8");
const constanten = bron.match(/^const BENCHMARK_KLEUREN.*$/m)[0] + "\n" + bron.match(/^const BENCHMARK_UITLEG[\s\S]*?^};\s*$/m)[0];
const knoppenFunctie = bron.match(/^function bouwBenchmarkKnoppen\(\) \{[\s\S]*?^\}$/m)[0];

class NepElement {
    constructor(tag) { this.tag = tag; this.className = ""; this.dataset = {}; this.children = []; this.style = { setProperty() {} }; this.textContent = ""; }
    addEventListener() {}
    append(...kinderen) { this.children.push(...kinderen); }
    replaceChildren(...kinderen) { this.children = kinderen; }
    alle(klasse) {
        return this.children.flatMap(k => [...(k.className.split(" ").includes(klasse) ? [k] : []), ...k.alle(klasse)]);
    }
}

function bouwKnoppen() {
    const houder = new NepElement("div");
    const activeerd = [];
    const context = {
        document: { createElement: tag => new NepElement(tag), getElementById: () => houder },
        activeerInfoTip: tip => activeerd.push(tip),
        wisselBenchmark() {},
    };
    vm.createContext(context);
    vm.runInContext(constanten + "\n" + knoppenFunctie + "\nbouwBenchmarkKnoppen();", context);
    return { houder, activeerd, context };
}

function pythonBenchmarkSleutels() {
    const py = fs.readFileSync(path.join(__dirname, "..", "backend", "statistieken.py"), "utf8");
    const blok = py.match(/^BENCHMARK_TICKERS = \{([\s\S]*?)^\}/m)[1];
    return [...blok.matchAll(/^\s*"([^"]+)":/gm)].map(m => m[1]);
}

test("elke sleutel in BENCHMARK_UITLEG zit ook in BENCHMARK_KLEUREN en BENCHMARK_TICKERS", () => {
    const { context } = bouwKnoppen();
    const kleuren = vm.runInContext("Object.keys(BENCHMARK_KLEUREN)", context);
    const uitleg = vm.runInContext("Object.keys(BENCHMARK_UITLEG)", context);
    const tickers = pythonBenchmarkSleutels();
    assert.ok(tickers.includes("Congres D (NANC)") && tickers.includes("Congres R (GOP)"));
    assert.deepEqual([...kleuren], tickers);
    uitleg.forEach(sleutel => assert.ok(kleuren.includes(sleutel) && tickers.includes(sleutel), sleutel));
});

test("elke benchmark heeft een eigen kleur", () => {
    const { context } = bouwKnoppen();
    const kleuren = vm.runInContext("Object.values(BENCHMARK_KLEUREN)", context);
    assert.equal(new Set(kleuren).size, kleuren.length);
});

test("precies twee infoTips, elk naast (niet in) de juiste knop", () => {
    const { houder, activeerd } = bouwKnoppen();
    assert.equal(houder.alle("infoTip").length, 2);
    assert.equal(activeerd.length, 2);
    const wrappers = houder.alle("benchmarkKnopMetInfo");
    assert.deepEqual(wrappers.map(w => w.children[0].dataset.waarde), ["Congres D (NANC)", "Congres R (GOP)"]);
    wrappers.forEach(w => {
        const [knop, tip] = w.children;
        assert.equal(knop.children.length, 0);
        assert.equal(tip.className, "infoTip");
        assert.equal(tip.dataset.label, knop.dataset.waarde);
    });
    assert.equal(houder.alle("vergelijkKnop").length, 5);
});
