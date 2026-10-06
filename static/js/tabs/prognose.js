// Tabblad Prognose: formulier en grafiek. De rekenkern staat in prognose.js.

// Invoer blijft bewaard zolang de pagina open is; rekenen pas na "Bereken".
let prognoseInvoer = { jaren: 10, rendement: 6, laag: 4, hoog: 10, jaarlijks: 0, maandelijks: 200 };
let prognoseResultaat = null;

// De invoer blijft bewust staan; alleen het resultaat hoort bij de vorige portfolio.
function resetPrognose() {
    prognoseResultaat = null;
}

const PROGNOSE_VELD_IDS = {
    jaren: "prognoseJaren",
    rendement: "prognoseRendement",
    laag: "prognoseLaag",
    hoog: "prognoseHoog",
    jaarlijks: "prognoseJaarlijks",
    maandelijks: "prognoseMaandelijks",
};

function leesPrognoseInvoer() {
    prognoseInvoer = {};
    Object.entries(PROGNOSE_VELD_IDS).forEach(([sleutel, id]) => {
        prognoseInvoer[sleutel] = parseFloat(document.getElementById(id).value);
    });
    return prognoseInvoer;
}

function vulPrognoseFormulier() {
    Object.entries(PROGNOSE_VELD_IDS).forEach(([sleutel, id]) => {
        document.getElementById(id).value = prognoseInvoer[sleutel];
    });
    document.getElementById("prognoseFoutmelding").style.display = "none";
    document.getElementById("prognoseWaarschuwing").style.display = "none";
}

Object.values(PROGNOSE_VELD_IDS).forEach(id => {
    document.getElementById(id).addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            berekenEnToonPrognose();
        }
    });
});

document.getElementById("prognoseBerekenBtn").addEventListener("click", () => berekenEnToonPrognose());

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

function berekenEnToonPrognose() {
    const invoer = leesPrognoseInvoer();
    const validatie = valideerPrognoseInvoer({
        jaren: invoer.jaren,
        rendementPct: invoer.rendement,
        laagPct: invoer.laag,
        hoogPct: invoer.hoog,
        jaarlijkseInleg: invoer.jaarlijks,
        maandelijkseInleg: invoer.maandelijks
    });

    const foutEl = document.getElementById("prognoseFoutmelding");
    const waarschuwingEl = document.getElementById("prognoseWaarschuwing");
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

    prognoseResultaat = bouwPrognoseGrafiekData(huidigeData.chart_data, invoer);
    tekenPrognoseChart(prognoseResultaat.datasets);
}

function toonPrognose() {
    vulPrognoseFormulier();
    if (prognoseResultaat) {
        tekenPrognoseChart(prognoseResultaat.datasets);
    } else {
        berekenEnToonPrognose();
    }
}
