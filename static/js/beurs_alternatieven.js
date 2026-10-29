// Pure tekstopbouw voor het paneel "Alternatieven op een thuisbeurs" (Verdeling → Beurs), zonder DOM, getest onder Node.
// Alle bedragen en oordelen komen uit de API (`/beurs-alternatieven`); hier wordt niets berekend.

(function (root) {
    "use strict";

    const { formatteerEuro, formatGetal } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    const KNOP_TEKST = "Alternatieven op een thuisbeurs";
    const ONBEKEND = "onbekend";
    const VASTE_REGELS = [
        "Dit is geen advies. Controleer in DEGIRO of het product op deze beurs te koop is; DEGIRO biedt niet elke notering aan. "
        + "Een vergelijkbaar fonds is niet hetzelfde: index, kosten en risico kunnen verschillen.",
    ];
    const SCHATTING_TOELICHTING = "schatting op basis van je eigen transactiekosten";
    const DIT_JAAR_AL_BETAALD = "De kosten voor dit jaar zijn al betaald; vanaf het jaar na je laatste transactie of positie "
        + "op deze beurs betaal je niet meer.";
    const NIET_OP_ISIN_TEKST = "Gevonden op naam, niet op ISIN. Controleer in DEGIRO of de ISIN gelijk is aan die van je positie "
        + "(bijv. niet Acc i.p.v. Dist).";
    const FOUT_TEKST = "Alternatieven ophalen is niet gelukt. Probeer het later opnieuw.";
    const ONGEVAL_TEKST = "Er ging iets mis bij het opzoeken van alternatieven.";
    const TE_LANG_TEKST = "Het ophalen duurde te lang. Probeer het later opnieuw.";

    function bedragOfOnbekend(bedrag) {
        return bedrag === null || bedrag === undefined ? ONBEKEND : formatteerEuro(bedrag);
    }

    function samenvattingTekst(antwoord) {
        const bedrag = antwoord.jaarkosten_eur === null || antwoord.jaarkosten_eur === undefined
            ? "aansluitingskosten" : formatteerEuro(antwoord.jaarkosten_eur);
        return `Zolang er een positie op ${antwoord.beurs_naam || antwoord.beurs} staat, betaal je elk jaar ${bedrag}.`;
    }

    function notering(n) {
        return `${n.ticker} op ${n.beurs_naam}`;
    }

    function afwijkingTekst(n) {
        if (n.afwijking_pct === null || n.afwijking_pct === undefined) return "";
        return ` (${n.afwijking_pct > 0 ? "+" : ""}${formatGetal(n.afwijking_pct, 1)}%)`;
    }

    // Regels per positie: hoofdtekst, eventuele waarschuwing en eventuele extra opmerking.
    function positieTeksten(p) {
        const extra = p.al_in_bezit_op_thuisbeurs ? "Je hebt dit product al op een thuisbeurs." : null;
        if (p.status === "gevonden") {
            const nietOpIsin = p.voorkeur.waarschuwing === "niet_op_isin_bevestigd";
            return {
                naam: p.naam, tekst: `Gevonden op een thuisbeurs: ${notering(p.voorkeur)}.`,
                waarschuwing: nietOpIsin ? NIET_OP_ISIN_TEKST : null, extra,
            };
        }
        if (p.status === "waarschuwing") {
            const eerste = p.noteringen[0];
            const reden = p.waarschuwing === "koers wijkt af"
                ? `de koers wijkt af${afwijkingTekst(eerste)} van je huidige koers, dus niet gekozen`
                : "de koers kon niet vergeleken worden, dus niet gekozen";
            return { naam: p.naam, tekst: `Notering gevonden (${notering(eerste)}).`, waarschuwing: `Let op: ${reden}.`, extra };
        }
        if (p.status === "fout") {
            return { naam: p.naam, tekst: p.fout || FOUT_TEKST, waarschuwing: null, extra };
        }
        return { naam: p.naam, tekst: "Niet gevonden op een thuisbeurs.", waarschuwing: null, extra };
    }

    function terugverdientijdTekst(antwoord) {
        if (antwoord.terugverdientijd_jaren !== null && antwoord.terugverdientijd_jaren !== undefined) {
            return `${formatGetal(antwoord.terugverdientijd_jaren, 1)} jaar`;
        }
        return antwoord.blijft_betalen ? "niet van toepassing" : ONBEKEND;
    }

    function blijftBetalenTekst(antwoord) {
        if (!antwoord.blijft_betalen) return null;
        const bedrag = antwoord.jaarkosten_eur === null || antwoord.jaarkosten_eur === undefined
            ? "aansluitingskosten" : `${formatteerEuro(antwoord.jaarkosten_eur)} per jaar`;
        return `Niet alle posities hebben een alternatief op een thuisbeurs; je blijft dan ${bedrag} betalen.`;
    }

    function alternatievenPaneel(antwoord) {
        return {
            samenvatting: samenvattingTekst(antwoord),
            posities: antwoord.posities.map(p => ({
                ...positieTeksten(p), ticker: p.ticker, isin: p.isin || null, echteNaam: p.echte_naam || null,
                faseB: Boolean(p.fase_b_mogelijk), alGevonden: p.status === "gevonden",
            })),
            blijftBetalen: blijftBetalenTekst(antwoord),
            regels: [
                { label: "Besparing per jaar", tekst: bedragOfOnbekend(antwoord.besparing_per_jaar_eur) },
                {
                    label: "Geschatte overstapkosten",
                    tekst: antwoord.overstapkosten_schatting_eur === null || antwoord.overstapkosten_schatting_eur === undefined
                        ? ONBEKEND : `${formatteerEuro(antwoord.overstapkosten_schatting_eur)} (${SCHATTING_TOELICHTING})`,
                },
                { label: "Terugverdientijd", tekst: terugverdientijdTekst(antwoord) },
            ],
            ditJaarAlBetaald: antwoord.betaald_dit_jaar ? DIT_JAAR_AL_BETAALD : null,
            vasteRegels: VASTE_REGELS,
        };
    }

    // 500: algemene tekst; 400 (en andere antwoorden met een melding): de melding uit de JSON; time-out of netwerkfout
    // (status 504 of 0): "duurde te lang".
    function alternatievenFoutTekst(status, data) {
        if (status === 500) return ONGEVAL_TEKST;
        if (status === 0 || status === 504) return TE_LANG_TEKST;
        if (data && typeof data.error === "string" && data.error) return data.error;
        return FOUT_TEKST;
    }

    const exportsObj = {
        KNOP_TEKST, VASTE_REGELS, DIT_JAAR_AL_BETAALD, SCHATTING_TOELICHTING, FOUT_TEKST, ONGEVAL_TEKST, TE_LANG_TEKST, NIET_OP_ISIN_TEKST,
        alternatievenPaneel, alternatievenFoutTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
