// Tabblad Instellingen > Diagnostiek: meldingen van deze laadbeurt. Tellen, groeperen en de blokindeling staan in diagnostiek.js.

// Gereset bij een nieuwe portfolio; bijnaam/code wijzigen laat de meldingen staan.
let diagnostiekMeldingen = [];
// Open/dicht-keuze van de gebruiker per categorie (overschrijft de
// standaard uit categorieStandaardOpen tot een nieuwe upload/code).
let diagnostiekOpenKeuze = new Map();
let diagnostiekNiveauFilter = null;

function resetDiagnostiek() {
    diagnostiekMeldingen = [];
    diagnostiekOpenKeuze = new Map();
    diagnostiekNiveauFilter = null;
}

function voegDiagnostiekToe(data) {
    diagnostiekMeldingen = voegMeldingenSamen(diagnostiekMeldingen, data && data.diagnostiek);
    if (actieveViewNaam() === "instellingen-diagnostiek") toonDiagnostiek();
}

function maakDiagnostiekTabel(tabel) {
    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper scrollbareTabel diagnostiekTabel";
    const tabelEl = document.createElement("table");
    tabelEl.className = "dataTabel";
    const kopRij = document.createElement("tr");
    tabel.kolommen.forEach(kolom => {
        const th = document.createElement("th");
        th.textContent = kolom;
        kopRij.appendChild(th);
    });
    const thead = document.createElement("thead");
    thead.appendChild(kopRij);
    const tbody = document.createElement("tbody");
    tabel.rijen.forEach(rij => {
        const tr = document.createElement("tr");
        rij.forEach(cel => tr.appendChild(maakCel(cel)));
        tbody.appendChild(tr);
    });
    tabelEl.appendChild(thead);
    tabelEl.appendChild(tbody);
    wrapper.appendChild(tabelEl);
    return wrapper;
}

function maakNiveauIcoon(niveau) {
    const icoon = document.createElement("span");
    icoon.className = `niveauIcoon icoon${niveau}`;
    icoon.textContent = DIAGNOSTIEK_NIVEAU_ICOON[niveau];
    icoon.title = DIAGNOSTIEK_NIVEAU_LABEL[niveau];
    icoon.setAttribute("aria-label", DIAGNOSTIEK_NIVEAU_LABEL[niveau]);
    return icoon;
}

function toonDiagnostiekChips(telling) {
    const plek = document.getElementById("diagnostiekChips");
    plek.replaceChildren();
    DIAGNOSTIEK_NIVEAUS.forEach(niveau => {
        const actief = diagnostiekNiveauFilter === niveau;
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `diagnostiekChip chip${niveau}` + (actief ? " actief" : "");
        chip.setAttribute("aria-pressed", String(actief));
        chip.disabled = telling[niveau] === 0 && !actief;
        chip.textContent = `${DIAGNOSTIEK_NIVEAU_ICOON[niveau]} ${DIAGNOSTIEK_NIVEAU_LABEL[niveau]} ${telling[niveau]}`;
        chip.addEventListener("click", () => {
            diagnostiekNiveauFilter = wisselNiveauFilter(diagnostiekNiveauFilter, niveau);
            toonDiagnostiek();
        });
        plek.appendChild(chip);
    });
}

function maakDiagnostiekMelding(melding, categorieNiveau) {
    const niveau = normaalNiveau(melding.niveau);
    const li = document.createElement("li");
    li.appendChild(maakNiveauIcoon(niveau));
    const inhoud = document.createElement("div");
    inhoud.className = "diagnostiekInhoud";
    const tekst = document.createElement("p");
    tekst.className = "diagnostiekTekst";
    if (niveau !== categorieNiveau) {
        const pil = document.createElement("span");
        pil.className = `badge badge${niveau}`;
        pil.textContent = DIAGNOSTIEK_NIVEAU_LABEL[niveau];
        tekst.appendChild(pil);
    }
    tekst.appendChild(document.createTextNode(melding.tekst));
    inhoud.appendChild(tekst);
    const tabel = diagnostiekTabelRijen(melding.tabel);
    if (tabel) inhoud.appendChild(maakDiagnostiekTabel(tabel));
    const actie = meldingActie(melding);
    if (actie) {
        const knop = document.createElement("button");
        knop.type = "button";
        knop.className = "diagnostiekActie";
        knop.textContent = actie.tekst;
        knop.addEventListener("click", () => gaNaarView(actie.tab));
        inhoud.appendChild(knop);
    }
    li.appendChild(inhoud);
    return li;
}

