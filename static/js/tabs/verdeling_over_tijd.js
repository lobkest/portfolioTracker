// "Over tijd" op Verdeling, Land, Sector, Valuta en Beurs (alleen met code): gestapelde vlakgrafiek in € of %.
// De keuzes Nu/Over tijd en %/€ zijn gedeeld tussen de tabbladen, zoals de taart/staaf-keuze.

const OVER_TIJD_UITLEG = {
    positie: "Aandeel van elke positie in de totale waarde, per week. Verschuivingen komen door aan- en verkopen én door koersverschillen tussen posities.",
    valuta: "Aandeel per noteringsvaluta, per week. Een ETF telt als één valuta (die van de notering, niet van de onderliggende aandelen).",
    beurs: "Aandeel per beurs waarop je de positie kocht, per week.",
    land: "Aandeel per land, per week. Voor ETF's wordt de samenstelling van vandaag gebruikt, ook voor het verleden: verschuivingen binnen een ETF (bijvoorbeeld een groter VS-aandeel in een wereld-ETF) zie je hier niet, alleen die door je eigen aan- en verkopen en koersverschillen tussen je posities.",
    sector: "Aandeel per sector, per week. Voor ETF's wordt de samenstelling van vandaag gebruikt, ook voor het verleden: verschuivingen binnen een ETF zie je hier niet, alleen die door je eigen aan- en verkopen en koersverschillen tussen je posities.",
};
// Land en sector halen ook voor gesloten ETF's holdings op; pas na de verrijking (dan zijn de huidige al gecachet).
const OVER_TIJD_NA_VERRIJKING = ["land", "sector"];
let overTijdModus = "nu";
let overTijdWeergave = "euro";
// Per "dimensie|samenvoegen" het antwoord van /verdeling-over-tijd.
let overTijdDataPerSleutel = {};

function resetVerdelingOverTijd() {
    overTijdModus = "nu";
    overTijdWeergave = "euro";
    overTijdDataPerSleutel = {};
}

// Zet de knoppen en de uitleg voor dit tabblad; true als "Over tijd" gekozen is (de aanroeper tekent dan niets zelf).
function toonOverTijdIndienGekozen(dimensie, samenvoegen = false) {
    const mogelijk = Boolean(huidigeData.code);
    if (!mogelijk) overTijdModus = "nu";
    const overTijd = overTijdModus === "tijd";
    document.getElementById("overTijdKeuze").style.display = mogelijk ? "flex" : "none";
    document.getElementById("overTijdWeergave").style.display = overTijd ? "" : "none";
    document.querySelectorAll("#overTijdKeuze .segmentKnop[data-modus]").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.modus === overTijdModus);
    });
    document.querySelectorAll("#overTijdWeergave .segmentKnop").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.weergave === overTijdWeergave);
    });
    const uitleg = document.getElementById("overTijdUitleg");
    uitleg.textContent = OVER_TIJD_UITLEG[dimensie];
    uitleg.style.display = overTijd ? "block" : "none";
    document.getElementById("overTijdMsg").style.display = "none";
    document.getElementById("overTijdDekking").style.display = "none";
    // Alleen de vlakgrafiek is zoombaar; plaatsGrafiek() zet de knop op deze tabbladen uit.
    document.getElementById("resetZoomBtn").style.display = overTijd ? "block" : "none";
    if (dimensie !== "positie") {
        document.getElementById("weergaveToggleBtn").style.display = overTijd ? "none" : "block";
    }
    if (!overTijd) return false;

    if (OVER_TIJD_NA_VERRIJKING.includes(dimensie)) {
        if (toonVerrijkingWachtstatusIndienNodig()) return true;
    } else {
        ["verrijkingLaadt", "verrijkingFout"].forEach(id => { document.getElementById(id).style.display = "none"; });
    }
    toonVerdelingOverTijd(dimensie, samenvoegen);
    return true;
}

function toonOverTijdMelding(tekst, fout) {
    const msg = document.getElementById("overTijdMsg");
    msg.classList.toggle("foutTekst", fout);
    msg.textContent = tekst;
    msg.style.display = "block";
}

function overTijdKleurVoor(dimensie) {
    return dimensie === "positie" ? kleurVoorTicker : (_, index) => kleurVoorIndex(index);
}

// Eén keer per portfolio en sleutel ophalen; een fout wordt niet bewaard, zodat opnieuw kiezen opnieuw probeert.
async function toonVerdelingOverTijd(dimensie, samenvoegen) {
    const sleutel = `${dimensie}|${samenvoegen ? 1 : 0}`;
    const data = overTijdDataPerSleutel[sleutel];
    if (!data) {
        const code = huidigeData.code;
        toonLaadOverlay(OVER_TIJD_NA_VERRIJKING.includes(dimensie)
            ? "Land/sector over tijd berekenen... De eerste keer kan dit langer duren."
            : "Verdeling over tijd berekenen...");
        let res, antwoord;
        try {
            res = await fetchMetTimeout(`/api/portfolio/${code}/verdeling-over-tijd?dimensie=${dimensie}${samenvoegen ? "&samenvoegen=1" : ""}`);
            antwoord = await res.json();
        } catch (e) {
            toonOverTijdMelding(e.message === "TIMEOUT"
                ? "Het berekenen duurde te lang. Probeer het opnieuw: wat al is opgehaald blijft bewaard, dus een tweede poging gaat sneller."
                : "Kon de verdeling over tijd niet ophalen. Probeer het opnieuw.", true);
            return;
        } finally {
            verbergLaadOverlay();
        }
        if (huidigeData.code !== code) return;
        if (!res.ok) {
            toonOverTijdMelding(antwoord.error || "De verdeling over tijd kon niet berekend worden.", true);
            return;
        }
        overTijdDataPerSleutel[sleutel] = antwoord;
        // Opnieuw via het actieve tabblad: dat kan intussen gewisseld zijn (of de Euronext-keuze).
        herTekenVerrijkingTabbladIndienActief();
        return;
    }
    if (data.labels.length === 0) {
        toonOverTijdMelding("Nog geen data om te tonen.", false);
        return;
    }
    const dekking = dimensie === "land" ? beperkteDekkingRegel(data.beperkte_dekking) : null;
    if (dekking) {
        const dekkingEl = document.getElementById("overTijdDekking");
        dekkingEl.textContent = dekking;
        dekkingEl.style.display = "block";
    }
    const datasets = verdelingOverTijdDatasets(data, overTijdWeergave, overTijdKleurVoor(dimensie), ONBEKEND_GRIJS);
    updateGestapeldeVlakChart(data.labels, datasets, overTijdWeergave === "pct");
}

document.querySelectorAll("#overTijdKeuze .segmentKnop[data-modus]").forEach(knop => {
    knop.addEventListener("click", () => {
        overTijdModus = knop.dataset.modus;
        herTekenVerrijkingTabbladIndienActief();
    });
});

document.querySelectorAll("#overTijdWeergave .segmentKnop").forEach(knop => {
    knop.addEventListener("click", () => {
        overTijdWeergave = knop.dataset.weergave;
        herTekenVerrijkingTabbladIndienActief();
    });
});
