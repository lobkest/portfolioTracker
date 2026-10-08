// Tabbladen Prognose en Huidige portfolio: formulier, verwacht dividend en grafiek. De rekenkern staat in prognose.js.

// Verwacht dividend en historisch rendement: één fetch per portfolio, gedeeld door beide tabbladen, pas als een vinkje
// aan staat (kost Yahoo-calls). {data, verzoek}; resetPrognose() maakt ze leeg.
let prognoseDividend = { data: null, verzoek: null };
let prognoseHistorie = { data: null, verzoek: null };

const DIVIDEND_BRON_TEKST = {
    yahoo_reeks: "Yahoo, afgelopen 12 mnd",
    eigen_data: "Eigen ontvangen dividend (12 mnd t/m {per_datum})",
    dividend_rate: "Yahoo, verwacht jaarbedrag",
    trailing_rate: "Yahoo, trailing 12 mnd",
    geen_uitkeringen: "Keert niet uit",
    onbekend: "Onbekend — niet meegeteld",
};

const BELASTING_BRON_TEKST = { eigen: "eigen data", land: "aanname land", standaard: "standaardaanname" };

function prognoseGetal(x, maxDecimalen) {
    return x.toLocaleString("nl-NL", { maximumFractionDigits: maxDecimalen });
}

function prognosePct(fractie, metTeken, decimalen = 1) {
    const teken = metTeken && fractie > 0 ? "+" : "";
    return `${teken}${prognoseGetal(fractie * 100, decimalen)}%`;
}

function dividendBronTekst(bron, perDatum) {
    const tekst = DIVIDEND_BRON_TEKST[bron] || bron;
    return tekst.replace("{per_datum}", perDatum ? formatDatum(perDatum) : "?");
}

