// Tabblad Rendement: rendement over tijd, optioneel naast een benchmark of een eigen aandeel.

let benchmarkVergelijkingData = null;
// Los van de benchmark: beide vergelijkingslijnen kunnen tegelijk zichtbaar zijn.
let eigenAandeelVergelijkingData = null;

function resetRendement() {
    benchmarkVergelijkingData = null;
    eigenAandeelVergelijkingData = null;
    document.getElementById("benchmarkSelect").value = "";
    document.getElementById("eigenAandeelSelect").innerHTML = '<option value="">Geen</option>';
}

function toonRendement() {
    const d = huidigeData.chart_data;
    const datasets = [{ label: "Rendement (€)", data: d.rendement, borderColor: "#2c7a4b" }];

    const meldingEl = document.getElementById("benchmarkMelding");
    meldingEl.style.display = "none";

    if (benchmarkVergelijkingData) {
        const b = benchmarkVergelijkingData;
        // b.labels is een slotstuk van d.labels: links opvullen met null.
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

    const eigenMeldingEl = document.getElementById("eigenAandeelMelding");
    eigenMeldingEl.style.display = "none";

    if (eigenAandeelVergelijkingData) {
        const e = eigenAandeelVergelijkingData;
        const offset = d.labels.length - e.labels.length;
        const reeks = offset > 0 ? Array(offset).fill(null).concat(e.rendement) : e.rendement;
        datasets.push({
            label: `Rendement ${e.naam} (hypothetisch, €)`,
            data: reeks,
            borderColor: "#8856a7",
            borderDash: [2, 2],
        });
        if (e.onvolledige_dekking) {
            eigenMeldingEl.textContent = `${e.naam} heeft pas koersdata vanaf ${formatDatum(e.vanaf_datum)} — inleg van vóór die datum telt niet mee in deze vergelijking.`;
            eigenMeldingEl.style.display = "block";
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

// Zoals wisselBenchmark(), met een eigen dataset zodat beide tegelijk zichtbaar zijn.
async function wisselEigenAandeel(ticker) {
    if (!ticker) {
        eigenAandeelVergelijkingData = null;
        toonRendement();
        return;
    }
    toonLaadOverlay("Vergelijking ophalen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/benchmark-vergelijking?eigen_ticker=${encodeURIComponent(ticker)}`);
        const data = await res.json();
        if (!res.ok) {
            eigenAandeelVergelijkingData = null;
            alert(data.error || "Vergelijking kon niet berekend worden.");
        } else {
            const tickerInfo = (huidigeData.tickers || []).find(t => t.ticker === ticker);
            eigenAandeelVergelijkingData = { naam: tickerInfo ? tickerInfo.naam : ticker, ...data };
        }
    } catch (e) {
        eigenAandeelVergelijkingData = null;
        alert("Vergelijking ophalen is mislukt.");
    } finally {
        verbergLaadOverlay();
        toonRendement();
    }
}

// Zelfde bron en volgorde als ververAandeelSelect(), met "Geen" als eerste optie.
function ververEigenAandeelSelect() {
    const select = document.getElementById("eigenAandeelSelect");
    const huidigeKeuze = select.value;
    select.innerHTML = '<option value="">Geen</option>';
    huidigeData.tickers.forEach(t => {
        const option = document.createElement("option");
        option.value = t.ticker;
        option.textContent = t.naam;
        select.appendChild(option);
    });
    if (huidigeData.tickers.some(t => t.ticker === huidigeKeuze)) {
        select.value = huidigeKeuze;
    }
}

document.getElementById("benchmarkSelect").addEventListener("change", (e) => {
    wisselBenchmark(e.target.value);
});

document.getElementById("eigenAandeelSelect").addEventListener("change", (e) => {
    wisselEigenAandeel(e.target.value);
});
