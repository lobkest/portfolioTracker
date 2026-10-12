// Tabblad Portfolio-home: waarde en geinvesteerd over tijd, met de totalen erboven.

// Alleen in het geheugen: na opnieuw laden staat de melding er weer, want het bestand blijft onvolledig.
const bestandsmeldingGesloten = { rekeningoverzicht: false, transactiebestand: false };

function resetBestandsmeldingen() {
    bestandsmeldingGesloten.rekeningoverzicht = false;
    bestandsmeldingGesloten.transactiebestand = false;
}

// prefix = begin van de element-id's in portfolio.html (rekeningoverzicht, transactiebestand).
function toonBestandsmelding(prefix, melding) {
    const banner = document.getElementById(`${prefix}Banner`);
    if (!melding || bestandsmeldingGesloten[prefix]) {
        banner.style.display = "none";
        return;
    }
    document.getElementById(`${prefix}Kop`).textContent = melding.kop;
    const lijst = document.getElementById(`${prefix}Redenen`);
    lijst.innerHTML = "";
    melding.redenen.forEach(tekst => {
        const li = document.createElement("li");
        li.textContent = tekst;
        lijst.appendChild(li);
    });
    const periode = document.getElementById(`${prefix}Periode`);
    periode.textContent = melding.periode || "";
    periode.style.display = melding.periode ? "block" : "none";
    const link = document.getElementById(`${prefix}Link`);
    link.textContent = melding.linkTekst;
    link.style.display = melding.linkNaarInstellingen ? "inline-block" : "none";
    const tip = document.getElementById(`${prefix}Tip`);
    tip.textContent = melding.linkNaarInstellingen ? "" : melding.linkTekst;
    tip.style.display = melding.linkNaarInstellingen ? "none" : "block";
    banner.style.display = "flex";
}

function toonBestandsmeldingen() {
    const totalen = huidigeData.statistieken && huidigeData.statistieken.totalen;
    const metCode = Boolean(huidigeData.code);
    toonBestandsmelding("rekeningoverzicht", rekeningoverzichtMelding(totalen && totalen.rekeningoverzicht_status, metCode));
    toonBestandsmelding("transactiebestand", transactiebestandMelding(totalen && totalen.transactiebestand_status, metCode));
}

["rekeningoverzicht", "transactiebestand"].forEach(prefix => {
    document.getElementById(`${prefix}Link`).addEventListener("click", () => gaNaarView("instellingen-bestanden"));
    document.getElementById(`${prefix}Sluit`).addEventListener("click", () => {
        bestandsmeldingGesloten[prefix] = true;
        document.getElementById(`${prefix}Banner`).style.display = "none";
    });
});

function toonPortfolio() {
    toonBestandsmeldingen();
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
