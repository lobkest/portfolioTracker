// Tabblad Per aandeel aankoop: koers met aankoop- en verkoopmomenten, en extra historie laden.

// Per ticker: welke "meer historie"-knoppen niets meer opleveren.
let meerHistorieUitgeput = {};

function resetPerAandeelAankoop() {
    meerHistorieUitgeput = {};
}

// Ook gebruikt voor de dummy-legenda-datasets.
const AANKOOP_KLEUR = "#2c7a4b";
const VERKOOP_KLEUR = "#9C0006"; // zelfde rood als de foutmeldingen en verwijderPortfolioBtn
const SPLIT_KLEUR = "#6f42c1";

// Eigen Chart i.p.v. updateChart(): twee y-assen en de annotation-plugin.
function toonPerAandeelAankoop(ticker) {
    if (chart) { chart.destroy(); chart = null; }
    const titelEl = document.getElementById("peraandeelAankoopTitel");
    const d = huidigeData.per_ticker_aankoop && huidigeData.per_ticker_aankoop[ticker];
    const knoppenContainer = document.getElementById("meerHistorieKnoppen");
    if (!d) {
        titelEl.style.display = "none";
        knoppenContainer.style.display = "none";
        return;
    }

    const tickerInfo = (huidigeData.tickers || []).find(t => t.ticker === ticker);
    titelEl.textContent = `Koers en aankoopmomenten — ${tickerInfo ? tickerInfo.naam : ticker} (${ticker})`;
    titelEl.style.display = "block";

    if (huidigeData.code) {
        knoppenContainer.style.display = "flex";
        document.getElementById("meerHistorieMsg").style.display = "none";
        werkMeerHistorieKnoppenBij(ticker, d);
    } else {
        knoppenContainer.style.display = "none";
    }

    const labelsNL = d.labels.map(formatDatum);
    // Annotaties matchen op de labeltekst, dus dezelfde dd-mm-jjjj-vorm als de x-as.
    function maakVerticaleAnnotaties(datums, prefix, kleur) {
        const labelsSet = new Set(datums.map(formatDatum));
        const annotaties = {};
        labelsNL.forEach((label, i) => {
            if (labelsSet.has(label)) {
                annotaties[`${prefix}-${i}`] = {
                    type: "line",
                    xMin: label,
                    xMax: label,
                    borderColor: kleur,
                    borderWidth: 1,
                    borderDash: [4, 4],
                };
            }
        });
        return annotaties;
    }
    // Ruwe koersen (zoals DeGiro ze toont) springen op een splitdag; het aantal springt dan andersom mee.
    const splitAnnotaties = {};
    (d.splits || []).forEach(split => {
        const i = splitLabelIndex(d.labels, split.datum);
        if (i === -1 || split.datum < d.labels[0]) return;
        splitAnnotaties[`split-${split.datum}`] = {
            type: "line",
            xMin: labelsNL[i],
            xMax: labelsNL[i],
            borderColor: SPLIT_KLEUR,
            borderWidth: 2,
            label: { display: true, content: splitLabel(split.ratio), position: "start", backgroundColor: SPLIT_KLEUR },
        };
    });
    const annotaties = {
        ...maakVerticaleAnnotaties(d.aankoop_datums, "aankoop", AANKOOP_KLEUR),
        ...maakVerticaleAnnotaties(d.verkoop_datums, "verkoop", VERKOOP_KLEUR),
        ...splitAnnotaties,
    };
    const splitLegenda = Object.keys(splitAnnotaties).length
        ? [{ label: "Split", data: [], borderColor: SPLIT_KLEUR, borderWidth: 2, pointRadius: 0 }]
        : [];

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "line",
        data: {
            labels: labelsNL,
            datasets: [
                {
                    label: "Koers (€)",
                    data: d.koers,
                    borderColor: "#2c7a4b",
                    yAxisID: "y",
                    spanGaps: true,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    borderWidth: 1.5,
                },
                {
                    label: "Aantal aandelen",
                    data: d.holdings,
                    borderColor: "#3182bd",
                    yAxisID: "y1",
                    // 'before' = steps-post (springt op het punt zelf); 'after' springt een punt te vroeg.
                    stepped: "before",
                    pointRadius: 0,
                    pointHoverRadius: 0,
                    borderWidth: 1.5,
                },
                // Dummy-datasets: annotaties komen anders niet in de legenda.
                {
                    label: "Aankoop",
                    data: [],
                    borderColor: AANKOOP_KLEUR,
                    borderDash: [5, 5],
                    borderWidth: 1.5,
                    pointRadius: 0,
                },
                {
                    label: "Verkoop",
                    data: [],
                    borderColor: VERKOOP_KLEUR,
                    borderDash: [5, 5],
                    borderWidth: 1.5,
                    pointRadius: 0,
                },
                ...splitLegenda,
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            locale: "nl-NL",
            scales: {
                y: { position: "left", beginAtZero: false, title: { display: true, text: "Koers (€)" } },
                y1: { position: "right", beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: "Aantal aandelen" } },
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: (ctx) => ctx.dataset.yAxisID === "y"
                            ? `Koers: ${ctx.parsed.y === null ? "—" : formatteerEuro(ctx.parsed.y)}`
                            : ctx.dataset.yAxisID === "y1"
                                ? `Aantal aandelen: ${ctx.parsed.y}`
                                : null
                    },
                    filter: (ctx) => ctx.dataset.yAxisID === "y" || ctx.dataset.yAxisID === "y1",
                },
                zoom: zoomOpties(),
                datalabels: { display: false },
                annotation: { annotations: annotaties },
            },
        },
    });
}