// Een onvolledig antwoord (nog niet alle koersen geladen) wordt niet bewaard: opnieuw vragen laadt verder.
function haalEenmaal(cache, pad, nietBeschikbaarTekst) {
    if (cache.data) return Promise.resolve(cache.data);
    if (cache.verzoek) return cache.verzoek;

    const code = huidigeData.code;
    const verzoek = (async () => {
        const res = await fetchMetTimeout(`/api/portfolio/${code}/${pad}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Ophalen is mislukt.");
        voegDiagnostiekToe(data);
        if (!data.beschikbaar) throw new Error(nietBeschikbaarTekst(data));
        if (huidigeData.code === code && !data.onvolledig) cache.data = data;
        return data;
    })();
    const wis = () => { if (cache.verzoek === verzoek) cache.verzoek = null; };
    verzoek.then(wis, wis);
    cache.verzoek = verzoek;
    return verzoek;
}

function haalDividendVerwachting() {
    return haalEenmaal(prognoseDividend, "dividend-verwachting",
        () => "Geen koersdata om het verwachte dividend te berekenen.");
}

function haalHistorischRendement() {
    return haalEenmaal(prognoseHistorie, "historisch-rendement", data => (data.onvolledig
        ? "Nog niet alle koershistorie geladen. Zet het vinkje opnieuw aan om verder te laden."
        : (data.melding || "Te weinig koershistorie.")));
}

const HISTORIE_STATUS_TEKST = {
    te_kort: "telt niet mee (minder dan 1 jaar koershistorie)",
    geen_koers: "geen koershistorie",
    nog_niet_geladen: "nog niet geladen",
};

const HISTORIE_VELD_NAAM = { rendement: "koersrendement", laag: "lage kant", hoog: "hoge kant" };

function historiePct(pct) {
    return pct === null || pct === undefined ? "—" : `${prognoseGetal(pct, 1)}%`;
}

function maakHistorieTabel(posities) {
    const telt = p => p.status === "ok";
    const kolommen = [
        { label: "Aandeel/ETF", renderTd: p => maakCel(p.bijnaam || p.ticker || p.isin) },
        { label: "Gewicht", waarde: p => p.gewicht, renderTd: p => maakCel(p.gewicht === null ? "—" : prognosePct(p.gewicht)) },
        {
            label: "Jaren data",
            waarde: p => p.beschikbare_jaren,
            renderTd: p => {
                const td = maakCel(prognoseGetal(p.beschikbare_jaren, 1));
                if (telt(p) && p.kort) {
                    const badge = document.createElement("span");
                    badge.className = "badge";
                    badge.textContent = "kort";
                    td.appendChild(badge);
                }
                return td;
            },
        },
        { label: "Vanaf", waarde: p => p.historie_vanaf, renderTd: p => maakCel(p.historie_vanaf === null ? "—" : String(p.historie_vanaf)) },
        {
            label: "Gem. stijging per jaar (CAGR)",
            waarde: p => (telt(p) ? p.cagr_pct : null),
            renderTd: p => maakCel(telt(p) ? historiePct(p.cagr_pct) : (HISTORIE_STATUS_TEKST[p.status] || p.status)),
        },
        {
            label: "1-jaars range (p10 – p90)",
            renderTd: p => maakCel(telt(p) && p.laag_1j_pct !== null
                ? `${historiePct(p.laag_1j_pct)} – ${historiePct(p.hoog_1j_pct)}` : "—"),
        },
    ];
    return maakSorteerbareTabel(kolommen, posities, { legeTekst: "Geen posities in bezit." });
}

// Vinkje "Rendement op basis van historie" (alleen Huidige portfolio): vult koersrendement, laag en hoog uit de backend;
// zolang niemand een veld aanpast, tekent de grafiek de bootstrap-paden (paden()).
function maakHistorieRegelaar({ veld, el, herbereken }) {
    const RENDEMENT_VELDEN = ["rendement", "laag", "hoog"];
    let aan = false;
    let data = null;
    // Waarden van vóór het aanvinken: uitvinken zet die terug.
    let handmatig = null;
    let aangepast = false;
    let horizon = null;

    function renderSectie() {
        let tekst = `Gebaseerd op historie vanaf ${data.historie_start.slice(0, 4)} ` +
            `(${prognoseGetal(data.historie_jaren, 1)} jaar).`;
        if (data.dekking_bij_start < 1) {
            tekst += ` Bij de start had ${prognosePct(data.dekking_bij_start, false, 0)} van je portfolio al koersen.`;
        }
        el("HistorieHorizon").textContent = tekst;
        el("HistorieTabel").replaceChildren(maakHistorieTabel(data.posities));
        const teksten = [...data.waarschuwingen, data.horizonnen[String(horizon)].waarschuwing].filter(Boolean);
        el("HistorieWaarschuwingen").replaceChildren(...teksten.map(tekst => {
            const p = document.createElement("p");
            p.className = "waarschuwingTekst prognoseMelding";
            p.textContent = tekst;
            return p;
        }));
        el("HistorieSectie").hidden = false;
    }

    function vulVelden() {
        horizon = kiesHistorieHorizon(parseFloat(veld("jaren").value));
        const percentielen = data.horizonnen[String(horizon)];
        const { waarden, afgekapt } = historieVeldwaarden(percentielen);
        RENDEMENT_VELDEN.forEach(sleutel => { veld(sleutel).value = waarden[sleutel]; });
        aangepast = false;
        el("HistorieHandmatig").hidden = true;
        el("HistorieKwartielen").textContent = `Waarschijnlijk (p25–p75): ${historiePct(percentielen.p25)} tot ` +
            `${historiePct(percentielen.p75)} per jaar over ${horizon} jaar.`;
        el("HistorieKwartielen").hidden = false;
        el("HistorieAfgekapt").hidden = afgekapt.length === 0;
        el("HistorieAfgekapt").textContent = afgekapt.map(a =>
            `Historisch ${prognoseGetal(a.historisch, 1)}% (${HISTORIE_VELD_NAAM[a.veld]}) afgekapt op ` +
            `${prognoseGetal(a.begrensd, 1)}% (grens van het formulier).`).join(" ");
        renderSectie();
    }

    async function zetAan() {
        handmatig = Object.fromEntries(RENDEMENT_VELDEN.map(sleutel => [sleutel, parseFloat(veld(sleutel).value)]));
        aan = true;
        el("HistorieLaden").hidden = false;
        el("HistorieFout").hidden = true;
        try {
            data = await haalHistorischRendement();
        } catch (err) {
            el("HistorieFout").textContent = err.message === "TIMEOUT"
                ? "Koershistorie ophalen duurde te lang. Probeer het opnieuw."
                : `Kon historisch rendement niet ophalen: ${err.message}`;
            el("HistorieFout").hidden = false;
            aan = false;
            handmatig = null;
            el("HistorieVinkje").checked = false;
            return;
        } finally {
            el("HistorieLaden").hidden = true;
        }
        // Intussen uitgevinkt of een andere portfolio geladen.
        if (!aan) return;
        vulVelden();
        herbereken();
    }

    function zetUit() {
        aan = false;
        if (handmatig) RENDEMENT_VELDEN.forEach(sleutel => { veld(sleutel).value = handmatig[sleutel]; });
        handmatig = null;
        aangepast = false;
        ["HistorieHandmatig", "HistorieAfgekapt", "HistorieKwartielen", "HistorieSectie"].forEach(naam => { el(naam).hidden = true; });
        herbereken();
    }

    el("HistorieVinkje").addEventListener("change", (e) => (e.target.checked ? zetAan() : zetUit()));
    RENDEMENT_VELDEN.forEach(sleutel => veld(sleutel).addEventListener("input", () => {
        if (!aan || !data) return;
        aangepast = true;
        el("HistorieHandmatig").hidden = false;
        el("HistorieKwartielen").hidden = true;
    }));
    veld("jaren").addEventListener("input", () => {
        if (aan && data && !aangepast) vulVelden();
    });

    return {
        paden() {
            return aan && data && !aangepast ? data.paden : null;
        },
        toon() {
            const heeftCode = Boolean(huidigeData.code);
            el("HistorieVinkje").disabled = !heeftCode;
            el("HistorieVinkje").checked = aan;
            el("HistorieNietBeschikbaar").hidden = heeftCode;
            el("HistorieFout").hidden = true;
            if (aan && data) {
                renderSectie();
                return;
            }
            ["HistorieHandmatig", "HistorieAfgekapt", "HistorieKwartielen", "HistorieSectie"].forEach(naam => { el(naam).hidden = true; });
        },
        // Geeft de handmatige waarden terug als de velden historische waarden bevatten.
        reset() {
            const terug = aan ? handmatig : null;
            aan = false;
            data = null;
            handmatig = null;
            aangepast = false;
            return terug;
        },
    };
}

function eigenDividendTekst(p) {
    if (p.eigen_bruto_eur_jaar === null || p.eigen_bruto_eur_jaar === undefined) return "—";
    let tekst = `${formatteerEuro(p.eigen_bruto_eur_jaar)} bruto (${p.eigen_aantal_uitkeringen} uitk.)`;
    if (p.afwijking_fractie !== null) tekst += `, afwijking ${prognosePct(p.afwijking_fractie, true)}`;
    return tekst;
}

function maakDividendVerwachtingTabel(posities, perDatum) {
    const kolommen = [
        { label: "Aandeel/ETF", renderTd: p => maakCel(p.bijnaam || p.ticker || p.isin) },
        { label: "Aantal", waarde: p => p.aantal, renderTd: p => maakCel(prognoseGetal(p.aantal, 4)) },
        {
            label: "Dividend per aandeel per jaar",
            renderTd: p => maakCel(p.per_aandeel_jaar === null ? "—" : `${prognoseGetal(p.per_aandeel_jaar, 4)} ${p.valuta || ""}`),
        },
        { label: "Bron", renderTd: p => maakCel(dividendBronTekst(p.bron, perDatum)) },
        {
            label: "Bronbelasting",
            waarde: p => p.belasting_fractie,
            renderTd: p => maakCel(`${prognosePct(p.belasting_fractie)} (${BELASTING_BRON_TEKST[p.belasting_bron] || p.belasting_bron})`),
        },
        {
            label: "Netto per jaar",
            waarde: p => p.netto_eur_jaar,
            renderTd: p => maakCel(p.meegeteld ? formatteerEuro(p.netto_eur_jaar) : "niet meegeteld"),
        },
        { label: "Eigen data", waarde: p => p.eigen_bruto_eur_jaar, renderTd: p => maakCel(eigenDividendTekst(p)) },
    ];
    return maakSorteerbareTabel(kolommen, posities, { legeTekst: "Geen posities in bezit." });
}

function renderDividendVerwachting(prefix, data) {
    document.getElementById(`${prefix}DividendTabel`).replaceChildren(
        maakDividendVerwachtingTabel(data.posities, data.eigen_per_datum));
    let totaal = `Totaal netto per jaar: ${formatteerEuro(data.totaal_netto_eur_jaar)}`;
    if (data.yield_netto !== null) {
        totaal += ` — netto yield ${prognosePct(data.yield_netto, false, 2)} van de huidige waarde (${formatteerEuro(data.huidige_waarde_eur)}).`;
    }
    if (!data.eigen_data) totaal += " Geen rekeningoverzicht geüpload: geen vergelijking met eigen dividend.";
    document.getElementById(`${prefix}DividendTotaal`).textContent = totaal;

    const nietMeegeteld = data.posities.filter(p => !p.meegeteld);
    const melding = document.getElementById(`${prefix}DividendNietMeegeteld`);
    melding.hidden = nietMeegeteld.length === 0;
    melding.textContent = `Niet meegeteld (geen bruikbaar dividendbedrag of wisselkoers): ${nietMeegeteld.map(p => p.bijnaam || p.ticker).join(", ")}.`;
}

// Echte tijd-as (niet updateChart()): dagelijkse historie en maandelijkse prognose in één grafiek.
function tekenPrognoseChart(datasets) {
    if (chart) chart.destroy();
    datasets = datasets.map(ds => ({ pointRadius: 0, pointHoverRadius: 4, borderWidth: 1.5, ...ds }));

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "line",
        data: { datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            locale: "nl-NL",
            scales: {
                x: {
                    type: "time",
                    time: {
                        tooltipFormat: "dd-MM-yyyy",
                        displayFormats: {
                            day: "dd-MM-yyyy",
                            week: "dd-MM-yyyy",
                            month: "MM-yyyy",
                            quarter: "MM-yyyy",
                            year: "yyyy"
                        }
                    }
                },
                y: { beginAtZero: false }
            },
            plugins: {
                legend: {
                    labels: {
                        filter: (item, data) => !(data.datasets[item.datasetIndex] || {})._verbergInLegenda
                    }
                },
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.dataset.label}: ${formatteerEuro(ctx.parsed.y)}`
                    }
                },
                zoom: zoomOpties(),
                datalabels: { display: false }
            }
        }
    });
}

