// Tabblad Portfolio-home: waarde en geinvesteerd over tijd, met de totalen erboven.

function toonPortfolio() {
    const d = huidigeData.chart_data;
    updateChart(d.labels, [
        { label: "Waarde (€)", data: d.waarde, borderColor: "#2c7a4b" },
        { label: "Geïnvesteerd (€)", data: d.geinvesteerd, borderColor: "#3182bd" }
    ]);

    // Zelfde totalen als Statistieken; geen extra API-call.
    const homeSectie = document.getElementById("homeTotalenSectie");
    homeSectie.innerHTML = "";
    if (huidigeData.statistieken) {
        homeSectie.appendChild(maakTotalenSectie(huidigeData.statistieken.totalen));
    }

    // Zonder koersdata de regel weglaten (anders "Invalid Date").
    const bijgewerktEl = document.getElementById("laatstBijgewerktText");
    if (huidigeData.laatste_koersdatum && huidigeData.laatst_opgehaald_op) {
        // Via de Date-methoden, niet slice(): de ISO-string is UTC en kan een dag afwijken.
        const opgehaald = new Date(huidigeData.laatst_opgehaald_op);
        const opgehaaldDatumStr = `${String(opgehaald.getDate()).padStart(2, "0")}-${String(opgehaald.getMonth() + 1).padStart(2, "0")}-${opgehaald.getFullYear()}`;
        const tijd = opgehaald.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit", hour12: false });
        bijgewerktEl.textContent = `Koersen laatst opgehaald ${opgehaaldDatumStr} om ${tijd}`;
        bijgewerktEl.style.display = "block";
    } else {
        bijgewerktEl.style.display = "none";
    }
}
