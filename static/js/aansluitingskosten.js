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
            .map(j => `${j.jaar === null || j.jaar === undefined ? "jaar onbekend" : j.jaar} ${formatteerEuro(j.bedrag_eur)}`)
            .join(" · ");
    }

    const STREEPJE = "–";
    const ONVOLLEDIG_TEKST = "onvolledig (koers ontbreekt)";
    const DIT_JAAR_BETAALD_TEKST = "dit jaar al betaald";

    function jaarTekst(jaar) {
        return jaar === null || jaar === undefined ? "jaar onbekend" : String(jaar);
    }

    function aantalTekst(aantal) {
        return `${aantal} ${aantal === 1 ? "transactie" : "transacties"}`;
    }

    // Een regel per jaar waarin kosten zijn betaald en/of getransacteerd is, oudste eerst: "2025 €2,50 · 3 transacties".
    function jaarRegels(beurs) {
        const kosten = new Map((beurs.per_jaar || []).map(j => [j.jaar, j.bedrag_eur]));
        const transacties = new Map((beurs.transacties_per_jaar || []).map(j => [j.jaar, j.aantal]));
        const jaren = [...new Set([...kosten.keys(), ...transacties.keys()])]
            .sort((a, b) => (a === null) - (b === null) || a - b);
        return jaren.map(jaar => {
            const delen = [`${jaarTekst(jaar)} ${kosten.has(jaar) ? formatteerEuro(kosten.get(jaar)) : "geen kosten"}`];
            if (transacties.has(jaar)) delen.push(aantalTekst(transacties.get(jaar)));
            else if (beurs.transacties_per_jaar) delen.push("geen transacties");
            return delen.join(" · ");
        });
    }

    function euroOfStreepje(bedrag) {
        return bedrag === null || bedrag === undefined ? STREEPJE : formatteerEuro(bedrag);
    }

    function wvTekst(bedrag, volledig) {
        const tekst = euroOfStreepje(bedrag);
        return volledig === false && tekst !== STREEPJE ? `${tekst} · ${ONVOLLEDIG_TEKST}` : tekst;
    }

    function opsomming(jaren) {
        return jaren.length < 2 ? jaren.join("") : `${jaren.slice(0, -1).join(", ")} en ${jaren.at(-1)}`;
    }

    // Neutrale meldingen bij de twee controlelijsten; geen advies.
    function beursMeldingen(beurs) {
        const naam = beurs.verdeling_naam && beurs.verdeling_naam !== beurs.code ? beurs.verdeling_naam : beurs.naam;
        const meldingen = [];
        if ((beurs.kosten_zonder_transacties || []).length > 0) {
            meldingen.push(`${naam}: kosten in ${opsomming(beurs.kosten_zonder_transacties)} zonder transacties op deze beurs.`);
        }
        if ((beurs.transacties_zonder_kosten || []).length > 0) {
            meldingen.push(`${naam}: transacties in ${opsomming(beurs.transacties_zonder_kosten)} zonder aansluitingskosten.`);
        }
        return meldingen;
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
                jaarRegels: jaarRegels(beurs),
                betaaldDitJaar: beurs.betaald_dit_jaar === true,
                wv_na_eur: beurs.wv_na_aansluitingskosten_eur,
                wvTekst: wvTekst(beurs.wv_eur, beurs.wv_volledig),
                wvNaTekst: wvTekst(beurs.wv_na_aansluitingskosten_eur, beurs.wv_volledig),
                transactiekostenTekst: euroOfStreepje(beurs.transactiekosten_eur),
                meldingen: beursMeldingen(beurs),
                inBezit: posities === null || beurs.code === BEURS_ONBEKEND
                    ? null
                    : Boolean(sleutel && Object.prototype.hasOwnProperty.call(posities, sleutel)),
            };
        }).sort((a, b) => (a.code === BEURS_ONBEKEND) - (b.code === BEURS_ONBEKEND)
            || b.totaal_eur - a.totaal_eur || a.beurs.localeCompare(b.beurs, "nl"));
    }

    // Totaalrij met de bedragen uit de API (de backend telt de beurzen op); alleen bij meer dan één beurs.
    function aansluitingskostenTotalen(samenvatting, aantalRijen) {
        if (!heeftAansluitingskosten(samenvatting) || aantalRijen <= 1) return null;
        const volledig = samenvatting.totaal_wv_volledig;
        return {
            kostenTekst: formatteerEuro(samenvatting.totaal_eur),
            wvTekst: wvTekst(samenvatting.totaal_wv_eur, volledig),
            wvNaTekst: wvTekst(samenvatting.totaal_wv_na_aansluitingskosten_eur, volledig),
            wv_na_eur: samenvatting.totaal_wv_na_aansluitingskosten_eur,
            transactiekostenTekst: euroOfStreepje(samenvatting.totaal_transactiekosten_eur),
        };
    }

    const exportsObj = {
        UITLEG_AANSLUITINGSKOSTEN, ONVOLLEDIG_TEKST, DIT_JAAR_BETAALD_TEKST, heeftAansluitingskosten,
        aansluitingskostenTegel, aansluitingskostenRijen, aansluitingskostenTotalen,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
