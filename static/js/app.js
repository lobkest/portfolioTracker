let chart = null;
let huidigeData = null;

// Status van de lui opgehaalde verrijking (Verdeling/Land/Sector/Bedrijven/
// ETF-overlap, zie analyze_transacties_verrijking in app.py): null zolang er
// geen aparte /verrijking-aanroep loopt (bv. een 'niet opslaan'-analyse, die
// deze velden al standaard meestuurt), "laden" tijdens de achtergrond-fetch,
// "fout" bij een timeout/mislukking (toont een "opnieuw proberen"-knop),
// "klaar" zodra huidigeData de verrijkingsvelden bevat.
let verrijkingStatus = null;

// Gedeelde fetch-met-timeout-helper: breekt de aanroep zelf af als de server
// (of de verbinding) veel te lang stil blijft -- bv. een gunicorn-worker die
// vastloopt zonder de verbinding netjes te sluiten. Zonder dit blijft de
// gebruiker naar een oneindige laadanimatie kijken zonder foutmelding (zie
// CLAUDE.md, Statistieken-incident 2026-08-31). Gooit een Error met
// message "TIMEOUT" bij een afgebroken aanroep, zodat de aanroeper dat
// specifieke geval kan onderscheiden van een gewone netwerkfout.
async function fetchMetTimeout(url, opties, timeoutMs = 55000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, { ...(opties || {}), signal: controller.signal });
    } catch (fout) {
        if (fout.name === "AbortError") {
            throw new Error("TIMEOUT");
        }
        throw fout;
    } finally {
        clearTimeout(timer);
    }
}

// Prognose-tabblad: invoer blijft bewaard zolang de pagina open is (ook als je
// naar een ander tabblad en terug gaat), berekening gebeurt pas na "Bereken".
let prognoseInvoer = { jaren: 10, rendement: 6, laag: 4, hoog: 10, jaarlijks: 0, maandelijks: 200 };
let prognoseResultaat = null;

// Rendement-tabblad: resultaat van de laatst opgehaalde "Vergelijk met..."
// benchmark (zie benchmarkSelect), of null als er geen benchmark gekozen is.
let benchmarkVergelijkingData = null;

// Land/Sector-tabblad: welke weergave staat aan, gedeeld tussen beide
// tabbladen (de knop "Wissel weergave" toggled dit, zie weergaveToggleBtn
// hieronder) -- "taart" is de bestaande toonPlatteVerdeling(), "staaf" de
// nieuwe gestapelde-staafgrafiek per bron (renderGestapeldeStaafgrafiek).
let landSectorWeergave = "taart";

Chart.register(ChartDataLabels);

// Chart.js herschaalt niet altijd meteen na een rotatie op mobiele Safari;
// de setTimeout is nodig omdat de nieuwe viewport-afmetingen niet altijd
// al klaar staan op het exacte moment van het orientationchange-event.
window.addEventListener("orientationchange", () => {
    setTimeout(() => { if (chart) chart.resize(); }, 200);
});

// Herbruikbare full-page laad-overlay, gebruikt voor elke actie die een
// serververzoek doet dat merkbaar kan duren (upload/analyseren, code
// ophalen, bijnaam opslaan/resetten, ticker-zekerheid ophalen, data
// verwijderen) zodat de gebruiker niet dubbel klikt of naar een ander
// tabblad navigeert terwijl het verzoek nog loopt. Bewust NIET gebruikt
// voor de Prognose-berekening: die is puur client-side rekenwerk zonder
// netwerk-call en in de praktijk instant.
function toonLaadOverlay(tekst) {
    verbergLaadOverlay();
    const overlay = document.createElement("div");
    overlay.id = "laadOverlay";
    overlay.className = "laadOverlay";
    const spinner = document.createElement("div");
    spinner.className = "laadSpinner";
    const label = document.createElement("div");
    label.className = "laadOverlayTekst";
    label.textContent = tekst;
    overlay.appendChild(spinner);
    overlay.appendChild(label);
    document.body.appendChild(overlay);
}

function verbergLaadOverlay() {
    const overlay = document.getElementById("laadOverlay");
    if (overlay) {
        overlay.remove();
    }
}

function formatDatum(isoDatum) {
    const [jaar, maand, dag] = isoDatum.split("-");
    return `${dag}-${maand}-${jaar}`;
}

// Vaste categorische volgorde (nooit cyclisch bedoeld te lezen tot 8 items;
// bij meer holdings dan dat herhalen kleuren, maar elke taart-punt heeft dan
// nog steeds een eigen naam+percentage-label, dus identiteit blijft niet
// afhankelijk van kleur alleen).
const CATEGORISCH_PALET = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
// Vlakken die te licht zijn voor witte tekst erop (donkere inkt leest daar beter).
const LICHTE_VLAKKEN = new Set(["#eda100", "#e87ba4"]);

function kleurVoorIndex(i) {
    return CATEGORISCH_PALET[i % CATEGORISCH_PALET.length];
}

function tekstKleurVoorVlak(hex) {
    return LICHTE_VLAKKEN.has(hex) ? "#0b0b0b" : "#ffffff";
}

function kortNaam(naam, maxLen = 14) {
    return naam.length > maxLen ? naam.slice(0, maxLen - 1) + "…" : naam;
}

// ETF-vlakken krijgen lichte diagonale strepen bovenop hun kleur (i.p.v. een
// rand) als onderscheid t.o.v. aandelen — een herhalend canvas-patroon dat
// Chart.js als backgroundColor accepteert.
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

// waardeFormatter: hoe een datapunt in de tooltip getoond wordt — Euro
// (standaard, voor de waarde-/rendement-in-€-tabbladen) of bv. een
// percentage-formatter (zie toonRendementOverTijd).
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
                zoom: {
                    pan: { enabled: true, mode: "x" },
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: "x"
                    }
                },
                datalabels: { display: false }
            }
        }
    });
}

function toonPortfolio() {
    const d = huidigeData.chart_data;
    updateChart(d.labels, [
        { label: "Waarde (€)", data: d.waarde, borderColor: "#2c7a4b" },
        { label: "Geïnvesteerd (€)", data: d.geinvesteerd, borderColor: "#3182bd" }
    ]);

    // Hergebruikt exact dezelfde totalen-data en -weergave als het
    // Statistieken-tabblad (huidigeData.statistieken, al standaard
    // meegestuurd bij het laden van een portfolio) — geen aparte
    // berekening of API-call.
    const homeSectie = document.getElementById("homeTotalenSectie");
    homeSectie.innerHTML = "";
    if (huidigeData.statistieken) {
        homeSectie.appendChild(maakTotalenSectie(huidigeData.statistieken.totalen));
    }

    // Alleen tonen als er daadwerkelijk koersdata is (zie laatste_koersdatum/
    // laatst_opgehaald_op in analyze_transacties_kern, app.py) -- anders de
    // regel gewoon weglaten i.p.v. "Invalid Date" o.i.d. te tonen.
    const bijgewerktEl = document.getElementById("laatstBijgewerktText");
    if (huidigeData.laatste_koersdatum && huidigeData.laatst_opgehaald_op) {
        // new Date(...) rekent de UTC-ISO-string (zie 'Z'-suffix, app.py) om
        // naar de lokale tijdzone van de browser -- dus zowel datum als tijd
        // hieronder via de Date-methoden opbouwen, NIET via een slice() op de
        // ruwe ISO-string (die blijft UTC en kan een dag verschillen van de
        // lokale datum, bv. rond middernacht).
        const opgehaald = new Date(huidigeData.laatst_opgehaald_op);
        const opgehaaldDatumStr = `${String(opgehaald.getDate()).padStart(2, "0")}-${String(opgehaald.getMonth() + 1).padStart(2, "0")}-${opgehaald.getFullYear()}`;
        const tijd = opgehaald.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit", hour12: false });
        bijgewerktEl.textContent = `Koersen bekend t/m ${formatDatum(huidigeData.laatste_koersdatum)} · laatst opgehaald ${opgehaaldDatumStr} om ${tijd}`;
        bijgewerktEl.style.display = "block";
    } else {
        bijgewerktEl.style.display = "none";
    }
}

function toonRendement() {
    const d = huidigeData.chart_data;
    const datasets = [{ label: "Rendement (€)", data: d.rendement, borderColor: "#2c7a4b" }];

    const meldingEl = document.getElementById("benchmarkMelding");
    meldingEl.style.display = "none";

    if (benchmarkVergelijkingData) {
        const b = benchmarkVergelijkingData;
        // b.labels is altijd een aaneengesloten SLOTSTUK van d.labels (zelfde
        // onderliggende datumreeks, de benchmark-reeks kan alleen later
        // beginnen — zie bereken_benchmark_vergelijking/"vanaf_datum") — dus
        // links opvullen met null (geen lijn) i.p.v. losse datum-matching.
        const offset = d.labels.length - b.labels.length;
        const reeks = offset > 0 ? Array(offset).fill(null).concat(b.rendement) : b.rendement;
        datasets.push({
            label: `Rendement ${b.naam} (hypothetisch, €)`,
            data: reeks,
            borderColor: "#eb6834",
            borderDash: [5, 5],
        });
        if (b.onvolledige_dekking) {
            meldingEl.textContent = `${b.naam} heeft pas koersdata vanaf ${formatDatum(b.vanaf_datum)} — inleg van vóór die datum telt niet mee in deze vergelijking.`;
            meldingEl.style.display = "block";
        }
    }

    updateChart(d.labels, datasets);
}

async function wisselBenchmark(benchmarkNaam) {
    if (!benchmarkNaam) {
        benchmarkVergelijkingData = null;
        toonRendement();
        return;
    }
    toonLaadOverlay("Benchmark ophalen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/benchmark-vergelijking?benchmark=${encodeURIComponent(benchmarkNaam)}`);
        const data = await res.json();
        if (!res.ok) {
            benchmarkVergelijkingData = null;
            alert(data.error || "Benchmarkvergelijking kon niet berekend worden.");
        } else {
            benchmarkVergelijkingData = { naam: benchmarkNaam, ...data };
        }
    } catch (e) {
        benchmarkVergelijkingData = null;
        alert("Benchmarkvergelijking ophalen is mislukt.");
    } finally {
        verbergLaadOverlay();
        toonRendement();
    }
}

// Standaard "maand" (licht, herberekent automatisch bij elk bezoek van dit
// tabblad) -- "dag" is preciezer maar herberekent XIRR per dag i.p.v. per
// maand, dus alleen op expliciet verzoek via xirrStapToggleBtn. Reset naar
// "maand" bij elke nieuwe portfolio, zie toonDashboard().
let xirrRendementStap = "maand";

// Per ticker: onthoudt of een "meer historie laden"-terugknop resp. de
// "Tot nu"-knop al niets meer opleverde (zie werkMeerHistorieKnoppenBij),
// zodat het uitgegrijsd blijven ook na het wisselen van tabblad/ticker en
// terugkomen behouden blijft. Reset bij elke nieuwe portfolio, zie
// toonDashboard().
let meerHistorieUitgeput = {};

// DB-backed (leest transacties via de code, zie _laad_transacties_en_
// resultaat in app.py) — net als Dividend/Ticker-zekerheid niet beschikbaar
// bij een 'niet opslaan'-analyse. Geen cache: elke keer dat dit tabblad
// geopend wordt, wordt opnieuw opgehaald (zelfde patroon als
// toonInstellingenTicker()) — de berekening zelf is licht bij stap="maand"
// (pure functie, geen Yahoo-calls), dus dat is geen probleem. Bij
// stap="dag" (na een klik op xirrStapToggleBtn) kan dit merkbaar langer
// duren -- vandaar het aparte laadbericht hieronder.
async function toonRendementOverTijd() {
    const msg = document.getElementById("xirrRendementMsg");
    const toggleBtn = document.getElementById("xirrStapToggleBtn");
    msg.style.display = "none";
    msg.style.color = "";

    if (!huidigeData.code) {
        if (chart) { chart.destroy(); chart = null; }
        document.getElementById("chartWrapper").style.display = "none";
        toggleBtn.style.display = "none";
        msg.textContent = "Dit tabblad is alleen beschikbaar voor een opgeslagen portfolio (niet bij een eenmalige, niet-opgeslagen analyse).";
        msg.style.display = "block";
        return;
    }

    toggleBtn.style.display = "block";
    toggleBtn.disabled = true;
    toggleBtn.textContent = xirrRendementStap === "dag" ? "Bezig met dagelijks berekenen..." : "Bereken per dag (kan lang duren)";

    document.getElementById("chartWrapper").style.display = "block";
    toonLaadOverlay(xirrRendementStap === "dag" ? "Rendement per dag berekenen (kan langer duren)..." : "Rendement over tijd berekenen...");
    let res, data;
    try {
        const url = `/api/portfolio/${huidigeData.code}/rendement-over-tijd` + (xirrRendementStap === "dag" ? "?stap=dag" : "");
        res = await fetch(url);
        data = await res.json();
    } catch (e) {
        msg.style.color = "#9C0006";
        msg.textContent = "Kon rendement-over-tijd niet ophalen (netwerkfout).";
        msg.style.display = "block";
        return;
    } finally {
        verbergLaadOverlay();
        toggleBtn.disabled = false;
        toggleBtn.textContent = xirrRendementStap === "dag" ? "Terug naar per maand" : "Bereken per dag (kan lang duren)";
    }

    if (!res.ok) {
        msg.style.color = "#9C0006";
        msg.textContent = data.error || "Rendement over tijd kon niet berekend worden.";
        msg.style.display = "block";
        return;
    }
    if (!data.labels || data.labels.length === 0) {
        msg.textContent = "Nog geen data om te tonen.";
        msg.style.display = "block";
        return;
    }

    updateChart(data.labels, [
        { label: "Rendement (%)", data: data.rendement_pct, borderColor: "#2c7a4b" },
        { label: "XIRR (%)", data: data.xirr_pct, borderColor: "#3182bd" },
        { label: "TWR (%)", data: data.twr_pct, borderColor: "#d9822b" }
    ], formatPct);
}

function toonPerAandeel(ticker) {
    const d = huidigeData.per_ticker[ticker];
    updateChart(d.labels, [
        { label: "Waarde (€)", data: d.waarde, borderColor: "#2c7a4b" },
        { label: "Geïnvesteerd (€)", data: d.geinvesteerd, borderColor: "#3182bd" }
    ]);
    toonEtfDrilldown(ticker);
}

// Kleuren voor de aankoop-/verkoop-annotaties -- ook hergebruikt voor de
// bijbehorende dummy-legenda-datasets hieronder, dus op 1 plek gedefinieerd.
const AANKOOP_KLEUR = "#2c7a4b";
const VERKOOP_KLEUR = "#9C0006"; // zelfde rood als errorMsg/verwijderPortfolioBtn elders in de app

