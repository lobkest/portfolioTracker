// Pure logica voor de dividendgrafiek (zonder DOM, getest onder Node).

(function (root) {  // omhulsel is een trucje om de exports te laten werken in Node en browser beide.
    "use strict";
    const { formatteerEuro } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    const DIVIDEND_START_DAGEN_VOOR_EERSTE_UITKERING = 30;

    function isoMinDagen(isoDatum, dagen) {
        const d = new Date(`${isoDatum.slice(0, 10)}T00:00:00Z`);
        d.setUTCDate(d.getUTCDate() - dagen);
        return d.toISOString().slice(0, 10);
    }

    // Eerste transactie alleen als die vóór de eerste uitkering ligt; anders zou het startpunt (0) ná een sprong komen.
    function bepaalDividendStart(eersteUitkering, eersteTransactie) {
        if (eersteTransactie && eersteTransactie.slice(0, 10) < eersteUitkering) {
            return { datum: eersteTransactie.slice(0, 10), bron: "eerste_transactie" };
        }
        return {
            datum: isoMinDagen(eersteUitkering, DIVIDEND_START_DAGEN_VOOR_EERSTE_UITKERING),
            bron: "eerste_uitkering_min_dagen",
        };
    }

    // Elke ticker krijgt dezelfde x-waarden (start, alle uitkeringsdatums, vandaag): nodig om goed te stapelen.
    function bouwDividendTrapreeksen(cumulatief, eersteTransactie, vandaag) {
        const datums = cumulatief.datums;
        if (datums.length === 0) return { start: null, per_ticker: {} };
        const start = bepaalDividendStart(datums[0], eersteTransactie);
        const eind = vandaag > datums[datums.length - 1] ? vandaag : null;

        const per_ticker = {};
        for (const [ticker, waarden] of Object.entries(cumulatief.per_ticker)) {
            const reeks = [{ x: start.datum, y: 0 }];
            datums.forEach((datum, i) => reeks.push({ x: datum, y: waarden[i] }));
            if (eind) reeks.push({ x: eind, y: waarden[waarden.length - 1] });
            per_ticker[ticker] = reeks;
        }
        return { start, per_ticker };
    }

    function bouwDividendDatasets(trapreeksen, naamVoorTicker, kleurVoorTicker) {
        return Object.keys(trapreeksen.per_ticker).map(ticker => {
            const kleur = kleurVoorTicker(ticker);
            return {
                label: naamVoorTicker(ticker),
                data: trapreeksen.per_ticker[ticker],
                borderColor: kleur,
                backgroundColor: kleur,
                fill: true,
                // "before": vlak op de vorige waarde tot de uitkeringsdatum, dan omhoog.
                stepped: "before",
                pointRadius: 0,
                pointHoverRadius: 4,
                borderWidth: 1.5,
            };
        });
    }

    // Mobiele rij van Alle uitkeringen: "01-10-2026 · USD"; EUR wordt niet genoemd.
    function dividendSubregel(datumTekst, valuta) {
        return valuta && valuta !== "EUR" ? `${datumTekst} · ${valuta}` : datumTekst;
    }

    // "bruto €25,11 · -€3,77"; zonder belasting alleen het brutobedrag. Belasting is altijd een aftrek.
    function dividendSubwaarde(brutoEur, belastingEur) {
        const bruto = `bruto ${formatteerEuro(brutoEur)}`;
        if (typeof belastingEur !== "number" || Math.abs(belastingEur) < 0.005) return bruto;
        return `${bruto} · ${formatteerEuro(-Math.abs(belastingEur))}`;
    }

    const exportsObj = {
        DIVIDEND_START_DAGEN_VOOR_EERSTE_UITKERING, bepaalDividendStart, bouwDividendTrapreeksen, bouwDividendDatasets,
        dividendSubregel, dividendSubwaarde,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
