// Tabblad Instellingen > Bestanden bijwerken: nieuwere DeGiro-export(s) toevoegen aan deze portfolio.

const bijwerkBestandKeuzes = ["bestand1", "bestand2"].map(koppelBestandWisKnop);

// De link in de banner zet dit; de toon-functie scrolt dan naar het eerste gemarkeerde veld.
let scrollNaarBijwerkMarkering = false;

function resetBestandenBijwerken() {
    scrollNaarBijwerkMarkering = false;
}

function toonBestandenBijwerken() {
    const totalen = huidigeData.statistieken && huidigeData.statistieken.totalen;
    const { velden, eerste } = bestandNiveaus(totalen && totalen.rekeningoverzicht_status, totalen && totalen.transactiebestand_status);
    Object.entries(velden).forEach(([veld, info]) => {
        const blok = document.getElementById(`${veld}Markering`);
        blok.hidden = info.niveau === "geen";
        if (blok.hidden) return;
        blok.className = `bestandMarkering bestandMarkering${info.niveau === "opnieuw" ? "Opnieuw" : "Aanbevolen"}`;
        blok.querySelector(".bestandMarkeringLabel").textContent = info.label;
        blok.querySelector(".bestandMarkeringTekst").textContent = info.tekst;
        blok.querySelector(".bestandMarkeringPeriode").textContent = info.periode ? `Huidig bestand: ${info.periode}` : "";
    });
    if (scrollNaarBijwerkMarkering && eerste) {
        document.getElementById(`${eerste}Markering`).scrollIntoView({ block: "center" });
    }
    scrollNaarBijwerkMarkering = false;
}

function toonBijwerkenMelding(tekst, klasse) {
    const msg = document.getElementById("bestandenBijwerkenMsg");
    msg.className = `melding ${klasse}`;
    msg.textContent = tekst;
    msg.style.display = "block";
}

document.getElementById("bestandenBijwerkenForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!huidigeData || !huidigeData.code) return;
    document.getElementById("bestandenBijwerkenMsg").style.display = "none";

    const formData = new FormData(e.target);
    for (const veld of ["bestand1", "bestand2"]) {
        const input = document.getElementById(veld);
        if (!input.files || input.files.length === 0) formData.delete(veld);
    }
    if (!formData.has("bestand1") && !formData.has("bestand2")) {
        toonBijwerkenMelding("Kies minstens één bestand.", "foutTekst");
        return;
    }

    toonLaadOverlay("Bijwerken...");
    try {
        const res = await fetchMetTimeout(`/api/portfolio/${huidigeData.code}/bijwerken`,
            { method: "POST", body: formData }, UPLOAD_TIMEOUT_MS);
        const data = await res.json();
        if (!res.ok) {
            toonBijwerkenMelding(data.error || "Bijwerken mislukt.", "foutTekst");
            return;
        }
        // Volledig opnieuw tonen (niet Object.assign): nieuwe transacties maken de oude verrijking en tab-toestand ongeldig.
        toonDashboard(data);
        e.target.reset();
        bijwerkBestandKeuzes.forEach(werkBij => werkBij());
        toonBijwerkenMelding(bijwerkenSuccesTekst(data.bijwerken || {}), "positief");
    } catch (err) {
        toonBijwerkenMelding(err.message === "TIMEOUT"
            ? "Bijwerken duurde te lang en is gestopt. Probeer het opnieuw."
            : "Er ging iets mis bij het bijwerken (netwerkfout). Probeer het opnieuw.", "foutTekst");
    } finally {
        verbergLaadOverlay();
    }
});
