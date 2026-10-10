// Pure logica voor Verdeling > Over tijd: Chart.js-datasets uit het antwoord van /verdeling-over-tijd, zonder DOM.

(function (root) {
    "use strict";

    const VERDELING_OVERIG_SLEUTEL = "__overig__";

    // weergave "pct" of "euro"; kleurVoor(sleutel) geeft de kleur, "Overig" krijgt overigKleur.
    // pct en waarde gaan beide mee voor de tooltip; null blijft null (totaal 0 op dat meetpunt).
    function verdelingOverTijdDatasets(data, weergave, kleurVoor, overigKleur) {
        return ((data && data.reeksen) || []).map((reeks, i) => {
            const kleur = reeks.sleutel === VERDELING_OVERIG_SLEUTEL ? overigKleur : kleurVoor(reeks.sleutel);
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

    const exportsObj = { VERDELING_OVERIG_SLEUTEL, verdelingOverTijdDatasets };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
