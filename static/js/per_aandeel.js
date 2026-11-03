// Pure logica voor het tabblad Per aandeel (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    // null = geen blok (ETF of verrijking nog niet binnen).
    function aandeelLandSectorRegels(perAandeel, ticker) {
        const info = perAandeel && perAandeel[ticker];
        if (!info) return null;
        return [["Land", info.land], ["Sector", info.sector]].map(([label, waarde]) => {
            const onbekend = !waarde || waarde === "Unknown";
            return { label, tekst: onbekend ? "onbekend" : waarde, onbekend };
        });
    }

    const { formatPct, formatteerEuro } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;
    const { sorteerTransacties } = typeof module !== "undefined" && module.exports ? require("./transacties.js") : root;

    // Huidige waarde in EUR van een positie in bezit; null bij een gesloten of onbekende positie.
    function etfPositieWaarde(statistieken, ticker) {
        const positie = ((statistieken && statistieken.posities) || []).find(p => p.ticker === ticker);
        return positie && positie.huidige_waarde != null ? positie.huidige_waarde : null;
    }

    // {tekst, grijs} onder de holdings-tabel van een ETF; null zonder holdings (data = antwoord van /api/etf-holdings).
    function etfHoldingsBijschrift(data) {
        if (!data || !data.holdings || data.holdings.length === 0) return null;
        if (data.bron === "provider_csv") {
            return { tekst: `Volledige lijst van de fondsaanbieder: ${data.aantal} bedrijven`, grijs: false };
        }
        return { tekst: `Alleen de top 10 via Yahoo, samen ${formatPct(data.gedekt_gewicht * 100, 1)} van het fonds`, grijs: true };
    }

    // info = statistieken.kosten_per_ticker[ticker]; onbekend (grijs) zonder info of zonder enig kostenbedrag, nooit €0,00.
    function aandeelKostenTekst(info) {
        if (!info || (info.transactiekosten == null && info.autofx_kosten == null)) {
            return { tekst: "Transactiekosten onbekend", onbekend: true };
        }
        const delen = [];
        if (info.transactiekosten == null) {
            delen.push("Transactiekosten onbekend");
        } else {
            const n = info.aantal_transacties;
            const aantal = n == null ? "" : ` (${n} ${n === 1 ? "transactie" : "transacties"})`;
            delen.push(`Transactiekosten ${formatteerEuro(info.transactiekosten)}${aantal}`);
        }
        if (info.autofx_kosten > 0) delen.push(`AutoFX ${formatteerEuro(info.autofx_kosten)}`);
        if (info.kostenkorting > 0) delen.push(`promotie −${formatteerEuro(info.kostenkorting)}`);
        return { tekst: delen.join(" · "), onbekend: false };
    }

    // Transacties van één positie, oud → nieuw; rijen zonder ticker vallen weg. De invoer blijft ongewijzigd.
    function transactiesVoorTicker(lijst, ticker) {
        return sorteerTransacties((lijst || []).filter(r => r.ticker === ticker), "datum_tijd", "asc");
    }

    // De lijst toont ook omboekings- en corporate-action-rijen die aantal_transacties niet telt; null als er geen verschil is.
    function boekingsrijenTekst(aantalRijen, info) {
        const extra = info ? aantalRijen - info.aantal_transacties : 0;
        if (!(extra > 0)) return null;
        return `Inclusief ${extra} ${extra === 1 ? "boekingsrij" : "boekingsrijen"} van DEGIRO (split/productwijziging), zonder kosten.`;
    }

    // Koers op de labels waarvan de ISO-datum in `datums` zit, anders null (één punt per aan-/verkoopdag).
    function koersOpDatums(labels, koers, datums) {
        const set = new Set(datums || []);
        return labels.map((label, i) => (set.has(label) && koers[i] != null ? koers[i] : null));
    }

    const exportsObj = { aandeelLandSectorRegels, etfPositieWaarde, etfHoldingsBijschrift, aandeelKostenTekst, transactiesVoorTicker, boekingsrijenTekst, koersOpDatums };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
