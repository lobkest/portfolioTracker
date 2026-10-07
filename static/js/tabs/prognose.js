// Tabbladen Prognose en Huidige portfolio: formulier, verwacht dividend en grafiek. De rekenkern staat in prognose.js.

// Verwacht dividend: één fetch voor beide tabbladen, pas als een dividend-vinkje aan gaat (kost Yahoo-calls).
let prognoseDividendData = null;
let prognoseDividendVerzoek = null;

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

function haalDividendVerwachting() {
    if (prognoseDividendData) return Promise.resolve(prognoseDividendData);
    if (prognoseDividendVerzoek) return prognoseDividendVerzoek;

    const code = huidigeData.code;
    const verzoek = (async () => {
        const res = await fetchMetTimeout(`/api/portfolio/${code}/dividend-verwachting`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Verwacht dividend ophalen is mislukt.");
        voegDiagnostiekToe(data);
        if (!data.beschikbaar) throw new Error("Geen koersdata om het verwachte dividend te berekenen.");
        if (huidigeData.code === code) prognoseDividendData = data;
        return data;
    })();
    const wis = () => { if (prognoseDividendVerzoek === verzoek) prognoseDividendVerzoek = null; };
    verzoek.then(wis, wis);
    prognoseDividendVerzoek = verzoek;
    return verzoek;
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
function maakPrognoseTab({ view, prefix, metInleg, standaardInvoer }) {
    const velden = ["jaren", "rendement", "laag", "hoog"].concat(metInleg ? ["jaarlijks", "maandelijks"] : []);
    const veld = sleutel => document.getElementById(prefix + sleutel[0].toUpperCase() + sleutel.slice(1));
    const el = naam => document.getElementById(prefix + naam);

    // Invoer blijft bewaard zolang de pagina open is; rekenen pas na "Bereken".
    let invoer = { ...standaardInvoer };
    let resultaat = null;
    // Standaard aan; zonder code (niet opslaan) kan het niet. Mislukt het ophalen, dan gaat het uit tot de volgende portfolio.
    let dividendAan = true;

    function leesInvoer() {
        invoer = { jaarlijks: 0, maandelijks: 0 };
        velden.forEach(sleutel => { invoer[sleutel] = parseFloat(veld(sleutel).value); });
        return invoer;
    }

    function toonDividendSectie() {
        const data = dividendAan ? prognoseDividendData : null;
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

        resultaat = bouwPrognoseGrafiekData(chartData, { ...invoer, dividendYield });
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
        },
    };
}

const prognoseTab = maakPrognoseTab({
    view: "prognose", prefix: "prognose", metInleg: true,
    standaardInvoer: { jaren: 10, rendement: 6, laag: 4, hoog: 10, jaarlijks: 0, maandelijks: 200 },
});
const prognoseHuidigTab = maakPrognoseTab({
    view: "prognose-huidig", prefix: "prognoseHuidig", metInleg: false,
    standaardInvoer: { jaren: 10, rendement: 6, laag: 4, hoog: 10 },
});

function resetPrognose() {
    prognoseDividendData = null;
    prognoseDividendVerzoek = null;
    prognoseTab.reset();
    prognoseHuidigTab.reset();
}

function toonPrognose() {
    prognoseTab.toon();
}

function toonPrognoseHuidig() {
    prognoseHuidigTab.toon();
}