// prefix = id-voorvoegsel uit de macro in portfolio.html; zonder inleg rekent de prognose met inleg 0.
function maakPrognoseTab({ view, prefix, metInleg, metHistorie, standaardInvoer }) {
    const velden = ["jaren", "rendement", "laag", "hoog"].concat(metInleg ? ["jaarlijks", "maandelijks"] : []);
    const veld = sleutel => document.getElementById(prefix + sleutel[0].toUpperCase() + sleutel.slice(1));
    const el = naam => document.getElementById(prefix + naam);

    // Invoer blijft bewaard zolang de pagina open is; rekenen pas na "Bereken".
    let invoer = { ...standaardInvoer };
    let resultaat = null;
    // Standaard aan; zonder code (niet opslaan) kan het niet. Mislukt het ophalen, dan gaat het uit tot de volgende portfolio.
    let dividendAan = true;
    const historie = metHistorie ? maakHistorieRegelaar({ veld, el, herbereken: () => berekenEnToon() }) : null;

    function leesInvoer() {
        invoer = { jaarlijks: 0, maandelijks: 0 };
        velden.forEach(sleutel => { invoer[sleutel] = parseFloat(veld(sleutel).value); });
        return invoer;
    }

    function toonDividendSectie() {
        const data = dividendAan ? prognoseDividend.data : null;
        el("DividendSectie").hidden = !data;
        if (data) renderDividendVerwachting(prefix, data);
    }

    function vulFormulier() {
        velden.forEach(sleutel => { veld(sleutel).value = invoer[sleutel]; });
        el("Foutmelding").style.display = "none";
        el("Waarschuwing").style.display = "none";
        const heeftCode = Boolean(huidigeData.code);
        if (!heeftCode) dividendAan = false;
        el("DividendVinkje").disabled = !heeftCode;
        el("DividendVinkje").checked = dividendAan;
        el("DividendNietBeschikbaar").hidden = heeftCode;
        el("DividendFout").hidden = true;
        toonDividendSectie();
        if (historie) historie.toon();
    }

    // null bij een fout: dan gaat het vinkje weer uit.
    async function laadDividend() {
        el("DividendLaden").hidden = false;
        el("DividendFout").hidden = true;
        try {
            return await haalDividendVerwachting();
        } catch (err) {
            el("DividendFout").textContent = err.message === "TIMEOUT"
                ? "Verwacht dividend ophalen duurde te lang. Probeer het opnieuw."
                : `Kon verwacht dividend niet ophalen: ${err.message}`;
            el("DividendFout").hidden = false;
            dividendAan = false;
            el("DividendVinkje").checked = false;
            return null;
        } finally {
            el("DividendLaden").hidden = true;
            toonDividendSectie();
        }
    }

    async function berekenEnToon() {
        const invoer = leesInvoer();
        const validatie = valideerPrognoseInvoer({
            jaren: invoer.jaren,
            rendementPct: invoer.rendement,
            laagPct: invoer.laag,
            hoogPct: invoer.hoog,
            jaarlijkseInleg: invoer.jaarlijks,
            maandelijkseInleg: invoer.maandelijks
        }, { metInleg });

        const foutEl = el("Foutmelding");
        const waarschuwingEl = el("Waarschuwing");
        foutEl.style.display = "none";
        waarschuwingEl.style.display = "none";

        if (!validatie.geldig) {
            foutEl.textContent = validatie.fouten.join(" ");
            foutEl.style.display = "block";
            return;
        }
        if (validatie.waarschuwing) {
            waarschuwingEl.textContent = validatie.waarschuwing;
            waarschuwingEl.style.display = "block";
        }

        const chartData = huidigeData.chart_data;
        let dividendYield;
        if (dividendAan) {
            const data = await laadDividend();
            // Intussen een andere portfolio geladen: dit resultaat hoort daar niet bij.
            if (huidigeData.chart_data !== chartData) return;
            if (data && data.yield_netto !== null) dividendYield = data.yield_netto;
        }

        const paden = historie ? historie.paden() : null;
        resultaat = bouwPrognoseGrafiekData(chartData, { ...invoer, dividendYield, paden });
        if (actieveViewNaam() === view) tekenPrognoseChart(resultaat.datasets);
    }

    velden.forEach(sleutel => {
        veld(sleutel).addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                berekenEnToon();
            }
        });
    });
    el("BerekenBtn").addEventListener("click", () => berekenEnToon());
    el("DividendVinkje").addEventListener("change", (e) => {
        dividendAan = e.target.checked;
        toonDividendSectie();
        berekenEnToon();
    });

    return {
        toon() {
            vulFormulier();
            if (resultaat) {
                tekenPrognoseChart(resultaat.datasets);
            } else {
                berekenEnToon();
            }
        },
        // De invoer blijft bewust staan; resultaat en dividend horen bij de vorige portfolio.
        reset() {
            resultaat = null;
            dividendAan = true;
            const handmatig = historie ? historie.reset() : null;
            if (handmatig) Object.assign(invoer, handmatig);
        },
    };
}

const prognoseTab = maakPrognoseTab({
    view: "prognose", prefix: "prognose", metInleg: true,
    standaardInvoer: { jaren: 10, rendement: 6, laag: 4, hoog: 10, jaarlijks: 0, maandelijks: 200 },
});
const prognoseHuidigTab = maakPrognoseTab({
    view: "prognose-huidig", prefix: "prognoseHuidig", metInleg: false, metHistorie: true,
    standaardInvoer: { jaren: 10, rendement: 6, laag: 4, hoog: 10 },
});

function resetPrognose() {
    prognoseDividend = { data: null, verzoek: null };
    prognoseHistorie = { data: null, verzoek: null };
    prognoseTab.reset();
    prognoseHuidigTab.reset();
}

function toonPrognose() {
    prognoseTab.toon();
}

function toonPrognoseHuidig() {
    prognoseHuidigTab.toon();
}
