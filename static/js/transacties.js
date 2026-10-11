// Sorteren en pagineren voor Transacties (zonder DOM, getest onder Node).
// maakSorteerbareTabel() gebruikt dit voor Transacties: sorteren (ook op tekst en datum+tijd) over alle pagina's heen.

(function (root) {
    "use strict";

    const { formatteerEuro } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    // "YYYY-MM-DDTHH:MM" sorteert als string chronologisch; een ontbrekende tijd telt als 00:00.
    const timestampSleutel = r => `${r.datum}T${r.tijd ?? "00:00"}`;
    const VERGELIJKERS = {
        datum_tijd: (a, b) => {
            const sa = timestampSleutel(a);
            const sb = timestampSleutel(b);
            return sa < sb ? -1 : sa > sb ? 1 : 0;
        },
        product: (a, b) => String(a.product).localeCompare(String(b.product), "nl"),
        isin: (a, b) => String(a.isin ?? "").localeCompare(String(b.isin ?? "")),
        beurs: (a, b) => String(a.beurs ?? "").localeCompare(String(b.beurs ?? "")),
        aantal: (a, b) => a.aantal - b.aantal,
        koers: (a, b) => (a.koers ?? -Infinity) - (b.koers ?? -Infinity),
        totaal_eur: (a, b) => a.totaal_eur - b.totaal_eur,
        wisselkoers: (a, b) => (a.wisselkoers ?? -Infinity) - (b.wisselkoers ?? -Infinity),
        transactiekosten: (a, b) => (a.transactiekosten ?? -Infinity) - (b.transactiekosten ?? -Infinity),
        resultaat_eur: (a, b) => a.resultaat_eur - b.resultaat_eur,
        dividend_eur: (a, b) => a.dividend_eur - b.dividend_eur,
    };
    // Alleen aankooprijen hebben een waarde: verkopen en onbekende koersen staan in beide richtingen onderaan.
    const LEEG_ONDERAAN = new Set(["resultaat_eur", "dividend_eur"]);

    // Sorteert een kopie; de invoer blijft ongewijzigd.
    function sorteerTransacties(rijen, kolomKey, richting) {
        const vergelijk = VERGELIJKERS[kolomKey];
        if (!vergelijk) return rijen.slice();
        const leegOnderaan = LEEG_ONDERAAN.has(kolomKey);
        const metWaarde = leegOnderaan ? rijen.filter(r => r[kolomKey] != null) : rijen.slice();
        const gesorteerd = metWaarde.sort(vergelijk);
        if (richting === "desc") gesorteerd.reverse();
        return leegOnderaan ? gesorteerd.concat(rijen.filter(r => r[kolomKey] == null)) : gesorteerd;
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

    // /transacties: {lijst, promotie}; promotie ontbreekt of is null zonder promotieregels.
    function leesTransactiesAntwoord(data) {
        return { lijst: data.lijst ?? [], promotie: data.promotie ?? null };
    }

    const datumNl = iso => iso.slice(0, 10).split("-").reverse().join("-");

    // "Toegerekend": het rekeningoverzicht zegt niet welke orders de korting kregen.
    function promotieLabelTekst(kortingEur) {
        return `promotie −${formatteerEuro(kortingEur)}`;
    }

    function promotieUitlegTekst(kortingEur) {
        return `DEGIRO-promotie: ${formatteerEuro(kortingEur)} kostenkorting, toegerekend aan deze transactie.`;
    }

    // promotie uit /transacties, totalen uit de statistieken (mogen ontbreken); null zonder toegepaste korting.
    function promotieSamenvattingTekst(promotie, totalen) {
        if (!promotie || !(promotie.bedrag_eur > 0)) return null;
        const regels = promotie.regels || [];
        const datums = regels.map(r => datumNl(r.datum));
        const wanneer = datums.length > 1
            ? `Promoties DEGIRO ${datums.slice(0, -1).join(", ")} en ${datums[datums.length - 1]}`
            : `Promotie DEGIRO ${datums[0] ?? datumNl(promotie.datum)}`;
        let tekst = `${wanneer}: ${formatteerEuro(promotie.bedrag_eur)} kostenkorting, toegerekend aan de eerste transacties.`;
        const somRegels = Math.round(regels.reduce((som, r) => som + r.bedrag_eur, 0) * 100) / 100;
        if (somRegels > promotie.bedrag_eur) {
            tekst += ` ${formatteerEuro(somRegels - promotie.bedrag_eur)} niet verrekend (te weinig kosten).`;
        }
        if (totalen && totalen.totale_transactiekosten != null && totalen.totale_transactiekosten_betaald != null) {
            tekst += ` Netto kosten: ${formatteerEuro(totalen.totale_transactiekosten)} `
                + `(betaald ${formatteerEuro(totalen.totale_transactiekosten_betaald)}).`;
        }
        return tekst;
    }

    const exportsObj = {
        sorteerTransacties, totaalPaginas, pagineer, leesTransactiesAntwoord, promotieLabelTekst, promotieUitlegTekst, promotieSamenvattingTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
