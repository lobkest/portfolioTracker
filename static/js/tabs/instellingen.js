// Tabblad Instellingen: tabbladen in ontwikkeling aan/uit, portfolio verwijderen en de code wijzigen.

// Zonder opslag (privévenster, geblokkeerd) gedraagt alles zich alsof het uit staat.
function leesOntwikkelAan() {
    try {
        const opslag = lokaleOpslag();
        return opslag ? leesAanGezet(opslag.getItem(ONTWIKKEL_OPSLAG_SLEUTEL)) : [];
    } catch (_) {
        return [];
    }
}

function bewaarOntwikkelAan(views) {
    try {
        lokaleOpslag()?.setItem(ONTWIKKEL_OPSLAG_SLEUTEL, JSON.stringify(views));
    } catch (_) {
        // niet bewaard: de keuze geldt dan niet na herladen
    }
}

function maakOntwikkelSchakelaar({ view, label }, aan) {
    const rij = document.createElement("label");
    rij.className = "schakelaar";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.setAttribute("role", "switch");
    input.checked = aan;
    input.dataset.ontwikkelView = view;
    const schuif = document.createElement("span");
    schuif.className = "schakelaarSchuif";
    const tekst = document.createElement("span");
    tekst.textContent = label;
    rij.append(input, schuif, tekst);
    return rij;
}

const aanBijStart = leesOntwikkelAan();
document.getElementById("ontwikkelSchakelaars").replaceChildren(
    ...ontwikkelViews().map(v => maakOntwikkelSchakelaar(v, aanBijStart.includes(v.view))),
);

document.getElementById("ontwikkelSchakelaars").addEventListener("change", (e) => {
    const view = e.target.dataset.ontwikkelView;
    if (!view) return;
    const aan = leesOntwikkelAan().filter(v => v !== view);
    if (e.target.checked) aan.push(view);
    bewaarOntwikkelAan(aan);
    if (!huidigeData) return;
    if (toegestaneViews().includes(actieveView)) ververMenu(actieveView);
    else gaNaarView(STANDAARD_VIEW);
});

document.getElementById("verwijderPortfolioBtn").addEventListener("click", async () => {
    if (!huidigeData || !huidigeData.code) return;
    const zeker = confirm(`Weet je zeker dat je portfolio ${huidigeData.code} permanent wilt verwijderen? Dit kan niet ongedaan worden gemaakt.`);
    if (!zeker) return;

    toonLaadOverlay("Verwijderen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}`, { method: "DELETE" });
        if (res.ok) {
            // replace: de terug-knop mag niet op de verwijderde portfolio uitkomen.
            location.replace(startPadMetMelding(MELDING_VERWIJDERD));
        } else {
            alert("Verwijderen is niet gelukt, probeer het later opnieuw.");
        }
    } finally {
        verbergLaadOverlay();
    }
});

document.getElementById("nieuweCodeInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        e.preventDefault();
        document.getElementById("wijzigCodeBtn").click();
    }
});

document.getElementById("wijzigCodeBtn").addEventListener("click", async () => {
    if (!huidigeData || !huidigeData.code) return;
    const input = document.getElementById("nieuweCodeInput");
    const nieuweCode = input.value.trim().toUpperCase();
    if (!nieuweCode) return;

    const msg = document.getElementById("instellingenMsg");
    toonLaadOverlay("Aanpassen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/wijzig-code`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nieuwe_code: nieuweCode })
        });
        const data = await res.json();
        if (!res.ok) {
            msg.className = "melding foutTekst";
            msg.textContent = data.error || "Code wijzigen mislukt.";
            msg.style.display = "block";
            return;
        }
        // Object.assign (zie CLAUDE.md: Frontend); verrijking opnieuw onder de nieuwe code.
        Object.assign(huidigeData, data);
        laadVerrijking(huidigeData.code);
        document.getElementById("dashCode").textContent = data.code || "";
        history.replaceState(null, "", portfolioPad(data.code) + location.hash);
        input.value = "";
        msg.className = "melding positief";
        msg.textContent = `Code gewijzigd naar ${data.code} — bewaar deze om later terug te komen.`;
        msg.style.display = "block";
    } finally {
        verbergLaadOverlay();
    }
});
