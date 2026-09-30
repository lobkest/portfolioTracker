// Tabblad XIRR & rendement: rendement, XIRR en TWR over tijd (alleen bij een opgeslagen portfolio).

// Geen cache: elke opening opnieuw ophalen (licht, geen Yahoo-calls).
async function toonRendementOverTijd() {
    const msg = document.getElementById("xirrRendementMsg");
    msg.style.display = "none";
    msg.classList.remove("foutTekst");

    if (!huidigeData.code) {
        if (chart) { chart.destroy(); chart = null; }
        document.getElementById("chartWrapper").style.display = "none";
        return;
    }

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
