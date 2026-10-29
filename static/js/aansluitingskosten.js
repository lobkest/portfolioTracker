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

    function aantalJarenTekst(perJaar) {
        return `(${(perJaar || []).length} jaar)`;
    }

    function kostenAlsAftrek(bedrag) {
        return formatteerEuro(bedrag > 0 ? -bedrag : bedrag);
    }

    const STREEPJE = "–";
    const ONVOLLEDIG_TEKST = "onvolledig (koers ontbreekt)";
    const DIT_JAAR_BETAALD_TEKST = "dit jaar al betaald";
    const POSITIE_AANGEHOUDEN_TEKST = "positie aangehouden";
    const POSITIES_ZICHTBAAR = 4;

    function jaarTekst(jaar) {
        return jaar === null || jaar === undefined ? "jaar onbekend" : String(jaar);
    }

    function aantalTekst(aantal) {
        return `${aantal} ${aantal === 1 ? "transactie" : "transacties"}`;
    }

    // Een regel per jaar met kosten en/of transacties, oudste eerst: "2025 €2,50 · 3 transacties". Zonder transacties
    // maar met positie: "positie aangehouden".
    function jaarRegels(beurs) {
        const kosten = new Map((beurs.per_jaar || []).map(j => [j.jaar, j.bedrag_eur]));
        const transacties = new Map((beurs.transacties_per_jaar || []).map(j => [j.jaar, j.aantal]));
        const positiejaren = new Set(beurs.jaren_met_positie || []);
        const jaren = [...new Set([...kosten.keys(), ...transacties.keys()])]
            .sort((a, b) => (a === null) - (b === null) || a - b);
        return jaren.map(jaar => {
            const delen = [`${jaarTekst(jaar)} ${kosten.has(jaar) ? formatteerEuro(kosten.get(jaar)) : "geen kosten"}`];
            if (transacties.has(jaar)) delen.push(aantalTekst(transacties.get(jaar)));
            else if (positiejaren.has(jaar)) delen.push(POSITIE_AANGEHOUDEN_TEKST);
            else if (beurs.transacties_per_jaar) delen.push("geen transacties");
            return delen.join(" · ");
        });
    }

    // Eén item per positie: naam, met "(verkocht)" en/of "(deels)" als het gewicht onder 1 ligt.
    function positieItems(beurs) {
        return (beurs.posities || []).map(p => {
            const toevoegingen = [];
            if (p.nog_in_bezit === false) toevoegingen.push("verkocht");
            if (p.gewicht < 1) toevoegingen.push("deels");
            return {
                tekst: toevoegingen.length ? `${p.naam} (${toevoegingen.join(", ")})` : p.naam,
                verkocht: p.nog_in_bezit === false,
            };
        });
    }

    // Eerste POSITIES_ZICHTBAAR posities; de rest klapt uit achter "en N meer".
    function positieRegel(beurs) {
        const items = positieItems(beurs);
        if (items.length === 0) return null;
        return {
            zichtbaar: items.slice(0, POSITIES_ZICHTBAAR),
            rest: items.slice(POSITIES_ZICHTBAAR),
            restLabel: items.length > POSITIES_ZICHTBAAR ? `en ${items.length - POSITIES_ZICHTBAAR} meer` : null,
            volledigeTekst: items.map(i => i.tekst).join(", "),
        };
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
        if ((beurs.kosten_zonder_positie_of_transacties || []).length > 0) {
            meldingen.push(`${naam}: kosten in ${opsomming(beurs.kosten_zonder_positie_of_transacties)} zonder positie of transacties op deze beurs.`);
        }
        if ((beurs.activiteit_zonder_kosten || []).length > 0) {
            meldingen.push(`${naam}: positie of transacties in ${opsomming(beurs.activiteit_zonder_kosten)} zonder aansluitingskosten.`);
        }
        return meldingen;
    }

    // beursVerdeling: huidigeData.beurs_verdeling, of undefined zolang de verrijking laadt (inBezit dan null).
    function aansluitingskostenRijen(samenvatting, beursVerdeling) {
        if (!heeftAansluitingskosten(samenvatting)) return [];
        const posities = beursVerdeling
            ? beursVerdeling.beurs || {}
            : null;
        return samenvatting.per_beurs.map(beurs => {
            const sleutel = beurs.verdeling_naam;
            return {
                code: beurs.code,
                beurs: beursWeergave(beurs),
                totaal_eur: beurs.totaal_eur,
                kostenTekst: kostenAlsAftrek(beurs.totaal_eur),
                aantalJarenTekst: aantalJarenTekst(beurs.per_jaar),
                jaarRegels: jaarRegels(beurs),
                positieRegel: positieRegel(beurs),
                heeftOpenPositie: beurs.heeft_open_positie === true,
                alternatievenInvoer: beurs.alternatieven_invoer || null,
                betaaldDitJaar: beurs.betaald_dit_jaar === true,
                wv_eur: beurs.wv_eur,
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
            kostenTekst: kostenAlsAftrek(samenvatting.totaal_eur),
            wvTekst: wvTekst(samenvatting.totaal_wv_eur, volledig),
            wvNaTekst: wvTekst(samenvatting.totaal_wv_na_aansluitingskosten_eur, volledig),
            wv_na_eur: samenvatting.totaal_wv_na_aansluitingskosten_eur,
            transactiekostenTekst: euroOfStreepje(samenvatting.totaal_transactiekosten_eur),
        };
    }

    const exportsObj = {
        UITLEG_AANSLUITINGSKOSTEN, ONVOLLEDIG_TEKST, DIT_JAAR_BETAALD_TEKST, POSITIE_AANGEHOUDEN_TEKST, heeftAansluitingskosten,
        aansluitingskostenTegel, aansluitingskostenRijen, aansluitingskostenTotalen,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
