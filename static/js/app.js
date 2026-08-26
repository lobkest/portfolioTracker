let chart = null;
let huidigeData = null;

function formatDatum(isoDatum) {
    const [jaar, maand, dag] = isoDatum.split("-");
    return `${dag}-${maand}-${jaar}`;
}

function kleurVoorIndex(isEtf, i) {
    const blauw = ["#08519c", "#3182bd", "#6baed6", "#9ecae1", "#c6dbef", "#deebf7"];
    const rood = ["#a50f15", "#cb181d", "#fb6a4a", "#fc9272", "#fcae91", "#fee5d9"];
    const palet = isEtf ? blauw : rood;
    return palet[i % palet.length];
}

function updateChart(labels, datasets) {
    if (chart) chart.destroy();
    const labelsNL = labels.map(formatDatum);
    datasets = datasets.map(ds => ({ pointRadius: 0, pointHoverRadius: 4, borderWidth: 1.5, ...ds }));

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "line",
        data: { labels: labelsNL, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { beginAtZero: false } },
            plugins: {
                zoom: {
                    pan: { enabled: true, mode: "x" },
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: "x"
                    }
                }
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
}

function toonRendement() {
    const d = huidigeData.chart_data;
    updateChart(d.labels, [
        { label: "Rendement (€)", data: d.rendement, borderColor: "#2c7a4b" }
    ]);
}

function toonPerAandeel(ticker) {
    const d = huidigeData.per_ticker[ticker];
    updateChart(d.labels, [
        { label: "Waarde (€)", data: d.waarde, borderColor: "#2c7a4b" },
        { label: "Geïnvesteerd (€)", data: d.geinvesteerd, borderColor: "#3182bd" }
    ]);
}

function toonVerdeling() {
    const items = huidigeData.verdeling;
    if (chart) chart.destroy();

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
    tekst.textContent = `ETF's: ${etfPct}% — Aandelen: ${aandeelPct}%`;

    let etfIdx = 0, stockIdx = 0;
    const kleuren = items.map(i => kleurVoorIndex(i.is_etf, i.is_etf ? etfIdx++ : stockIdx++));

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "pie",
        data: {
            labels: items.map(i => i.naam),
            datasets: [{ data: items.map(i => i.waarde), backgroundColor: kleuren }]
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
                            return `${ctx.label}: €${ctx.parsed.toFixed(2)} (${pct}%)`;
                        }
                    }
                }
            }
        }
    });
}

async function slaBijnaamOp(ticker, bijnaam) {
    const res = await fetch(`/api/portfolio/${huidigeData.code}/bijnaam`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker, bijnaam })
    });
    const data = await res.json();
    if (res.ok) { huidigeData = data; toonInstellingen(); }
}

async function resetBijnaam(ticker) {
    const res = await fetch(`/api/portfolio/${huidigeData.code}/reset-bijnaam`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker })
    });
    const data = await res.json();
    if (res.ok) { huidigeData = data; toonInstellingen(); }
}

function toonInstellingen() {
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

function wisselView(view) {
    document.querySelectorAll(".menuBtn[data-view]").forEach(btn => {
        btn.classList.toggle("actief", btn.dataset.view === view);
    });
    document.getElementById("aandeelSelect").style.display = view === "peraandeel" ? "block" : "none";
    document.getElementById("codeText").style.display = view === "portfolio" ? "block" : "none";
    document.getElementById("resetZoomBtn").style.display = (view === "verdeling" || view === "instellingen") ? "none" : "block";
    document.getElementById("chartWrapper").style.display = view === "instellingen" ? "none" : "block";
    document.getElementById("instellingenSectie").style.display = view === "instellingen" ? "block" : "none";

    if (view !== "verdeling") {
        document.getElementById("verdelingTekst").style.display = "none";
    }

    if (view === "portfolio") toonPortfolio();
    else if (view === "rendement") toonRendement();
    else if (view === "verdeling") toonVerdeling();
    else if (view === "instellingen") toonInstellingen();
    else if (view === "peraandeel") {
        const select = document.getElementById("aandeelSelect");
        toonPerAandeel(select.value);
    }
}

function toonDashboard(data) {
    huidigeData = data;
    document.getElementById("uploadSection").style.display = "none";
    document.getElementById("dashboardSection").style.display = "flex";
    document.getElementById("dashCode").textContent = data.code;

    if (!data.chart_data) {
        document.getElementById("geenData").style.display = "block";
        return;
    }

    const select = document.getElementById("aandeelSelect");
    select.innerHTML = "";
    data.tickers.forEach(t => {
        const option = document.createElement("option");
        option.value = t.ticker;
        option.textContent = t.naam;
        select.appendChild(option);
    });

    wisselView("portfolio");
}

document.querySelectorAll(".menuBtn[data-view]").forEach(btn => {
    btn.addEventListener("click", () => wisselView(btn.dataset.view));
});

document.getElementById("aandeelSelect").addEventListener("change", (e) => {
    toonPerAandeel(e.target.value);
});

document.getElementById("resetZoomBtn").addEventListener("click", () => {
    if (chart) chart.resetZoom();
});

document.getElementById("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    document.getElementById("errorMsg").textContent = "Bezig met verwerken...";
    const formData = new FormData(e.target);
    const res = await fetch("/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
        document.getElementById("errorMsg").textContent = data.error || "Er ging iets mis.";
        return;
    }
    document.getElementById("errorMsg").textContent = "";
    toonDashboard(data);
});

// document.getElementById("codeForm").addEventListener("submit", async (e) => {
//     e.preventDefault();
//     const code = document.getElementById("codeInput").value.trim();
document.getElementById("codeForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = document.getElementById("codeInput").value.trim().toUpperCase();
    if (!code) return;
    const res = await fetch(`/api/portfolio/${code}`);
    const data = await res.json();
    if (!res.ok) {
        document.getElementById("errorMsg").textContent = data.error || "Code niet gevonden.";
        return;
    }
    document.getElementById("errorMsg").textContent = "";
    toonDashboard(data);
});

document.getElementById("terugKnop").addEventListener("click", () => {
    document.getElementById("dashboardSection").style.display = "none";
    document.getElementById("uploadSection").style.display = "block";
});