// "Meer historie laden" vult huidigeData.per_ticker_aankoop[ticker] in-place aan.

function berekenNieuweVanafDatum(vroegsteIso, periode) {
    const dt = new Date(vroegsteIso + "T00:00:00Z");
    if (periode === "6m") dt.setUTCMonth(dt.getUTCMonth() - 6);
    else if (periode === "1j") dt.setUTCFullYear(dt.getUTCFullYear() - 1);
    else if (periode === "3j") dt.setUTCFullYear(dt.getUTCFullYear() - 3);
    return dt.toISOString().slice(0, 10);
}

// data.labels is oplopend; ISO-datums zijn als string te vergelijken.
function mergePrependHistorie(d, data) {
    if (!data.labels.length) return 0;
    const grens = d.labels[0];
    let eindIdx = 0;
    while (eindIdx < data.labels.length && data.labels[eindIdx] < grens) eindIdx++;
    if (eindIdx === 0) return 0;
    const nieuweLabels = data.labels.slice(0, eindIdx);
    const nieuweKoers = data.koers.slice(0, eindIdx);
    d.labels = nieuweLabels.concat(d.labels);
    d.koers = nieuweKoers.concat(d.koers);
    d.holdings = data.holdings.slice(0, eindIdx).concat(d.holdings);
    return nieuweLabels.length;
}

function mergeAppendHistorie(d, data) {
    if (!data.labels.length) return 0;
    const grens = d.labels[d.labels.length - 1];
    let startIdx = data.labels.length;
    for (let i = data.labels.length - 1; i >= 0 && data.labels[i] > grens; i--) {
        startIdx = i;
    }
    if (startIdx === data.labels.length) return 0;
    const nieuweLabels = data.labels.slice(startIdx);
    const nieuweKoers = data.koers.slice(startIdx);
    d.labels = d.labels.concat(nieuweLabels);
    d.koers = d.koers.concat(nieuweKoers);
    d.holdings = d.holdings.concat(data.holdings.slice(startIdx));
    return nieuweLabels.length;
}

function meerHistorieStatus(ticker) {
    if (!meerHistorieUitgeput[ticker]) meerHistorieUitgeput[ticker] = { terug: false, totnu: false };
    return meerHistorieUitgeput[ticker];
}

