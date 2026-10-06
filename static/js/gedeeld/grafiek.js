// Grafiek-helpers op Chart.js: kleuren, de gedeelde lijngrafiek en de gestapelde staafgrafiek.

Chart.register(ChartDataLabels);

// Boven 8 items herhalen de kleuren; elk taartpunt heeft ook een eigen label.
const CATEGORISCH_PALET = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
// Vlakken die te licht zijn voor witte tekst erop (donkere inkt leest daar beter).
const LICHTE_VLAKKEN = new Set(["#eda100", "#e87ba4"]);

// Eigen grijs, zodat "Unknown" nooit opgaat in de categoriekleuren.
const ONBEKEND_GRIJS = "#6b6b66";

function kleurVoorIndex(i) {
    return CATEGORISCH_PALET[i % CATEGORISCH_PALET.length];
}

// Volgorde van huidigeData.tickers, zodat een positie overal dezelfde kleur heeft.
function kleurVoorTicker(ticker) {
    const idx = huidigeData.tickers.findIndex(t => t.ticker === ticker);
    return kleurVoorIndex(idx >= 0 ? idx : 0);
}

function tekstKleurVoorVlak(hex) {
    return LICHTE_VLAKKEN.has(hex) ? "#0b0b0b" : "#ffffff";
}

// Diagonale strepen onderscheiden ETF's van aandelen.
function maakStrepenPatroon(kleurHex) {
    const c = document.createElement("canvas");
    c.width = 10;
    c.height = 10;
    const pctx = c.getContext("2d");
    pctx.fillStyle = kleurHex;
    pctx.fillRect(0, 0, 10, 10);
    pctx.strokeStyle = "rgba(255, 255, 255, 0.6)";
    pctx.lineWidth = 2;
    pctx.beginPath();
    [-2, 8].forEach(offset => {
        pctx.moveTo(offset, 10);
        pctx.lineTo(offset + 10, 0);
    });
    pctx.stroke();
    return pctx.createPattern(c, "repeat");
}

// Het ronde reset-icoon in de grafiek (alleen zichtbaar op mobiel, zie style.css).
function werkZoomIcoonBij(c) {
    document.getElementById("zoomIcoonKnop").hidden = !(c && c.isZoomedOrPanned());
}

// Voor de zoombare lijngrafieken op het gedeelde canvas. Een nieuwe grafiek is niet ingezoomd: icoon weg.
function zoomOpties() {
    werkZoomIcoonBij(null);
    return {
        pan: { enabled: true, mode: "x", onPanComplete: ({ chart: c }) => werkZoomIcoonBij(c) },
        zoom: {
            wheel: { enabled: true },
            pinch: { enabled: true },
            mode: "x",
            onZoomComplete: ({ chart: c }) => werkZoomIcoonBij(c),
        },
    };
}

function resetZoom() {
    if (chart) chart.resetZoom();
    werkZoomIcoonBij(null);
}

