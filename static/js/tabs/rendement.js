// Tabblad Rendement: in € optioneel naast een benchmark of eigen aandeel, in % met XIRR en TWR (alleen met code).

let benchmarkVergelijkingData = null;
// Los van de benchmark: beide vergelijkingslijnen kunnen tegelijk zichtbaar zijn.
let eigenAandeelVergelijkingData = null;
let rendementWeergave = "euro";

function resetRendement() {
    rendementWeergave = "euro";
    benchmarkVergelijkingData = null;
    eigenAandeelVergelijkingData = null;
    document.getElementById("benchmarkSelect").value = "";
    document.getElementById("eigenAandeelSelect").innerHTML = '<option value="">Geen</option>';
}

function toonRendement() {
    const procent = Boolean(huidigeData.code) && rendementWeergave === "pct";
    document.querySelectorAll("#rendementWeergaveWrapper .segmentKnop").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.weergave === rendementWeergave);
    });
    ["benchmarkSelectWrapper", "eigenAandeelSelectWrapper"].forEach(id => {
        if (huidigeData.code) document.getElementById(id).style.display = procent ? "none" : "block";
    });
    if (procent) {
        ["benchmarkMelding", "eigenAandeelMelding"].forEach(id => { document.getElementById(id).style.display = "none"; });
        toonRendementOverTijd();
    } else {
        document.getElementById("xirrRendementMsg").style.display = "none";
        toonRendementEuro();
    }
}

function toonRendementEuro() {
    markeerVergelijkKnoppen();
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
        toonRendementEuro();
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
        toonRendementEuro();
    }
}

// Zoals wisselBenchmark(), met een eigen dataset zodat beide tegelijk zichtbaar zijn.
async function wisselEigenAandeel(ticker) {
    if (!ticker) {
        eigenAandeelVergelijkingData = null;
        toonRendementEuro();
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
        toonRendementEuro();
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
    bouwVergelijkKnoppen();
}

// Website: knoppen i.p.v. de dropdowns; de select blijft de bron van de keuze (CSS verbergt de een of de ander).
// Nogmaals klikken op de actieve knop zet de keuze terug op "Geen".
function bouwVergelijkKnoppen() {
    document.querySelectorAll("[data-vergelijk-knoppen]").forEach(houder => {
        const select = document.getElementById(houder.dataset.vergelijkKnoppen);
        const knoppen = Array.from(select.options).filter(optie => optie.value !== "").map(optie => {
            const knop = document.createElement("button");
            knop.type = "button";
            knop.className = `keuzeKnop vergelijkKnop ${houder.dataset.kleur}`;
            knop.dataset.waarde = optie.value;
            knop.textContent = optie.textContent;
            knop.addEventListener("click", () => {
                select.value = select.value === optie.value ? "" : optie.value;
                select.dispatchEvent(new Event("change"));
            });
            return knop;
        });
        houder.replaceChildren(...knoppen);
    });
    markeerVergelijkKnoppen();
}

function markeerVergelijkKnoppen() {
    document.querySelectorAll("[data-vergelijk-knoppen]").forEach(houder => {
        const gekozen = document.getElementById(houder.dataset.vergelijkKnoppen).value;
        houder.querySelectorAll(".vergelijkKnop").forEach(knop => {
            knop.classList.toggle("actief", knop.dataset.waarde === gekozen);
        });
    });
}

bouwVergelijkKnoppen();

document.getElementById("benchmarkSelect").addEventListener("change", (e) => {
    wisselBenchmark(e.target.value);
});

document.getElementById("eigenAandeelSelect").addEventListener("change", (e) => {
    wisselEigenAandeel(e.target.value);
});

document.querySelectorAll("#rendementWeergaveWrapper .segmentKnop").forEach(knop => {
    knop.addEventListener("click", () => {
        rendementWeergave = knop.dataset.weergave;
        toonRendement();
    });
});

// Geen cache: elke keer opnieuw ophalen (licht, geen Yahoo-calls).
async function toonRendementOverTijd() {
    const msg = document.getElementById("xirrRendementMsg");
    msg.style.display = "none";
    msg.classList.remove("foutTekst");

    toonLaadOverlay("Rendement over tijd berekenen...");
    let res, data;
    try {
        res = await fetch(`/api/portfolio/${huidigeData.code}/rendement-over-tijd`);
        data = await res.json();
    } catch (e) {
        msg.classList.add("foutTekst");
        msg.textContent = "Kon rendement-over-tijd niet ophalen (netwerkfout).";
        msg.style.display = "block";
        return;
    } finally {
        verbergLaadOverlay();
    }

    if (rendementWeergave !== "pct") return;
    if (!res.ok) {
        msg.classList.add("foutTekst");
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