// Koers per aandeel + aankoop-/verkoopmomenten (groene/rode stippellijnen)
// + aantal aangehouden aandelen (blauwe trapvormige lijn, rechter y-as) --
// ander soort grafiek dan toonPerAandeel() hierboven (waarde/geïnvesteerd),
// zie analysis.compute_per_ticker_koers_en_aankopen(). Gebruikt EIGEN
// Chart-opbouw i.p.v. updateChart(), want dat ondersteunt geen twee
// y-assen of de annotation-plugin (verticale lijnen).
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

    // "meer historie laden"-endpoint is DB-backed (leest transacties via de
    // code) -- niet beschikbaar bij een 'niet opslaan'-analyse, zelfde
    // beperking als Dividend/Instellingen/Benchmarkvergelijking elders.
    if (huidigeData.code) {
        knoppenContainer.style.display = "flex";
        document.getElementById("meerHistorieMsg").style.display = "none";
        werkMeerHistorieKnoppenBij(ticker, d);
    } else {
        knoppenContainer.style.display = "none";
    }

    const labelsNL = d.labels.map(formatDatum);
    // aankoop_datums/verkoop_datums (ISO) -> dezelfde dd-mm-jjjj-vorm als
    // de x-as-labels, zodat de annotation-plugin (die op de category-as
    // matcht via de labelwaarde zelf, niet via een echte tijdschaal) de
    // juiste kolom raakt.
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
    const annotaties = {
        ...maakVerticaleAnnotaties(d.aankoop_datums, "aankoop", AANKOOP_KLEUR),
        ...maakVerticaleAnnotaties(d.verkoop_datums, "verkoop", VERKOOP_KLEUR),
    };

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
                    // 'before' (NIET 'after'!) is het Chart.js-equivalent van
                    // matplotlib's drawstyle="steps-post": de waarde blijft
                    // vlak op de OUDE stand staan tot exact het punt waar de
                    // nieuwe stand hoort, en springt daar pas. Chart.js'
                    // 'after' doet het omgekeerde (springt al direct na het
                    // VORIGE punt) -- zie _steppedLineTo in Chart.js' bron
                    // (helpers.canvas.js): met 'after' wordt eerst
                    // ctx.lineTo(previous.x, target.y) getekend, d.w.z. de
                    // sprong naar de NIEUWE waarde gebeurt al op de x van het
                    // VORIGE punt. Dat gaf precies de gemelde bug: de sprong
                    // in deze lijn liep een dag/punt vóór op de groene/rode
                    // aankoop-/verkoop-stippellijn (die wel op de juiste,
                    // exacte transactiedatum staat).
                    stepped: "before",
                    pointRadius: 0,
                    pointHoverRadius: 0,
                    borderWidth: 1.5,
                },
                // Dummy-datasets (geen datapunten) puur om Aankoop/Verkoop
                // in de standaard-legenda te krijgen -- de annotation-plugin
                // voegt de echte verticale lijnen zelf niet aan de legenda
                // toe, dit is de eenvoudigste robuuste workaround daarvoor.
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
                zoom: {
                    pan: { enabled: true, mode: "x" },
                    zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" }
                },
                datalabels: { display: false },
                annotation: { annotations: annotaties },
            },
        },
    });
}

// --- "Meer historie laden"-knoppen op Per aandeel aankoop -----------------
// Vullen huidigeData.per_ticker_aankoop[ticker] client-side aan met extra
// koersdata buiten de standaard-crop (zie /api/portfolio/<code>/ticker-
// koers-bereik in app.py). d wordt in-place gemuteerd, dus opnieuw
// selecteren van dezelfde ticker (zonder portfolio-herlaad) toont de
// uitgebreide reeks weer -- geen aparte cache nodig.

function berekenNieuweVanafDatum(vroegsteIso, periode) {
    const dt = new Date(vroegsteIso + "T00:00:00Z");
    if (periode === "6m") dt.setUTCMonth(dt.getUTCMonth() - 6);
    else if (periode === "1j") dt.setUTCFullYear(dt.getUTCFullYear() - 1);
    else if (periode === "3j") dt.setUTCFullYear(dt.getUTCFullYear() - 3);
    return dt.toISOString().slice(0, 10);
}

// data.labels komt gesorteerd oplopend terug (zie ticker_koers_bereik in
// app.py, dat de pandas-index-volgorde volgt) -- vergelijken als string
// werkt voor ISO-datums (YYYY-MM-DD) net zo goed als chronologisch
// vergelijken, dus geen Date-parsing nodig.
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
    // Alles vóór de oorspronkelijke crop-range heeft per definitie 0
    // holdings (dat is precies wat de crop-logica eraf trimt) -- geen
    // backend-call nodig.
    d.holdings = nieuweLabels.map(() => 0).concat(d.holdings);
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
    d.holdings = d.holdings.concat(nieuweLabels.map(() => 0));
    return nieuweLabels.length;
}

function meerHistorieStatus(ticker) {
    if (!meerHistorieUitgeput[ticker]) meerHistorieUitgeput[ticker] = { terug: false, totnu: false };
    return meerHistorieUitgeput[ticker];
}

// Grijst de terugknoppen uit zodra de historie daar al ophield, en "Tot nu"
// zodra die datum al bereikt is -- ook preventief voor een nog-aangehouden
// positie (die loopt sowieso al tot de laatst beschikbare handelsdag,
// klikken zou dus toch niets opleveren).
function werkMeerHistorieKnoppenBij(ticker, d) {
    const status = meerHistorieStatus(ticker);
    const nogInBezit = d.holdings.length > 0 && Math.abs(d.holdings[d.holdings.length - 1]) > 1e-6;
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
            // Niet alleen "geen nieuwe datums toegevoegd" -- ook als DEZE
            // aanroep net de laatste ontbrekende dagen tot vandaag ophaalde,
            // is een volgende klik zinloos. Zonder deze check zou de knop
            // pas na een extra, altijd-lege klik uitgrijzen.
            geenVooruitgang = d.labels[d.labels.length - 1] >= vandaagIso;
            if (geenVooruitgang) status.totnu = true;
        } else {
            mergePrependHistorie(d, data);
            geenVooruitgang = !data.vroegste_beschikbare_datum || data.vroegste_beschikbare_datum >= oudeVroegste;
            if (geenVooruitgang) status.terug = true;
        }
        // Chart wordt hier volledig opnieuw opgebouwd (destroy + new Chart) --
        // dat reset meteen ook de zoom/pan-viewport naar het volledige nieuwe
        // bereik, dus geen aparte resetZoom()-aanroep nodig. Dit zet ook de
        // knop-status terug op basis van 'status' hierboven, en verbergt
        // (nog) de melding hieronder -- dus pas NA deze aanroep de melding
        // tonen, anders wist toonPerAandeelAankoop() 'm meteen weer.
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

function maakVerdelingLijst(titel, verdelingObj) {
    const wrapper = document.createElement("div");
    wrapper.style.minWidth = "180px";

    const kop = document.createElement("div");
    kop.textContent = titel;
    kop.style.fontWeight = "bold";
    kop.style.marginBottom = "4px";
    wrapper.appendChild(kop);

    const entries = Object.entries(verdelingObj || {}).sort((a, b) => {
        if (a[0] === "Unknown") return 1;
        if (b[0] === "Unknown") return -1;
        return b[1] - a[1];
    });

    const lijst = document.createElement("ul");
    lijst.style.margin = "0";
    lijst.style.paddingLeft = "18px";
    entries.forEach(([naam, fractie]) => {
        const li = document.createElement("li");
        li.style.fontSize = "0.9em";
        if (naam === "Unknown") li.style.color = "#888";
        li.textContent = `${naam}: ${(fractie * 100).toFixed(1)}%`;
        lijst.appendChild(li);
    });
    wrapper.appendChild(lijst);

    return wrapper;
}

// Alleen aandelen die getagd zijn als ETF hebben een per_etf-entry (zie
// analysis.compute_land_sector_verdeling) — dat gebruiken we hier als
// signaal of dit een ETF is, in plaats van een los "is_etf"-veld door te
// geven: als er geen entry is, is het gewoon een los aandeel/n.v.t.
function toonEtfDrilldown(ticker) {
    const container = document.getElementById("etfDrilldown");
    const lsv = huidigeData.land_sector_verdeling;
    const info = lsv && lsv.per_etf && lsv.per_etf[ticker];

    if (!info) {
        container.style.display = "none";
        container.innerHTML = "";
        return;
    }

    container.innerHTML = "";
    const titel = document.createElement("div");
    titel.textContent = "Land- en sectorverdeling van deze ETF";
    titel.style.fontWeight = "bold";
    titel.style.marginBottom = "4px";
    container.appendChild(titel);

    const brontekst = document.createElement("div");
    brontekst.style.fontSize = "0.8em";
    brontekst.style.marginBottom = "10px";
    if (info.land_bron === "provider_csv") {
        brontekst.style.color = "#2c7a4b";
        brontekst.textContent = "Land: op basis van de volledige holdings-lijst van de fondsprovider.";
    } else {
        brontekst.style.color = "#888";
        brontekst.textContent = "Land: op basis van top-10-holdings (beperkte dekking) — grotendeels \"Unknown\".";
    }
    container.appendChild(brontekst);

    const rij = document.createElement("div");
    rij.style.display = "flex";
    rij.style.gap = "40px";
    rij.appendChild(maakVerdelingLijst("Land", info.land));
    rij.appendChild(maakVerdelingLijst("Sector", info.sector));
    container.appendChild(rij);

    container.style.display = "block";
}

function toonVerdeling() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    if (toonVerrijkingWachtstatusIndienNodig()) return;

    const items = huidigeData.verdeling;
    if (!items || items.length === 0) {
        document.getElementById("geenData").style.display = "block";
        document.getElementById("verdelingTekst").style.display = "none";
        return;
    }
    document.getElementById("geenData").style.display = "none";

    const totaal = items.reduce((som, i) => som + i.waarde, 0);
    const etfWaarde = items.filter(i => i.is_etf).reduce((som, i) => som + i.waarde, 0);
    const aandeelWaarde = totaal - etfWaarde;
    const etfPct = totaal ? (etfWaarde / totaal * 100).toFixed(1) : 0;
    const aandeelPct = totaal ? (aandeelWaarde / totaal * 100).toFixed(1) : 0;

    const tekst = document.getElementById("verdelingTekst");
    tekst.style.display = "block";
    tekst.textContent = `ETF's (streeppatroon): ${etfPct}% — Aandelen: ${aandeelPct}%`;

    const kleuren = items.map((_, i) => kleurVoorIndex(i));
    const vlakken = items.map((item, i) => item.is_etf ? maakStrepenPatroon(kleuren[i]) : kleuren[i]);

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "pie",
        data: {
            labels: items.map(i => i.naam),
            datasets: [{
                data: items.map(i => i.waarde),
                backgroundColor: vlakken,
                borderColor: "#fcfcfb",
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: "right" },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const pct = totaal ? (ctx.parsed / totaal * 100).toFixed(1) : 0;
                            return `${ctx.label}: ${formatteerEuro(ctx.parsed)} (${pct}%)`;
                        }
                    }
                },
                datalabels: {
                    color: (ctx) => tekstKleurVoorVlak(kleuren[ctx.dataIndex]),
                    font: { weight: "bold", size: 11 },
                    formatter: (value, ctx) => {
                        const pct = totaal ? (value / totaal * 100) : 0;
                        if (pct < 3) return null;
                        return [kortNaam(items[ctx.dataIndex].naam), `${pct.toFixed(1)}%`];
                    }
                }
            }
        }
    });
}

// Grijs voor de "Unknown"-bucket in Land/Sector — bewust GEEN kleur uit het
// categorische palet, zodat "we weten dit gewoon niet" nooit opgaat in de
// rest van de kleuren en altijd als aparte, herkenbare categorie oogt.
const ONBEKEND_GRIJS = "#6b6b66";

function toonPlatteVerdeling(verdelingObj) {
    if (chart) chart.destroy();

    const entries = Object.entries(verdelingObj || {}).filter(([, bedrag]) => bedrag > 0);
    if (entries.length === 0) {
        document.getElementById("geenData").style.display = "block";
        return;
    }
    document.getElementById("geenData").style.display = "none";

    // "Overig" (kleine landen samengevoegd, zie analysis._voeg_kleine_landen_samen)
    // en "Unknown" krijgen altijd de laatste plekken in de legenda — Overig
    // vlak vóór Unknown, de rest aflopend op bedrag. Dezelfde volgorde-/
    // kleurbehandeling voor allebei, voor visuele consistentie.
    const NEUTRALE_VOLGORDE = { "Overig": 1, "Unknown": 2 };
    entries.sort((a, b) => {
        const va = NEUTRALE_VOLGORDE[a[0]] || 0;
        const vb = NEUTRALE_VOLGORDE[b[0]] || 0;
        if (va !== vb) return va - vb;
        return b[1] - a[1];
    });

    const totaal = entries.reduce((som, [, bedrag]) => som + bedrag, 0);
    let kleurIdx = 0;
    const kleuren = entries.map(([naam]) => (naam === "Unknown" || naam === "Overig") ? ONBEKEND_GRIJS : kleurVoorIndex(kleurIdx++));

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "pie",
        data: {
            labels: entries.map(([naam]) => naam),
            datasets: [{
                data: entries.map(([, bedrag]) => bedrag),
                backgroundColor: kleuren,
                borderColor: "#fcfcfb",
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: "right" },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const pct = totaal ? (ctx.parsed / totaal * 100).toFixed(1) : 0;
                            return `${ctx.label}: ${formatteerEuro(ctx.parsed)} (${pct}%)`;
                        }
                    }
                },
                datalabels: {
                    color: (ctx) => tekstKleurVoorVlak(kleuren[ctx.dataIndex]),
                    font: { weight: "bold", size: 11 },
                    formatter: (value, ctx) => {
                        const pct = totaal ? (value / totaal * 100) : 0;
                        if (pct < 3) return null;
                        return [kortNaam(entries[ctx.dataIndex][0]), `${pct.toFixed(1)}%`];
                    }
                }
            }
        }
    });
}

// Bronnen (ETF-tickers/losse aandelen) met een verwaarloosbare totale
// bijdrage over alle categorieën heen worden samengevoegd tot "Overige
// bronnen" -- voorkomt een onleesbaar volle legenda bij veel posities.
// Zelfde soort drempel-principe als analysis.LAND_OVERIG_DREMPEL, hier
// client-side toegepast omdat de drempel op de RENDER-eenheid (bronnen in
// de legenda) werkt, niet op de data zelf.
const BRON_OVERIG_DREMPEL = 0.005;
const BRON_OVERIG_SLEUTEL = "__overige_bronnen__";