// waardeFormatter: opmaak in de tooltip, standaard euro.
function updateChart(labels, datasets, waardeFormatter = formatteerEuro) {
    if (chart) chart.destroy();
    const labelsNL = labels.map(formatDatum);
    datasets = datasets.map(ds => ({ pointRadius: 0, pointHoverRadius: 4, borderWidth: 1.5, ...ds }));

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "line",
        data: { labels: labelsNL, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            locale: "nl-NL",
            scales: { y: { beginAtZero: false } },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y === null ? "—" : waardeFormatter(ctx.parsed.y)}`
                    }
                },
                zoom: zoomOpties(),
                datalabels: { display: false }
            }
        }
    });
}

// Alleen voor een leesbare legenda; de data verandert niet.
const BRON_OVERIG_DREMPEL = 0.005;
const BRON_OVERIG_SLEUTEL = "__overige_bronnen__";

// Eén staaf per categorie, één dataset per bron. categorieData: {categorie: {bron: waarde}};
// zonder opts.totaal zijn de waarden al percentages. opts.horizontaal, labelVoorCategorie,
// tooltipTitel en tooltipFooter gebruikt alleen Top-bedrijven.
// Geeft de nieuwe Chart terug (null zonder data); de aanroeper ruimt zijn vorige grafiek zelf op.
function renderGestapeldeStaafgrafiek(canvasId, categorieData, bronNamen, opts) {
    opts = opts || {};

    if (!categorieData || Object.keys(categorieData).length === 0) {
        document.getElementById("geenData").style.display = "block";
        return null;
    }
    document.getElementById("geenData").style.display = "none";

    // Aflopend op totaal; "Overig" altijd als laatste staaf.
    const categorieen = Object.keys(categorieData).sort((a, b) => {
        if (a === "Overig" || b === "Overig") return (a === "Overig") - (b === "Overig");
        const totaalA = Object.values(categorieData[a]).reduce((som, w) => som + w, 0);
        const totaalB = Object.values(categorieData[b]).reduce((som, w) => som + w, 0);
        return totaalB - totaalA;
    });

    const bronTotalen = {};
    categorieen.forEach(cat => {
        Object.entries(categorieData[cat]).forEach(([bron, waarde]) => {
            bronTotalen[bron] = (bronTotalen[bron] || 0) + waarde;
        });
    });
    const totaalAlleBronnen = Object.values(bronTotalen).reduce((som, w) => som + w, 0);

    let bronnenVolgorde = Object.keys(bronTotalen).sort((a, b) => bronTotalen[b] - bronTotalen[a]);
    const kleineBronnen = new Set(bronnenVolgorde.filter(
        b => totaalAlleBronnen > 0 && (bronTotalen[b] / totaalAlleBronnen) < BRON_OVERIG_DREMPEL
    ));
    // Alleen samenvoegen als het ook echt de legenda opschoont (>1 kleine bron).
    if (kleineBronnen.size > 1) {
        bronnenVolgorde = bronnenVolgorde.filter(b => !kleineBronnen.has(b)).concat([BRON_OVERIG_SLEUTEL]);
    } else {
        kleineBronnen.clear();
    }

    const deler = opts.totaal;
    function waardeVoorCategorie(cat, bron) {
        let w;
        if (bron === BRON_OVERIG_SLEUTEL) {
            w = 0;
            kleineBronnen.forEach(kb => { w += (categorieData[cat][kb] || 0); });
        } else {
            w = categorieData[cat][bron] || 0;
        }
        return deler ? (w / deler * 100) : w;
    }

    let volgendeKleur = 0;
    const kleurenMap = {};
    bronnenVolgorde.forEach(bron => {
        kleurenMap[bron] = (bron === BRON_OVERIG_SLEUTEL || bron === "Unknown") ? ONBEKEND_GRIJS : kleurVoorIndex(volgendeKleur++);
    });

    const datasets = bronnenVolgorde.map(bron => ({
        label: bron === BRON_OVERIG_SLEUTEL ? "Overige bronnen" : ((bronNamen && bronNamen[bron]) || bron),
        data: categorieen.map(cat => waardeVoorCategorie(cat, bron)),
        backgroundColor: kleurenMap[bron],
    }));

    // Totaal-%-label boven elke staaf.
    const horizontaal = Boolean(opts.horizontaal);
    const totalenPlugin = {
        id: "totalenBovenStaaf",
        afterDatasetsDraw(c) {
            const eersteMeta = c.getDatasetMeta(0);
            if (!eersteMeta || !eersteMeta.data.length) return;
            const { ctx, scales } = c;
            ctx.save();
            ctx.font = "bold 11px sans-serif";
            ctx.fillStyle = "#333";
            ctx.textAlign = horizontaal ? "left" : "center";
            ctx.textBaseline = horizontaal ? "middle" : "alphabetic";
            categorieen.forEach((_, i) => {
                const som = datasets.reduce((s, ds) => s + ds.data[i], 0);
                const bar = eersteMeta.data[i];
                if (!bar) return;
                if (horizontaal) {
                    ctx.fillText(`${som.toFixed(1)}%`, scales.x.getPixelForValue(som) + 6, bar.y);
                } else {
                    ctx.fillText(`${som.toFixed(1)}%`, bar.x, scales.y.getPixelForValue(som) - 6);
                }
            });
            ctx.restore();
        }
    };

    const procentAs = { stacked: true, ticks: { callback: v => `${v}%` } };
    const tooltipCallbacks = {
        label: (ctx) => `${ctx.dataset.label}: ${(horizontaal ? ctx.parsed.x : ctx.parsed.y).toFixed(1)}%`
    };
    if (opts.tooltipTitel) {
        tooltipCallbacks.title = (items) => opts.tooltipTitel(categorieen[items[0].dataIndex]);
    }
    if (opts.tooltipFooter) {
        tooltipCallbacks.footer = (items) => opts.tooltipFooter(categorieen[items[0].dataIndex]);
    }

    const chartOpties = {
        responsive: true,
        maintainAspectRatio: false,
        scales: { x: { stacked: true }, y: procentAs },
        plugins: {
            legend: { position: legendaPositie() },
            tooltip: { callbacks: tooltipCallbacks },
            datalabels: { display: false }
        }
    };
    if (horizontaal) {
        chartOpties.indexAxis = "y";
        // autoSkip uit: elk bedrijf houdt zijn label; ruimte rechts voor het %-label.
        chartOpties.scales = { x: procentAs, y: { stacked: true, ticks: { autoSkip: false } } };
        chartOpties.layout = { padding: { right: 44 } };
        chartOpties.plugins.legend.position = "bottom";
    } else if (opts.labelVoorCategorie) {
        chartOpties.scales.x.ticks = { autoSkip: false };
    }

    return new Chart(document.getElementById(canvasId), {
        type: "bar",
        data: {
            labels: categorieen.map(c => opts.labelVoorCategorie ? opts.labelVoorCategorie(c) : kortNaam(c, 20)),
            datasets
        },
        options: chartOpties,
        plugins: [totalenPlugin]
    });
}

// Zelfde voorwaarde als de mobiele layout in style.css: onder de grafiek, anders ernaast.
function legendaPositie() {
    return window.matchMedia("(max-width: 768px), (max-width: 900px) and (orientation: landscape)").matches ? "bottom" : "right";
}
