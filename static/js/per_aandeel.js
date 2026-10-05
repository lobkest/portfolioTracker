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

    const exportsObj = { aandeelLandSectorRegels };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
