// Pure logica voor het "Top N bedrijven"-tabblad: nettere bedrijfsnamen
// (alleen voor weergave), de keuze van N en het inkorten van de door de
// backend meegeleverde lijst. Los van DOM/Chart.js gehouden (net als menu.js
// en transacties.js) zodat het zowel in de browser (index.html) als onder
// Node (tests/test_bedrijven.js) draait.
//
// BELANGRIJK: de opmaak van namen is UITSLUITEND voor weergave. Groeperen,
// optellen en matchen (Top-bedrijven, ETF-overlap) gebeurt in de backend op
// de ruwe naam via _normaliseer_bedrijfsnaam() -- gebruik
// maakBedrijfsnaamLeesbaar() nooit als sleutel of om holdings te matchen.

(function (root) {
    "use strict";

    // Snelknoppen naast het invulveld. Moeten <= BEDRIJVEN_TOP_N_MAX zijn
    // (portfolio_verdeling.py); de eerste is ook de standaard-keuze.
    const BEDRIJVEN_TOP_N_KNOPPEN = [10, 20, 50];

    // Vanaf zoveel bedrijven (of op een smal scherm) horizontale staven i.p.v.
    // staande: bij veel bedrijven worden staande staven met volledige namen
    // onleesbaar. De breedte is gelijk aan het mobiele breakpoint in style.css.
    const BEDRIJVEN_HORIZONTAAL_VANAF = 15;
    const BEDRIJVEN_SMAL_SCHERM_MAX_BREEDTE = 768;

    // Juridische achtervoegsels die aan het eind van de naam wegvallen
    // (vergelijking op kleine letters, zonder punten/komma's: "Inc." = inc).
    // "limited" staat erbij als voluit geschreven "Ltd".
    const JURIDISCHE_SUFFIXEN = new Set([
        "inc", "corp", "corporation", "ltd", "limited", "plc", "nv", "sa", "ag", "se", "co", "company",
    ]);

    // Vaste schrijfwijze van een juridisch achtervoegsel dat WEL blijft staan
    // (alleen bij een botsing tussen twee bedrijven, zie maakUniekeWeergaveNamen).
    const SUFFIX_WEERGAVE = {
        inc: "Inc", corp: "Corp", corporation: "Corporation", ltd: "Ltd", limited: "Limited",
        plc: "PLC", nv: "NV", sa: "SA", ag: "AG", se: "SE", co: "Co", company: "Company",
    };

    // Afkortingen/merknamen die in hoofdletters blijven. Naast deze lijst
    // blijft ook elk woord van max. 4 letters zonder klinker (HSBC, UBS, BP,
    // KKR, LG, ...) in hoofdletters, zie ZONDER_KLINKER_MAX_LENGTE.
    const HOOFDLETTER_WOORDEN = new Set([
        "ASML", "TSMC", "SAP", "AMD", "IBM", "ABB", "ING", "AXA", "BNP", "LVMH", "BASF", "BMW", "RWE",
        "ENI", "OMV", "BAE", "CRH", "BNY", "ANZ", "NAB", "AIA", "ADP", "GE", "AT&T", "UPS", "ADR", "GDR",
        "REIT", "USA", "UK", "US", "ITC", "TCS", "HDFC", "ICICI", "BYD", "NTT", "KDDI", "TDK", "ASM",
        "UBS", "ABN",
    ]);
    const ZONDER_KLINKER_MAX_LENGTE = 4;
    // Korte woorden zonder klinker die géén afkorting zijn.
    const GEEN_AFKORTING = new Set(["ST", "MR", "MRS", "DR", "SR", "JR"]);

    // Woorden met een eigen schrijfwijze (sleutel: kleine letters).
    const WOORD_SCHRIJFWIJZEN = {
        "jpmorgan": "JPMorgan",
        "paypal": "PayPal",
        "ebay": "eBay",
        "mcdonald's": "McDonald's",
        "l'oreal": "L'Oréal",
    };

    // Hele namen (sleutel: kleine letters, ZONDER juridisch achtervoegsel en
    // zonder "Class X") met een eigen schrijfwijze. "Amazon.com" komt bij
    // iShares als "AMAZON COM INC" binnen, zonder punt.
    const NAAM_SCHRIJFWIJZEN = {
        "amazon com": "Amazon.com",
        "amazon.com": "Amazon.com",
        "jd.com": "JD.com",
    };

    // Kleine verbindingswoorden blijven klein (behalve als eerste woord).
    const KLEINE_WOORDEN = new Set(["of", "and", "de", "van", "von", "der", "den", "del", "la", "le", "du", "des", "di", "da", "et"]);

    const KLASSE_WOORDEN = /^(class|cl|klasse)\.?$/i;
    const KLASSE_LETTER = /^[a-z0-9]{1,3}$/i;

    function titelCase(woord) {
        let uit = "";
        let hoofdletterVolgende = true;
        for (let i = 0; i < woord.length; i++) {
            const c = woord[i];
            uit += hoofdletterVolgende ? c.toUpperCase() : c.toLowerCase();
            // Streepje/slash/haakje/& starten een nieuw deel; een apostrof alleen
            // na één letter (L'Oreal, D'Ieteren), niet bij "McDonald's".
            hoofdletterVolgende = c === "-" || c === "/" || c === "(" || c === "&" || (c === "'" && i === 1);
        }
        return uit;
    }

    function formatteerWoord(woord, isEerste) {
        const zonderLeesteken = woord.replace(/[.,]+$/, "");
        const sleutel = zonderLeesteken.toLowerCase();
        if (Object.prototype.hasOwnProperty.call(WOORD_SCHRIJFWIJZEN, sleutel)) {
            return WOORD_SCHRIJFWIJZEN[sleutel] + woord.slice(zonderLeesteken.length);
        }
        const hoofd = zonderLeesteken.toUpperCase();
        if (HOOFDLETTER_WOORDEN.has(hoofd)) return hoofd + woord.slice(zonderLeesteken.length);

        const letters = zonderLeesteken.replace(/[^a-zA-Z]/g, "");
        const isAllesHoofdletters = letters.length > 0 && zonderLeesteken === hoofd;
        if (isAllesHoofdletters) {
            if (letters.length < 2) return woord; // "A", "3M": laten staan
            if (letters.length <= ZONDER_KLINKER_MAX_LENGTE && !/[AEIOUY]/i.test(letters) && !GEEN_AFKORTING.has(hoofd)) {
                return woord;
            }
            if (!isEerste && KLEINE_WOORDEN.has(sleutel)) return sleutel + woord.slice(zonderLeesteken.length);
            return titelCase(woord);
        }
        if (letters.length > 0 && zonderLeesteken === zonderLeesteken.toLowerCase()) {
            if (!isEerste && KLEINE_WOORDEN.has(sleutel)) return woord;
            return woord.charAt(0).toUpperCase() + woord.slice(1);
        }
        return woord; // gemengde hoofdletters (Apple, eBay): de bron weet het best
    }

    // Maakt van een ruwe holdingsnaam (meestal HOOFDLETTERS uit een
    // ETF-bestand) een nette weergavenaam: nette hoofdletters, juridisch
    // achtervoegsel weg, onderscheidende "Class A/B/C" blijft. Lege of
    // niet-tekst-invoer komt ongewijzigd terug. opties.behoudSuffix laat het
    // juridische achtervoegsel staan (gebruikt door maakUniekeWeergaveNamen).
    function maakBedrijfsnaamLeesbaar(naam, opties) {
        if (typeof naam !== "string" || naam.trim() === "") return naam;
        const behoudSuffix = Boolean(opties && opties.behoudSuffix);

        // "META PLATFORMS INC- CLASS A" / "ALPHABET INC. - CLASS A": een
        // losstaand streepje weg, streepjes binnen een woord (Coca-Cola) blijven.
        let tokens = naam.trim().replace(/\s+-\s*|\s*-\s+/g, " ").replace(/\s+/g, " ").split(" ");

        let klasse = [];
        const n = tokens.length;
        if (n >= 3 && KLASSE_WOORDEN.test(tokens[n - 2]) && KLASSE_LETTER.test(tokens[n - 1])) {
            klasse = ["Class", tokens[n - 1].toUpperCase()];
            tokens = tokens.slice(0, n - 2);
        }

        if (!behoudSuffix) {
            while (tokens.length > 1) {
                const schoon = tokens[tokens.length - 1].replace(/[.,]/g, "").toLowerCase();
                if (JURIDISCHE_SUFFIXEN.has(schoon) || schoon === "&" || schoon === "") {
                    tokens.pop();
                } else {
                    break;
                }
            }
            tokens[tokens.length - 1] = tokens[tokens.length - 1].replace(/,+$/, "");
        }

        const naamSleutel = tokens.join(" ").toLowerCase();
        if (!behoudSuffix && Object.prototype.hasOwnProperty.call(NAAM_SCHRIJFWIJZEN, naamSleutel)) {
            return [NAAM_SCHRIJFWIJZEN[naamSleutel]].concat(klasse).join(" ");
        }

        // Bij behoudSuffix: de staart van juridische achtervoegsels krijgt een
        // vaste schrijfwijze, de rest de gewone opmaak.
        let kernEinde = tokens.length;
        if (behoudSuffix) {
            while (kernEinde > 1 && SUFFIX_WEERGAVE[tokens[kernEinde - 1].replace(/[.,]/g, "").toLowerCase()]) {
                kernEinde--;
            }
        }
        const kern = tokens.slice(0, kernEinde).map((t, i) => formatteerWoord(t, i === 0));
        const staart = tokens.slice(kernEinde).map(t => SUFFIX_WEERGAVE[t.replace(/[.,]/g, "").toLowerCase()]);
        return kern.concat(staart, klasse).join(" ");
    }

    // Weergavenamen voor een lijst ruwe namen, gelijk aan de volgorde van de
    // invoer. Twee VERSCHILLENDE ruwe namen krijgen nooit hetzelfde label:
    // botsen ze na de opmaak (bv. "Rio Tinto PLC" en "Rio Tinto Ltd", beide
    // "Rio Tinto"), dan houden die het juridische achtervoegsel, en als ook dat
    // niet onderscheidt, de ruwe naam.
    function maakUniekeWeergaveNamen(ruweNamen) {
        const nette = ruweNamen.map(r => maakBedrijfsnaamLeesbaar(r));
        const ruwPerNet = new Map();
        ruweNamen.forEach((r, i) => {
            if (!ruwPerNet.has(nette[i])) ruwPerNet.set(nette[i], new Set());
            ruwPerNet.get(nette[i]).add(r);
        });

        return ruweNamen.map((r, i) => {
            if (ruwPerNet.get(nette[i]).size <= 1) return nette[i];
            const metSuffix = maakBedrijfsnaamLeesbaar(r, { behoudSuffix: true });
            const zelfdeMetSuffix = new Set(
                Array.from(ruwPerNet.get(nette[i])).filter(
                    andere => maakBedrijfsnaamLeesbaar(andere, { behoudSuffix: true }) === metSuffix
                )
            );
            return zelfdeMetSuffix.size <= 1 ? metSuffix : r;
        });
    }

    // Breekt een label af op woordgrenzen in regels van hooguit maxTekens
    // (een los woord dat langer is, blijft heel; "Class A" blijft bij elkaar).
    // Chart.js toont een array als meerregelig label, zodat lange namen niet
    // met "…" afgekapt hoeven.
    function breekLabelAf(tekst, maxTekens) {
        if (typeof tekst !== "string" || tekst.length <= maxTekens) return [tekst];
        const regels = [];
        let huidig = "";
        const woorden = tekst.split(" ");
        const n = woorden.length;
        if (n >= 3 && woorden[n - 2] === "Class") {
            woorden.splice(n - 2, 2, `${woorden[n - 2]} ${woorden[n - 1]}`);
        }
        woorden.forEach(woord => {
            if (huidig === "") {
                huidig = woord;
            } else if ((huidig + " " + woord).length <= maxTekens) {
                huidig += " " + woord;
            } else {
                regels.push(huidig);
                huidig = woord;
            }
        });
        if (huidig !== "") regels.push(huidig);
        return regels;
    }

    // Klemt het gekozen aantal in [1, beschikbaar].
    function effectieveTopN(gekozen, beschikbaar) {
        return Math.max(1, Math.min(gekozen, beschikbaar));
    }

    // Verwerkt wat de gebruiker in het invulveld typt: een geheel getal >= 1
    // (boven het beschikbare aantal wordt het maximum). Leeg, decimaal,
    // negatief, 0 of tekst geeft het laatst geldige aantal terug.
    function kiesTopN(invoer, beschikbaar, laatstGeldig) {
        const tekst = String(invoer === null || invoer === undefined ? "" : invoer).trim();
        if (!/^\d+$/.test(tekst)) return laatstGeldig;
        const n = parseInt(tekst, 10);
        if (n < 1) return laatstGeldig;
        return Math.min(n, beschikbaar);
    }

    // Knipt de (aflopend gesorteerde) lijst van de backend in tot de top N en
    // rekent het restant opnieuw uit: backend-"overig" (bedrijven buiten de
    // meegeleverde lijst + niet-gedekte ETF-holdings) plus de meegeleverde
    // bedrijven die buiten de gekozen top N vallen.
    function snijTopBedrijven(data, n) {
        const top = (data && data.top) || [];
        const aantal = Math.max(0, Math.min(n, top.length));
        const buitenTopN = top.slice(aantal).reduce((som, e) => som + e.waarde, 0);
        const overigWaarde = ((data && data.overig) || 0) + buitenTopN;
        const overigPct = data && data.totaal_waarde ? (overigWaarde / data.totaal_waarde) * 100 : 0;
        return { getoond: top.slice(0, aantal), overigWaarde, overigPct };
    }

    function gebruikHorizontaleStaven(aantal, schermBreedte) {
        return aantal > BEDRIJVEN_HORIZONTAAL_VANAF || schermBreedte <= BEDRIJVEN_SMAL_SCHERM_MAX_BREEDTE;
    }

    function bedrijvenTitel(n) {
        return n === 1 ? "Top 1 bedrijf" : `Top ${n} bedrijven`;
    }

    const exportsObj = {
        BEDRIJVEN_TOP_N_KNOPPEN,
        maakBedrijfsnaamLeesbaar,
        maakUniekeWeergaveNamen,
        breekLabelAf,
        effectieveTopN,
        kiesTopN,
        snijTopBedrijven,
        gebruikHorizontaleStaven,
        bedrijvenTitel,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
