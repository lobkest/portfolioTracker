// Pure logica voor de dividendgrafiek (zonder DOM, getest onder Node).

(function (root) {  // omhulsel is een trucje om de exports te laten werken in Node en browser beide. 
    "use strict"; 

    const DIVIDEND_ENKEL_PUNT_RADIUS = 5;

    function bouwDividendDatasets(cumulatief, naamVoorTicker, kleurVoorTicker) {
        // Eén datum geeft geen lijnstuk: zonder zichtbare punten blijft de grafiek leeg.
        const puntRadius = cumulatief.datums.length === 1 ? DIVIDEND_ENKEL_PUNT_RADIUS : 0;
        return Object.keys(cumulatief.per_ticker).map(ticker => {
            const kleur = kleurVoorTicker(ticker);
            return {
                label: naamVoorTicker(ticker),
                data: cumulatief.per_ticker[ticker],
                borderColor: kleur,
                backgroundColor: kleur,
                fill: true,
                pointRadius: puntRadius,
                pointHoverRadius: Math.max(4, puntRadius),
                borderWidth: 1.5,
            };
        });
    }

    const exportsObj = { DIVIDEND_ENKEL_PUNT_RADIUS, bouwDividendDatasets };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