// Herbruikbare gestapelde-staafgrafiek: 1 staaf per categorie (bedrijf,
// land of sector), opgebouwd uit 1 dataset per bron (ETF-ticker of los
// aandeel) die aan die categorie bijdraagt -- zelfde patroon als het oude
// class_degiro.py/trading_degiro.py se plot_top_categories() (pivot per
// categorie x bron, gestapelde staaf, %-label erboven), hier met Chart.js.
// 'categorieData' is {categorieNaam: {bronTicker: waarde}}; 'waarde' mag
// euro's of al-percentages zijn -- opts.totaal (som waarmee gedeeld wordt
// om tot % te komen) bepaalt dat; zonder opts.totaal wordt de ruwe waarde
// getoond zoals-ie is (voor bedrijven-data die al in % van de portfolio zit).
function renderGestapeldeStaafgrafiek(categorieData, bronNamen, opts) {
    opts = opts || {};
    if (chart) chart.destroy();

    if (!categorieData || Object.keys(categorieData).length === 0) {
        document.getElementById("geenData").style.display = "block";
        return;
    }
    document.getElementById("geenData").style.display = "none";

    // Aflopend op totaal (som van alle bronnen per categorie) -- hoogste %
    // eerst. Voor bedrijven (al aflopend gesorteerd door
    // bereken_bedrijven_verdeling) verandert dit niets; voor Land/Sector
    // kwamen categorieën anders in willekeurige object-volgorde binnen.
    const categorieen = Object.keys(categorieData).sort((a, b) => {
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

    // Totaal-%-label boven elke staaf -- zelfde effect als de
    // ax.text(...)-regel in het oude script, hier als klein Chart.js-plugin
    // dat na het tekenen van de stacks de som per x-index erboven zet.
    const totalenPlugin = {
        id: "totalenBovenStaaf",
        afterDatasetsDraw(c) {
            const eersteMeta = c.getDatasetMeta(0);
            if (!eersteMeta || !eersteMeta.data.length) return;
            const { ctx, scales } = c;
            ctx.save();
            ctx.font = "bold 11px sans-serif";
            ctx.fillStyle = "#333";
            ctx.textAlign = "center";
            categorieen.forEach((_, i) => {
                const som = datasets.reduce((s, ds) => s + ds.data[i], 0);
                const bar = eersteMeta.data[i];
                if (!bar) return;
                const y = scales.y.getPixelForValue(som);
                ctx.fillText(`${som.toFixed(1)}%`, bar.x, y - 6);
            });
            ctx.restore();
        }
    };

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "bar",
        data: { labels: categorieen.map(c => kortNaam(c, 20)), datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { stacked: true },
                y: { stacked: true, ticks: { callback: v => `${v}%` } }
            },
            plugins: {
                legend: { position: "right" },
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}%`
                    }
                },
                datalabels: { display: false }
            }
        },
        plugins: [totalenPlugin]
    });
}

function toonLand() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    const europaCheckbox = document.getElementById("europaCheckbox");
    if (toonVerrijkingWachtstatusIndienNodig()) {
        document.getElementById("europaCheckboxWrapper").style.display = "none";
        return;
    }

    const lsv = huidigeData.land_sector_verdeling;
    document.getElementById("europaCheckboxWrapper").style.display = "flex";

    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values((lsv && lsv.land) || {}).reduce((s, w) => s + w, 0);
        renderGestapeldeStaafgrafiek(lsv && lsv.land_per_bron, tickerNamen, { totaal });
    } else {
        // land_europa is server-side voorberekend (zelfde als "land" maar met
        // alle EU/UK/etc. samengevoegd tot één "Europe"-post, zie
        // analysis.compute_land_sector_verdeling) — geen her-berekening of
        // extra API-call nodig bij het aan/uit-zetten van de toggle.
        const bron = europaCheckbox.checked ? (lsv && lsv.land_europa) : (lsv && lsv.land);
        toonPlatteVerdeling(bron);
    }

    // Welke ETF's hebben nog de beperkte (top-10-only) landdekking? Puur
    // informatief, zodat duidelijk is welk deel van "Unknown" hier
    // structureel is (geen bekende provider-bron) i.p.v. een bug.
    const dekkingTekst = document.getElementById("landDekkingTekst");
    const perEtf = (lsv && lsv.per_etf) || {};
    const beperkt = Object.entries(perEtf)
        .filter(([, info]) => info.land_bron !== "provider_csv")
        .map(([ticker]) => ticker);
    if (beperkt.length > 0) {
        dekkingTekst.textContent = `Beperkte landdekking (alleen top-10-holdings) voor: ${beperkt.join(", ")}.`;
        dekkingTekst.style.display = "block";
    } else {
        dekkingTekst.style.display = "none";
    }
}

function toonSector() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    if (toonVerrijkingWachtstatusIndienNodig()) return;

    const lsv = huidigeData.land_sector_verdeling;
    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values((lsv && lsv.sector) || {}).reduce((s, w) => s + w, 0);
        renderGestapeldeStaafgrafiek(lsv && lsv.sector_per_bron, tickerNamen, { totaal });
    } else {
        toonPlatteVerdeling(lsv && lsv.sector);
    }
}

function ververAandeelSelect() {
    const select = document.getElementById("aandeelSelect");
    const huidigeKeuze = select.value;
    select.innerHTML = "";
    huidigeData.tickers.forEach(t => {
        const option = document.createElement("option");
        option.value = t.ticker;
        option.textContent = t.nog_in_bezit === false ? `${t.naam} (oud)` : t.naam;
        select.appendChild(option);
    });
    if (huidigeData.tickers.some(t => t.ticker === huidigeKeuze)) {
        select.value = huidigeKeuze;
    }
}

async function slaBijnaamOp(ticker, bijnaam) {
    toonLaadOverlay("Aanpassen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/bijnaam`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker, bijnaam })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Opslaan mislukt.");
        }
        // build_portfolio_response() geeft sinds het gefaseerd-laden-werk
        // alleen nog de kern terug (zie CLAUDE.md) -- Object.assign i.p.v.
        // huidigeData = data zodat de al opgehaalde verrijkingsvelden
        // (Verdeling/Land/Sector/Bedrijven/ETF-overlap) niet verdwijnen.
        // laadVerrijking ververst ze daarna alsnog op de achtergrond, want
        // de net gewijzigde bijnaam wijzigt ook namen dáárin.
        Object.assign(huidigeData, data);
        ververAandeelSelect();
        toonInstellingen();
        toonTickerWaarschuwingBanner(data.ticker_waarschuwingen || []);
        laadVerrijking(huidigeData.code);
        const msg = document.getElementById("instellingenMsg");
        msg.style.color = "#2c7a4b";
        msg.textContent = "Bijnaam opgeslagen.";
        msg.style.display = "block";
    } catch (e) {
        const msg = document.getElementById("instellingenMsg");
        msg.style.color = "#9C0006";
        msg.textContent = "Bijnaam opslaan mislukt. Probeer het opnieuw.";
        msg.style.display = "block";
    } finally {
        verbergLaadOverlay();
    }
}

async function resetBijnaam(ticker) {
    toonLaadOverlay("Aanpassen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/reset-bijnaam`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Reset mislukt.");
        }
        // Zelfde reden als slaBijnaamOp(): Object.assign i.p.v. overschrijven,
        // plus een verse laadVerrijking() voor de bijgewerkte namen daarin.
        Object.assign(huidigeData, data);
        ververAandeelSelect();
        toonInstellingen();
        toonTickerWaarschuwingBanner(data.ticker_waarschuwingen || []);
        laadVerrijking(huidigeData.code);
    } catch (e) {
        const msg = document.getElementById("instellingenMsg");
        msg.style.color = "#9C0006";
        msg.textContent = "Bijnaam resetten mislukt. Probeer het opnieuw.";
        msg.style.display = "block";
    } finally {
        verbergLaadOverlay();
    }
}

function toonInstellingen() {
    document.getElementById("instellingenMsg").style.display = "none";
    const sectie = document.getElementById("instellingenSectie");
    sectie.innerHTML = "<p>Geef per aandeel/ETF optioneel een eigen bijnaam op.</p>";

    huidigeData.tickers.forEach(t => {
        const rij = document.createElement("div");
        rij.style.marginBottom = "12px";

        const label = document.createElement("div");
        label.textContent = `${t.ticker} (origineel: ${t.echte_naam})`;
        label.style.fontSize = "0.85em";
        label.style.color = "#666";

        const input = document.createElement("input");
        input.type = "text";
        input.value = t.naam;
        input.style.width = "300px";
        input.style.marginRight = "8px";

        const opslaanBtn = document.createElement("button");
        opslaanBtn.textContent = "Opslaan";
        opslaanBtn.onclick = () => slaBijnaamOp(t.ticker, input.value.trim());

        const resetBtn = document.createElement("button");
        resetBtn.textContent = "Reset";
        resetBtn.style.marginLeft = "6px";
        resetBtn.onclick = () => resetBijnaam(t.ticker);

        rij.appendChild(label);
        rij.appendChild(input);
        rij.appendChild(opslaanBtn);
        rij.appendChild(resetBtn);
        sectie.appendChild(rij);
    });
}

const ZEKERHEID_LABELS = {
    zeker: "Zeker",
    onzeker: "Onzeker",
    geen_match: "Geen match gevonden",
    onbekend: "Onbekend (opnieuw uploaden om te verversen)"
};

function voegInfoRegelToe(container, label, waarde) {
    const regel = document.createElement("div");
    regel.style.fontSize = "0.85em";
    regel.style.color = waarde ? "#666" : "#999";
    const labelSpan = document.createElement("span");
    labelSpan.textContent = `${label}: `;
    regel.appendChild(labelSpan);
    regel.appendChild(document.createTextNode(waarde || "onbekend"));
    container.appendChild(regel);
}

function maakBeursRegel(excelBeurs, yahooBeurs, beursKlopt) {
    const beursRegel = document.createElement("div");
    beursRegel.style.fontSize = "0.85em";
    beursRegel.style.marginTop = "2px";
    const tekst = `Beurs — Excel: ${excelBeurs || "onbekend"}, Yahoo: ${yahooBeurs || "onbekend"}`;
    if (beursKlopt === false) {
        beursRegel.style.color = "#9C0006";
        beursRegel.textContent = `⚠️ ${tekst} — komt niet overeen`;
    } else if (beursKlopt === true) {
        beursRegel.style.color = "#2c7a4b";
        beursRegel.textContent = `✓ ${tekst}`;
    } else {
        beursRegel.style.color = "#666";
        beursRegel.textContent = tekst;
    }
    return beursRegel;
}

function maakPrijscontroleTabel(prijsChecks) {
    if (!prijsChecks || prijsChecks.length === 0) {
        const p = document.createElement("p");
        p.style.fontSize = "0.85em";
        p.style.color = "#999";
        p.textContent = "Geen prijscontrole beschikbaar (geen transacties met een koers > 0 gevonden).";
        return p;
    }

    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";

    const tabel = document.createElement("table");
    tabel.style.fontSize = "0.85em";
    tabel.style.borderCollapse = "collapse";
    tabel.style.marginTop = "4px";
    wrapper.appendChild(tabel);

    const kop = document.createElement("tr");
    ["Datum", "Excel-koers", "Yahoo-koers", "High", "Low", "Binnen dagrange", "Afwijking", ""].forEach(tekst => {
        const th = document.createElement("th");
        th.textContent = tekst;
        th.style.textAlign = "left";
        th.style.padding = "2px 14px 2px 0";
        th.style.borderBottom = "1px solid #ddd";
        kop.appendChild(th);
    });
    tabel.appendChild(kop);

    let heeftSplitCorrectie = false;

    prijsChecks.forEach(c => {
        const rij = document.createElement("tr");
        const gecorrigeerd = c.yahoo_koers_gecorrigeerd != null;
        if (gecorrigeerd) heeftSplitCorrectie = true;
        const yahooKoersTekst = c.yahoo_koers == null
            ? "onbekend"
            : gecorrigeerd ? `${c.yahoo_koers_gecorrigeerd.toFixed(3)} *` : c.yahoo_koers.toFixed(3);

        [
            c.datum,
            c.bekende_koers != null ? c.bekende_koers.toFixed(3) : "-",
            yahooKoersTekst,
            c.high != null ? c.high.toFixed(3) : "-",
            c.low != null ? c.low.toFixed(3) : "-",
        ].forEach((tekst, i) => {
            const td = document.createElement("td");
            td.textContent = tekst;
            td.style.padding = "2px 14px 2px 0";
            if (i === 2 && gecorrigeerd) {
                td.title = `Ruwe Yahoo-koers ${c.yahoo_koers.toFixed(3)}, gecorrigeerd voor een split sinds deze `
                    + `datum (factor ×${c.split_factor.toFixed(4)}).`;
            }
            rij.appendChild(td);
        });

        const dagrangeTd = document.createElement("td");
        dagrangeTd.style.padding = "2px 14px 2px 0";
        if (c.binnen_dagrange === true) {
            dagrangeTd.textContent = "✓";
            dagrangeTd.style.color = "#2c7a4b";
            dagrangeTd.title = "Excel-koers valt binnen het intraday-high/low van deze handelsdag";
        } else if (c.binnen_dagrange === false) {
            dagrangeTd.textContent = "✗";
            dagrangeTd.style.color = "#9C0006";
            dagrangeTd.title = "Excel-koers valt buiten het intraday-high/low van deze handelsdag";
        } else {
            dagrangeTd.textContent = "–";
            dagrangeTd.style.color = "#999";
            dagrangeTd.title = "Geen High/Low-data beschikbaar voor deze datum";
        }
        rij.appendChild(dagrangeTd);

        const afwijkingTd = document.createElement("td");
        afwijkingTd.textContent = c.afwijking_pct != null ? `${c.afwijking_pct.toFixed(1)}%` : "-";
        afwijkingTd.style.padding = "2px 14px 2px 0";
        rij.appendChild(afwijkingTd);

        const iconTd = document.createElement("td");
        if (c.niveau === "ok") {
            iconTd.textContent = "✓";
            iconTd.style.color = "#2c7a4b";
            iconTd.title = "Prijs komt overeen";
        } else if (c.niveau === "mild") {
            iconTd.textContent = "🔍";
            iconTd.style.color = "#B8860B";
            iconTd.title = "Klein verschil — waarschijnlijk normaal (Yahoo's slotkoers vs. een "
                + "intraday-transactieprijs), geen reden om de ticker te wantrouwen";
        } else if (c.niveau === "waarschuwing") {
            iconTd.textContent = "⚠️";
            iconTd.style.color = "#9C0006";
            iconTd.title = "Grote afwijking — mogelijk toch de verkeerde ticker";
        } else {
            iconTd.textContent = "?";
            iconTd.style.color = "#999";
        }
        rij.appendChild(iconTd);
        tabel.appendChild(rij);
    });

    if (heeftSplitCorrectie) {
        const voetnoot = document.createElement("p");
        voetnoot.style.fontSize = "0.85em";
        voetnoot.style.color = "#999";
        voetnoot.style.marginTop = "4px";
        voetnoot.textContent = "* gecorrigeerd voor een aandelensplitsing die na deze datum heeft plaatsgevonden "
            + "(zweef over de koers voor details).";
        wrapper.appendChild(voetnoot);
    }

    return wrapper;
}

