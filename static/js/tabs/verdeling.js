// Tabblad Verdeling: taart van de posities, ETF's met een streeppatroon.

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

    // De verhouding komt uit de backend; het totaal is alleen voor de %-labels.
    const samenvatting = huidigeData.verdeling_samenvatting;
    const totaal = samenvatting.totaal;

    const tekst = document.getElementById("verdelingTekst");
    tekst.style.display = "block";
    tekst.textContent = `ETF's (streeppatroon): ${samenvatting.etf_pct.toFixed(1)}% — Aandelen: ${samenvatting.aandeel_pct.toFixed(1)}%`;

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
                        return [kortNaam(items[ctx.dataIndex].naam), `${pct.toFixed(1)}%`];
                    }
                }
            }
        }
    });
}
