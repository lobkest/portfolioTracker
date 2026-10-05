// Tabbladen Land, Sector en Valuta: taart of gestapelde staaf, met een gedeelde weergavekeuze.

// "taart" of "staaf", gedeeld tussen Land, Sector en Valuta.
let landSectorWeergave = "taart";

function toonPlatteVerdeling(verdelingObj) {
    if (chart) chart.destroy();

    const entries = Object.entries(verdelingObj || {}).filter(([, bedrag]) => bedrag > 0);
    if (entries.length === 0) {
        document.getElementById("geenData").style.display = "block";
        return;
    }
    document.getElementById("geenData").style.display = "none";

    // "Overig" en daarna "Unknown" altijd onderaan de legenda.
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
        // Top 10 + "Overig" komt al uit de backend (bewust anders dan de taart).
        const perBron = europaCheckbox.checked ? (lsv && lsv.land_per_bron_europa_top) : (lsv && lsv.land_per_bron_top);
        chart = renderGestapeldeStaafgrafiek("rendementChart", perBron, tickerNamen, { totaal });
    } else {
        // land_europa komt al uit de backend; de toggle kost geen request.
        const bron = europaCheckbox.checked ? (lsv && lsv.land_europa) : (lsv && lsv.land);
        toonPlatteVerdeling(bron);
    }

    // ETF's met alleen top-10-landdekking: verklaart een deel van "Unknown".
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
        chart = renderGestapeldeStaafgrafiek("rendementChart", lsv && lsv.sector_per_bron, tickerNamen, { totaal });
    } else {
        toonPlatteVerdeling(lsv && lsv.sector);
    }
}

function toonValuta() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    if (toonVerrijkingWachtstatusIndienNodig()) return;

    const vv = huidigeData.valuta_verdeling;
    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values((vv && vv.valuta) || {}).reduce((s, w) => s + w, 0);
        chart = renderGestapeldeStaafgrafiek("rendementChart", vv && vv.valuta_per_bron, tickerNamen, { totaal });
    } else {
        toonPlatteVerdeling(vv && vv.valuta);
    }
}

document.getElementById("europaCheckbox").addEventListener("change", () => {
    toonLand();
});

// De keuze geldt voor Land, Sector én Valuta; de data staat al in huidigeData.
document.getElementById("weergaveToggleBtn").addEventListener("click", () => {
    landSectorWeergave = landSectorWeergave === "taart" ? "staaf" : "taart";
    const actieveKnop = document.querySelector(".menuBtn[data-view].actief");
    const actieveView = actieveKnop ? actieveKnop.dataset.view : null;
    if (actieveView === "land") toonLand();
    else if (actieveView === "sector") toonSector();
    else if (actieveView === "valuta") toonValuta();
});
