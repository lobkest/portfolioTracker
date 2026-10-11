// Pure opmaak van de aansluitingskosten (Home-tegel en Verdeling → Beurs), zonder DOM, getest onder Node.
// De bedragen komen uit de API (veld `aansluitingskosten`); hier wordt niets berekend.

(function (root) {
    "use strict";

    const { formatteerEuro } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    const BEURS_ONBEKEND = "onbekend";
    const UITLEG_AANSLUITINGSKOSTEN = "Jaarlijkse kosten van DEGIRO per beurs waarop je handelt. Niet meegeteld in "
        + "transactiekosten of rendement. Per beurs zie je ze onder Verdeling → Beurs.";

    // Alleen met minstens één kostenregel: zonder rekeningoverzicht of zonder kosten geen tegel en geen blok.
    function heeftAansluitingskosten(samenvatting) {
        return Boolean(samenvatting && samenvatting.aantal_regels > 0);
    }

    function aansluitingskostenTegel(samenvatting) {
        if (!heeftAansluitingskosten(samenvatting)) return null;
        return {
            label: "Aansluitingskosten",
            waardeTekst: formatteerEuro(samenvatting.totaal_eur),
            uitleg: UITLEG_AANSLUITINGSKOSTEN,
        };
    }

    // Naam zoals op de grafiek van Verdeling → Beurs; een code die Verdeling niet kent toont daar rauw, hier de DEGIRO-naam.
    function beursWeergave(beurs) {
        if (beurs.code === BEURS_ONBEKEND) return "Onbekende beurs";
        const naam = beurs.verdeling_naam && beurs.verdeling_naam !== beurs.code ? beurs.verdeling_naam : beurs.naam;
        return `${naam} (${beurs.code})`;
    }

    function perJaarTekst(perJaar) {
        return (perJaar || [])
            .map(j => `${j.jaar === null || j.jaar === undefined ? "jaar onbekend" : j.jaar}: ${formatteerEuro(j.bedrag_eur)}`)
            .join(" · ");
    }

    // beursVerdeling: huidigeData.beurs_verdeling, of undefined zolang de verrijking laadt (inBezit dan null).
    function aansluitingskostenRijen(samenvatting, beursVerdeling, euronextSamenvoegen) {
        if (!heeftAansluitingskosten(samenvatting)) return [];
        const posities = beursVerdeling
            ? (euronextSamenvoegen ? beursVerdeling.beurs_euronext : beursVerdeling.beurs) || {}
            : null;
        return samenvatting.per_beurs.map(beurs => {
            const sleutel = euronextSamenvoegen ? beurs.verdeling_naam_euronext : beurs.verdeling_naam;
            return {
                code: beurs.code,
                beurs: beursWeergave(beurs),
                totaal_eur: beurs.totaal_eur,
                totaalTekst: formatteerEuro(beurs.totaal_eur),
                perJaar: perJaarTekst(beurs.per_jaar),
                inBezit: posities === null || beurs.code === BEURS_ONBEKEND
                    ? null
                    : Boolean(sleutel && Object.prototype.hasOwnProperty.call(posities, sleutel)),
            };
        }).sort((a, b) => (a.code === BEURS_ONBEKEND) - (b.code === BEURS_ONBEKEND)
            || b.totaal_eur - a.totaal_eur || a.beurs.localeCompare(b.beurs, "nl"));
    }

    const exportsObj = {
        UITLEG_AANSLUITINGSKOSTEN, heeftAansluitingskosten, aansluitingskostenTegel, aansluitingskostenRijen,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