// Grijst knoppen uit die niets meer opleveren; "Tot nu" ook preventief bij een aangehouden positie.
function werkMeerHistorieKnoppenBij(ticker, d) {
    const status = meerHistorieStatus(ticker);
    const nogInBezit = d.nog_in_bezit === true;
    const totNuKnop = document.getElementById("meerHistorieTotNuBtn");
    ["meerHistorie6mBtn", "meerHistorie1jBtn", "meerHistorie3jBtn"].forEach(id => {
        document.getElementById(id).disabled = status.terug;
    });
    totNuKnop.disabled = status.totnu || nogInBezit;
    totNuKnop.title = nogInBezit
        ? "Positie wordt nog aangehouden -- koers loopt al tot de laatst beschikbare handelsdag."
        : "";
}

function setMeerHistorieKnoppenBezig(bezig) {
    ["meerHistorie6mBtn", "meerHistorie1jBtn", "meerHistorie3jBtn", "meerHistorieTotNuBtn"].forEach(id => {
        document.getElementById(id).disabled = bezig;
    });
}

async function laadMeerHistorie(periode) {
    const ticker = document.getElementById("aandeelSelect").value;
    if (!ticker || !huidigeData.code) return;
    const d = huidigeData.per_ticker_aankoop && huidigeData.per_ticker_aankoop[ticker];
    if (!d || !d.labels.length) return;

    const msgEl = document.getElementById("meerHistorieMsg");
    msgEl.style.display = "none";
    setMeerHistorieKnoppenBezig(true);

    const oudeVroegste = d.labels[0];
    const oudeLaatste = d.labels[d.labels.length - 1];
    const vandaagIso = new Date().toISOString().slice(0, 10);
    let vanaf, tot;
    if (periode === "totnu") {
        vanaf = oudeLaatste;
        tot = vandaagIso;
    } else {
        vanaf = berekenNieuweVanafDatum(oudeVroegste, periode);
    }

    try {
        const params = new URLSearchParams({ ticker, vanaf });
        if (tot) params.set("tot", tot);
        const res = await fetchMetTimeout(`/api/portfolio/${huidigeData.code}/ticker-koers-bereik?${params}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Historie ophalen mislukt.");

        const status = meerHistorieStatus(ticker);
        let geenVooruitgang = false;
        if (periode === "totnu") {
            mergeAppendHistorie(d, data);
            // Ook uitgrijzen als deze aanroep net de laatste dagen tot vandaag ophaalde.
            geenVooruitgang = d.labels[d.labels.length - 1] >= vandaagIso;
            if (geenVooruitgang) status.totnu = true;
        } else {
            mergePrependHistorie(d, data);
            geenVooruitgang = !data.vroegste_beschikbare_datum || data.vroegste_beschikbare_datum >= oudeVroegste;
            if (geenVooruitgang) status.terug = true;
        }
        // Melding pas ná het hertekenen tonen: toonPerAandeelAankoop() verbergt hem.
        toonPerAandeelAankoop(ticker);
        if (geenVooruitgang) {
            msgEl.textContent = periode === "totnu"
                ? "Geen nieuwere koersdata beschikbaar."
                : "Geen oudere koersdata beschikbaar.";
            msgEl.style.display = "inline";
        }
    } catch (err) {
        msgEl.textContent = "Historie ophalen mislukt: " + err.message;
        msgEl.style.display = "inline";
        setMeerHistorieKnoppenBezig(false);
        werkMeerHistorieKnoppenBij(ticker, d);
    }
}

["meerHistorie6mBtn", "meerHistorie1jBtn", "meerHistorie3jBtn", "meerHistorieTotNuBtn"].forEach(id => {
    document.getElementById(id).addEventListener("click", (e) => {
        laadMeerHistorie(e.currentTarget.dataset.periode);
    });
});
