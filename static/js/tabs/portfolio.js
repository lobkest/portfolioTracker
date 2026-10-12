// Tabblad Portfolio-home: waarde en geinvesteerd over tijd, met de totalen erboven.

// Eén keer per portfolio-laad bepaald (toonDashboard); staat buiten de tabpanelen en is niet te verbergen.
function toonBestandsmelding() {
    const banner = document.getElementById("bestandsBanner");
    const totalen = huidigeData.statistieken && huidigeData.statistieken.totalen;
    const melding = bestandsmelding(totalen && totalen.rekeningoverzicht_status, totalen && totalen.transactiebestand_status, Boolean(huidigeData.code));
    if (!melding) {
        banner.style.display = "none";
        return;
    }
    document.getElementById("bestandsBannerKop").textContent = melding.kop;
    document.getElementById("bestandsBannerGevolg").textContent = melding.gevolg;
    const link = document.getElementById("bestandsBannerLink");
    link.textContent = melding.linkTekst;
    link.style.display = melding.linkNaarInstellingen ? "inline-block" : "none";
    const tip = document.getElementById("bestandsBannerTip");
    tip.textContent = melding.linkNaarInstellingen ? "" : melding.linkTekst;
    tip.style.display = melding.linkNaarInstellingen ? "none" : "block";

    const blokken = document.getElementById("bestandsBannerBlokken");
    blokken.innerHTML = "";
    melding.blokken.forEach(blok => {
        const el = document.createElement("section");
        el.className = "bestandsBannerBlok";
        const kop = document.createElement("h4");
        kop.textContent = blok.titel;
        el.appendChild(kop);
        if (blok.periode) {
            const periode = document.createElement("p");
            periode.className = "bestandsBannerPeriode";
            periode.textContent = `Periode: ${blok.periode}`;
            el.appendChild(periode);
        }
        if (blok.tekst) {
            const tekst = document.createElement("p");
            tekst.textContent = blok.tekst;
            el.appendChild(tekst);
        }
        if (blok.redenen.length) {
            const lijst = document.createElement("ul");
            lijst.className = "bannerLijst";
            blok.redenen.forEach(reden => {
                const li = document.createElement("li");
                li.textContent = reden;
                lijst.appendChild(li);
            });
            el.appendChild(lijst);
        }
        blokken.appendChild(el);
    });
    banner.style.display = "block";
}

// Compact buiten Overzicht (kop en link op één regel); Details klapt de blokken uit, de banner blijft staan.
function zetBestandsmeldingCompact(view) {
    document.getElementById("bestandsBannerDetails").open = view === "portfolio";
}

document.getElementById("bestandsBannerLink").addEventListener("click", () => {
    scrollNaarBijwerkMarkering = true;
    gaNaarView("instellingen-bestanden");
});

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
