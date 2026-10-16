// Pure logica voor de periodeknoppen op de grafieken van Samenvatting en Rendement (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    // Een periode erbij = één regel; maanden: null betekent alles.
    const GRAFIEK_PERIODES = [
        { id: "alles", label: "Alles", maanden: null },
        { id: "1j", label: "1 jaar", maanden: 12 },
    ];

    function dagenInMaand(jaar, maand) {
        return new Date(jaar, maand, 0).getDate();
    }

    // Rekenen op de datumstring, niet via Date-parsing: die geeft UTC en kan een dag verschuiven.
    function grensDatum(vandaag, maanden) {
        const [j, m, d] = vandaag.split("-").map(Number);
        const totaal = j * 12 + (m - 1) - maanden;
        const jaar = Math.floor(totaal / 12);
        const maand = (totaal % 12) + 1;
        const dag = Math.min(d, dagenInMaand(jaar, maand));
        return `${jaar}-${String(maand).padStart(2, "0")}-${String(dag).padStart(2, "0")}`;
    }

    // vandaag: "YYYY-MM-DD"; isoLabels oplopend.
    function startIndexVoorPeriode(isoLabels, maanden, vandaag) {
        if (maanden === null || isoLabels.length === 0) return 0;
        const grens = grensDatum(vandaag, maanden);
        const idx = isoLabels.findIndex(l => l >= grens);
        return idx === -1 ? isoLabels.length - 1 : idx;
    }

    function periodeBeschikbaar(isoLabels, maanden, vandaag) {
        if (maanden === null) return true;
        return isoLabels.length > 0 && isoLabels[0] < grensDatum(vandaag, maanden);
    }

    const exportsObj = { GRAFIEK_PERIODES, grensDatum, startIndexVoorPeriode, periodeBeschikbaar };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
