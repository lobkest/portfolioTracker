// Tabbladen Land, Sector, Valuta en Beurs: taart of gestapelde staaf, met een gedeelde weergavekeuze.

// "taart" of "staaf", gedeeld tussen Land, Sector, Valuta en Beurs.
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
                legend: { position: legendaPositie() },
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

    // Verklaart een deel van "Unknown", en waar het land via een proxy-ETF benaderd is.
    const dekkingTekst = document.getElementById("landDekkingTekst");
    const tickerNamen = {};
    (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
    const regels = landDekkingRegels(lsv && lsv.per_etf, tickerNamen);
    dekkingTekst.replaceChildren(...regels.flatMap((regel, i) => (i > 0 ? [document.createElement("br"), regel] : [regel])));
    dekkingTekst.style.display = regels.length > 0 ? "block" : "none";
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

function toonBeurs() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    const tekst = document.getElementById("beursTekst");
    tekst.style.display = "none";
    if (toonVerrijkingWachtstatusIndienNodig()) {
        document.getElementById("euronextCheckboxWrapper").style.display = "none";
        return;
    }

    const bv = huidigeData.beurs_verdeling;
    document.getElementById("euronextCheckboxWrapper").style.display = "flex";
    const samen = document.getElementById("euronextCheckbox").checked;
    const verdeling = bv && (samen ? bv.beurs_euronext : bv.beurs);

    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values(verdeling || {}).reduce((s, w) => s + w, 0);
        const perBron = bv && (samen ? bv.beurs_euronext_per_bron : bv.beurs_per_bron);
        chart = renderGestapeldeStaafgrafiek("rendementChart", perBron, tickerNamen, { totaal });
    } else {
        toonPlatteVerdeling(verdeling);
    }

    const aantal = bv ? (samen ? bv.aantal_beurzen_euronext : bv.aantal_beurzen) : 0;
    if (aantal > 0) {
        tekst.textContent = `Je posities staan op ${aantal} ${aantal === 1 ? "beurs" : "beurzen"}.`;
        tekst.style.display = "block";
    }
}

document.getElementById("europaCheckbox").addEventListener("change", () => {
    toonLand();
});

document.getElementById("euronextCheckbox").addEventListener("change", () => {
    toonBeurs();
});

// De keuze geldt voor Land, Sector, Valuta én Beurs; de data staat al in huidigeData.
document.getElementById("weergaveToggleBtn").addEventListener("click", () => {
    landSectorWeergave = landSectorWeergave === "taart" ? "staaf" : "taart";
    const view = actieveViewNaam();
    if (view === "land") toonLand();
    else if (view === "sector") toonSector();
    else if (view === "valuta") toonValuta();
    else if (view === "beurs") toonBeurs();
});
