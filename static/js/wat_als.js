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
    const WAT_ALS_TOP_BEDRIJVEN = 10;
    const WAT_ALS_TOP_PROFIEL_BEDRIJVEN = 5;
    const WAT_ALS_OVERLAP_ONBEKEND = "Overlap onbekend: de bedrijven in dit fonds zijn niet beschikbaar";

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
        geen_dividend_data: "De dividendgegevens van dit effect konden niet worden opgehaald.",
        valuta_onbekend: "De valuta van dit effect is onbekend, dus het dividend is niet om te rekenen naar euro's.",
    };

    const WAT_ALS_GEEN_FONDSDATA = "Van dit fonds zijn land, sector en bedrijven niet beschikbaar.";

    const WAT_ALS_DIVIDEND_BRONNEN = {
        yahoo_reeks: "Som van de uitkeringen van de afgelopen 12 maanden volgens Yahoo.",
        dividend_rate: "Het verwachte jaardividend volgens Yahoo.",
        trailing_rate: "Het dividend van het afgelopen jaar volgens Yahoo.",
    };
    const WAT_ALS_BELASTING_BRONNEN = {
        land: "de bronbelasting van het land van de ISIN",
        standaard: "een standaardaanname, omdat de ISIN geen bekend land heeft",
        aanname_etf: "een aanname, omdat de ISIN van het fonds onbekend is",
    };
    const WAT_ALS_BELASTING_ONZEKER_TIP = "De ISIN van dit fonds is onbekend, dus de bronbelasting is een aanname (15 %). "
        + "Bij een Ierse of Luxemburgse ETF is er geen dividendbelasting en is netto gelijk aan bruto.";
    const WAT_ALS_DIVIDEND_NIET_UIT = "Keert geen dividend uit (vermoedelijk accumulerend)";
    const WAT_ALS_DIVIDEND_ONBEKEND = "Dividend onbekend";
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

    // Alleen als de profielholdings echt de top 10 zijn; bij provider-holdings of zonder holdings valt de zin weg.
    function watAlsGeenIsinTekst(zekerheid, holdings) {
        if (zekerheid !== "geen" || !holdings || holdings.bron !== "yfinance_top10") return null;
        return watAlsIsinTekst("geen", null);
    }

    // Waarschuwingsteksten van het profiel; geen_sector en geen_holdings samen wordt één regel.
    function watAlsWaarschuwingTeksten(codes) {
        const lijst = codes || [];
        const samen = lijst.includes("geen_sector") && lijst.includes("geen_holdings");
        const teksten = [];
        lijst.forEach(code => {
            if (!samen || (code !== "geen_sector" && code !== "geen_holdings")) teksten.push(watAlsWaarschuwingTekst(code));
            else if (!teksten.includes(WAT_ALS_GEEN_FONDSDATA)) teksten.push(WAT_ALS_GEEN_FONDSDATA);
        });
        return teksten;
    }

    // Kop van de bedrijvenlijst: bij een bekend totaalaantal "top 5 van 101".
    function watAlsBedrijvenKop(holdings) {
        const aantal = holdings && Number.isFinite(holdings.aantal) ? holdings.aantal : null;
        return aantal === null || aantal <= WAT_ALS_TOP_PROFIEL_BEDRIJVEN
            ? `Bedrijven (top ${WAT_ALS_TOP_PROFIEL_BEDRIJVEN})`
            : `Bedrijven (top ${WAT_ALS_TOP_PROFIEL_BEDRIJVEN} van ${aantal})`;
    }

    // Een volledig onbekende verdeling (bepaald door de backend) toont geen rijen: de lijst zegt dan "onbekend".
    function watAlsVerdelingRijen(verdeling, onbekend, n) {
        return onbekend ? [] : watAlsTopRijen(verdeling, n);
    }

    function watAlsSectorBronTekst(profiel) {
        if (!profiel || !profiel.sector_aanbieder) return null;
        if (profiel.sector_bron === "provider") {
            return `Sectoren volgens ${profiel.sector_aanbieder} (vertaald naar Morningstar-indeling)`;
        }
        if (profiel.sector_bron === "referentie" && Number.isFinite(profiel.sector_dekking)) {
            return `Sectoren per bedrijf opgezocht in ${profiel.sector_aanbieder} (${formatPct(profiel.sector_dekking, 1)} van het fonds gevonden); `
                + "de rest telt als onbekend.";
        }
        return null;
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

    function watAlsDividendBedrag(bedrag) {
        return formatteerEuro(bedrag, Math.abs(bedrag) < 1 ? 4 : 2);
    }

    function watAlsDividendHerkomst(bronCode, belastingBronCode) {
        return {
            bron: Object.hasOwn(WAT_ALS_DIVIDEND_BRONNEN, bronCode) ? WAT_ALS_DIVIDEND_BRONNEN[bronCode] : "",
            belastingBron: Object.hasOwn(WAT_ALS_BELASTING_BRONNEN, belastingBronCode)
                ? WAT_ALS_BELASTING_BRONNEN[belastingBronCode] : "een aanname",
        };
    }

    function watAlsNettoTeken(onzeker) {
        return onzeker ? "≈ " : "";
    }

    // Profielregel van dividend: {tekst, tip: {label, tekst} of null}; alle bedragen komen kant-en-klaar van de backend.
    function watAlsDividendRegel(dividend) {
        if (!dividend || dividend.status === "onbekend") return { tekst: WAT_ALS_DIVIDEND_ONBEKEND, tip: null };
        if (dividend.status === "keert_niet_uit") return { tekst: WAT_ALS_DIVIDEND_NIET_UIT, tip: null };
        const { bron, belastingBron } = watAlsDividendHerkomst(dividend.bron, dividend.belasting_bron);
        const yieldTekst = Number.isFinite(dividend.yield_bruto) ? ` (bruto ${formatPct(dividend.yield_bruto * 100, 2)})` : "";
        return {
            tekst: `Verwacht dividend: ${watAlsDividendBedrag(dividend.per_stuk_bruto_eur)} bruto / `
                + `${watAlsNettoTeken(dividend.belasting_onzeker)}${watAlsDividendBedrag(dividend.per_stuk_netto_eur)} netto per stuk per jaar${yieldTekst}`,
            tip: {
                label: "Verwacht dividend",
                tekst: `${bron} Netto is bruto min ${formatPct(dividend.belasting_fractie * 100, 0)} bronbelasting (${belastingBron}). `
                    + `Aantal uitkeringen in 12 maanden: ${dividend.uitkeringen_12m}.`
                    + (dividend.belasting_onzeker ? ` ${WAT_ALS_BELASTING_ONZEKER_TIP}` : ""),
            },
        };
    }

    // Resultaatregel van wat-als: het jaardividend voor het gekozen aantal, of de status als dat onbekend is.
    function watAlsDividendResultaatTekst(aankoop) {
        if (aankoop.dividend_status === "keert_niet_uit") return WAT_ALS_DIVIDEND_NIET_UIT;
        if (!Number.isFinite(aankoop.dividend_jaar_bruto_eur) || !Number.isFinite(aankoop.dividend_jaar_netto_eur)) {
            return WAT_ALS_DIVIDEND_ONBEKEND;
        }
        return `Verwacht dividend per jaar: ${formatteerEuro(aankoop.dividend_jaar_bruto_eur)} bruto / `
            + `${watAlsNettoTeken(aankoop.dividend_belasting_onzeker)}${formatteerEuro(aankoop.dividend_jaar_netto_eur)} netto`;
    }

    // Uitleg bij de resultaatregel; alleen bij een bekend uitkerend dividend. "aantal × per stuk" komt kant-en-klaar uit de backend.
    function watAlsDividendResultaatUitleg(aankoop) {
        if (!aankoop || aankoop.dividend_status !== "keert_uit") return null;
        const { aantal, dividend_per_stuk_bruto_eur: perStuk, dividend_jaar_bruto_eur: bruto } = aankoop;
        if (!Number.isFinite(aantal) || !Number.isFinite(perStuk) || !Number.isFinite(bruto)
            || !Number.isFinite(aankoop.dividend_belasting_fractie)) return null;
        const { bron, belastingBron } = watAlsDividendHerkomst(aankoop.dividend_bron, aankoop.dividend_belasting_bron);
        const uitkeringen = Number.isFinite(aankoop.dividend_uitkeringen_12m)
            ? ` Aantal uitkeringen in 12 maanden: ${aankoop.dividend_uitkeringen_12m}.` : "";
        return `${aantal} ${aantal === 1 ? "stuk" : "stuks"} × ${watAlsDividendBedrag(perStuk)} per stuk = ${formatteerEuro(bruto)} bruto. `
            + `${bron}${uitkeringen} Omgerekend tegen de huidige wisselkoers. `
            + `Netto = bruto min ${formatPct(aankoop.dividend_belasting_fractie * 100, 0)} bronbelasting (${belastingBron}). `
            + (aankoop.dividend_belasting_onzeker ? `${WAT_ALS_BELASTING_ONZEKER_TIP} ` : "")
            + "Niet herbelegd; geen garantie voor de toekomst.";
    }

    function watAlsDatum(iso) {
        const delen = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
        return delen ? `${delen[3]}-${delen[2]}-${delen[1]}` : "onbekende datum";
    }

    // Uitleg bij de koerssamenvatting boven de grafiek; noemt het dividend alleen als er in de periode dividend was.
    function watAlsRendementUitleg(uitleg, koersRendement, totaalRendement) {
        if (!uitleg || !Number.isFinite(uitleg.koers_begin_eur) || !Number.isFinite(uitleg.koers_eind_eur)) return null;
        const koers = `Koers: ${formatPct(koersRendement, 2, true)}, van ${formatteerEuro(uitleg.koers_begin_eur)} `
            + `(${watAlsDatum(uitleg.begin_datum)}) naar ${formatteerEuro(uitleg.koers_eind_eur)} (${watAlsDatum(uitleg.eind_datum)}).`;
        if (!(uitleg.dividend_som_eur > 0)) return `${koers} In EUR.`;
        return `${koers} Totaal: ${formatPct(totaalRendement, 2, true)}, koers plus ${formatteerEuro(uitleg.dividend_som_eur)} aan `
            + "bruto dividend in die periode (ex-datums), niet herbelegd, gedeeld door de beginkoers. In EUR.";
    }

    // Alleen de lineaire omzetting van de linker asgrenzen (EUR) naar rendement t.o.v. de beginkoers.
    function watAlsProcentGrenzen(min, max, basis) {
        if (!(basis > 0) || !Number.isFinite(min) || !Number.isFinite(max)) return null;
        return { min: (min / basis - 1) * 100, max: (max / basis - 1) * 100 };
    }

    function watAlsEuroAsTick(bedrag) {
        return formatteerEuro(bedrag, Math.abs(bedrag) >= 10 ? 0 : 2);
    }

    function watAlsProcentAsTick(pct) {
        return formatPct(Math.round(pct * 10) / 10, 0, true).replace("%", " %");
    }

    // indexWaarde komt uit de backend (koers_index / totaal_index); het percentage is index - 100.
    function watAlsKoersTooltipTekst(label, bedrag, indexWaarde) {
        return `${label}: ${formatteerEuro(bedrag)} (${formatPct(indexWaarde - 100, 1, true).replace("%", " %")})`;
    }

    const WAT_ALS_WEEK_AS_TIP = "Rechter as: ten opzichte van het eerste weekgemiddelde; het % boven de grafiek "
        + "gebruikt de dagkoersen en kan iets afwijken.";

    function watAlsMeldingZelfdeFonds(tickers) {
        if (!Array.isArray(tickers) || tickers.length === 0) return null;
        const wie = tickers.length === 1
            ? `je positie ${tickers[0]}`
            : `je posities ${tickers.slice(0, -1).join(", ")} en ${tickers[tickers.length - 1]}`;
        const positie = tickers.length === 1 ? "die positie" : "die posities";
        return `Dit is hetzelfde fonds als ${wie}, op een andere beurs. De berekening telt het toch als een nieuwe positie, `
            + `dus de overlap met ${positie} is hoog.`;
    }

    const WAT_ALS_ORDERKOSTEN_ONBEKEND = "Orderkosten onbekend";
    const WAT_ALS_ORDERKOSTEN_TIP = "Schatting uit je eigen orders; tarieven en promoties kunnen veranderen.";

    // Regels van de orderkosten: {tekst, aansluiting: tekst of null, tip}. Alle bedragen komen van de backend.
    function watAlsOrderkostenRegels(orderkosten) {
        const regels = { tekst: WAT_ALS_ORDERKOSTEN_ONBEKEND, aansluiting: null, tip: WAT_ALS_ORDERKOSTEN_TIP };
        if (!orderkosten) return regels;
        const beurzen = (orderkosten.beurs_namen || []).join(" en ");
        if (orderkosten.status === "geschat") {
            const periode = orderkosten.periode_dagen ? "laatste 12 maanden" : "uit al je orders";
            regels.tekst = `Geschatte orderkosten: ${formatteerEuro(orderkosten.mediaan_eur)} `
                + `(mediaan van ${orderkosten.aantal_orders} eigen orders op ${beurzen}, ${periode})`;
        } else if (orderkosten.status === "geen_eigen_orders") {
            regels.tekst = `${WAT_ALS_ORDERKOSTEN_ONBEKEND}: je hebt nog geen orders op ${beurzen}`;
        }
        const aansluiting = orderkosten.aansluitingskosten;
        if (aansluiting) {
            const eigen = Number.isFinite(aansluiting.bedrag_eur) && aansluiting.jaar
                ? ` Je betaalde hiervoor in ${aansluiting.jaar} ${formatteerEuro(aansluiting.bedrag_eur)}.` : "";
            regels.aansluiting = `Deze beurs is geen thuisbeurs en je hebt er geen open positie: er kunnen jaarlijkse aansluitingskosten bijkomen.${eigen}`;
        }
        return regels;
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

    // Hoofdregel van de overlapsectie; status en getallen komen uit de backend.
    function watAlsOverlapRegel(overlap) {
        if (overlap.holdings_status === "onbekend") return WAT_ALS_OVERLAP_ONBEKEND;
        const onvolledig = overlap.al_in_portfolio_onvolledig ? ` ${watAlsOverlapOnvolledigTekst(overlap.etf_zonder_holdings)}` : "";
        if (overlap.holdings_status === "top10") {
            return `Minstens ${watAlsAlInPortfolioTekst(overlap.al_in_portfolio_pct)} `
                + `(op basis van de top 10, ${formatPct(overlap.gedekt_pct, 1)} van het fonds)${onvolledig}`;
        }
        return `${watAlsAlInPortfolioTekst(overlap.al_in_portfolio_pct)}${onvolledig}`;
    }

    function watAlsOverlapOnvolledigTekst(aantal) {
        const wie = aantal === 1 ? "1 ETF" : Number.isFinite(aantal) ? `${aantal} ETF's` : "een ETF";
        return `(minstens; van ${wie} zijn de bedrijven onbekend)`;
    }

    function watAlsOverlapToontLijst(overlap) {
        return overlap.holdings_status !== "onbekend";
    }

    function watAlsOverlapEtfTekst(pct, holdingsStatus) {
        if (!Number.isFinite(pct)) return "onbekend";
        return holdingsStatus === "top10" ? `minstens ${formatPct(pct, 1)}` : formatPct(pct, 1);
    }

    const WAT_ALS_ONBEKEND_ONDERDELEN = { land: ["Land", "landen"], sector: ["Sector", "sectoren"], bedrijven: ["Top 10 bedrijven", "bedrijven"] };

    // Eén regel in plaats van een tabel als het onderdeel voor de aankoop helemaal onbekend is; anders null.
    function watAlsOnbekendTekst(aankoop, onderdeel) {
        if (!aankoop || !aankoop.onbekend || !aankoop.onbekend[onderdeel]) return null;
        const [kop, meervoud] = WAT_ALS_ONBEKEND_ONDERDELEN[onderdeel];
        return `${kop}: niet te berekenen. De ${meervoud} in ${aankoop.naam || aankoop.ticker} zijn onbekend.`;
    }

    // Top-5-bedrijven van het profiel: {rijen: [{naam, pct}], bronTekst of null}. Zonder holdings zijn de rijen leeg.
    function watAlsProfielBedrijven(holdings) {
        if (!holdings || !Array.isArray(holdings.top5)) return { rijen: [], bronTekst: null };
        const rijen = holdings.top5.slice(0, WAT_ALS_TOP_PROFIEL_BEDRIJVEN)
            .filter(h => Number.isFinite(h.gewicht))
            .map(h => ({ naam: h.naam, pct: h.gewicht * 100 }));
        const bronTekst = holdings.bron === "yfinance_top10" && Number.isFinite(holdings.gedekt_gewicht)
            ? `op basis van de 10 grootste holdings (${formatPct(holdings.gedekt_gewicht * 100, 1)} van het fonds)` : null;
        return { rijen, bronTekst };
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
        WAT_ALS_TOP_BEDRIJVEN, WAT_ALS_BALK_MAX_PP, WAT_ALS_WAARSCHUWING_TEKSTEN, WAT_ALS_ONBEKENDE_WAARSCHUWING,
        isWatAlsIsin, watAlsAantal, watAlsGeschatteWaarde, watAlsGeschatteWaardeTekst, watAlsWaarschuwingTekst,
        watAlsGeenIsinTekst, watAlsWaarschuwingTeksten, watAlsBedrijvenKop, watAlsVerdelingRijen, WAT_ALS_GEEN_FONDSDATA,
        watAlsSectorBronTekst, watAlsTypeLabel, watAlsNoteringLabel, watAlsIsinTekst, watAlsTopRijen, watAlsMeldingZelfdeFonds, watAlsBalk,
        watAlsRij, watAlsAlInPortfolioTekst, watAlsAlInBezitRegels, watAlsFoutTekst, WAT_ALS_TOP_PROFIEL_BEDRIJVEN,
        watAlsOverlapRegel, watAlsOverlapEtfTekst, watAlsOverlapToontLijst, watAlsOnbekendTekst, watAlsProfielBedrijven,
        watAlsDividendRegel, watAlsDividendResultaatTekst, watAlsDividendResultaatUitleg, watAlsRendementUitleg, watAlsOrderkostenRegels,
        watAlsProcentGrenzen, watAlsEuroAsTick, watAlsProcentAsTick, watAlsKoersTooltipTekst, WAT_ALS_WEEK_AS_TIP,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
