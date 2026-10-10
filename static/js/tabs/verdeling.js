// Tabblad Verdeling: taart van de posities, ETF's met een streeppatroon; de uitleg ETF/Aandeel staat in HTML onder de grafiek.
// In ontwikkeling: "Over tijd" (alleen met code), een gestapelde vlakgrafiek in % of €.

const VERDELING_OVER_TIJD_ID = "verdeling-over-tijd";
let verdelingModus = "nu";
let verdelingTijdWeergave = "pct";
let verdelingOverTijdData = null;

function resetVerdeling() {
    verdelingModus = "nu";
    verdelingTijdWeergave = "pct";
    verdelingOverTijdData = null;
}

function toonVerdeling() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    const overTijdMogelijk = Boolean(huidigeData.code) && ontwikkelAan(VERDELING_OVER_TIJD_ID, leesOntwikkelAan());
    if (!overTijdMogelijk) verdelingModus = "nu";
    const overTijd = verdelingModus === "tijd";
    document.getElementById("verdelingModusWrapper").style.display = overTijdMogelijk ? "flex" : "none";
    document.getElementById("verdelingTijdWeergave").style.display = overTijd ? "" : "none";
    document.querySelectorAll("#verdelingModusWrapper .segmentKnop[data-modus]").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.modus === verdelingModus);
    });
    document.querySelectorAll("#verdelingTijdWeergave .segmentKnop").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.weergave === verdelingTijdWeergave);
    });
    document.getElementById("verdelingOverTijdUitleg").style.display = overTijd ? "block" : "none";
    document.getElementById("verdelingOverTijdMsg").style.display = "none";
    // Alleen de vlakgrafiek is zoombaar; plaatsGrafiek() zet de knop bij Verdeling uit.
    document.getElementById("resetZoomBtn").style.display = overTijd ? "block" : "none";
    if (overTijd) {
        ["verdelingTekst", "verdelingUitleg", "verrijkingLaadt", "verrijkingFout"].forEach(id => {
            document.getElementById(id).style.display = "none";
        });
        toonVerdelingOverTijd();
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

function toonVerdelingOverTijdMelding(tekst, fout) {
    const msg = document.getElementById("verdelingOverTijdMsg");
    msg.classList.toggle("foutTekst", fout);
    msg.textContent = tekst;
    msg.style.display = "block";
}

// Eén keer per portfolio ophalen; een fout wordt niet bewaard, zodat opnieuw kiezen opnieuw probeert.
async function toonVerdelingOverTijd() {
    if (!verdelingOverTijdData) {
        const code = huidigeData.code;
        toonLaadOverlay("Verdeling over tijd berekenen...");
        let res, data;
        try {
            res = await fetch(`/api/portfolio/${code}/verdeling-over-tijd?dimensie=positie`);
            data = await res.json();
        } catch (e) {
            toonVerdelingOverTijdMelding("Kon de verdeling over tijd niet ophalen (netwerkfout).", true);
            return;
        } finally {
            verbergLaadOverlay();
        }
        if (huidigeData.code !== code) return;
        if (!res.ok) {
            toonVerdelingOverTijdMelding(data.error || "De verdeling over tijd kon niet berekend worden.", true);
            return;
        }
        verdelingOverTijdData = data;
    }
    if (verdelingModus !== "tijd" || actieveView !== "verdeling") return;
    if (verdelingOverTijdData.labels.length === 0) {
        toonVerdelingOverTijdMelding("Nog geen data om te tonen.", false);
        return;
    }
    const datasets = verdelingOverTijdDatasets(
        verdelingOverTijdData, verdelingTijdWeergave, kleurVoorTicker, ONBEKEND_GRIJS);
    updateGestapeldeVlakChart(verdelingOverTijdData.labels, datasets, verdelingTijdWeergave === "pct");
}

document.querySelectorAll("#verdelingModusWrapper .segmentKnop[data-modus]").forEach(knop => {
    knop.addEventListener("click", () => {
        verdelingModus = knop.dataset.modus;
        toonVerdeling();
    });
});

document.querySelectorAll("#verdelingTijdWeergave .segmentKnop").forEach(knop => {
    knop.addEventListener("click", () => {
        verdelingTijdWeergave = knop.dataset.weergave;
        toonVerdeling();
    });
});
