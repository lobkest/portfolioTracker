// Tabblad Portfolio-home: waarde en geinvesteerd over tijd, met de totalen erboven.

// Alleen in het geheugen: na opnieuw laden staat de melding er weer, want het overzicht blijft onvolledig.
let rekeningoverzichtMeldingGesloten = false;

function resetRekeningoverzichtMelding() {
    rekeningoverzichtMeldingGesloten = false;
}

function toonRekeningoverzichtMelding() {
    const banner = document.getElementById("rekeningoverzichtBanner");
    const status = huidigeData.statistieken && huidigeData.statistieken.totalen.rekeningoverzicht_status;
    const melding = rekeningoverzichtMeldingGesloten ? null : rekeningoverzichtMelding(status, Boolean(huidigeData.code));
    if (!melding) {
        banner.style.display = "none";
        return;
    }
    document.getElementById("rekeningoverzichtKop").textContent = melding.kop;
    const lijst = document.getElementById("rekeningoverzichtRedenen");
    lijst.innerHTML = "";
    melding.redenen.forEach(tekst => {
        const li = document.createElement("li");
        li.textContent = tekst;
        lijst.appendChild(li);
    });
    const periode = document.getElementById("rekeningoverzichtPeriode");
    periode.textContent = melding.periode || "";
    periode.style.display = melding.periode ? "block" : "none";
    const link = document.getElementById("rekeningoverzichtLink");
    link.textContent = melding.linkTekst;
    link.style.display = melding.linkNaarInstellingen ? "inline-block" : "none";
    const tip = document.getElementById("rekeningoverzichtTip");
    tip.textContent = melding.linkNaarInstellingen ? "" : melding.linkTekst;
    tip.style.display = melding.linkNaarInstellingen ? "none" : "block";
    banner.style.display = "flex";
}

document.getElementById("rekeningoverzichtLink").addEventListener("click", () => gaNaarView("instellingen-bestanden"));
document.getElementById("rekeningoverzichtSluit").addEventListener("click", () => {
    rekeningoverzichtMeldingGesloten = true;
    document.getElementById("rekeningoverzichtBanner").style.display = "none";
});

function toonPortfolio() {
    toonRekeningoverzichtMelding();
    const d = huidigeData.chart_data;
    updateChart(d.labels, [
        { label: "Waarde (€)", data: d.waarde, borderColor: "#2c7a4b" },
        { label: "Geïnvesteerd (€)", data: d.geinvesteerd, borderColor: "#3182bd" }
    ]);

    // Zelfde totalen als Statistieken; geen extra API-call.
    const homeSectie = document.getElementById("homeTotalenSectie");
    homeSectie.innerHTML = "";
    if (huidigeData.statistieken) {
        homeSectie.appendChild(maakHomeSectie(huidigeData.statistieken.totalen, huidigeData.aansluitingskosten));
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
