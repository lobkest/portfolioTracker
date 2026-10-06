// Unit tests voor static/js/land_sector.js.
//   node --test tests/test_land_sector.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { landProxyBijschrift, landDekkingRegels } = require("../static/js/land_sector.js");

const MET_PROXY = {
    land_bron: "proxy",
    land_proxy: { naam: "iShares MSCI All Country World UCITS ETF", max_afwijking_pp: 0.47 },
};

test("bijschrift met proxy: naam en afwijking met 1 decimaal en komma", () => {
    assert.equal(landProxyBijschrift(MET_PROXY),
        "Land benaderd via iShares MSCI All Country World UCITS ETF (top-10 wijkt max. 0,5 pp af)");
});

test("geen bijschrift zonder proxy", () => {
    assert.equal(landProxyBijschrift({ land_bron: "yfinance_top10", land_proxy: null }), null);
    assert.equal(landProxyBijschrift(undefined), null);
});

test("regels: proxy-ETF telt niet als beperkt en krijgt een eigen regel met bijnaam", () => {
    const perEtf = {
        "VWCE.DE": MET_PROXY,
        "IWDA.AS": { land_bron: "provider_csv", land_proxy: null },
        "XYZ.AS": { land_bron: "yfinance_top10", land_proxy: null },
    };
    assert.deepEqual(landDekkingRegels(perEtf, { "VWCE.DE": "All-World" }), [
        "Beperkte landdekking (alleen top-10-holdings) voor: XYZ.AS.",
        "All-World: Land benaderd via iShares MSCI All Country World UCITS ETF (top-10 wijkt max. 0,5 pp af).",
    ]);
});

test("regels: niets te melden geeft een lege lijst", () => {
    assert.deepEqual(landDekkingRegels({ "IWDA.AS": { land_bron: "provider_csv" } }, {}), []);
    assert.deepEqual(landDekkingRegels(undefined, undefined), []);
});
