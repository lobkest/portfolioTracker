// Startpagina: upload- en code-formulier. Het antwoord gaat via de overdracht mee naar de portfolio-pagina.

const OVERDRACHT_TE_GROOT_TEKST = "Deze analyse is te groot om zonder opslaan te tonen. "
    + "Upload opnieuw zonder het vinkje 'Niet opslaan'.";

function toonStartMelding() {
    const sleutel = new URLSearchParams(location.search).get(MELDING_PARAM);
    if (sleutel === null) return;
    const tekst = startMeldingTekst(sleutel);
    if (tekst) document.getElementById("startMelding").textContent = tekst;
    // Anders komt de melding terug bij een refresh.
    history.replaceState(null, "", START_PAD);
}

toonStartMelding();

// false = op de startpagina gebleven. Bij opslaan is een mislukte overdracht geen probleem:
// de portfolio-pagina haalt dan zelf op.
function gaNaarPortfolioPagina(data) {
    const bewaard = bewaarOverdracht(sessieOpslag(), data);
    if (!data.code && !bewaard) {
        document.getElementById("errorMsg").textContent = OVERDRACHT_TE_GROOT_TEKST;
        return false;
    }
    location.assign(data.code ? portfolioPad(data.code) : ANALYSE_PAD);
    return true;
}

const BESTAND_IDS = ["bestand1", "bestand2"];
const bestandKeuzeBijwerkers = BESTAND_IDS.map(koppelBestandWisKnop);

function werkKnoppenBij() {
    const transacties = document.getElementById("bestand1");
    document.getElementById("uploadKnop").disabled = !uploadKnopActief(transacties.files ? transacties.files.length : 0);
    const codeInput = document.getElementById("codeInput");
    document.getElementById("ophaalKnop").disabled = !ophaalKnopActief(codeInput.value, Number(codeInput.dataset.codeLengte));
}

function werkBestandKeuzesBij() {
    bestandKeuzeBijwerkers.forEach((werkBij) => werkBij());
    werkKnoppenBij();
}

// Alleen het eerste Excel-bestand: de inputs nemen er maar één.
function koppelSleepNaarVak(inputId) {
    const input = document.getElementById(inputId);
    const vak = input.closest(".bestandVak");
    ["dragenter", "dragover"].forEach((type) => vak.addEventListener(type, (e) => {
        e.preventDefault();
        vak.classList.add("slepen");
    }));
    vak.addEventListener("dragleave", (e) => {
        if (!vak.contains(e.relatedTarget)) vak.classList.remove("slepen");
    });
    vak.addEventListener("drop", (e) => {
        e.preventDefault();
        vak.classList.remove("slepen");
        const bestand = Array.from(e.dataTransfer.files).find((f) => isExcelBestandsnaam(f.name));
        if (!bestand) return;
        const overdracht = new DataTransfer();
        overdracht.items.add(bestand);
        input.files = overdracht.files;
        input.dispatchEvent(new Event("change"));
    });
}

BESTAND_IDS.forEach((id) => {
    koppelSleepNaarVak(id);
    document.getElementById(id).addEventListener("change", werkKnoppenBij);
    // Na de listener van koppelBestandWisKnop(), die de input al heeft geleegd.
    document.getElementById(`${id}WisKnop`).addEventListener("click", werkKnoppenBij);
});
document.getElementById("codeInput").addEventListener("input", werkKnoppenBij);

// reset geeft geen `change` en de inputs zijn pas daarna leeg: volgende tick.
document.getElementById("uploadForm").addEventListener("reset", () => {
    setTimeout(werkBestandKeuzesBij, 0);
});
// Na de terugknop (bfcache) kunnen de inputs afwijken van de rij, en staat de
// overlay nog aan: we navigeren weg terwijl hij zichtbaar is.
window.addEventListener("pageshow", () => {
    werkBestandKeuzesBij();
    verbergLaadOverlay();
});

document.getElementById("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault(); // voorkom dat de browser het formulier zelf verstuurt
    document.getElementById("errorMsg").textContent = ""; // reset bij elke nieuwe poging
    const formData = new FormData(e.target); // alle inputs, inclusief bestanden

    const bestand2Input = document.getElementById("bestand2");
    if (!bestand2Input.files || bestand2Input.files.length === 0) { // als er geen bestand is gekozen, verwijderen we het uit de FormData
        formData.delete("bestand2");
    }

    let navigeert = false;
    toonLaadOverlay("Analyseren...");
    try {
        const res = await fetchMetTimeout("/upload", { method: "POST", body: formData }, UPLOAD_TIMEOUT_MS);
        const data = await res.json();
        if (!res.ok) {
            document.getElementById("errorMsg").textContent = data.error || "Er ging iets mis.";
            return;
        }
        navigeert = gaNaarPortfolioPagina(data);
    } catch (err) {
        document.getElementById("errorMsg").textContent = err.message === "TIMEOUT"
            ? "Het ophalen duurde te lang en is gestopt. Dit gebeurt soms bij een nieuwe portfolio — druk gerust "
              + "nog 1 of 2 keer op de upload-knop, dat lukt meestal wél (de koersen die al opgehaald zijn, staan "
              + "dan al in de cache, dus de volgende poging is sneller)."
            : "Er ging iets mis bij het analyseren (netwerkfout). Probeer het opnieuw.";
    } finally {
        // Tijdens het navigeren blijft de overlay staan.
        if (!navigeert) verbergLaadOverlay();
    }
});

document.getElementById("codeForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = normaliseerCode(document.getElementById("codeInput").value);
    if (!code) return;
    document.getElementById("errorMsg").textContent = "";
    // Dit vinkje komt nooit in de URL van de portfolio-pagina: een refresh zou de dure herbepaling herhalen.
    const herbepaalAlleTickers = document.getElementById("herbepaalAlleTickersCode").checked;
    const url = herbepaalAlleTickers
        ? `/api/portfolio/${encodeURIComponent(code)}?herbepaal_alle_tickers=true`
        : `/api/portfolio/${encodeURIComponent(code)}`;
    let navigeert = false;
    toonLaadOverlay("Ophalen...");
    try {
        const res = await fetch(url);
        const data = await res.json();
        if (!res.ok) {
            document.getElementById("errorMsg").textContent = data.error || "Code niet gevonden.";
            return;
        }
        navigeert = gaNaarPortfolioPagina(data);
    } finally {
        if (!navigeert) verbergLaadOverlay();
    }
});
