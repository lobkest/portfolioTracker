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

test("elke sleutel in BENCHMARK_UITLEG bestaat ook in BENCHMARK_KLEUREN", () => {
    const { context } = bouwKnoppen();
    const kleuren = vm.runInContext("Object.keys(BENCHMARK_KLEUREN)", context);
    const uitleg = vm.runInContext("Object.keys(BENCHMARK_UITLEG)", context);
    assert.ok(uitleg.length > 0);
    uitleg.forEach(sleutel => assert.ok(kleuren.includes(sleutel), sleutel));
});

test("NANC heeft een eigen kleur", () => {
    const { context } = bouwKnoppen();
    const kleuren = vm.runInContext("Object.values(BENCHMARK_KLEUREN)", context);
    assert.equal(new Set(kleuren).size, kleuren.length);
});

test("precies een infoTip, naast (niet in) de NANC-knop", () => {
    const { houder, activeerd } = bouwKnoppen();
    const tips = houder.alle("infoTip");
    assert.equal(tips.length, 1);
    assert.equal(activeerd.length, 1);
    const wrapper = houder.alle("benchmarkKnopMetInfo")[0];
    const knop = wrapper.children[0];
    assert.equal(knop.dataset.waarde, "Congres (NANC)");
    assert.equal(knop.children.length, 0);
    assert.equal(wrapper.children[1], tips[0]);
    assert.equal(tips[0].dataset.label, "NANC");
    assert.equal(houder.alle("vergelijkKnop").length, 4);
});
