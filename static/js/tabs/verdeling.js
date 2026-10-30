// Tabblad Verdeling: as Fonds = taart van de posities, ETF's met een streeppatroon; de uitleg ETF/Aandeel staat in HTML onder de grafiek.
// De andere assen (Land, Sector, Valuta, Beurs) staan in tabs/land_sector.js; "Over tijd": zie tabs/verdeling_over_tijd.js.

// Een voorkeur per browser, dus niet in RESET_PER_TAB.
let verdelingAs = leesOpgeslagenAs(lokaleOpslag());

function toonVerdelingMetAs() {
    ververVerdelingAsKnoppen();
    const toon = { fonds: toonVerdeling, land: toonLand, sector: toonSector, valuta: toonValuta, beurs: toonBeurs }[verdelingAs];
    toon();
}

function ververVerdelingAsKnoppen() {
    document.querySelectorAll("#verdelingAsKeuze .keuzeKnop").forEach(knop => {
        const actief = knop.dataset.as === verdelingAs;
        knop.classList.toggle("actief", actief);
        knop.setAttribute("aria-pressed", String(actief));
    });
}

function kiesVerdelingAs(as) {
    verdelingAs = geldigeAs(as);
    bewaarAs(lokaleOpslag(), verdelingAs);
    pasViewToe("verdeling");
}

VERDELING_ASSEN.forEach(({ as, label }) => {
    const knop = document.createElement("button");
    knop.type = "button";
    knop.className = "keuzeKnop";
    knop.dataset.as = as;
    knop.textContent = label;
    knop.addEventListener("click", () => kiesVerdelingAs(as));
    document.getElementById("verdelingAsKeuze").appendChild(knop);
});

function toonVerdeling() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    if (toonOverTijdIndienGekozen("positie")) {
        ["verdelingTekst", "verdelingUitleg"].forEach(id => { document.getElementById(id).style.display = "none"; });
        return;
    }
    if (toonVerrijkingWachtstatusIndienNodig()) return;

    const items = huidigeData.verdeling;
    if (!items || items.length === 0) {
        document.getElementById("geenData").style.display = "block";
        document.getElementById("verdelingTekst").style.display = "none";
        document.getElementById("verdelingUitleg").style.display = "none";
        return;
    }
    document.getElementById("geenData").style.display = "none";

    // De verhouding komt uit de backend; het totaal is alleen voor de %-labels.
    const samenvatting = huidigeData.verdeling_samenvatting;
    const totaal = samenvatting.totaal;

    const tekst = document.getElementById("verdelingTekst");
    tekst.style.display = "block";
    tekst.textContent = `ETF's: ${formatPct(samenvatting.etf_pct, 1)} — Aandelen: ${formatPct(samenvatting.aandeel_pct, 1)}`;
    document.getElementById("verdelingUitleg").style.display = "flex";
    const labelMinPct = taartLabelMinPct();

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
                            const pct = totaal ? ctx.parsed / totaal * 100 : 0;
                            return `${ctx.label}: ${formatteerEuro(ctx.parsed)} (${formatPct(pct, 1)})`;
                        }
                    }
                },
                datalabels: {
                    color: (ctx) => tekstKleurVoorVlak(kleuren[ctx.dataIndex]),
                    font: { weight: "bold", size: 11 },
                    formatter: (value, ctx) => {
                        const pct = totaal ? (value / totaal * 100) : 0;
                        if (pct < labelMinPct) return null;
                        return [kortNaam(items[ctx.dataIndex].naam), formatPct(pct, 1)];
                    }
                }
            }
        }
    });
}
