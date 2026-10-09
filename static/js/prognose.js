// Pure logica voor de Prognose (zonder DOM, getest onder Node).
// Maandelijks samengesteld; inleg komt ná de groei van die periode.

(function (root) {
    "use strict";
    const { formatGetal, formatteerEuro } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    // Geometrisch, niet /12: over 12 maanden komt dan precies het jaarrendement uit.
    function maandRenteVanJaarPct(rendementPct) {
        return Math.pow(1 + rendementPct / 100, 1 / 12) - 1;
    }

    // Index 0 = startWaarde, index i = waarde na i maanden.
    function berekenPrognosePad(startWaarde, rendementPct, jaren, jaarlijkseInleg, maandelijkseInleg) {
        const maandRente = maandRenteVanJaarPct(rendementPct);
        const totaalMaanden = Math.round(jaren * 12);
        const punten = [startWaarde];
        let waarde = startWaarde;
        for (let m = 1; m <= totaalMaanden; m++) {
            waarde = waarde * (1 + maandRente) + maandelijkseInleg;
            if (m % 12 === 0) {
                waarde += jaarlijkseInleg;
            }
            punten.push(waarde);
        }
        return punten;
    }

    function berekenGeinvesteerdPad(startGeinvesteerd, jaren, jaarlijkseInleg, maandelijkseInleg) {
        const totaalMaanden = Math.round(jaren * 12);
        const punten = [startGeinvesteerd];
        let geinvesteerd = startGeinvesteerd;
        for (let m = 1; m <= totaalMaanden; m++) {
            geinvesteerd += maandelijkseInleg;
            if (m % 12 === 0) {
                geinvesteerd += jaarlijkseInleg;
            }
            punten.push(geinvesteerd);
        }
        return punten;
    }

    function berekenPrognose(input) {
        const {
            startWaarde, startGeinvesteerd, jaren,
            rendementPct, laagPct, hoogPct,
            jaarlijkseInleg, maandelijkseInleg
        } = input;

        return {
            midden: berekenPrognosePad(startWaarde, rendementPct, jaren, jaarlijkseInleg, maandelijkseInleg),
            laag: berekenPrognosePad(startWaarde, laagPct, jaren, jaarlijkseInleg, maandelijkseInleg),
            hoog: berekenPrognosePad(startWaarde, hoogPct, jaren, jaarlijkseInleg, maandelijkseInleg),
            geinvesteerd: berekenGeinvesteerdPad(startGeinvesteerd, jaren, jaarlijkseInleg, maandelijkseInleg)
        };
    }

    // Niet herbelegd: elke maand 1/12 van de jaaryield over de waarde aan het begin van die maand, zonder groei.
    function berekenDividendCumulatief(waardePad, yieldNettoJaar) {
        const punten = [0];
        for (let m = 1; m < waardePad.length; m++) {
            punten.push(punten[m - 1] + waardePad[m - 1] * yieldNettoJaar / 12);
        }
        return punten;
    }

    // Begint bij vanafIso + 1 maand. UTC, anders kan de tijdzone een dag verschuiven.
    function genereerToekomstDatums(vanafIso, aantalMaanden) {
        const basis = new Date(vanafIso + "T00:00:00Z");
        const datums = [];
        for (let m = 1; m <= aantalMaanden; m++) {
            const d = new Date(Date.UTC(basis.getUTCFullYear(), basis.getUTCMonth() + m, basis.getUTCDate()));
            datums.push(d.toISOString().slice(0, 10));
        }
        return datums;
    }

    const RENDEMENT_MIN_PCT = -20;
    const RENDEMENT_MAX_PCT = 30;
    const JAREN_MAX = 60;
    // Gelijk aan HISTORIE_MAX_HORIZON_JAREN in historisch_rendement.py.
    const HISTORIE_MAX_HORIZON_JAREN = JAREN_MAX;

    // opties.metInleg = false: zonder inlegvelden (tabblad Huidige portfolio).
    function valideerPrognoseInvoer(input, opties) {
        const metInleg = !opties || opties.metInleg !== false;
        const { jaren, rendementPct, laagPct, hoogPct, jaarlijkseInleg, maandelijkseInleg } = input;
        const fouten = [];

        if (!Number.isFinite(jaren) || jaren < 0 || jaren > JAREN_MAX) {
            fouten.push(`Aantal jaren vooruit moet tussen 0 en ${JAREN_MAX} liggen.`);
        }
        [
            ["Verwacht koersrendement", rendementPct],
            ["Lage kant van de bandbreedte", laagPct],
            ["Hoge kant van de bandbreedte", hoogPct]
        ].forEach(([naam, waarde]) => {
            if (!Number.isFinite(waarde) || waarde < RENDEMENT_MIN_PCT || waarde > RENDEMENT_MAX_PCT) {
                fouten.push(`${naam} moet tussen ${RENDEMENT_MIN_PCT}% en ${RENDEMENT_MAX_PCT}% liggen.`);
            }
        });
        if (Number.isFinite(laagPct) && Number.isFinite(hoogPct) && laagPct >= hoogPct) {
            fouten.push("De lage kant van de bandbreedte moet kleiner zijn dan de hoge kant.");
        }
        if (metInleg && (!Number.isFinite(jaarlijkseInleg) || jaarlijkseInleg < 0)) {
            fouten.push("Jaarlijkse inleg moet een positief bedrag zijn.");
        }
        if (metInleg && (!Number.isFinite(maandelijkseInleg) || maandelijkseInleg < 0)) {
            fouten.push("Maandelijkse inleg moet een positief bedrag zijn.");
        }

        let waarschuwing = null;
        if (
            fouten.length === 0 &&
            (rendementPct < laagPct || rendementPct > hoogPct)
        ) {
            waarschuwing = "Het verwachte koersrendement valt buiten de opgegeven bandbreedte.";
        }

        return { geldig: fouten.length === 0, fouten, waarschuwing };
    }

    // Horizon (jaren) van de historische bandbreedte die bij "Aantal jaren vooruit" past: afgerond, max. maxHorizon, min. 1.
    function kiesHistorieHorizon(jaren, maxHorizon = HISTORIE_MAX_HORIZON_JAREN) {
        const gewenst = Number.isFinite(jaren) ? Math.round(jaren) : 1;
        return Math.max(1, Math.min(gewenst, maxHorizon));
    }

    function begrensRendement(pct) {
        const waarde = Math.min(RENDEMENT_MAX_PCT, Math.max(RENDEMENT_MIN_PCT, pct));
        return { waarde, afgekapt: waarde !== pct };
    }

    // {p10, p50, p90} van één horizon -> veldwaarden binnen de formuliergrenzen, plus wat er is afgekapt.
    function historieVeldwaarden(horizon) {
        const historisch = { rendement: horizon.p50, laag: horizon.p10, hoog: horizon.p90 };
        const waarden = {};
        const afgekapt = [];
        Object.entries(historisch).forEach(([veld, pct]) => {
            const begrensd = begrensRendement(pct);
            waarden[veld] = begrensd.waarde;
            if (begrensd.afgekapt) afgekapt.push({ veld, historisch: pct, begrensd: begrensd.waarde });
        });
        return { waarden, afgekapt };
    }

    // paden: groeifactoren per maand uit de backend ({p10: [...], ...}) -> waarden, afgekapt op het aantal jaren.
    function historiePrognosePaden(paden, startWaarde, jaren) {
        const lengte = Math.round(jaren * 12) + 1;
        return Object.fromEntries(Object.entries(paden).map(
            ([sleutel, factoren]) => [sleutel, factoren.slice(0, lengte).map(f => startWaarde * f)]));
    }

    // chartData als parameter (niet globaal), zodat nooit data van een vorige portfolio meekomt.
    // {x, y}-punten voor de tijd-as; historie en prognose delen het laatste punt.
    // invoer.dividendYield (netto, fractie per jaar): optioneel, geeft een extra lijn waarde + cumulatief dividend.
    // invoer.paden (historie-modus): prognose uit de bootstrap-percentielen, met twee banden; anders constant rendement.
    function bouwPrognoseGrafiekData(chartData, invoer) {
        const d = chartData;
        const H = d.labels.length;
        const laatsteDatum = d.labels[H - 1];
        const startWaarde = d.waarde[H - 1];
        const startGeinvesteerd = d.geinvesteerd[H - 1];

        const constant = berekenPrognose({
            startWaarde, startGeinvesteerd,
            jaren: invoer.jaren,
            rendementPct: invoer.rendement,
            laagPct: invoer.laag,
            hoogPct: invoer.hoog,
            jaarlijkseInleg: invoer.jaarlijks,
            maandelijkseInleg: invoer.maandelijks
        });
        const historie = invoer.paden ? historiePrognosePaden(invoer.paden, startWaarde, invoer.jaren) : null;
        const middenPad = historie ? historie.p50 : constant.midden;

        const totaalMaanden = Math.round(invoer.jaren * 12);
        const toekomstDatums = genereerToekomstDatums(laatsteDatum, totaalMaanden);

        const historischPad = (reeks) => d.labels.map((iso, i) => ({ x: iso, y: reeks[i] }));
        // pad[0] hoort bij laatsteDatum, zodat de lijn aansluit op historischPad.
        const toekomstPad = (pad) =>
            [{ x: laatsteDatum, y: pad[0] }].concat(toekomstDatums.map((iso, i) => ({ x: iso, y: pad[i + 1] })));

        // Bovenrand (verborgen in de legenda) + onderrand die de ruimte ertussen vult.
        const band = (hoog, laag, label, vulling) => [
            { label: `${label} (hoog)`, data: toekomstPad(hoog), borderColor: "rgba(44, 122, 75, 0.35)", borderDash: [2, 3], fill: false, _verbergInLegenda: true },
            { label, data: toekomstPad(laag), borderColor: "rgba(44, 122, 75, 0.35)", borderDash: [2, 3], backgroundColor: vulling, fill: "-1" }
        ];
        const banden = historie
            ? band(historie.p90, historie.p10, "Bandbreedte p10–p90", "rgba(44, 122, 75, 0.12)")
                .concat(band(historie.p75, historie.p25, "Waarschijnlijk p25–p75", "rgba(44, 122, 75, 0.3)"))
            : [
                { label: "Bandbreedte (hoog)", data: toekomstPad(constant.hoog), borderColor: "rgba(44, 122, 75, 0.35)", borderDash: [2, 3], fill: false, _verbergInLegenda: true },
                { label: "Bandbreedte laag–hoog", data: toekomstPad(constant.laag), borderColor: "rgba(44, 122, 75, 0.35)", borderDash: [2, 3], backgroundColor: "rgba(44, 122, 75, 0.15)", fill: "-1" }
            ];

        const datasets = [
            { label: "Waarde (€)", data: historischPad(d.waarde), borderColor: "#2c7a4b" },
            { label: "Waarde — prognose (€)", data: toekomstPad(middenPad), borderColor: "#2c7a4b", borderDash: [6, 4] },
            ...banden,
            { label: "Geïnvesteerd (€)", data: historischPad(d.geinvesteerd), borderColor: "#3182bd" },
            { label: "Geïnvesteerd — prognose (€)", data: toekomstPad(constant.geinvesteerd), borderColor: "#3182bd", borderDash: [6, 4] }
        ];

        if (typeof invoer.dividendYield === "number" && Number.isFinite(invoer.dividendYield)) {
            const cumulatief = berekenDividendCumulatief(middenPad, invoer.dividendYield);
            datasets.splice(2, 0, {
                label: "Waarde + verwacht dividend, niet herbelegd (€)",
                data: toekomstPad(middenPad.map((w, m) => w + cumulatief[m])),
                borderColor: "#b7791f",
                borderDash: [3, 3]
            });
        }

        return { datasets };
    }

    function historiePct(pct) {
        return pct === null || pct === undefined ? "—" : `${formatGetal(pct, 1, 0)}%`;
    }

    function fractieAlsPct(fractie, decimalen = 1) {
        return `${formatGetal(fractie * 100, decimalen, 0)}%`;
    }

    function historieKwartielenTekst(p25, p75, horizon) {
        return `Waarschijnlijk ${formatGetal(p25, 1, 0)}–${historiePct(p75)} per jaar (historie, ${horizon} jaar)`;
    }

    // De volledige statustekst past niet in de smalle waardekolom; de subregel toont de jaren al.
    const HISTORIE_STATUS_KORT = { te_kort: "telt niet mee", geen_koers: "geen koershistorie", nog_niet_geladen: "nog niet geladen" };

    function historieMobielSubregel(p) {
        const delen = [p.gewicht === null ? "—" : fractieAlsPct(p.gewicht)];
        if (p.beschikbare_jaren !== null && p.beschikbare_jaren !== undefined) {
            const vanaf = p.historie_vanaf === null || p.historie_vanaf === undefined ? "" : ` (vanaf ${p.historie_vanaf})`;
            delen.push(`${formatGetal(p.beschikbare_jaren, 1, 0)} jaar data${vanaf}`);
        }
        return delen.join(" · ");
    }

    function historieMobielWaarde(p) {
        return p.status === "ok" ? `${historiePct(p.cagr_pct)}/jaar` : (HISTORIE_STATUS_KORT[p.status] || p.status);
    }

    function historieMobielSubwaarde(p) {
        if (p.status !== "ok" || p.laag_1j_pct === null || p.laag_1j_pct === undefined) return "";
        return `1j: ${historiePct(p.laag_1j_pct)} – ${historiePct(p.hoog_1j_pct)}`;
    }

    function dividendMobielSubregel(p) {
        const delen = [`${formatGetal(p.aantal, 4, 0)} st`];
        if (p.per_aandeel_jaar !== null && p.per_aandeel_jaar !== undefined) {
            delen.push(`${formatGetal(p.per_aandeel_jaar, 4, 0)} ${p.valuta || ""}`.trim());
        }
        return delen.join(" · ");
    }

    function dividendMobielWaarde(p) {
        return p.meegeteld ? formatteerEuro(p.netto_eur_jaar) : "niet meegeteld";
    }

    function dividendMobielSubwaarde(p) {
        return `bronbelasting ${fractieAlsPct(p.belasting_fractie)}`;
    }

    // {hoofd, noot}: hoofd = totaal + yield, noot alleen zonder rekeningoverzicht.
    function dividendTotaalTekst(data) {
        let hoofd = `Totaal netto ${formatteerEuro(data.totaal_netto_eur_jaar)} per jaar`;
        if (data.yield_netto !== null) {
            hoofd += ` · netto yield ${fractieAlsPct(data.yield_netto, 2)} van ${formatteerEuro(data.huidige_waarde_eur)}`;
        }
        const noot = data.eigen_data ? "" : "Geen rekeningoverzicht geüpload: geen vergelijking met eigen dividend.";
        return { hoofd, noot };
    }

    const exportsObj = {
        maandRenteVanJaarPct,
        berekenPrognosePad,
        berekenGeinvesteerdPad,
        berekenPrognose,
        berekenDividendCumulatief,
        genereerToekomstDatums,
        valideerPrognoseInvoer,
        kiesHistorieHorizon,
        begrensRendement,
        historieVeldwaarden,
        historiePrognosePaden,
        bouwPrognoseGrafiekData,
        RENDEMENT_MIN_PCT,
        RENDEMENT_MAX_PCT,
        HISTORIE_MAX_HORIZON_JAREN,
        historiePct,
        historieKwartielenTekst,
        historieMobielSubregel,
        historieMobielWaarde,
        historieMobielSubwaarde,
        dividendMobielSubregel,
        dividendMobielWaarde,
        dividendMobielSubwaarde,
        dividendTotaalTekst
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