// Laatste (meest recente) prijscheck met een bekende dagrange -- voor de
// ETF-weergave hieronder, waar High/Low i.p.v. land/sector het prominente
// signaal is (zie CLAUDE.md/opdracht_ticker_zekerheid_dagrange_performance.md).
function laatsteDagrangeUitChecks(prijsChecks) {
    if (!prijsChecks) return null;
    for (let i = prijsChecks.length - 1; i >= 0; i--) {
        const c = prijsChecks[i];
        if (c.high != null && c.low != null) return { high: c.high, low: c.low };
    }
    return null;
}

// Alternatieve kandidaten als tabel (i.p.v. een platte bullet-lijst), zelfde
// opmaak als maakPrijscontroleTabel. isEtf bepaalt de kolomset: Land/Sector
// voor een aandeel-kandidaat, High/Low voor een ETF-kandidaat -- 'alt' is
// altijd al vooraf op is_etf gefilterd door de aanroeper.
function maakAlternatievenTabel(alternatieven, aanbevolenAlternatief, isEtf) {
    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";

    const tabel = document.createElement("table");
    tabel.style.fontSize = "0.85em";
    tabel.style.borderCollapse = "collapse";
    tabel.style.marginTop = "4px";
    wrapper.appendChild(tabel);

    const kolomLabels = isEtf
        ? ["Ticker", "Beurs", "High", "Low", "Valuta", "Gem. afwijking", "Matches", ""]
        : ["Ticker", "Beurs", "Land", "Sector", "Valuta", "Gem. afwijking", "Matches", ""];

    const kop = document.createElement("tr");
    kolomLabels.forEach(tekst => {
        const th = document.createElement("th");
        th.textContent = tekst;
        th.style.textAlign = "left";
        th.style.padding = "2px 14px 2px 0";
        th.style.borderBottom = "1px solid #ddd";
        kop.appendChild(th);
    });
    tabel.appendChild(kop);

    alternatieven.forEach(alt => {
        const rij = document.createElement("tr");
        const aanbevolen = aanbevolenAlternatief === alt.ticker;
        if (aanbevolen) rij.style.background = "#eaf6ee";

        const afwijkingTekst = alt.gemiddelde_afwijking_pct != null
            ? `${alt.gemiddelde_afwijking_pct.toFixed(1)}%`
            : "geen prijsdata";
        const waarden = isEtf
            ? [
                alt.ticker, alt.beurs || "onbekend",
                alt.high != null ? alt.high.toFixed(3) : "-", alt.low != null ? alt.low.toFixed(3) : "-",
                alt.valuta || "onbekend", afwijkingTekst, String(alt.aantal_matches),
            ]
            : [
                alt.ticker, alt.beurs || "onbekend", alt.land || "onbekend", alt.sector || "onbekend",
                alt.valuta || "onbekend", afwijkingTekst, String(alt.aantal_matches),
            ];
        waarden.forEach(tekst => {
            const td = document.createElement("td");
            td.textContent = tekst;
            td.style.padding = "2px 14px 2px 0";
            rij.appendChild(td);
        });

        const labelTd = document.createElement("td");
        labelTd.style.padding = "2px 14px 2px 0";
        if (aanbevolen) {
            labelTd.textContent = "← aanbevolen";
            labelTd.style.color = "#2c7a4b";
            labelTd.style.fontWeight = "bold";
        }
        rij.appendChild(labelTd);

        tabel.appendChild(rij);
    });

    return wrapper;
}

// Eén samenvattingsregel voor de OpenFIGI-root-check (server-side al
// berekend, zie _openfigi_root_bekend()/_voeg_openfigi_check_toe() in
// analysis.py) -- i.p.v. de volledige ruwe resultatentabel uit de vorige
// opdracht, die vooral ruis bleek (soms 100+ rijen per positie). Geen regel
// bij p.openfigi_root_bekend === null/undefined (geen ticker of geen
// OpenFIGI-resultaten om tegen te vergelijken -- geen oordeel mogelijk).
function maakOpenfigiRegel(p) {
    if (p.openfigi_root_bekend == null) return null;

    const regel = document.createElement("div");
    regel.style.fontSize = "0.85em";
    regel.style.marginTop = "2px";
    const root = p.ticker ? p.ticker.split(".")[0] : "";

    if (p.openfigi_root_bekend) {
        const n = p.openfigi_root_matches || 0;
        regel.style.color = "#2c7a4b";
        regel.textContent = `✓ OpenFIGI: root '${root}' bevestigd (${n} resultaat${n === 1 ? "" : "en"})`;
    } else {
        regel.style.color = "#9C0006";
        regel.style.fontWeight = "bold";
        regel.textContent = `⚠️ OpenFIGI: root '${root}' niet gevonden — mogelijk verkeerde ticker`;
    }
    return regel;
}

function maakTickerZekerheidKaart(p) {
    const rij = document.createElement("div");
    rij.style.marginBottom = "18px";
    rij.style.paddingBottom = "14px";
    rij.style.borderBottom = "1px solid #eee";

    if (p.waarschuwing) {
        const banner = document.createElement("div");
        // p.waarschuwing kan meerdere \n-gescheiden boodschappen bevatten
        // (bv. de prijscontrole + een aanvullende OpenFIGI-bevinding, zie
        // _voeg_openfigi_check_toe in analysis.py) -- pre-line houdt die
        // op aparte regels i.p.v. ze aaneen te laten lopen.
        banner.textContent = `⚠️ ${p.waarschuwing}`;
        banner.style.whiteSpace = "pre-line";
        banner.style.background = "#fdecea";
        banner.style.color = "#9C0006";
        banner.style.padding = "8px 10px";
        banner.style.borderRadius = "4px";
        banner.style.marginBottom = "8px";
        banner.style.fontSize = "0.9em";
        rij.appendChild(banner);
    }

    const titel = document.createElement("div");
    const label = ZEKERHEID_LABELS[p.zekerheid] || p.zekerheid;
    const naamStrong = document.createElement("strong");
    naamStrong.textContent = p.naam;
    titel.appendChild(naamStrong);
    titel.appendChild(document.createTextNode(p.ticker ? ` (${p.ticker}) — ${label}` : ` — ${label}`));
    rij.appendChild(titel);

    if (!p.ticker) {
        const geenTicker = document.createElement("p");
        geenTicker.style.fontSize = "0.85em";
        geenTicker.style.color = "#999";
        geenTicker.textContent = "Geen ticker gevonden voor deze positie.";
        rij.appendChild(geenTicker);
        return rij;
    }

    if (p.is_etf) {
        // Land/sector is voor een ETF een zwak signaal ("Land grootste
        // holding: United States" zegt weinig) -- High/Low is voor een ETF
        // juist een sterk signaal, dus die krijgt hier de prominente plek
        // die land/sector bij een aandeel heeft. Voor een aandeel blijft de
        // bestaande weergave (hieronder, in de else-tak) ongewijzigd.
        voegInfoRegelToe(rij, "Valuta", p.valuta);
        voegInfoRegelToe(rij, "Fondsfamilie", p.fondsfamilie);
        voegInfoRegelToe(rij, "Categorie", p.category);
        rij.appendChild(maakBeursRegel(p.excel_beurs, p.yahoo_beurs, p.beurs_klopt));
        const dagrange = laatsteDagrangeUitChecks(p.prijs_checks);
        if (dagrange) {
            voegInfoRegelToe(rij, "High/Low (laatste controle)", `${dagrange.high.toFixed(3)} / ${dagrange.low.toFixed(3)}`);
        }
    } else {
        // Een ETF heeft geen eigen "Land" (te weinig precisie voor een
        // wereldwijd fonds) — dan tonen we in plaats daarvan het land van de
        // grootste holding, expliciet als apart, anders genoemd veld. Is geen
        // van beide bekend, dan de regel gewoon weglaten i.p.v. "onbekend" te
        // tonen voor iets dat sowieso geen zinnig enkelvoudig antwoord heeft.
        if (p.land) {
            voegInfoRegelToe(rij, "Land", p.land);
        } else if (p.top_holding_land) {
            voegInfoRegelToe(rij, "Land grootste holding", p.top_holding_land);
        }
        voegInfoRegelToe(rij, "Sector", p.sector);
        voegInfoRegelToe(rij, "Valuta", p.valuta);
        voegInfoRegelToe(rij, "Fondsfamilie", p.fondsfamilie);
        voegInfoRegelToe(rij, "Categorie", p.category);
        rij.appendChild(maakBeursRegel(p.excel_beurs, p.yahoo_beurs, p.beurs_klopt));
    }

    const openfigiRegel = maakOpenfigiRegel(p);
    if (openfigiRegel) rij.appendChild(openfigiRegel);

    const prijsKop = document.createElement("div");
    prijsKop.textContent = "Prijscontrole";
    prijsKop.style.fontWeight = "bold";
    prijsKop.style.marginTop = "8px";
    rij.appendChild(prijsKop);
    rij.appendChild(maakPrijscontroleTabel(p.prijs_checks));

    if (p.alternatieven && p.alternatieven.length > 0) {
        const altKop = document.createElement("div");
        altKop.textContent = "Alternatieve kandidaten";
        altKop.style.fontWeight = "bold";
        altKop.style.marginTop = "8px";
        rij.appendChild(altKop);

        // Per kandidaat onderscheiden op ETF/aandeel (niet één vaste
        // kolommenset voor iedereen) -- meestal zijn alle kandidaten van
        // hetzelfde type als de hoofdpositie, maar dat hoeft niet zo te zijn.
        const aandeelAlternatieven = p.alternatieven.filter(alt => !alt.is_etf);
        const etfAlternatieven = p.alternatieven.filter(alt => alt.is_etf);
        if (aandeelAlternatieven.length > 0) {
            rij.appendChild(maakAlternatievenTabel(aandeelAlternatieven, p.aanbevolen_alternatief, false));
        }
        if (etfAlternatieven.length > 0) {
            rij.appendChild(maakAlternatievenTabel(etfAlternatieven, p.aanbevolen_alternatief, true));
        }
    }

    return rij;
}

// Voert taakFn uit voor elk item in 'items', met maximaal 'limiet' taken
// tegelijk in de lucht -- niet alles in één keer (rate-limit-risico bij
// Yahoo/gunicorn-workers) en niet na elkaar (traag bij veel posities). Geen
// SSE/websockets nodig, gewone fetch()-calls met deze eenvoudige worker-pool
// zijn genoeg (zie CLAUDE.md/opdracht_ticker_zekerheid_dagrange_performance.md).
async function voerMetConcurrencyLimietUit(items, limiet, taakFn) {
    let volgendeIndex = 0;
    async function werker() {
        while (volgendeIndex < items.length) {
            const i = volgendeIndex++;
            await taakFn(items[i], i);
        }
    }
    const workers = Array.from({ length: Math.min(limiet, items.length) }, () => werker());
    await Promise.all(workers);
}

// Placeholder-kaart voor 1 positie terwijl de bijbehorende /positie-aanroep
// nog loopt -- wordt in-place vervangen door maakTickerZekerheidKaart()'s
// volledige kaart zodra het resultaat binnen is (zie toonInstellingenTicker).
function maakTickerZekerheidPlaceholder(p) {
    const rij = document.createElement("div");
    rij.style.marginBottom = "18px";
    rij.style.paddingBottom = "14px";
    rij.style.borderBottom = "1px solid #eee";

    const titel = document.createElement("div");
    const naamStrong = document.createElement("strong");
    naamStrong.textContent = p.naam;
    titel.appendChild(naamStrong);
    rij.appendChild(titel);

    const status = document.createElement("p");
    status.className = "tickerZekerheidStatus";
    status.style.fontSize = "0.85em";
    status.style.color = "#999";
    status.textContent = "Bezig met controleren...";
    rij.appendChild(status);

    return rij;
}

function toonTickerZekerheidPositieFout(kaart, tekst) {
    const status = kaart.querySelector(".tickerZekerheidStatus");
    if (status) {
        status.textContent = `⚠️ ${tekst}`;
        status.style.color = "#9C0006";
    }
}

