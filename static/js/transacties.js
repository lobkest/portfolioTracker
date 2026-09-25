// Sorteren en pagineren voor Transacties (zonder DOM, getest onder Node).
// Eigen kern i.p.v. maakSorteerbareTabel(): sorteren moet over alle pagina's heen.

(function (root) {
    "use strict";

    // "YYYY-MM-DDTHH:MM" sorteert als string chronologisch; een ontbrekende tijd telt als 00:00.
    const timestampSleutel = r => `${r.datum}T${r.tijd ?? "00:00"}`;
    const VERGELIJKERS = {
        datum_tijd: (a, b) => {
            const sa = timestampSleutel(a);
            const sb = timestampSleutel(b);
            return sa < sb ? -1 : sa > sb ? 1 : 0;
        },
        product: (a, b) => String(a.product).localeCompare(String(b.product), "nl"),
        aantal: (a, b) => a.aantal - b.aantal,
        koers: (a, b) => (a.koers ?? -Infinity) - (b.koers ?? -Infinity),
        totaal_eur: (a, b) => a.totaal_eur - b.totaal_eur,
        transactiekosten: (a, b) => (a.transactiekosten ?? -Infinity) - (b.transactiekosten ?? -Infinity),
    };

    // Sorteert een kopie; de invoer blijft ongewijzigd.
    function sorteerTransacties(rijen, kolomKey, richting) {
        const vergelijk = VERGELIJKERS[kolomKey];
        if (!vergelijk) return rijen.slice();
        const gesorteerd = rijen.slice().sort(vergelijk);
        if (richting === "desc") gesorteerd.reverse();
        return gesorteerd;
    }

    // Minimaal 1, ook zonder rijen.
    function totaalPaginas(totaalRijen, paginaGrootte) {
        return Math.max(1, Math.ceil(totaalRijen / paginaGrootte));
    }

    // paginaNummer (1-based) wordt geklemd, zodat een verouderd nummer nooit een lege pagina geeft.
    function pagineer(rijen, paginaGrootte, paginaNummer) {
        const maxPagina = totaalPaginas(rijen.length, paginaGrootte);
        const pagina = Math.min(Math.max(1, paginaNummer), maxPagina);
        const start = (pagina - 1) * paginaGrootte;
        return rijen.slice(start, start + paginaGrootte);
    }

    const exportsObj = { sorteerTransacties, totaalPaginas, pagineer };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
