// Pure logica voor de melding op Home als het transactiebestand niet de hele periode lijkt te beslaan (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    const KOP = "Je transactiebestand (Transactions.xlsx) lijkt niet de hele periode te beslaan. " +
        "Posities, GAK en rendement kunnen daardoor niet kloppen.";
    const LINK_MET_CODE = "Upload een volledig transactiebestand";
    const LINK_ZONDER_CODE = "Upload bij de volgende analyse het volledige transactiebestand";

    function datumNl(iso) {
        const [jaar, maand, dag] = iso.slice(0, 10).split("-");
        return `${dag}-${maand}-${jaar}`;
    }

    function periodeTekst(status) {
        if (!status.eerste_transactie_datum) return null;
        const einde = status.laatste_transactie_datum ? ` t/m ${datumNl(status.laatste_transactie_datum)}` : "";
        return `transacties: ${datumNl(status.eerste_transactie_datum)}${einde}`;
    }

    // null bij een volledig bestand of zonder veld. `metCode` false = "niet opslaan".
    function transactiebestandMelding(status, metCode) {
        if (!status || status.volledig !== false) return null;
        return {
            kop: KOP,
            redenen: (status.redenen || []).map(reden => reden.tekst),
            periode: periodeTekst(status),
            linkTekst: metCode ? LINK_MET_CODE : LINK_ZONDER_CODE,
            linkNaarInstellingen: Boolean(metCode),
        };
    }

    const exportsObj = { transactiebestandMelding };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof globalThis !== "undefined" ? globalThis : this);