async function toonInstellingenTicker() {
    // Een eenmalige ("niet opslaan") analyse heeft geen code om de losse
    // /ticker-zekerheid-endpoints mee aan te roepen (die lezen transacties
    // uit de database). De GOEDKOPE ticker-match (zonder Yahoo-
    // prijsverificatie) heeft /upload toen al meegestuurd onder
    // huidigeData.ticker_zekerheid — de dure, prijs-geverifieerde variant is
    // hier een losse, door de gebruiker aangevraagde actie geworden (zie
    // toonInstellingenTickerBasis), want die liep bij grotere portfolio's
    // met een koude cache ruim over de gunicorn-timeout heen als hij hier
    // altijd synchroon voor de volle portfolio draaide.
    if (!huidigeData.code) {
        toonInstellingenTickerBasis();
        return;
    }

    const sectie = document.getElementById("instellingenTickerSectie");
    sectie.innerHTML = "";

    // Alleen de (vrijwel instante) lijst van posities ophalen -- de dure
    // prijscontrole gebeurt hieronder per positie apart, zodat één trage/
    // rate-limited positie niet meer de hele pagina laat mislukken (zie
    // CLAUDE.md/opdracht_ticker_zekerheid_dagrange_performance.md).
    toonLaadOverlay("Posities ophalen...");
    let data;
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/ticker-zekerheid/lijst`);
        data = await res.json();
        if (!res.ok) {
            const foutmelding = document.createElement("p");
            foutmelding.style.color = "#9C0006";
            foutmelding.textContent = data.error || "Kon de lijst met posities niet ophalen.";
            sectie.appendChild(foutmelding);
            return;
        }
    } catch (e) {
        const foutmelding = document.createElement("p");
        foutmelding.style.color = "#9C0006";
        foutmelding.textContent = "Kon de lijst met posities niet ophalen (netwerkfout).";
        sectie.appendChild(foutmelding);
        return;
    } finally {
        verbergLaadOverlay();
    }

    const intro = document.createElement("p");
    intro.textContent = "Hoe zeker is de gevonden ticker per positie? De prijs op een paar transactiedatums wordt "
        + "vergeleken met de historische Yahoo-koers — dat is een sterker signaal dan alleen de beurs-match.";
    sectie.appendChild(intro);

    const posities = data.posities || [];
    if (posities.length === 0) {
        const p = document.createElement("p");
        p.textContent = "Geen posities gevonden.";
        sectie.appendChild(p);
        return;
    }

    const kaarten = {};
    posities.forEach(p => {
        const kaart = maakTickerZekerheidPlaceholder(p);
        sectie.appendChild(kaart);
        kaarten[`${p.isin}|${p.beurs}`] = kaart;
    });

    // Concurrency-limiet van 4: elke aparte /positie-aanroep is klein genoeg
    // om nooit tegen een timeout aan te lopen, en zodra er één terugkomt
    // wordt precies die rij bijgewerkt -- de rest blijft gewoon "bezig...".
    await voerMetConcurrencyLimietUit(posities, 4, async (p) => {
        const key = `${p.isin}|${p.beurs}`;
        const kaart = kaarten[key];
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000);
        try {
            const url = `/api/portfolio/${huidigeData.code}/ticker-zekerheid/positie`
                + `?isin=${encodeURIComponent(p.isin)}&beurs=${encodeURIComponent(p.beurs)}`;
            const res = await fetch(url, { signal: controller.signal });
            const resultaat = await res.json();
            if (!res.ok) {
                toonTickerZekerheidPositieFout(kaart, resultaat.error || "Kon deze positie niet controleren.");
                return;
            }
            kaart.replaceWith(maakTickerZekerheidKaart(resultaat));
        } catch (e) {
            toonTickerZekerheidPositieFout(
                kaart,
                e.name === "AbortError"
                    ? "Duurde te lang en is afgebroken."
                    : "Netwerkfout bij het controleren van deze positie."
            );
        } finally {
            clearTimeout(timeoutId);
        }
    });
}

// Lichte weergave voor een eenmalige ("niet opslaan") analyse: toont eerst
// de goedkope, niet-prijsgeverifieerde match die /upload al meestuurde, met
// een knop om alsnog de uitgebreide (prijs-geverifieerde) check op te
// vragen via /api/ticker-zekerheid-check — een losse request zodat de
// hoofd-upload niet meer het risico loopt op een gunicorn-timeout bij een
// grotere portfolio (zie toonInstellingenTicker hierboven).
function toonInstellingenTickerBasis() {
    const sectie = document.getElementById("instellingenTickerSectie");
    sectie.innerHTML = "";

    const intro = document.createElement("p");
    intro.textContent = "Dit is de snelle ticker-match, zonder prijsvergelijking (zelfde manier als bij een "
        + "normale upload). Voor de uitgebreide check — vergelijkt transactieprijzen met Yahoo's historische "
        + "koersen, een sterker signaal — klik op de knop hieronder. Dat kan bij veel posities een paar "
        + "seconden tot een halve minuut duren.";
    sectie.appendChild(intro);

    const foutEl = document.createElement("p");
    foutEl.style.color = "#9C0006";
    foutEl.style.display = "none";

    const lijst = document.createElement("div");
    (huidigeData.ticker_zekerheid || []).forEach(p => lijst.appendChild(maakTickerZekerheidKaart(p)));

    const knop = document.createElement("button");
    knop.textContent = "Controleer ticker-zekerheid (uitgebreid)";
    knop.style.marginBottom = "14px";
    knop.addEventListener("click", () => controleerTickerZekerheidUitgebreid(knop, foutEl, lijst));

    sectie.appendChild(knop);
    sectie.appendChild(foutEl);
    sectie.appendChild(lijst);
}

async function controleerTickerZekerheidUitgebreid(knop, foutEl, lijst) {
    foutEl.style.display = "none";
    knop.disabled = true;
    knop.textContent = "Bezig met controleren...";

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 60000);
    try {
        const res = await fetch("/api/ticker-zekerheid-check", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ posities: huidigeData.ticker_posities_ruw || [] }),
            signal: controller.signal,
        });
        const data = await res.json();
        if (!res.ok) {
            foutEl.textContent = data.error || "Kon ticker-zekerheid niet controleren.";
            foutEl.style.display = "block";
            return;
        }
        huidigeData.ticker_zekerheid = data.posities;
        lijst.innerHTML = "";
        data.posities.forEach(p => lijst.appendChild(maakTickerZekerheidKaart(p)));
        knop.style.display = "none";
    } catch (e) {
        foutEl.textContent = e.name === "AbortError"
            ? "De uitgebreide controle duurt te lang en is afgebroken. Probeer het later opnieuw."
            : "Er ging iets mis bij het controleren (netwerkfout). Probeer het opnieuw.";
        foutEl.style.display = "block";
    } finally {
        clearTimeout(timeoutId);
        knop.disabled = false;
        if (knop.style.display !== "none") knop.textContent = "Controleer ticker-zekerheid (uitgebreid)";
    }
}

// Kleur per ticker consistent met de rest van het dashboard: dezelfde
// volgorde als huidigeData.tickers (die ook de Verdeling-taart en de
// aandeel-select vult), zodat eenzelfde positie overal dezelfde kleur heeft.
function kleurVoorTicker(ticker) {
    const idx = huidigeData.tickers.findIndex(t => t.ticker === ticker);
    return kleurVoorIndex(idx >= 0 ? idx : 0);
}

function maakDividendTabel(perTicker) {
    const tabel = document.createElement("table");
    tabel.style.fontSize = "0.9em";
    tabel.style.borderCollapse = "collapse";
    tabel.style.marginTop = "8px";

    const kop = document.createElement("tr");
    ["Aandeel/ETF", "Netto dividend"].forEach(tekst => {
        const th = document.createElement("th");
        th.textContent = tekst;
        th.style.textAlign = "left";
        th.style.padding = "3px 20px 3px 0";
        th.style.borderBottom = "1px solid #ddd";
        kop.appendChild(th);
    });
    tabel.appendChild(kop);

    perTicker.forEach(item => {
        const rij = document.createElement("tr");
        const naamTd = document.createElement("td");
        naamTd.textContent = item.bijnaam;
        naamTd.style.padding = "3px 20px 3px 0";
        const bedragTd = document.createElement("td");
        bedragTd.textContent = formatteerEuro(item.totaal_netto);
        bedragTd.style.padding = "3px 20px 3px 0";
        rij.appendChild(naamTd);
        rij.appendChild(bedragTd);
        tabel.appendChild(rij);
    });

    return tabel;
}

function renderDividendStats(data) {
    const sectie = document.getElementById("dividendStatsSectie");
    sectie.innerHTML = "";

    const totaalDiv = document.createElement("div");
    totaalDiv.style.fontSize = "1.8em";
    totaalDiv.style.fontWeight = "bold";
    totaalDiv.style.color = "#2c7a4b";
    totaalDiv.textContent = `${formatteerEuro(data.totaal_netto)} totaal ontvangen dividend`;
    sectie.appendChild(totaalDiv);

    if (!data.per_ticker || data.per_ticker.length === 0) {
        const p = document.createElement("p");
        p.style.color = "#888";
        p.textContent = "Nog geen dividend ontvangen.";
        sectie.appendChild(p);
        return;
    }

    sectie.appendChild(maakDividendTabel(data.per_ticker));
}

function toonDividendChart(cumulatief) {
    if (chart) chart.destroy();
    const labelsNL = cumulatief.datums.map(formatDatum);
    const tickers = Object.keys(cumulatief.per_ticker);

    const datasets = tickers.map(ticker => {
        const kleur = kleurVoorTicker(ticker);
        const naam = (huidigeData.tickers.find(t => t.ticker === ticker) || {}).naam || ticker;
        return {
            label: naam,
            data: cumulatief.per_ticker[ticker],
            borderColor: kleur,
            backgroundColor: kleur,
            fill: true,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderWidth: 1.5,
        };
    });

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "line",
        data: { labels: labelsNL, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            locale: "nl-NL",
            scales: {
                y: { stacked: true, beginAtZero: true, title: { display: true, text: "Cumulatief dividend (€)" } }
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.dataset.label}: ${formatteerEuro(ctx.parsed.y)}`
                    }
                },
                zoom: {
                    pan: { enabled: true, mode: "x" },
                    zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" }
                },
                datalabels: { display: false }
            }
        }
    });
}

// Losstaand van de rest van het dashboard: alleen het Dividend-tabblad
// heeft een Account-overzicht nodig, dus alleen déze weergave valt terug
// op deze melding — andere tabbladen (Portfolio-home, Verdeling, ...)
// blijven gewoon werken zonder Account-bestand.
function maakGeenRekeningoverzichtMelding() {
    const container = document.createElement("div");

    const p = document.createElement("p");
    p.textContent = "Dividendgegevens niet beschikbaar — upload eerst je Account-overzicht (Excel) om dit te kunnen zien.";
    container.appendChild(p);

    const knop = document.createElement("button");
    knop.textContent = "Terug naar upload";
    knop.onclick = gaTerugNaarUpload;
    container.appendChild(knop);

    return container;
}

async function toonDividend() {
    const sectie = document.getElementById("dividendStatsSectie");
    document.getElementById("chartWrapper").style.display = "block";
    sectie.innerHTML = "<p>Bezig met laden...</p>";

    if (!huidigeData.code) {
        if (chart) { chart.destroy(); chart = null; }
        document.getElementById("chartWrapper").style.display = "none";
        sectie.innerHTML = "";
        sectie.appendChild(maakGeenRekeningoverzichtMelding());
        return;
    }

    let res, data;
    try {
        res = await fetch(`/api/portfolio/${huidigeData.code}/dividend`);
        data = await res.json();
    } catch (e) {
        sectie.innerHTML = "";
        const p = document.createElement("p");
        p.style.color = "#9C0006";
        p.textContent = "Kon dividendgegevens niet ophalen.";
        sectie.appendChild(p);
        return;
    }

    if (!res.ok || !data.beschikbaar) {
        if (chart) { chart.destroy(); chart = null; }
        document.getElementById("chartWrapper").style.display = "none";
        sectie.innerHTML = "";
        sectie.appendChild(maakGeenRekeningoverzichtMelding());
        return;
    }

    renderDividendStats(data);
    toonDividendChart(data.cumulatief);
}

// Centrale plek voor alle euro-opmaak in de app — Nederlandse notatie
// (punt als duizendtal-scheiding, komma als decimaalteken), bv. €20.966,43.
// Minteken vóór het €-teken bij negatieve bedragen (-€1.234,56), niet erna.
function formatteerEuro(bedrag, decimalen = 2) {
    if (bedrag === null || bedrag === undefined || Number.isNaN(bedrag)) return "onbekend";
    const teken = bedrag < 0 ? "-" : "";
    const getalTekst = new Intl.NumberFormat("nl-NL", {
        minimumFractionDigits: decimalen,
        maximumFractionDigits: decimalen,
    }).format(Math.abs(bedrag));
    return `${teken}€${getalTekst}`;
}

function formatPct(pct) {
    return (pct === null || pct === undefined) ? "onbekend" : `${pct.toFixed(2)}%`;
}

function kleurVoorRendement(pct) {
    if (pct === null || pct === undefined) return "#333";
    return pct >= 0 ? "#2c7a4b" : "#9C0006";
}

function maakStatTegel(label, waardeTekst, kleur) {
    const tegel = document.createElement("div");
    tegel.style.minWidth = "160px";

    const labelDiv = document.createElement("div");
    labelDiv.textContent = label;
    labelDiv.style.fontSize = "0.85em";
    labelDiv.style.color = "#666";
    tegel.appendChild(labelDiv);

    const waardeDiv = document.createElement("div");
    waardeDiv.textContent = waardeTekst;
    waardeDiv.style.fontSize = "1.4em";
    waardeDiv.style.fontWeight = "bold";
    waardeDiv.style.color = kleur || "#333";
    tegel.appendChild(waardeDiv);

    return tegel;
}

function maakTotalenSectie(totalen) {
    const rij = document.createElement("div");
    rij.style.display = "flex";
    rij.style.flexWrap = "wrap";
    rij.style.gap = "24px";
    rij.style.marginBottom = "24px";

    rij.appendChild(maakStatTegel("Totaal geïnvesteerd", formatteerEuro(totalen.geinvesteerd)));
    rij.appendChild(maakStatTegel("Totale huidige waarde", formatteerEuro(totalen.waarde)));
    rij.appendChild(maakStatTegel("Totaal rendement (€)", formatteerEuro(totalen.rendement_eur), kleurVoorRendement(totalen.rendement_eur)));
    rij.appendChild(maakStatTegel("Totaal rendement (%)", formatPct(totalen.rendement_pct), kleurVoorRendement(totalen.rendement_pct)));

    if (totalen.all_time_high && totalen.all_time_high.waarde !== null) {
        rij.appendChild(maakStatTegel(
            "Hoogste rendement",
            `${formatteerEuro(totalen.all_time_high.waarde)} (${formatDatum(totalen.all_time_high.datum)})`
        ));
    }

    if (totalen.transactiekosten_beschikbaar) {
        rij.appendChild(maakStatTegel("Totale transactiekosten", formatteerEuro(totalen.totale_transactiekosten)));
    }

    const container = document.createElement("div");
    container.appendChild(rij);

    if (!totalen.transactiekosten_beschikbaar) {
        const kostenNotitie = document.createElement("p");
        kostenNotitie.style.fontSize = "0.85em";
        kostenNotitie.style.color = "#888";
        kostenNotitie.textContent = "Totale transactiekosten: data ontbreekt — het geüploade transactiebestand bevat geen aparte kostenkolom.";
        container.appendChild(kostenNotitie);
    }

    return container;
}

// Gedeelde cel voor een rendement-kolom in "€ (percentage%)"-notatie
// (zelfde patroon overal: Huidige posities, Verkochte posities, Rendement
// per jaar), groen/rood op basis van het €-bedrag.
function maakRendementCel(eurWaarde, pctWaarde) {
    const td = document.createElement("td");
    td.style.padding = "4px 16px 4px 0";
    td.style.color = kleurVoorRendement(eurWaarde);
    td.style.fontWeight = "bold";
    td.textContent = `${formatteerEuro(eurWaarde)} (${formatPct(pctWaarde)})`;
    return td;
}

