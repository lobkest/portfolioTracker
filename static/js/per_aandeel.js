// Pure logica voor het tabblad Per aandeel (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    // null = geen blok (ETF of verrijking nog niet binnen).
    function aandeelLandSectorRegels(perAandeel, ticker) {
        const info = perAandeel && perAandeel[ticker];
        if (!info) return null;
        return [["Land", info.land], ["Sector", info.sector]].map(([label, waarde]) => {
            const onbekend = !waarde || waarde === "Unknown";
            return { label, tekst: onbekend ? "onbekend" : waarde, onbekend };
        });
    }

    const { formatPct } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    // Huidige waarde in EUR van een positie in bezit; null bij een gesloten of onbekende positie.
    function etfPositieWaarde(statistieken, ticker) {
        const positie = ((statistieken && statistieken.posities) || []).find(p => p.ticker === ticker);
        return positie && positie.huidige_waarde != null ? positie.huidige_waarde : null;
    }

    // {tekst, grijs} onder de holdings-tabel van een ETF; null zonder holdings (data = antwoord van /api/etf-holdings).
    function etfHoldingsBijschrift(data) {
        if (!data || !data.holdings || data.holdings.length === 0) return null;
        if (data.bron === "provider_csv") {
            return { tekst: `Volledige lijst van de fondsaanbieder: ${data.aantal} bedrijven`, grijs: false };
        }
        return { tekst: `Alleen de top 10 via Yahoo, samen ${formatPct(data.gedekt_gewicht * 100, 1)} van het fonds`, grijs: true };
    }

    const exportsObj = { aandeelLandSectorRegels, etfPositieWaarde, etfHoldingsBijschrift };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
