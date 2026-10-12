// Pure logica voor de melding op Home als het rekeningoverzicht niet de hele periode beslaat (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    const KOP = "Je rekeningoverzicht (Account.xlsx) beslaat niet de hele periode. " +
        "Netto gestort, vrije ruimte (cash) en Totaal W/V kunnen daardoor niet kloppen.";
    const LINK_MET_CODE = "Upload een volledig overzicht";
    const LINK_ZONDER_CODE = "Upload bij de volgende analyse het volledige rekeningoverzicht";

    function datumNl(iso) {
        const [jaar, maand, dag] = iso.slice(0, 10).split("-");
        return `${dag}-${maand}-${jaar}`;
    }

    function periodeTekst(status) {
        if (!status.eerste_regel_datum) return null;
        const einde = status.laatste_regel_datum ? ` t/m ${datumNl(status.laatste_regel_datum)}` : "";
        const transacties = status.eerste_transactie_datum
            ? `; transacties vanaf ${datumNl(status.eerste_transactie_datum)}` : "";
        return `overzicht: ${datumNl(status.eerste_regel_datum)}${einde}${transacties}`;
    }

    // null bij een volledig overzicht of zonder overzicht (geen veld). `metCode` false = "niet opslaan".
    function rekeningoverzichtMelding(status, metCode) {
        if (!status || status.volledig !== false) return null;
        return {
            kop: KOP,
            redenen: status.redenen_tekst || [],
            periode: periodeTekst(status),
            linkTekst: metCode ? LINK_MET_CODE : LINK_ZONDER_CODE,
            linkNaarInstellingen: Boolean(metCode),
        };
    }

    const exportsObj = { rekeningoverzichtMelding };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof globalThis !== "undefined" ? globalThis : this);