// Herbruikbare sorteerbare-tabel-helper voor het Statistieken-tabblad.
// kolommen: array van { label, waarde: fn(rij) => getal|null (optioneel —
// zonder 'waarde' is de kolom niet klikbaar/sorteerbaar), renderTd:
// fn(rij) => HTMLTableCellElement }.
// Initiële weergave = de 'rijen'-array precies zoals meegegeven (de
// bestaande backend-default-sortering, bv. Huidige posities al aflopend op
// huidige waarde) — er is dus geen actieve sortering totdat de gebruiker op
// een kolomkop klikt. Eerste klik op een kolom sorteert aflopend (hoogste/
// nieuwste eerst is meestal de nuttigste eerste blik), een tweede klik op
// dezelfde kolom draait de richting om. Rijen waarvan waarde(rij)
// null/undefined teruggeeft, blijven altijd onderaan, in beide richtingen.
function maakSorteerbareTabel(kolommen, rijen, opts) {
    opts = opts || {};
    if (!rijen || rijen.length === 0) {
        const p = document.createElement("p");
        p.style.color = "#888";
        p.textContent = opts.legeTekst || "Geen data beschikbaar.";
        return p;
    }

    const tabel = document.createElement("table");
    tabel.style.fontSize = "0.9em";
    tabel.style.borderCollapse = "collapse";
    tabel.style.width = "100%";

    const kopRij = document.createElement("tr");
    const tbody = document.createElement("tbody");

    let sorteerKolom = null;
    let sorteerRichting = "desc";
    const headerInfos = [];

    function tekenTbody() {
        let getoond = rijen;
        if (sorteerKolom !== null) {
            const kol = kolommen[sorteerKolom];
            const metWaarde = [];
            const zonderWaarde = [];
            rijen.forEach(rij => {
                const w = kol.waarde(rij);
                (w === null || w === undefined ? zonderWaarde : metWaarde).push(rij);
            });
            metWaarde.sort((a, b) => {
                const wa = kol.waarde(a), wb = kol.waarde(b);
                return sorteerRichting === "asc" ? wa - wb : wb - wa;
            });
            getoond = metWaarde.concat(zonderWaarde);
        }
        tbody.innerHTML = "";
        getoond.forEach(rij => {
            const tr = document.createElement("tr");
            kolommen.forEach(kol => tr.appendChild(kol.renderTd(rij)));
            tbody.appendChild(tr);
        });
    }

    function werkIndicatorsBij() {
        headerInfos.forEach(({ th, label, index }) => {
            const indicator = sorteerKolom === index ? (sorteerRichting === "asc" ? " ▲" : " ▼") : "";
            th.textContent = label + indicator;
        });
    }

    kolommen.forEach((kol, index) => {
        const th = document.createElement("th");
        th.textContent = kol.label;
        th.style.textAlign = "left";
        th.style.padding = "4px 16px 4px 0";
        th.style.borderBottom = "1px solid #ddd";
        if (kol.waarde) {
            th.style.cursor = "pointer";
            th.style.userSelect = "none";
            th.addEventListener("click", () => {
                if (sorteerKolom === index) {
                    sorteerRichting = sorteerRichting === "asc" ? "desc" : "asc";
                } else {
                    sorteerKolom = index;
                    sorteerRichting = "desc";
                }
                werkIndicatorsBij();
                tekenTbody();
            });
        }
        kopRij.appendChild(th);
        headerInfos.push({ th, label: kol.label, index });
    });

    tabel.appendChild(kopRij);
    tabel.appendChild(tbody);
    tekenTbody();

    // Wrapper i.p.v. de tabel direct teruggeven: laat de tabel op smalle
    // schermen zelf horizontaal scrollen (overflow-x: auto in style.css)
    // i.p.v. de hele pagina breder te maken.
    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";
    wrapper.appendChild(tabel);
    return wrapper;
}

function maakPositieTabel(posities, tickerNamen) {
    const kolommen = [
        {
            label: "Naam/ticker",
            renderTd: p => {
                const td = document.createElement("td");
                const naam = (tickerNamen && tickerNamen[p.ticker]) || p.ticker;
                td.textContent = `${naam} (${p.ticker})`;
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Aantal",
            waarde: p => p.aantal,
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = p.aantal.toLocaleString("nl-NL", { maximumFractionDigits: 4 });
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Huidige waarde",
            waarde: p => p.huidige_waarde,
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(p.huidige_waarde);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "GAK",
            waarde: p => p.gak,
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(p.gak);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Huidige koers",
            waarde: p => p.huidige_koers,
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(p.huidige_koers);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            // Sorteert op rendement_pct (het getal), niet op de
            // samengestelde "€... (...%)"-weergavetekst.
            label: "Rendement",
            waarde: p => p.rendement_pct,
            renderTd: p => maakRendementCel(p.rendement_eur, p.rendement_pct),
        },
        {
            label: "Dividend ontvangen",
            waarde: p => p.dividend_ontvangen || 0,
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(p.dividend_ontvangen || 0);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
    ];
    return maakSorteerbareTabel(kolommen, posities, { legeTekst: "Geen open posities." });
}

function maakGeslotenPositiesTabel(geslotenPosities) {
    const kolommen = [
        {
            label: "Status",
            renderTd: p => {
                const td = document.createElement("td");
                td.style.padding = "4px 16px 4px 0";
                const badge = document.createElement("span");
                badge.textContent = p.nog_in_bezit ? "Deels verkocht" : "Gesloten";
                badge.style.display = "inline-block";
                badge.style.padding = "2px 8px";
                badge.style.borderRadius = "10px";
                badge.style.fontSize = "0.85em";
                badge.style.fontWeight = "bold";
                if (p.nog_in_bezit) {
                    badge.style.background = "#fff3cd";
                    badge.style.color = "#856404";
                } else {
                    badge.style.background = "#e2e3e5";
                    badge.style.color = "#383d41";
                }
                td.appendChild(badge);
                return td;
            },
        },
        {
            label: "Naam/ticker",
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = `${p.naam} (${p.ticker})`;
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Aantal",
            waarde: p => p.aantal,
            renderTd: p => {
                const td = document.createElement("td");
                const aantalTekst = p.aantal.toLocaleString("nl-NL", { maximumFractionDigits: 4 });
                if (p.nog_in_bezit) {
                    const resterendTekst = (p.resterend_aantal || 0).toLocaleString("nl-NL", { maximumFractionDigits: 4 });
                    td.textContent = `${aantalTekst} verkocht, ${resterendTekst} nog in bezit`;
                } else {
                    td.textContent = aantalTekst;
                }
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Gem. aankoopkoers",
            waarde: p => p.gemiddelde_aankoopkoers,
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(p.gemiddelde_aankoopkoers);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Gem. verkoopkoers",
            waarde: p => p.gemiddelde_verkoopkoers,
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = p.gemiddelde_verkoopkoers !== null ? formatteerEuro(p.gemiddelde_verkoopkoers) : "onbekend";
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Rendement (koers)",
            waarde: p => p.rendement_pct,
            renderTd: p => maakRendementCel(p.rendement_eur, p.rendement_pct),
        },
        {
            label: "Dividend ontvangen",
            waarde: p => p.dividend_ontvangen || 0,
            renderTd: p => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(p.dividend_ontvangen || 0);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
    ];
    return maakSorteerbareTabel(kolommen, geslotenPosities, { legeTekst: "Geen verkochte of deels verkochte posities." });
}

function maakJarenTabel(jaren) {
    const kolommen = [
        {
            label: "Jaar",
            waarde: j => j.jaar,
            renderTd: j => {
                const td = document.createElement("td");
                td.style.padding = "4px 16px 4px 0";
                const jaarStrong = document.createElement("strong");
                jaarStrong.textContent = j.jaar;
                td.appendChild(jaarStrong);
                const detail = document.createElement("div");
                detail.style.fontSize = "0.85em";
                detail.style.color = "#888";
                detail.textContent = `${j.dagen_verstreken}d, ${j.pct_van_jaar}% van jaar`;
                td.appendChild(detail);
                return td;
            },
        },
        {
            label: "Startwaarde",
            waarde: j => j.startwaarde,
            renderTd: j => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(j.startwaarde);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Ingelegd",
            waarde: j => j.ingelegd,
            renderTd: j => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(j.ingelegd);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Eindwaarde",
            waarde: j => j.eindwaarde,
            renderTd: j => {
                const td = document.createElement("td");
                td.textContent = formatteerEuro(j.eindwaarde);
                td.style.padding = "4px 16px 4px 0";
                return td;
            },
        },
        {
            label: "Winst",
            waarde: j => j.winst_eur,
            renderTd: j => maakRendementCel(j.winst_eur, j.winst_pct),
        },
    ];

    // Standaard/initiële volgorde: nieuwste jaar eerst (ongewijzigd t.o.v.
    // de vorige implementatie).
    const rijen = [...jaren].reverse();
    return maakSorteerbareTabel(kolommen, rijen, { legeTekst: "Geen jaargegevens beschikbaar." });
}

function maakGeavanceerdSectie(geavanceerd) {
    const rij = document.createElement("div");
    rij.style.display = "flex";
    rij.style.flexWrap = "wrap";
    rij.style.gap = "24px";
    rij.style.margin = "16px 0 8px 0";

    rij.appendChild(maakStatTegel("Gemiddeld jaarrendement", formatPct(geavanceerd.gemiddeld_jaarrendement_pct), kleurVoorRendement(geavanceerd.gemiddeld_jaarrendement_pct)));
    rij.appendChild(maakStatTegel("XIRR", formatPct(geavanceerd.xirr_pct), kleurVoorRendement(geavanceerd.xirr_pct)));
    rij.appendChild(maakStatTegel("TWR", formatPct(geavanceerd.twr_pct), kleurVoorRendement(geavanceerd.twr_pct)));
    rij.appendChild(maakStatTegel("Aantal jaren", geavanceerd.aantal_jaren !== null ? geavanceerd.aantal_jaren.toFixed(2) : "onbekend"));

    return rij;
}

function maakUitlegSectie() {
    const box = document.createElement("div");
    box.style.fontSize = "0.85em";
    box.style.color = "#666";
    box.style.background = "#f4f4f4";
    box.style.borderRadius = "4px";
    box.style.padding = "10px 14px";
    box.style.marginTop = "8px";

    const p1 = document.createElement("p");
    p1.style.margin = "0 0 6px 0";
    p1.innerHTML = "<strong>XIRR</strong> is de eerlijkste rendementsmaatstaf hierboven — die houdt (anders dan de andere percentages) rekening met WANNEER je geld hebt ingelegd, in plaats van alleen met hoeveel.";
    box.appendChild(p1);

    const p2 = document.createElement("p");
    p2.style.margin = "0 0 6px 0";
    p2.innerHTML = "<strong>Jaarrendement</strong> (per jaar, en het gemiddelde daarvan) wordt gedeeld door het bedrag dat dát jaar ingezet was (startwaarde + dat jaar ingelegd) — niet door het totaal over alle jaren. Dit is, net als het totaalrendement, een simpele ratio zonder tijdweging.";
    box.appendChild(p2);

    const p3 = document.createElement("p");
    p3.style.margin = "0";
    p3.innerHTML = "<strong>TWR</strong> (time-weighted return) sluit — anders dan XIRR — het effect van WANNEER je stort juist helemaal uit, door elke periode los te rendementeren en die aan elkaar te koppelen. Handig om je eigen beleggingskeuzes te beoordelen los van je stortingsgedrag; reageert daardoor niet extreem vlak na een storting zoals XIRR dat wel doet.";
    box.appendChild(p3);

    return box;
}

function toonStatistieken() {
    const sectie = document.getElementById("statistiekenSectie");
    sectie.innerHTML = "";

    const stats = huidigeData.statistieken;
    if (!stats) {
        const p = document.createElement("p");
        p.style.color = "#888";
        p.textContent = "Geen statistieken beschikbaar voor deze portfolio.";
        sectie.appendChild(p);
        return;
    }

    const tickerNamen = {};
    (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });

    sectie.appendChild(maakTotalenSectie(stats.totalen));

    const positiesKop = document.createElement("h3");
    positiesKop.textContent = "Posities";
    positiesKop.style.marginBottom = "6px";
    sectie.appendChild(positiesKop);
    sectie.appendChild(maakPositieTabel(stats.posities, tickerNamen));

    const geslotenKop = document.createElement("h3");
    geslotenKop.textContent = "Verkochte posities";
    geslotenKop.style.margin = "24px 0 6px 0";
    sectie.appendChild(geslotenKop);
    sectie.appendChild(maakGeslotenPositiesTabel(stats.gesloten_posities));

    const jarenKop = document.createElement("h3");
    jarenKop.textContent = "Rendement per jaar";
    jarenKop.style.margin = "24px 0 6px 0";
    sectie.appendChild(jarenKop);
    sectie.appendChild(maakJarenTabel(stats.jaren));

    const geavanceerdKop = document.createElement("h3");
    geavanceerdKop.textContent = "Samengesteld rendement";
    geavanceerdKop.style.margin = "24px 0 0 0";
    sectie.appendChild(geavanceerdKop);
    sectie.appendChild(maakGeavanceerdSectie(stats.geavanceerd));
    sectie.appendChild(maakUitlegSectie());
}

// Top 20 bedrijven-tabblad: gestapelde staafgrafiek, 1 staaf per bedrijf,
// onderverdeeld naar welke ETF/los aandeel eraan bijdraagt (zelfde
// renderGestapeldeStaafgrafiek als Land/Sector-staaf hieronder). "Overig"
// bundelt zowel bedrijven buiten de top-20 als het niet-gedekte restant van
// ETF-holdings, dus die twee zijn hier niet los te onderscheiden --
// dekkingTekst hierboven de grafiek maakt wel duidelijk hoe compleet het
// totaal is. per_bron/totaal_pct komen al als percentage van de
// portfoliowaarde uit analysis.bereken_bedrijven_verdeling, dus geen
// aparte 'totaal'-deler nodig.
function toonBedrijven() {
    const sectie = document.getElementById("bedrijvenSectie");
    sectie.innerHTML = "";
    document.getElementById("geenData").style.display = "none";
    if (toonVerrijkingWachtstatusIndienNodig()) {
        if (chart) chart.destroy();
        return;
    }

    const data = huidigeData.bedrijven_verdeling;
    if (!data || !data.top || data.top.length === 0) {
        document.getElementById("geenData").style.display = "block";
        if (chart) chart.destroy();
        return;
    }

    const dekkingTekst = document.createElement("p");
    dekkingTekst.style.fontSize = "0.85em";
    dekkingTekst.style.color = "#888";
    const overigPct = data.totaal_waarde ? (data.overig / data.totaal_waarde * 100) : 0;
    dekkingTekst.textContent = `Dekking: ${(data.dekking_pct * 100).toFixed(1)}% van de portfoliowaarde is toegewezen aan een bekend bedrijf. Het restant (bedrijven buiten de top 20 + niet-gedekte ETF-holdings, samen ${overigPct.toFixed(1)}%) is hier niet in weergegeven.`;
    sectie.appendChild(dekkingTekst);

    const bronNamen = {};
    (data.bronnen || []).forEach(b => { bronNamen[b.ticker] = b.naam; });

    const categorieData = {};
    data.top.forEach(entry => { categorieData[entry.bedrijf] = entry.per_bron; });

    renderGestapeldeStaafgrafiek(categorieData, bronNamen);
}

// ETF-overlap-tabblad: eenvoudige HTML-matrix (geen Chart.js) met
// achtergrondkleur-intensiteit naar overlap% -- zie
// analysis.bereken_etf_overlap. Minder dan 2 aangehouden ETF's -> lege
// matrix van de backend, toon dan een duidelijke melding i.p.v. een tabel
// met 0 of 1 kolom.
function renderEtfOverlapTabel() {
    const sectie = document.getElementById("etfOverlapSectie");
    sectie.innerHTML = "";
    if (toonVerrijkingWachtstatusIndienNodig()) return;

    const matrix = huidigeData.etf_overlap || {};
    const etfs = Object.keys(matrix);

    if (etfs.length < 2) {
        const p = document.createElement("p");
        p.style.color = "#888";
        p.textContent = "Minimaal 2 aangehouden ETF's nodig om overlap te berekenen.";
        sectie.appendChild(p);
        return;
    }

    const tickerNamen = {};
    (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });

    const tabel = document.createElement("table");
    tabel.style.borderCollapse = "collapse";
    tabel.style.fontSize = "0.85em";

    const kopRij = document.createElement("tr");
    kopRij.appendChild(document.createElement("th"));
    etfs.forEach(ticker => {
        const th = document.createElement("th");
        th.textContent = tickerNamen[ticker] || ticker;
        th.title = tickerNamen[ticker] || ticker;
        th.style.padding = "4px 8px";
        th.style.textAlign = "center";
        th.style.whiteSpace = "nowrap";
        th.style.maxWidth = "140px";
        th.style.overflow = "hidden";
        th.style.textOverflow = "ellipsis";
        kopRij.appendChild(th);
    });
    tabel.appendChild(kopRij);

    etfs.forEach(rijTicker => {
        const tr = document.createElement("tr");
        const rijKop = document.createElement("th");
        rijKop.textContent = tickerNamen[rijTicker] || rijTicker;
        rijKop.title = tickerNamen[rijTicker] || rijTicker;
        rijKop.style.padding = "4px 8px";
        rijKop.style.textAlign = "left";
        rijKop.style.whiteSpace = "nowrap";
        rijKop.style.maxWidth = "140px";
        rijKop.style.overflow = "hidden";
        rijKop.style.textOverflow = "ellipsis";
        tr.appendChild(rijKop);

        etfs.forEach(kolTicker => {
            const td = document.createElement("td");
            td.style.padding = "4px 8px";
            td.style.textAlign = "center";
            td.style.border = "1px solid #eee";
            if (rijTicker === kolTicker) {
                td.textContent = "—";
                td.style.color = "#ccc";
            } else {
                const pct = matrix[rijTicker][kolTicker] * 100;
                td.textContent = `${pct.toFixed(0)}%`;
                td.style.backgroundColor = `rgba(44, 122, 75, ${Math.min(pct / 100, 1) * 0.7 + (pct > 0 ? 0.1 : 0)})`;
                td.style.color = pct > 50 ? "#fff" : "#333";
            }
            tr.appendChild(td);
        });
        tabel.appendChild(tr);
    });

    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";
    wrapper.appendChild(tabel);
    sectie.appendChild(wrapper);
}

function maakPrognoseInputVeld(id, labelTekst, waarde, opts) {
    opts = opts || {};
    const wrapper = document.createElement("div");
    wrapper.style.width = "170px";

    const label = document.createElement("label");
    label.setAttribute("for", id);
    label.textContent = labelTekst;
    label.style.marginTop = "0";
    label.style.fontSize = "0.85em";

    const input = document.createElement("input");
    input.type = "number";
    input.id = id;
    input.value = waarde;
    if (opts.step !== undefined) input.step = opts.step;
    if (opts.min !== undefined) input.min = opts.min;
    if (opts.max !== undefined) input.max = opts.max;

    wrapper.appendChild(label);
    wrapper.appendChild(input);
    return wrapper;
}

function leesPrognoseInvoer() {
    const getal = (id) => parseFloat(document.getElementById(id).value);
    prognoseInvoer = {
        jaren: getal("prognoseJaren"),
        rendement: getal("prognoseRendement"),
        laag: getal("prognoseLaag"),
        hoog: getal("prognoseHoog"),
        jaarlijks: getal("prognoseJaarlijks"),
        maandelijks: getal("prognoseMaandelijks")
    };
    return prognoseInvoer;
}

function renderPrognoseFormulier() {
    const sectie = document.getElementById("prognoseSectie");
    sectie.innerHTML = "";

    const uitleg = document.createElement("p");
    uitleg.style.color = "#666";
    uitleg.style.fontSize = "0.9em";
    uitleg.style.marginTop = "0";
    uitleg.textContent = "Projectie op basis van je huidige portfoliowaarde en -geschiedenis, aangevuld met instelbare aannames voor de toekomst. Rendement wordt maandelijks samengesteld; inleg wordt telkens ná de groei van die periode toegevoegd.";
    sectie.appendChild(uitleg);

    const rij = document.createElement("div");
    rij.style.display = "flex";
    rij.style.flexWrap = "wrap";
    rij.style.gap = "16px";
    rij.style.marginTop = "10px";

    rij.appendChild(maakPrognoseInputVeld("prognoseJaren", "Aantal jaren vooruit", prognoseInvoer.jaren, { min: 0, max: 100, step: 1 }));
    rij.appendChild(maakPrognoseInputVeld("prognoseRendement", "Verwacht rendement per jaar (%)", prognoseInvoer.rendement, { step: 0.1 }));
    rij.appendChild(maakPrognoseInputVeld("prognoseLaag", "Bandbreedte — laag (%)", prognoseInvoer.laag, { step: 0.1 }));
    rij.appendChild(maakPrognoseInputVeld("prognoseHoog", "Bandbreedte — hoog (%)", prognoseInvoer.hoog, { step: 0.1 }));
    rij.appendChild(maakPrognoseInputVeld("prognoseJaarlijks", "Jaarlijkse inleg (€)", prognoseInvoer.jaarlijks, { min: 0, step: 50 }));
    rij.appendChild(maakPrognoseInputVeld("prognoseMaandelijks", "Maandelijkse inleg (€)", prognoseInvoer.maandelijks, { min: 0, step: 10 }));
    sectie.appendChild(rij);

    const berekenBtn = document.createElement("button");
    berekenBtn.textContent = "Bereken";
    berekenBtn.style.marginTop = "14px";
    berekenBtn.onclick = berekenEnToonPrognose;
    sectie.appendChild(berekenBtn);

    const fout = document.createElement("p");
    fout.id = "prognoseFoutmelding";
    fout.style.display = "none";
    fout.style.color = "#9C0006";
    fout.style.fontSize = "0.9em";
    fout.style.marginTop = "10px";
    sectie.appendChild(fout);

    const waarschuwing = document.createElement("p");
    waarschuwing.id = "prognoseWaarschuwing";
    waarschuwing.style.display = "none";
    waarschuwing.style.color = "#a66a00";
    waarschuwing.style.fontSize = "0.9em";
    waarschuwing.style.marginTop = "10px";
    sectie.appendChild(waarschuwing);

    const legenda = document.createElement("p");
    legenda.style.color = "#888";
    legenda.style.fontSize = "0.8em";
    legenda.style.marginTop = "10px";
    legenda.textContent = "Doorgetrokken lijn = historische data. Gestippelde lijn + gearceerd gebied = prognose (aanname, geen garantie).";
    sectie.appendChild(legenda);
}

// bouwPrognoseGrafiekData zelf staat in static/js/prognose.js (puur, met
// chart_data als expliciete parameter i.p.v. de globale huidigeData) zodat
// hij via tests/test_prognose.js onder Node getest kan worden en nooit meer
// per ongeluk data van een eerder geladen portfolio kan hergebruiken — zie
// prognoseResultaat = null in toonDashboard() voor de andere helft van die fix.

// Los van updateChart() (die de category-as gebruikt voor de andere
// tabbladen, waar dat prima werkt omdat die series allemaal dezelfde
// (dagelijkse) puntdichtheid hebben): de Prognose-grafiek combineert
// dagelijkse historische punten met maandelijkse prognosepunten in één
// grafiek, en dat vereist een echte tijd-as (type: 'time') zodat elk punt
// op de plek staat die met zijn werkelijke datum overeenkomt i.p.v. een
// vaste breedte per punt te krijgen.
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
                zoom: {
                    pan: { enabled: true, mode: "x" },
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: "x"
                    }
                },
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
    renderPrognoseFormulier();
    if (prognoseResultaat) {
        tekenPrognoseChart(prognoseResultaat.datasets);
    } else {
        berekenEnToonPrognose();
    }
}

