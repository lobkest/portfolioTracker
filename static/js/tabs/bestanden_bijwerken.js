// Tabblad Instellingen > Bestanden bijwerken: nieuwere DeGiro-export(s) toevoegen aan deze portfolio.

const bijwerkBestandKeuzes = ["bestand1", "bestand2"].map(koppelBestandWisKnop);

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
