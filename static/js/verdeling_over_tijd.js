// Pure logica voor "Over tijd" (Verdeling, Land, Sector, Valuta, Beurs): Chart.js-datasets uit het antwoord van /verdeling-over-tijd, zonder DOM.

(function (root) {
    "use strict";

    const VERDELING_OVERIG_SLEUTEL = "__overig__";
    // Grijs, net als "Overig" en "Unknown" in de taarten.
    const NEUTRALE_SLEUTELS = new Set([VERDELING_OVERIG_SLEUTEL, "Unknown", "Onbekend"]);

    // weergave "pct" of "euro"; kleurVoor(sleutel, index) geeft de kleur, index telt alleen de niet-neutrale reeksen.
    // pct en waarde gaan beide mee voor de tooltip; null blijft null (totaal 0 op dat meetpunt).
    function verdelingOverTijdDatasets(data, weergave, kleurVoor, overigKleur) {
        let kleurIndex = 0;
        return ((data && data.reeksen) || []).map((reeks, i) => {
            const kleur = NEUTRALE_SLEUTELS.has(reeks.sleutel) ? overigKleur : kleurVoor(reeks.sleutel, kleurIndex++);
            return {
                label: reeks.naam,
                sleutel: reeks.sleutel,
                data: weergave === "euro" ? reeks.waarde : reeks.pct,
                pct: reeks.pct,
                waarde: reeks.waarde,
                borderColor: kleur,
                backgroundColor: kleur,
                fill: i === 0 ? "origin" : "-1",
            };
        });
    }

    // null zonder ETF's die alleen een top-10 hebben.
    function beperkteDekkingRegel(namen) {
        if (!namen || namen.length === 0) return null;
        return `Beperkte landdekking (alleen top-10 holdings) voor: ${namen.join(", ")}.`;
    }

    const exportsObj = { VERDELING_OVERIG_SLEUTEL, verdelingOverTijdDatasets, beperkteDekkingRegel };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
