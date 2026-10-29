// Pure tekstopbouw voor "Zoek vergelijkbare fondsen op een thuisbeurs" (fase B van het alternatievenpaneel), zonder DOM,
// getest onder Node. Overlap, gelijkenis en selectie komen uit de API (`/vergelijkbare-etfs`); hier wordt niets berekend.

(function (root) {
    "use strict";

    const { formatGetal } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    const VERGELIJK_KNOP_TEKST = "Zoek vergelijkbare fondsen op een thuisbeurs";
    const VERGELIJK_AL_GEVONDEN_TEKST = "Exact hetzelfde fonds is op een thuisbeurs al te koop; dit zoekt daarnaast andere, "
        + "vergelijkbare fondsen.";
    const VERGELIJK_GEEN_KANDIDATEN_TEKST = "Geen vergelijkbare fondsen automatisch gevonden.";
    const VERGELIJK_ONVOLLEDIG_TEKST = "Niet alle kandidaten zijn binnen de tijd onderzocht; de lijst kan onvolledig zijn.";
    const VERGELIJK_BEZIG_TEKST = "Vergelijkbare fondsen opzoeken...";
    const VERGELIJK_FOUT_TEKST = "Vergelijkbare fondsen opzoeken is niet gelukt. Probeer het later opnieuw.";
    const VERGELIJK_ONGEVAL_TEKST = "Er ging iets mis bij het opzoeken van vergelijkbare fondsen.";
    const VERGELIJK_TE_LANG_TEKST = "Het ophalen duurde te lang. Probeer het later opnieuw.";
    const VERGELIJK_VASTE_REGELS = [
        "Dit is geen advies. Een vergelijkbaar fonds is niet hetzelfde: index, kosten en risico kunnen verschillen.",
        "Controleer in DEGIRO of het fonds op deze beurs te koop is.",
    ];
    const ONBEKEND = "onbekend";
    const UITKERINGSVORM_TEKST = { ACC: "accumulerend", DIS: "uitkerend" };

    function pct(waarde, decimalen = 1) {
        return waarde === null || waarde === undefined ? ONBEKEND : `${formatGetal(waarde, decimalen)}%`;
    }

    function uitkeringsvormTekst(vorm) {
        return UITKERINGSVORM_TEKST[vorm] || ONBEKEND;
    }

    function bedrijfRegel(b) {
        return `${b.naam} ${pct(b.gewicht_pct, 2)}`;
    }

    function alternatiefKaart(a, bronTop10) {
        return {
            titel: a.naam,
            notering: `${a.ticker} op ${a.beurs_naam}`,
            hoofdgetal: pct(a.overlap_pct),
            hoofdLabel: "overlap",
            regels: [
                { label: "Landen", tekst: `${pct(a.land_gelijkenis_pct)} gelijk` },
                { label: "Sectoren", tekst: a.sector_gelijkenis_pct === null || a.sector_gelijkenis_pct === undefined ? ONBEKEND : `${pct(a.sector_gelijkenis_pct)} gelijk` },
                { label: "Aantal holdings", tekst: formatGetal(a.aantal_holdings, 0) },
                { label: "Uitkeringsvorm", tekst: uitkeringsvormTekst(a.uitkeringsvorm) },
            ],
            top10: { bron: bronTop10.map(bedrijfRegel), alternatief: a.top10.map(bedrijfRegel) },
        };
    }

    function waarschuwingTekst(antwoord, bronNaam) {
        if (antwoord.waarschuwing === "bron_alleen_top10") {
            return `Van ${bronNaam} zijn alleen de 10 grootste posities bekend; de overlap is een onderschatting.`;
        }
        return null;
    }

    function geenAlternatievenTekst(antwoord) {
        return `Geen vergelijkbaar fonds op een thuisbeurs met minstens ${formatGetal(antwoord.min_overlap_pct, 0)}% overlap gevonden.`;
    }

    // `bronNaam`: de naam van de positie (uit het fase A-antwoord), voor de waarschuwing.
    function vergelijkbaarPaneel(antwoord, bronNaam) {
        const paneel = {
            melding: null, waarschuwing: waarschuwingTekst(antwoord, bronNaam || antwoord.naam), kaarten: [],
            telling: null, onvolledig: antwoord.onvolledig ? VERGELIJK_ONVOLLEDIG_TEKST : null,
            bronTop10Titel: bronNaam || antwoord.naam, vasteRegels: VERGELIJK_VASTE_REGELS,
        };
        if (antwoord.status === "fout") {
            return { ...paneel, melding: antwoord.fout || VERGELIJK_FOUT_TEKST };
        }
        if (antwoord.status === "geen_kandidaten") {
            return { ...paneel, melding: VERGELIJK_GEEN_KANDIDATEN_TEKST };
        }
        const telling = `${antwoord.aantal_onderzocht} van ${antwoord.aantal_kandidaten} kandidaten hadden een notering op een `
            + "thuisbeurs en volledige holdings.";
        if (antwoord.status === "geen_alternatieven") {
            return { ...paneel, melding: geenAlternatievenTekst(antwoord), telling };
        }
        return { ...paneel, telling, kaarten: antwoord.alternatieven.map(a => alternatiefKaart(a, antwoord.bron.top10)) };
    }

    // 500: algemene tekst; 400/404 (en andere antwoorden met een melding): de melding uit de JSON; time-out of netwerkfout
    // (status 504 of 0): "duurde te lang".
    function vergelijkFoutTekst(status, data) {
        if (status === 500) return VERGELIJK_ONGEVAL_TEKST;
        if (status === 0 || status === 504) return VERGELIJK_TE_LANG_TEKST;
        if (data && typeof data.error === "string" && data.error) return data.error;
        return VERGELIJK_FOUT_TEKST;
    }

    const exportsObj = {
        VERGELIJK_KNOP_TEKST, VERGELIJK_AL_GEVONDEN_TEKST, VERGELIJK_GEEN_KANDIDATEN_TEKST, VERGELIJK_ONVOLLEDIG_TEKST,
        VERGELIJK_BEZIG_TEKST, VERGELIJK_FOUT_TEKST, VERGELIJK_ONGEVAL_TEKST, VERGELIJK_TE_LANG_TEKST, VERGELIJK_VASTE_REGELS,
        vergelijkbaarPaneel, vergelijkFoutTekst, uitkeringsvormTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
