// DOM voor "Alternatieven op een thuisbeurs" onder Verdeling → Beurs (teksten: beurs_alternatieven.js). Lui: pas bij een klik.

function nieuweAlternatievenToestand() {
    return { open: null, volgnr: 0 };
}

let alternatievenToestand = nieuweAlternatievenToestand();

function alternatievenPlek() {
    return document.getElementById("aansluitingskostenAlternatieven");
}

function resetBeursAlternatieven() {
    alternatievenToestand = nieuweAlternatievenToestand();
    const plek = alternatievenPlek();
    if (plek) {
        plek.replaceChildren();
        plek.hidden = true;
    }
}

function maakAlternatievenKnop(rij) {
    if (!rij.heeftOpenPositie) return null;
    const knop = document.createElement("button");
    knop.type = "button";
    knop.className = "linkKnop alternatievenKnop";
    knop.textContent = KNOP_TEKST;
    knop.setAttribute("aria-expanded", String(alternatievenToestand.open === rij.code));
    knop.addEventListener("click", () => wisselAlternatieven(rij));
    return knop;
}

function maakAlternatievenRegel(klasse, tekst) {
    const p = document.createElement("p");
    p.className = klasse;
    p.textContent = tekst;
    return p;
}

function renderAlternatievenPaneel(antwoord) {
    const paneel = alternatievenPaneel(antwoord);
    const lijst = document.createElement("ul");
    paneel.posities.forEach(p => {
        const li = document.createElement("li");
        const naam = document.createElement("strong");
        naam.textContent = p.naam;
        li.append(naam, ` – ${p.tekst}`);
        [p.waarschuwing, p.extra].filter(Boolean).forEach(tekst => li.appendChild(maakAlternatievenRegel("kleineMelding", tekst)));
        lijst.appendChild(li);
    });
    const regels = document.createElement("ul");
    paneel.regels.forEach(r => {
        const li = document.createElement("li");
        li.textContent = `${r.label}: ${r.tekst}`;
        regels.appendChild(li);
    });
    const nodes = [maakAlternatievenRegel("", paneel.samenvatting), lijst, regels];
    if (paneel.blijftBetalen) nodes.push(maakAlternatievenRegel("", paneel.blijftBetalen));
    if (paneel.ditJaarAlBetaald) nodes.push(maakAlternatievenRegel("kleineMelding", paneel.ditJaarAlBetaald));
    paneel.vasteRegels.forEach(tekst => nodes.push(maakAlternatievenRegel("kleineMelding", tekst)));
    return nodes;
}

async function haalAlternatieven(rij) {
    if (huidigeData.code) {
        return fetchMetTimeout(`/api/portfolio/${encodeURIComponent(huidigeData.code)}/beurs-alternatieven?beurs=${encodeURIComponent(rij.code)}`);
    }
    // Bij "niet opslaan" bestaat de portfolio alleen in dit antwoord: de invoer komt mee uit het kernantwoord.
    return fetchMetTimeout("/api/beurs-alternatieven", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ invoer: rij.alternatievenInvoer }),
    });
}

async function wisselAlternatieven(rij) {
    const t = alternatievenToestand;
    const plek = alternatievenPlek();
    if (t.open === rij.code) {
        resetBeursAlternatieven();
        toonAansluitingskosten(document.getElementById("euronextCheckbox").checked);
        return;
    }
    t.open = rij.code;
    t.volgnr += 1;
    const mijn = t.volgnr;
    plek.hidden = false;
    plek.replaceChildren(maakAlternatievenRegel("kleineMelding", "Alternatieven opzoeken..."));
    toonAansluitingskosten(document.getElementById("euronextCheckbox").checked);
    const actueel = () => t === alternatievenToestand && mijn === t.volgnr;
    try {
        const res = await haalAlternatieven(rij);
        const data = await res.json().catch(() => null);
        if (!actueel()) return;
        plek.replaceChildren(...(res.ok ? renderAlternatievenPaneel(data)
            : [maakAlternatievenRegel("kleineMelding", alternatievenFoutTekst(res.status, data))]));
    } catch (fout) {
        if (!actueel()) return;
        plek.replaceChildren(maakAlternatievenRegel("kleineMelding", alternatievenFoutTekst(fout.message === "TIMEOUT" ? 504 : 0, null)));
    }
}
