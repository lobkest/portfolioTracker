// Pure logica voor het tabblad Zoeken & wat als (zonder DOM, getest onder Node). Alle percentages en verschillen komen
// kant-en-klaar uit de backend; hier alleen teksten, opmaak en de breedte van een balkje.

(function (root) {
    "use strict";

    const { formatGetal, formatteerEuro, formatPct } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    // Gelijk aan de backend (wat_als.py en app.py).
    const WAT_ALS_MAX_AANTAL = 1000000;
    const WAT_ALS_MIN_ZOEKTEKENS = 2;
    const WAT_ALS_MAX_ZOEKTEKENS = 60;

    const WAT_ALS_TOP_LANDEN = 5;
    const WAT_ALS_TOP_SECTOREN = 5;
    const WAT_ALS_TOP_HOLDINGS = 10;
    const WAT_ALS_TOP_BEDRIJVEN = 10;

    // Een verschil van dit aantal procentpunten (of meer) vult het balkje helemaal.
    const WAT_ALS_BALK_MAX_PP = 20;

    const ISIN_PATROON = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

    const WAT_ALS_WAARSCHUWING_TEKSTEN = {
        geen_koers: "Er is geen actuele koers gevonden, dus de waarde van de aankoop is niet te berekenen.",
        geen_sector: "De sectorverdeling van dit fonds is niet beschikbaar.",
        geen_holdings: "De holdings van dit fonds zijn niet beschikbaar, dus land en bedrijven zijn onbekend.",
        alleen_top10: "Alleen de 10 grootste holdings zijn bekend. Land en bedrijven zijn daardoor een schatting.",
        isin_gekozen: "Je hebt een ISIN gekozen die niet aantoonbaar bij deze notering hoort. Controleer of dit de juiste variant van het fonds is.",
        land_via_proxy: "Het land is afgeleid uit een vergelijkbaar iShares-fonds, omdat van dit fonds alleen de top 10 bekend is.",
    };
    const WAT_ALS_ONBEKENDE_WAARSCHUWING = "Let op: er is een onverwachte opmerking over deze gegevens.";

    function isWatAlsIsin(tekst) {
        return ISIN_PATROON.test(String(tekst || "").trim().toUpperCase());
    }

    // Alleen een geheel aantal stuks; "1,5" of "1.5" wijzen we af in plaats van af te ronden.
    function watAlsAantal(tekst) {
        const schoon = String(tekst === null || tekst === undefined ? "" : tekst).trim();
        if (!/^\d+$/.test(schoon)) return { geldig: false, aantal: null };
        const aantal = Number(schoon);
        if (aantal < 1 || aantal > WAT_ALS_MAX_AANTAL) return { geldig: false, aantal: null };
        return { geldig: true, aantal };
    }

    // Alleen ter informatie bij het aantal-veld; de echte waarde rekent de backend uit.
    function watAlsGeschatteWaarde(aantal, koersEur) {
        if (!Number.isFinite(aantal) || !Number.isFinite(koersEur)) return null;
        return aantal * koersEur;
    }

    function watAlsGeschatteWaardeTekst(aantal, koersEur) {
        const waarde = watAlsGeschatteWaarde(aantal, koersEur);
        return waarde === null ? "" : `≈ ${formatteerEuro(waarde)}`;
    }

    function watAlsWaarschuwingTekst(code) {
        // hasOwn: een code als "constructor" mag niet bij Object.prototype uitkomen.
        return Object.hasOwn(WAT_ALS_WAARSCHUWING_TEKSTEN, code) ? WAT_ALS_WAARSCHUWING_TEKSTEN[code] : WAT_ALS_ONBEKENDE_WAARSCHUWING;
    }

    function watAlsTypeLabel(type) {
        return type === "ETF" ? "ETF" : "Aandeel";
    }

    function watAlsNoteringLabel(notering) {
        const beurs = notering.beurs_naam || notering.beurs;
        return beurs ? `${beurs} · ${notering.ticker}` : notering.ticker;
    }

    // Tekst bij het ISIN-oordeel van /api/isin-kandidaten.
    function watAlsIsinTekst(zekerheid, bron) {
        if (zekerheid === "zeker") {
            const herkomsten = {
                zoekopdracht: "uit je zoekopdracht",
                eigen_data: "uit je eigen transacties",
                ishares_naam: "gevonden op de naam van het fonds",
            };
            return Object.hasOwn(herkomsten, bron) ? `ISIN ${herkomsten[bron]}.` : "De ISIN van dit fonds is bekend.";
        }
        if (zekerheid === "kiezen") {
            return "Er zijn meerdere varianten van dit fonds (bijvoorbeeld uitkerend of accumulerend). Kies de juiste ISIN; "
                + "die bepaalt welke holdings gebruikt worden.";
        }
        return "Zonder ISIN zijn alleen de 10 grootste holdings bekend. Land en bedrijven zijn dan een schatting.";
    }

    // Eerste n namen van {naam: fractie 0-1}, aflopend, als {naam, pct (0-100)}: sorteren en weergave, geen berekening.
    function watAlsTopRijen(verdeling, n) {
        return Object.entries(verdeling || {})
            .filter(([, fractie]) => Number.isFinite(fractie))
            .sort((a, b) => b[1] - a[1])
            .slice(0, n)
            .map(([naam, fractie]) => ({ naam, pct: fractie * 100 }));
    }

    function watAlsMeldingZelfdeFonds(tickers) {
        if (!Array.isArray(tickers) || tickers.length === 0) return null;
        const wie = tickers.length === 1
            ? `je positie ${tickers[0]}`
            : `je posities ${tickers.slice(0, -1).join(", ")} en ${tickers[tickers.length - 1]}`;
        const positie = tickers.length === 1 ? "die positie" : "die posities";
        return `Dit is hetzelfde fonds als ${wie}, op een andere beurs. De berekening telt het toch als een nieuwe positie, `
            + `dus de overlap met ${positie} is hoog.`;
    }

    function watAlsBalk(verschilPp) {
        if (!Number.isFinite(verschilPp) || verschilPp === 0) return { richting: "geen", breedtePct: 0 };
        const breedtePct = Math.min(Math.abs(verschilPp) / WAT_ALS_BALK_MAX_PP, 1) * 100;
        return { richting: verschilPp > 0 ? "plus" : "min", breedtePct };
    }

    // Eén vóór/ná-rij van de backend ({naam, voor_pct, na_pct, verschil_pp}) als weergavetekst.
    function watAlsRij(rij) {
        const verschil = rij.verschil_pp;
        const teken = Number.isFinite(verschil) && Math.abs(verschil) >= 0.05 ? (verschil > 0 ? "+" : "−") : "";
        return {
            naam: rij.naam,
            voorTekst: formatPct(rij.voor_pct, 1),
            naTekst: formatPct(rij.na_pct, 1),
            verschilTekst: Number.isFinite(verschil) ? `${teken}${formatGetal(Math.abs(verschil), 1)} pp` : "onbekend",
            verschilKlasse: teken === "+" ? "positief" : teken === "−" ? "negatief" : "gedempt",
            balk: watAlsBalk(verschil),
        };
    }

    function watAlsAlInPortfolioTekst(pct) {
        return `${formatPct(pct, 1)} van deze aankoop zit al in je portfolio`;
    }

    // al_in_bezit van een aandeel: direct, via ETF's en per ETF.
    function watAlsAlInBezitRegels(alInBezit) {
        if (!alInBezit || !alInBezit.gematchte_naam) {
            return ["Dit bedrijf zat niet in je portfolio, ook niet via je ETF's. Let op: dit matcht op de naam van het bedrijf en kan dus iets missen."];
        }
        const regels = [
            `Direct: ${formatteerEuro(alInBezit.direct_eur)}. Via je ETF's: ${formatteerEuro(alInBezit.via_etf_eur)}.`,
        ];
        Object.entries(alInBezit.per_etf || {}).sort((a, b) => b[1] - a[1])
            .forEach(([ticker, eur]) => regels.push(`${ticker}: ${formatteerEuro(eur)}`));
        regels.push("Dit matcht op de naam van het bedrijf en kan dus iets missen.");
        return regels;
    }

    // De foutmelding van de backend wint bij 400 en 429 (die noemt de wachttijd); de rest krijgt een eigen tekst.
    function watAlsFoutTekst(status, backendTekst) {
        if (status === 400 || status === 429) return backendTekst || "Deze aanvraag is niet toegestaan.";
        if (status === 404) return "Niet gevonden.";
        if (status === 422) return "Voor dit effect is geen actuele koers beschikbaar.";
        return "Er ging iets mis. Probeer het later opnieuw.";
    }

    const exportsObj = {
        WAT_ALS_MAX_AANTAL, WAT_ALS_MIN_ZOEKTEKENS, WAT_ALS_MAX_ZOEKTEKENS, WAT_ALS_TOP_LANDEN, WAT_ALS_TOP_SECTOREN,
        WAT_ALS_TOP_HOLDINGS, WAT_ALS_TOP_BEDRIJVEN, WAT_ALS_BALK_MAX_PP, WAT_ALS_WAARSCHUWING_TEKSTEN, WAT_ALS_ONBEKENDE_WAARSCHUWING,
        isWatAlsIsin, watAlsAantal, watAlsGeschatteWaarde, watAlsGeschatteWaardeTekst, watAlsWaarschuwingTekst,
        watAlsTypeLabel, watAlsNoteringLabel, watAlsIsinTekst, watAlsTopRijen, watAlsMeldingZelfdeFonds, watAlsBalk,
        watAlsRij, watAlsAlInPortfolioTekst, watAlsAlInBezitRegels, watAlsFoutTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
