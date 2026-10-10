const { test } = require("node:test");
const assert = require("node:assert/strict");

const { VERDELING_OVERIG_SLEUTEL, verdelingOverTijdDatasets, beperkteDekkingRegel } = require("../static/js/verdeling_over_tijd.js");

const DATA = {
    labels: ["2024-01-07", "2024-01-14"],
    totaal: [100, 0],
    reeksen: [
        { sleutel: "A", naam: "Fonds A", waarde: [60, 0], pct: [60, null] },
        { sleutel: VERDELING_OVERIG_SLEUTEL, naam: "Overig", waarde: [40, 0], pct: [40, null] },
    ],
};
const kleurVoor = sleutel => `kleur-${sleutel}`;

test("verdelingOverTijdDatasets: % gebruikt pct, null blijft null", () => {
    const [a, overig] = verdelingOverTijdDatasets(DATA, "pct", kleurVoor, "grijs");
    assert.deepEqual(a.data, [60, null]);
    assert.deepEqual(overig.data, [40, null]);
    assert.equal(a.label, "Fonds A");
});

test("verdelingOverTijdDatasets: € gebruikt waarde, pct en waarde gaan mee voor de tooltip", () => {
    const [a] = verdelingOverTijdDatasets(DATA, "euro", kleurVoor, "grijs");
    assert.deepEqual(a.data, [60, 0]);
    assert.deepEqual(a.pct, [60, null]);
    assert.deepEqual(a.waarde, [60, 0]);
});

test("verdelingOverTijdDatasets: kleur per sleutel, Overig grijs", () => {
    const [a, overig] = verdelingOverTijdDatasets(DATA, "pct", kleurVoor, "grijs");
    assert.equal(a.backgroundColor, "kleur-A");
    assert.equal(overig.backgroundColor, "grijs");
    assert.equal(overig.borderColor, "grijs");
});

test("verdelingOverTijdDatasets: Unknown en Onbekend grijs, de kleurindex telt alleen gekleurde reeksen", () => {
    const data = { reeksen: ["Unknown", "EUR", "Onbekend", "USD"].map(sleutel => ({ sleutel, naam: sleutel, waarde: [1], pct: [25] })) };
    const kleuren = verdelingOverTijdDatasets(data, "pct", (sleutel, index) => `kleur-${index}`, "grijs").map(ds => ds.backgroundColor);
    assert.deepEqual(kleuren, ["grijs", "kleur-0", "grijs", "kleur-1"]);
});

test("verdelingOverTijdDatasets: eerste vlak vanaf de as, de rest gestapeld op de vorige", () => {
    const datasets = verdelingOverTijdDatasets(DATA, "pct", kleurVoor, "grijs");
    assert.deepEqual(datasets.map(ds => ds.fill), ["origin", "-1"]);
});

test("verdelingOverTijdDatasets: zonder reeksen een lege lijst", () => {
    assert.deepEqual(verdelingOverTijdDatasets({ labels: [], totaal: [], reeksen: [] }, "pct", kleurVoor, "grijs"), []);
    assert.deepEqual(verdelingOverTijdDatasets(null, "pct", kleurVoor, "grijs"), []);
});

test("beperkteDekkingRegel: namen in een regel, null zonder namen", () => {
    assert.equal(beperkteDekkingRegel(["Wereld ETF", "EM ETF"]), "Beperkte landdekking (alleen top-10 holdings) voor: Wereld ETF, EM ETF.");
    assert.equal(beperkteDekkingRegel([]), null);
    assert.equal(beperkteDekkingRegel(undefined), null);
});