function wisselView(view) {
    // Korte fade i.p.v. een abrupte wissel: content eerst naar opacity 0
    // laten faden (CSS transition, .tabWisselt in style.css), pas ná die
    // 90ms de content daadwerkelijk wisselen en weer laten infaden. Een
    // ECHTE setTimeout-pauze (i.p.v. bv. dubbele requestAnimationFrame of
    // een geforceerde reflow via offsetHeight) bleek in de praktijk de
    // enige betrouwbare manier: browsers kunnen een class die binnen
    // hetzelfde renderframe wordt toegevoegd én weer verwijderd (zoals bij
    // de eerdere, snellere pogingen) samenvoegen tot "geen wijziging",
    // waardoor er nooit een zichtbare transitie start. Bij snel
    // achter-elkaar wisselen (nieuwe klik terwijl de fade-out van de
    // vorige nog loopt) slaan we die wachttijd over, anders voelt
    // navigeren traag aan.
    const content = document.querySelector(".content");
    if (content.classList.contains("tabWisselt")) {
        pasViewToe(view);
        return;
    }
    content.classList.add("tabWisselt");
    setTimeout(() => pasViewToe(view), 90);
}

function pasViewToe(view) {
    const content = document.querySelector(".content");
    const isInstellingenView = view === "instellingen" || view === "instellingen-bijnamen" || view === "instellingen-ticker";

    document.querySelectorAll(".menuBtn[data-view]").forEach(btn => {
        btn.classList.toggle("actief", btn.dataset.view === view);
    });
    document.getElementById("aandeelSelect").style.display = (view === "peraandeel" || view === "peraandeelaankoop") ? "block" : "none";
    // Benchmarkvergelijking is DB-backed (leest transacties via de code) --
    // niet beschikbaar bij een 'niet opslaan'-analyse, zelfde beperking als
    // Instellingen/Bijnamen/Dividend (zie toonDashboard()).
    document.getElementById("benchmarkSelectWrapper").style.display = (view === "rendement" && huidigeData.code) ? "block" : "none";
    document.getElementById("codeText").style.display = (view === "portfolio" && huidigeData.code) ? "block" : "none";
    document.getElementById("nietOpgeslagenText").style.display = (view === "portfolio" && !huidigeData.code) ? "block" : "none";
    document.getElementById("resetZoomBtn").style.display = (view === "verdeling" || view === "land" || view === "sector" || view === "bedrijven" || view === "etfoverlap" || view === "statistieken" || isInstellingenView || (view === "xirr-rendement" && !huidigeData.code)) ? "none" : "block";
    document.getElementById("chartWrapper").style.display = (isInstellingenView || view === "statistieken" || view === "etfoverlap") ? "none" : "block";
    document.getElementById("instellingenHoofdSectie").style.display = view === "instellingen" ? "block" : "none";
    document.getElementById("instellingenSectie").style.display = view === "instellingen-bijnamen" ? "block" : "none";
    document.getElementById("instellingenTickerSectie").style.display = view === "instellingen-ticker" ? "block" : "none";
    document.getElementById("dividendStatsSectie").style.display = view === "dividend" ? "block" : "none";
    document.getElementById("statistiekenSectie").style.display = view === "statistieken" ? "block" : "none";
    document.getElementById("bedrijvenSectie").style.display = view === "bedrijven" ? "block" : "none";
    document.getElementById("etfOverlapSectie").style.display = view === "etfoverlap" ? "block" : "none";
    document.getElementById("prognoseSectie").style.display = view === "prognose" ? "block" : "none";
    document.getElementById("homeTotalenSectie").style.display = view === "portfolio" ? "block" : "none";
    document.getElementById("laatstBijgewerktText").style.display = "none";
    document.getElementById("weergaveToggleBtn").style.display = (view === "land" || view === "sector") ? "block" : "none";

    if (view !== "instellingen-bijnamen") {
        document.getElementById("instellingenMsg").style.display = "none";
    }
    if (view !== "verdeling") {
        document.getElementById("verdelingTekst").style.display = "none";
    }
    if (view !== "land") {
        document.getElementById("landDekkingTekst").style.display = "none";
        document.getElementById("europaCheckboxWrapper").style.display = "none";
    }
    if (view !== "peraandeel") {
        document.getElementById("etfDrilldown").style.display = "none";
    }
    if (view !== "peraandeelaankoop") {
        document.getElementById("peraandeelAankoopTitel").style.display = "none";
        document.getElementById("meerHistorieKnoppen").style.display = "none";
    }
    if (view !== "rendement") {
        document.getElementById("benchmarkMelding").style.display = "none";
    }
    if (view !== "xirr-rendement") {
        document.getElementById("xirrRendementMsg").style.display = "none";
        document.getElementById("xirrStapToggleBtn").style.display = "none";
    }
    // toonVerdeling()/toonPlatteVerdeling() zetten dit bericht aan als er
    // voor Verdeling/Land/Sector/Bedrijven geen data is — zonder reset
    // bleef het staan bij het wisselen naar een compleet ander tabblad.
    if (view !== "verdeling" && view !== "land" && view !== "sector" && view !== "bedrijven") {
        document.getElementById("geenData").style.display = "none";
    }
    // Zelfde reden, voor de laad-/foutindicator van de lui opgehaalde
    // verrijking (zie laadVerrijking/toonVerrijkingWachtstatusIndienNodig)
    // -- nu ook voor etfoverlap, dat als enige van de 5 geen geenData
    // gebruikt (eigen "minimaal 2 ETF's"-melding in de sectie zelf).
    if (view !== "verdeling" && view !== "land" && view !== "sector" && view !== "bedrijven" && view !== "etfoverlap") {
        document.getElementById("verrijkingLaadt").style.display = "none";
        document.getElementById("verrijkingFout").style.display = "none";
    }

    if (view === "portfolio") toonPortfolio();
    else if (view === "rendement") toonRendement();
    else if (view === "verdeling") toonVerdeling();
    else if (view === "land") toonLand();
    else if (view === "sector") toonSector();
    else if (view === "instellingen-bijnamen") toonInstellingen();
    else if (view === "instellingen-ticker") toonInstellingenTicker();
    else if (view === "dividend") toonDividend();
    else if (view === "statistieken") toonStatistieken();
    else if (view === "bedrijven") toonBedrijven();
    else if (view === "etfoverlap") renderEtfOverlapTabel();
    else if (view === "xirr-rendement") toonRendementOverTijd();
    else if (view === "prognose") toonPrognose();
    else if (view === "peraandeel") {
        const select = document.getElementById("aandeelSelect");
        toonPerAandeel(select.value);
    }
    else if (view === "peraandeelaankoop") {
        const select = document.getElementById("aandeelSelect");
        toonPerAandeelAankoop(select.value);
    }

    content.classList.remove("tabWisselt");
}

