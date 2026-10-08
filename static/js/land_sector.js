// Pure logica voor de landbron van ETF's (tabbladen Land en Per aandeel), zonder DOM, getest onder Node.

(function (root) {
    "use strict";

    const { formatGetal } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    // null zonder proxy; etfInfo is een entry uit land_sector_verdeling.per_etf.
    function landProxyBijschrift(etfInfo) {
        const proxy = etfInfo && etfInfo.land_proxy;
        if (!proxy || !proxy.naam) return null;
        const afwijking = formatGetal(Number(proxy.max_afwijking_pp || 0), 1);
        return `Land benaderd via ${proxy.naam} (top-10 wijkt max. ${afwijking} pp af)`;
    }

    // Regels onder de landgrafiek: eerst de ETF's met alleen hun eigen top-10, dan één regel per proxy.
    function landDekkingRegels(perEtf, tickerNamen) {
        const entries = Object.entries(perEtf || {});
        const beperkt = entries
            .filter(([, info]) => info.land_bron !== "provider_csv" && info.land_bron !== "proxy")
            .map(([ticker]) => ticker);
        const regels = [];
        if (beperkt.length > 0) {
            regels.push(`Beperkte landdekking (alleen top-10-holdings) voor: ${beperkt.join(", ")}.`);
        }
        entries.forEach(([ticker, info]) => {
            const bijschrift = landProxyBijschrift(info);
            if (bijschrift) regels.push(`${(tickerNamen && tickerNamen[ticker]) || ticker}: ${bijschrift}.`);
        });
        return regels;
    }

    const exportsObj = { landProxyBijschrift, landDekkingRegels };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
