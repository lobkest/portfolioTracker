// Rekenkern voor het Transacties-tabblad (sortering + paginering). Puur JS,
// geen DOM-afhankelijkheden, zodat dit zowel in de browser (index.html) als
// onder Node (tests/test_transacties.js) draait -- zelfde opzet als
// prognose.js/menu.js.
//
// Los van maakSorteerbareTabel() (app.js) gehouden: die helper sorteert en
// tekent zelf de VOLLEDIGE meegegeven rijenlijst opnieuw bij een kolomklik,
// wat prima is voor een tabel zonder paginering (Statistieken), maar hier
// moet gesorteerd worden over de VOLLEDIGE dataset en pas DAARNA gepagineerd
// -- vandaar een eigen, kleine sorteer+pagineer-kern i.p.v. die helper aan
// te passen (die wordt ook door het Statistieken-tabblad gebruikt).

(function (root) {
    "use strict";

    // Kolomsleutels in vaste volgorde, met een vergelijkingsfunctie per kolom.
    // "datum" is een ISO-string (YYYY-MM-DD) -- lexicografisch sorteren geeft
    // hier al chronologische volgorde, geen Date-parsing nodig.
    const VERGELIJKERS = {
        datum: (a, b) => (a.datum < b.datum ? -1 : a.datum > b.datum ? 1 : 0),
        tijd: (a, b) => (a.tijd ?? "").localeCompare(b.tijd ?? ""),
        product: (a, b) => String(a.product).localeCompare(String(b.product), "nl"),
        aantal: (a, b) => a.aantal - b.aantal,
        koers: (a, b) => (a.koers ?? -Infinity) - (b.koers ?? -Infinity),
        totaal_eur: (a, b) => a.totaal_eur - b.totaal_eur,
        transactiekosten: (a, b) => (a.transactiekosten ?? -Infinity) - (b.transactiekosten ?? -Infinity),
    };

    // Sorteert een KOPIE van de rijenlijst (nooit de meegegeven array zelf
    // muteren) op kolomKey, in richting "asc" of "desc".
    function sorteerTransacties(rijen, kolomKey, richting) {
        const vergelijk = VERGELIJKERS[kolomKey];
        if (!vergelijk) return rijen.slice();
        const gesorteerd = rijen.slice().sort(vergelijk);
        if (richting === "desc") gesorteerd.reverse();
        return gesorteerd;
    }

    // Aantal pagina's voor `totaalRijen` rijen bij `paginaGrootte` per pagina
    // -- minimaal 1, ook als totaalRijen 0 is (dan gewoon 1 lege pagina).
    function totaalPaginas(totaalRijen, paginaGrootte) {
        return Math.max(1, Math.ceil(totaalRijen / paginaGrootte));
    }

    // Geeft de rijen terug voor `paginaNummer` (1-based). paginaNummer wordt
    // geklemd tussen 1 en het werkelijke aantal pagina's, zodat een verouderd
    // paginanummer (bv. na een filtersnede of kleinere paginaGrootte) nooit
    // een lege pagina teruggeeft zolang er rijen zijn.
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