function toonDashboard(data) {
    huidigeData = data;
    // Zonder reset bleef een eerder berekende prognose (van een andere
    // portfolio, of van vóór "Terug naar upload" + een nieuwe code) gewoon
    // staan: toonPrognose() hergebruikt prognoseResultaat zolang die niet
    // null is, en dat werd nooit bijgewerkt bij het wisselen van portfolio.
    prognoseResultaat = null;
    // Zelfde reden als prognoseResultaat hierboven: een benchmark-keuze van
    // een vorige portfolio slaat nergens meer op zodra de data wisselt.
    benchmarkVergelijkingData = null;
    // Zelfde reden: stap="dag" van een vorige portfolio moet niet blijven
    // hangen -- elke nieuwe portfolio start weer bij het lichte "maand".
    xirrRendementStap = "maand";
    // Zelfde reden: uitgegrijsde "meer historie laden"-knoppen van een
    // vorige portfolio slaan nergens meer op.
    meerHistorieUitgeput = {};
    document.getElementById("benchmarkSelect").value = "";
    document.getElementById("uploadSection").style.display = "none";
    document.getElementById("dashboardSection").style.display = "flex";
    document.getElementById("dashCode").textContent = data.code || "";

    const instellingenBtn = document.querySelector('.menuBtn[data-view="instellingen"]');
    const bijnamenBtn = document.querySelector('.menuBtn[data-view="instellingen-bijnamen"]');
    const dividendBtn = document.querySelector('.menuBtn[data-view="dividend"]');
    instellingenBtn.style.display = data.code ? "" : "none";
    bijnamenBtn.style.display = data.code ? "" : "none";
    // Dividend is DB-backed (bereken_dividend_samenvatting leest de
    // dividenden-tabel via de code) — een 'niet opslaan'-analyse heeft geen
    // code en dus nooit dividenddata, dus verberg de knop net als bij
    // Instellingen/Bijnamen.
    dividendBtn.style.display = data.code ? "" : "none";

    toonTickerWaarschuwingBanner(data.ticker_waarschuwingen || []);

    if (!data.chart_data) {
        document.getElementById("geenData").style.display = "block";
        return;
    }

    ververAandeelSelect();
    wisselView("portfolio");

    // Verdeling/Land/Sector/Bedrijven/ETF-overlap zitten sinds het gefaseerd-
    // laden-werk (CLAUDE.md) niet meer standaard in dit antwoord -- behalve
    // bij een 'niet opslaan'-analyse (geen code, dus geen latere /verrijking-
    // aanroep mogelijk), die analyze_transacties()'s wrapper gebruikt en ze
    // dus al meestuurt. Alleen lui ophalen als ze er nog niet zijn.
    if (data.verdeling !== undefined) {
        verrijkingStatus = "klaar";
    } else if (data.code) {
        laadVerrijking(data.code);
    } else {
        verrijkingStatus = "klaar";
    }
}

function actieveViewNaam() {
    const knop = document.querySelector(".menuBtn[data-view].actief");
    return knop ? knop.dataset.view : null;
}

// Tekent alleen opnieuw als de gebruiker toevallig al op een van de 5
// verrijkings-tabbladen staat -- anders is er niets zichtbaars om bij te
// werken, dat gebeurt vanzelf zodra de gebruiker ernaartoe navigeert (zie
// wisselView, dat toonVerdeling()/toonLand()/etc. bij elke tabwissel aanroept).
function herTekenVerrijkingTabbladIndienActief() {
    const view = actieveViewNaam();
    if (view === "verdeling") toonVerdeling();
    else if (view === "land") toonLand();
    else if (view === "sector") toonSector();
    else if (view === "bedrijven") toonBedrijven();
    else if (view === "etfoverlap") renderEtfOverlapTabel();
}

// Toont/verbergt de laad-/foutindicator voor de 5 verrijkings-tabbladen,
// gedeeld door elk van hun toon-functies (zie hieronder). Geeft true terug
// als de aanroeper meteen mag stoppen (nog aan het laden, of mislukt) --
// dan is er niets zinnigs te tekenen.
function toonVerrijkingWachtstatusIndienNodig() {
    const laadt = document.getElementById("verrijkingLaadt");
    const fout = document.getElementById("verrijkingFout");
    laadt.style.display = verrijkingStatus === "laden" ? "block" : "none";
    fout.style.display = verrijkingStatus === "fout" ? "block" : "none";
    return verrijkingStatus === "laden" || verrijkingStatus === "fout";
}

async function laadVerrijking(code) {
    verrijkingStatus = "laden";
    herTekenVerrijkingTabbladIndienActief();
    try {
        // Bewust geen expliciete timeoutMs -- de default (55s) van
        // fetchMetTimeout is prima hier, dit endpoint is per definitie het
        // netwerk-zware deel (zie CLAUDE.md / opdracht_gefaseerd_laden.md).
        const res = await fetchMetTimeout(`/api/portfolio/${code}/verrijking`);
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Verrijking ophalen mislukt.");
        }
        Object.assign(huidigeData, data);
        verrijkingStatus = "klaar";
    } catch (err) {
        console.error("[verrijking] ophalen mislukt:", err.message);
        verrijkingStatus = "fout";
    }
    herTekenVerrijkingTabbladIndienActief();
}

document.getElementById("verrijkingOpnieuwBtn").addEventListener("click", () => {
    if (huidigeData && huidigeData.code) {
        laadVerrijking(huidigeData.code);
    }
});

// Opvallende, niet-blokkerende banner (blijft zichtbaar ongeacht welk
// tabblad open staat) wanneer find_ticker_met_snelle_prijscheck (de
// standaard lichte prijscontrole, zie CLAUDE.md) bij 1 of meer posities een
// afwijkende koers vond. Wijst door naar Ticker-zekerheid i.p.v. zelf een
// alternatieve ticker te tonen/kiezen — dat blijft altijd een suggestie die
// de gebruiker daar zelf bevestigt.
function toonTickerWaarschuwingBanner(waarschuwingen) {
    const banner = document.getElementById("tickerWaarschuwingBanner");
    if (!waarschuwingen || waarschuwingen.length === 0) {
        banner.style.display = "none";
        return;
    }

    const namen = waarschuwingen.map(w => w.naam || w.ticker).join(", ");
    const tekst = waarschuwingen.length === 1
        ? `⚠️ Bij 1 positie (${namen}) wijkt de koers meer dan verwacht af van Yahoo Finance — controleer het Ticker-zekerheid-tabblad.`
        : `⚠️ Bij ${waarschuwingen.length} posities (${namen}) wijkt de koers meer dan verwacht af van Yahoo Finance — controleer het Ticker-zekerheid-tabblad.`;
    document.getElementById("tickerWaarschuwingTekst").textContent = tekst;
    banner.style.display = "block";
}

// Hamburger-menu (alleen zichtbaar op mobiel, zie style.css): open/dicht-
// status wordt berekend door static/js/menu.js (puur, apart getest), deze
// functie past dat resultaat toe op de DOM.
let menuOpen = false;
const hamburgerBtn = document.getElementById("hamburgerBtn");
const sidebarMenu = document.getElementById("sidebarMenu");
const menuOverlay = document.getElementById("menuOverlay");

function pasMenuStatusToe(open) {
    menuOpen = open;
    sidebarMenu.classList.toggle("open", open);
    menuOverlay.classList.toggle("open", open);
    hamburgerBtn.setAttribute("aria-expanded", String(open));
    hamburgerBtn.innerHTML = open ? "&times;" : "&#9776;";
}

hamburgerBtn.addEventListener("click", () => pasMenuStatusToe(volgendeMenuOpenStatus(menuOpen)));
menuOverlay.addEventListener("click", () => pasMenuStatusToe(false));
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menuOpen) pasMenuStatusToe(false);
});

document.querySelectorAll(".menuBtn[data-view]").forEach(btn => {
    btn.addEventListener("click", () => {
        wisselView(btn.dataset.view);
        pasMenuStatusToe(menuOpenStatusNaViewKeuze());
    });
});

document.getElementById("aandeelSelect").addEventListener("change", (e) => {
    const actieveKnop = document.querySelector(".menuBtn[data-view].actief");
    if (actieveKnop && actieveKnop.dataset.view === "peraandeelaankoop") {
        toonPerAandeelAankoop(e.target.value);
    } else {
        toonPerAandeel(e.target.value);
    }
});

document.getElementById("benchmarkSelect").addEventListener("change", (e) => {
    wisselBenchmark(e.target.value);
});

document.getElementById("resetZoomBtn").addEventListener("click", () => {
    if (chart) chart.resetZoom();
});

// Toggle tussen stap="maand" (standaard, licht) en stap="dag" (preciezer
// maar herberekent XIRR per dag i.p.v. per maand, dus trager) op het
// "XIRR & rendement"-tabblad -- zie xirrRendementStap/toonRendementOverTijd().
document.getElementById("xirrStapToggleBtn").addEventListener("click", () => {
    xirrRendementStap = xirrRendementStap === "dag" ? "maand" : "dag";
    toonRendementOverTijd();
});

document.getElementById("europaCheckbox").addEventListener("change", () => {
    toonLand();
});

// Toggle tussen taart (toonPlatteVerdeling) en gestapelde staaf per bron
// (renderGestapeldeStaafgrafiek) op het Land- en Sector-tabblad. Gedeelde
// state (landSectorWeergave) i.p.v. per-tabblad, dus de keuze blijft staan
// bij het wisselen tussen Land en Sector. Data staat al in
// huidigeData.land_sector_verdeling (land_per_bron/sector_per_bron) --
// geen nieuwe serveraanroep nodig.
document.getElementById("weergaveToggleBtn").addEventListener("click", () => {
    landSectorWeergave = landSectorWeergave === "taart" ? "staaf" : "taart";
    const actieveKnop = document.querySelector(".menuBtn[data-view].actief");
    const actieveView = actieveKnop ? actieveKnop.dataset.view : null;
    if (actieveView === "land") toonLand();
    else if (actieveView === "sector") toonSector();
});

document.getElementById("tickerWaarschuwingKnop").addEventListener("click", () => {
    wisselView("instellingen-ticker");
    pasMenuStatusToe(menuOpenStatusNaViewKeuze());
});

document.getElementById("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    document.getElementById("errorMsg").textContent = "";
    const formData = new FormData(e.target);
    // FormData(form) neemt bestand2 altijd mee, ook als er niets is
    // geselecteerd (dan als lege file-entry) — expliciet verwijderen zodat
    // een niet-ingevuld (optioneel) rekeningoverzicht niet als "leeg bestand"
    // bij Flask binnenkomt.
    const bestand2Input = document.getElementById("bestand2");
    if (!bestand2Input.files || bestand2Input.files.length === 0) {
        formData.delete("bestand2");
    }
    toonLaadOverlay("Analyseren...");
    try {
        // fetchMetTimeout breekt zelf af als de server te lang stil blijft
        // (zie CLAUDE.md, Statistieken-incident 2026-08-31) -- 60s, iets
        // ruimer dan de standaard 55s-default omdat een upload (Excel
        // parsen + ticker-resolutie) doorgaans iets meer tijd nodig heeft
        // dan een lui geladen tabblad.
        const res = await fetchMetTimeout("/upload", { method: "POST", body: formData }, 60000);
        const data = await res.json();
        if (!res.ok) {
            document.getElementById("errorMsg").textContent = data.error || "Er ging iets mis.";
            return;
        }
        toonDashboard(data);
    } catch (err) {
        document.getElementById("errorMsg").textContent = err.message === "TIMEOUT"
            ? "Het ophalen duurde te lang en is gestopt. Dit gebeurt soms bij een nieuwe portfolio — druk gerust "
              + "nog 1 of 2 keer op de upload-knop, dat lukt meestal wél (de koersen die al opgehaald zijn, staan "
              + "dan al in de cache, dus de volgende poging is sneller)."
            : "Er ging iets mis bij het analyseren (netwerkfout). Probeer het opnieuw.";
    } finally {
        verbergLaadOverlay();
    }
});

document.getElementById("codeForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = document.getElementById("codeInput").value.trim().toUpperCase();
    if (!code) return;
    document.getElementById("errorMsg").textContent = "";
    toonLaadOverlay("Ophalen...");
    try {
        const res = await fetch(`/api/portfolio/${code}`);
        const data = await res.json();
        if (!res.ok) {
            document.getElementById("errorMsg").textContent = data.error || "Code niet gevonden.";
            return;
        }
        toonDashboard(data);
    } finally {
        verbergLaadOverlay();
    }
});

function gaTerugNaarUpload() {
    document.getElementById("dashboardSection").style.display = "none";
    document.getElementById("uploadSection").style.display = "block";
}

document.getElementById("terugKnop").addEventListener("click", gaTerugNaarUpload);

document.getElementById("verwijderPortfolioBtn").addEventListener("click", async () => {
    if (!huidigeData || !huidigeData.code) return;
    const zeker = confirm(`Weet je zeker dat je portfolio ${huidigeData.code} permanent wilt verwijderen? Dit kan niet ongedaan worden gemaakt.`);
    if (!zeker) return;

    toonLaadOverlay("Verwijderen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}`, { method: "DELETE" });
        if (res.ok) {
            huidigeData = null;
            document.getElementById("dashboardSection").style.display = "none";
            document.getElementById("uploadSection").style.display = "block";
            alert("Portfolio verwijderd.");
        } else {
            alert("Verwijderen is niet gelukt, probeer het later opnieuw.");
        }
    } finally {
        verbergLaadOverlay();
    }
});

document.getElementById("wijzigCodeBtn").addEventListener("click", async () => {
    if (!huidigeData || !huidigeData.code) return;
    const input = document.getElementById("nieuweCodeInput");
    const nieuweCode = input.value.trim().toUpperCase();
    if (!nieuweCode) return;

    const msg = document.getElementById("instellingenMsg");
    toonLaadOverlay("Aanpassen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/wijzig-code`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nieuwe_code: nieuweCode })
        });
        const data = await res.json();
        if (!res.ok) {
            msg.style.color = "#9C0006";
            msg.textContent = data.error || "Code wijzigen mislukt.";
            msg.style.display = "block";
            return;
        }
        // huidigeData bevat de code waarmee alle andere tabbladen (Statistieken,
        // Dividend, Ticker-zekerheid) hun API-calls doen -- meteen bijwerken zodat
        // de rest van de sessie de nieuwe code gebruikt zonder herladen.
        // Object.assign i.p.v. overschrijven: build_portfolio_response() geeft
        // sinds het gefaseerd-laden-werk alleen de kern terug (zie CLAUDE.md),
        // dus de al opgehaalde verrijkingsvelden moeten bewaard blijven; de
        // verse laadVerrijking() hieronder haalt ze daarna alsnog opnieuw op
        // onder de nieuwe code (de oude code bestaat straks niet meer).
        Object.assign(huidigeData, data);
        laadVerrijking(huidigeData.code);
        document.getElementById("dashCode").textContent = data.code || "";
        input.value = "";
        msg.style.color = "#2c7a4b";
        msg.textContent = `Code gewijzigd naar ${data.code} — bewaar deze om later terug te komen.`;
        msg.style.display = "block";
    } finally {
        verbergLaadOverlay();
    }
});