function maakDiagnostiekCategorie(groep, standaardOpen) {
    const blok = document.createElement("details");
    blok.className = "diagnostiekBlok";
    blok.open = diagnostiekOpenKeuze.has(groep.categorie) ? diagnostiekOpenKeuze.get(groep.categorie) : standaardOpen;

    const hoogste = hoogsteNiveau(groep.meldingen);
    const kop = document.createElement("summary");
    kop.className = "diagnostiekCategorie";
    // Via de klik (ook Enter/Spatie) i.p.v. het toggle-event: dat gaat
    // ook af op het programmatisch zetten van blok.open hierboven.
    kop.addEventListener("click", () => diagnostiekOpenKeuze.set(groep.categorie, !blok.open));
    kop.appendChild(maakNiveauIcoon(hoogste));
    const naam = document.createElement("span");
    naam.className = "diagnostiekNaam";
    naam.textContent = groep.categorie;
    const aantal = document.createElement("span");
    aantal.className = "diagnostiekAantal";
    aantal.textContent = String(groep.meldingen.length);
    const samenvatting = document.createElement("span");
    samenvatting.className = "diagnostiekSamenvatting";
    samenvatting.textContent = categorieSamenvatting(groep.meldingen);
    kop.append(naam, aantal, samenvatting);
    blok.appendChild(kop);

    const ul = document.createElement("ul");
    ul.className = "diagnostiekLijst";
    groep.meldingen.forEach(melding => ul.appendChild(maakDiagnostiekMelding(melding, hoogste)));
    blok.appendChild(ul);
    return blok;
}

function maakDiagnostiekSectie(titel, groepen, standaardOpen) {
    const sectie = document.createElement("section");
    sectie.className = "diagnostiekSectie";
    const kop = document.createElement("h3");
    kop.className = "diagnostiekSectieKop";
    kop.textContent = titel;
    sectie.appendChild(kop);
    groepen.forEach(groep => sectie.appendChild(maakDiagnostiekCategorie(groep, standaardOpen)));
    return sectie;
}

// Conclusie + filterchips bovenaan; op desktop links "Aandacht nodig", rechts "In orde" en "Technisch".
function toonDiagnostiek() {
    const teller = document.getElementById("diagnostiekTeller");
    const chips = document.getElementById("diagnostiekChips");
    const lijst = document.getElementById("diagnostiekLijst");
    lijst.replaceChildren();
    lijst.classList.remove("eenKolom");

    if (diagnostiekMeldingen.length === 0) {
        teller.textContent = "Nog geen meldingen voor deze laadbeurt.";
        chips.replaceChildren();
        return;
    }
    teller.textContent = diagnostiekConclusie(diagnostiekMeldingen);
    toonDiagnostiekChips(telPerNiveau(diagnostiekMeldingen));

    const blokken = deelInBlokken(filterOpNiveau(diagnostiekMeldingen, diagnostiekNiveauFilter));
    // Met een filter staat alles open: je hebt dan zelf gekozen wat je wilt zien.
    const metFilter = diagnostiekNiveauFilter !== null;
    const links = document.createElement("div");
    links.className = "diagnostiekKolom";
    const rechts = document.createElement("div");
    rechts.className = "diagnostiekKolom";
    if (blokken.aandacht.length > 0) links.appendChild(maakDiagnostiekSectie("Aandacht nodig", blokken.aandacht, true));
    if (blokken.inOrde.length > 0) rechts.appendChild(maakDiagnostiekSectie("In orde", blokken.inOrde, metFilter));
    if (blokken.technisch.length > 0) rechts.appendChild(maakDiagnostiekSectie("Technisch", blokken.technisch, metFilter));

    if (links.childElementCount === 0) {
        lijst.classList.add("eenKolom");
    } else {
        lijst.appendChild(links);
    }
    if (rechts.childElementCount > 0) lijst.appendChild(rechts);
    if (lijst.childElementCount === 0) {
        const leeg = document.createElement("p");
        leeg.className = "gedempt";
        leeg.textContent = "Geen meldingen met dit niveau.";
        lijst.appendChild(leeg);
    }
}
