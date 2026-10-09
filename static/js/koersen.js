// Pure logica voor de koers- en tickermelding bovenaan het dashboard en de splitlabels in de koersgrafiek (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    const { formatGetal } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    function positieNamen(posities) {
        return posities.map(p => (p.naam && p.naam !== p.ticker ? `${p.naam} (${p.ticker})` : p.ticker)).join(", ");
    }

    function aantalPosities(n) {
        return n === 1 ? "1 positie" : `${n} posities`;
    }

    // null als alle posities koersen hebben.
    function koersMeldingTekst(onvolledig, ontbreken) {
        const delen = [];
        if (onvolledig && onvolledig.length) {
            delen.push(`Nog geen koersen voor ${aantalPosities(onvolledig.length)} (${positieNamen(onvolledig)}): het ophalen ` +
                "kostte te lang en gaat verder als je het portfolio opnieuw opent. Tot dan zijn waarde en rendement te laag.");
        }
        if (ontbreken && ontbreken.length) {
            delen.push(`Yahoo Finance geeft geen koersen voor ${aantalPosities(ontbreken.length)} (${positieNamen(ontbreken)}): ` +
                "die tellen niet mee in de waarde, de inleg wel.");
        }
        return delen.length ? `⚠️ ${delen.join(" ")}` : null;
    }

    const TICKER_REDEN_TEKST = {
        koers: "wijkt de koers meer dan verwacht af van Yahoo Finance",
        beide: "wijkt de koers af van Yahoo Finance én staat de ticker niet bij OpenFIGI",
    };

    function tickerReden(w) {
        const redenen = w.redenen || ["koers"];
        if (redenen.includes("koers") && redenen.includes("openfigi")) return "beide";
        return redenen.includes("openfigi") ? "openfigi" : "koers";
    }

    // [{ticker, naam, redenen}] uit de backend; null zonder waarschuwingen. Eén zin per reden.
    // Alleen een OpenFIGI-mismatch geeft bewust geen banner: die staat alleen op het Ticker-zekerheid-tabblad.
    function tickerWaarschuwingTekst(waarschuwingen) {
        if (!waarschuwingen || !waarschuwingen.length) return null;
        const zinnen = ["koers", "beide"].map(reden => {
            const groep = waarschuwingen.filter(w => tickerReden(w) === reden);
            if (!groep.length) return null;
            const namen = groep.map(w => w.naam || w.ticker).join(", ");
            return `Bij ${aantalPosities(groep.length)} (${namen}) ${TICKER_REDEN_TEKST[reden]}.`;
        }).filter(Boolean);
        if (!zinnen.length) return null;
        return `⚠️ ${zinnen.join(" ")} Controleer het Ticker-zekerheid-tabblad.`;
    }

    // Yahoo-ratio: 4 = 4 nieuwe stukken voor 1 oud, 0,05 = 1 nieuw voor 20 oud.
    function splitLabel(ratio) {
        return ratio >= 1 ? `Split ${formatGetal(ratio, 2, 0)}:1` : `Reverse split 1:${formatGetal(1 / ratio, 2, 0)}`;
    }

    // Index van de eerste grafiekdatum op of na de splitdatum (ISO-datums), of -1.
    function splitLabelIndex(labelsIso, splitDatum) {
        return labelsIso.findIndex(label => label >= splitDatum);
    }

    // Kandidaten zonder koersdata én zonder valuta/land/sector zeggen niets; die worden alleen geteld.
    function splitsAlternatieven(alternatieven) {
        const zichtbaar = [];
        let aantalVerborgen = 0;
        (alternatieven || []).forEach(alt => {
            const leeg = !alt.aantal_gecontroleerd && !alt.valuta && !alt.land && !alt.sector;
            if (leeg) aantalVerborgen += 1;
            else zichtbaar.push(alt);
        });
        return { zichtbaar, aantalVerborgen };
    }

    function verborgenAlternatievenTekst(aantal) {
        if (!aantal) return null;
        return `${aantal} ${aantal === 1 ? "kandidaat" : "kandidaten"} zonder koersdata verborgen`;
    }

    function prijscontroleYahooTekst(c) {
        if (c.yahoo_koers == null) return "onbekend";
        return c.yahoo_koers_gecorrigeerd != null ? `${formatGetal(c.yahoo_koers_gecorrigeerd, 3)} *` : formatGetal(c.yahoo_koers, 3);
    }

    function prijscontroleSubregel(c) {
        const excel = c.bekende_koers != null ? formatGetal(c.bekende_koers, 3) : "-";
        return `Excel ${excel} · Yahoo ${prijscontroleYahooTekst(c)}`;
    }

    // null zonder split-correctie.
    function prijscontroleSplitTekst(c) {
        if (c.yahoo_koers_gecorrigeerd == null) return null;
        return `×${formatGetal(c.split_factor, 4, 0)}, ruwe Yahoo-koers ${formatGetal(c.yahoo_koers, 3)}`;
    }

    const DAGRANGE_OORDEEL = {
        true: { tekst: "✓", klasse: "positief", uitleg: "Excel-koers valt binnen het intraday-high/low van deze handelsdag (±2% of €0,50)" },
        false: { tekst: "✗", klasse: "negatief", uitleg: "Excel-koers valt buiten het intraday-high/low van deze handelsdag (±2% of €0,50)" },
        null: { tekst: "–", klasse: "gedempt", uitleg: "Geen High/Low-data beschikbaar voor deze datum" },
    };

    function dagrangeOordeel(binnenDagrange) {
        return DAGRANGE_OORDEEL[typeof binnenDagrange === "boolean" ? binnenDagrange : null];
    }

    function alternatiefDagrangeTekst(alt) {
        if (!alt.aantal_gecontroleerd) return "geen prijsdata";
        return `${alt.aantal_matches}/${alt.aantal_gecontroleerd}${alt.uitkeringsvorm_strijdig ? " (DIS/ACC wijkt af)" : ""}`;
    }

    function alternatiefSubregel(alt, isEtf) {
        const delen = [alt.beurs || "onbekend", alt.valuta || "onbekend"];
        if (!isEtf) delen.push(alt.land || "onbekend", alt.sector || "onbekend");
        return delen.join(" · ");
    }

    function alternatiefControleTekst(alt) {
        if (!alt.aantal_gecontroleerd) return "geen koersdata om te controleren";
        return `${alt.aantal_matches} van ${alt.aantal_gecontroleerd} datums binnen de dagrange`;
    }

    // null als de uitkeringsvorm niet strijdig is.
    function alternatiefUitkeringsvormTekst(alt) {
        return alt.uitkeringsvorm_strijdig ? "DIS/ACC in de Yahoo-naam wijkt af van de DeGiro-naam" : null;
    }

    const exportsObj = {
        koersMeldingTekst, tickerWaarschuwingTekst, splitLabel, splitLabelIndex,
        splitsAlternatieven, verborgenAlternatievenTekst,
        prijscontroleYahooTekst, prijscontroleSubregel, prijscontroleSplitTekst, dagrangeOordeel,
        alternatiefDagrangeTekst, alternatiefSubregel, alternatiefControleTekst, alternatiefUitkeringsvormTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
