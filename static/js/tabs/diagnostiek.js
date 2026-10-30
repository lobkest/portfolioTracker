// Tabblad Instellingen > Diagnostiek: meldingen van deze laadbeurt. Tellen, groeperen en de blokindeling staan in diagnostiek.js.

// Gereset bij een nieuwe portfolio; bijnaam/code wijzigen laat de meldingen staan.
let diagnostiekMeldingen = [];
let diagnostiekControles = [];
// Open/dicht-keuze van de gebruiker per categorie (overschrijft de
// standaard uit categorieStandaardOpen tot een nieuwe upload/code).
let diagnostiekOpenKeuze = new Map();
let diagnostiekZichtbareNiveaus = STANDAARD_ZICHTBARE_NIVEAUS.slice();
// Bewaarde meldingen van de laatste uploads (nieuwste eerst), lui opgehaald bij het openen van dit tabblad.
let diagnostiekUploads = null;
let diagnostiekMaxUploads = null;
let diagnostiekUploadsCode = null;

function resetDiagnostiek() {
    diagnostiekMeldingen = [];
    diagnostiekControles = [];
    diagnostiekOpenKeuze = new Map();
    diagnostiekZichtbareNiveaus = STANDAARD_ZICHTBARE_NIVEAUS.slice();
    diagnostiekUploads = null;
    diagnostiekMaxUploads = null;
    diagnostiekUploadsCode = null;
}

function diagnostiekIsOntwikkelaar() {
    return Boolean(huidigeData) && huidigeData.ontwikkelaar === true;
}

// Eén keer per code; bij "niet opslaan" (geen code) is er niets bewaard.
async function laadUploadMeldingen() {
    const code = huidigeData && huidigeData.code;
    if (!code || diagnostiekUploadsCode === code) return;
    diagnostiekUploadsCode = code;
    try {
        const res = await fetchMetTimeout(`/api/portfolio/${code}/upload-meldingen`);
        if (!res.ok) return;
        const data = await res.json();
        if (!huidigeData || huidigeData.code !== code) return;
        diagnostiekUploads = data.uploads || [];
        diagnostiekMaxUploads = data.max_uploads;
        if (actieveViewNaam() === "instellingen-diagnostiek") toonDiagnostiek();
    } catch (e) {
        // Volgende keer opnieuw proberen; de live meldingen staan er wel.
        diagnostiekUploadsCode = null;
        console.warn("[diagnostiek] upload-meldingen niet geladen", e);
    }
}

function voegDiagnostiekToe(data) {
    diagnostiekMeldingen = voegMeldingenSamen(diagnostiekMeldingen, data && data.diagnostiek);
    diagnostiekControles = voegControlesSamen(diagnostiekControles, data && data.diagnostiek_controles);
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

function maakDiagnostiekRijen(blok) {
    const details = document.createElement("details");
    details.className = "diagnostiekRijen";
    const samenvatting = document.createElement("summary");
    samenvatting.textContent = blok.titel;
    details.appendChild(samenvatting);
    details.appendChild(maakDiagnostiekTabel(blok));
    if (blok.meerTekst) {
        const meer = document.createElement("p");
        meer.className = "diagnostiekRijenMeer";
        meer.textContent = blok.meerTekst;
        details.appendChild(meer);
    }
    return details;
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
        const actief = diagnostiekZichtbareNiveaus.includes(niveau);
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `diagnostiekChip chip${niveau}` + (actief ? " actief" : "");
        chip.setAttribute("aria-pressed", String(actief));
        if (niveau === "GOED") chip.title = GOED_CHIP_UITLEG;
        chip.disabled = telling[niveau] === 0 && !actief;
        chip.textContent = `${DIAGNOSTIEK_NIVEAU_ICOON[niveau]} ${DIAGNOSTIEK_NIVEAU_LABEL[niveau]} (${telling[niveau]})`;
        chip.addEventListener("click", () => {
            diagnostiekZichtbareNiveaus = wisselNiveau(diagnostiekZichtbareNiveaus, niveau);
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
    const rijenBlok = diagnostiekRijenBlok(melding.rijen);
    if (rijenBlok) inhoud.appendChild(maakDiagnostiekRijen(rijenBlok));
    const actie = meldingActie(melding, toegestaneViews());
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

// keuzePrefix: de bewaarde uploads hebben dezelfde categorienamen als de live meldingen, maar een eigen open/dicht-keuze.
function maakDiagnostiekCategorie(groep, standaardOpen, keuzePrefix = "") {
    const blok = document.createElement("details");
    blok.className = "diagnostiekBlok";
    const keuzeSleutel = keuzePrefix + groep.categorie;
    blok.dataset.blok = keuzeSleutel;
    blok.open = diagnostiekOpenKeuze.has(keuzeSleutel) ? diagnostiekOpenKeuze.get(keuzeSleutel) : standaardOpen;

    const hoogste = hoogsteNiveau(groep.meldingen);
    const kop = document.createElement("summary");
    kop.className = "diagnostiekCategorie";
    // Via de klik (ook Enter/Spatie) i.p.v. het toggle-event: dat gaat
    // ook af op het programmatisch zetten van blok.open hierboven.
    kop.addEventListener("click", () => diagnostiekOpenKeuze.set(keuzeSleutel, !blok.open));
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

// standaardOpen null: per categorie open als er een fout of let op in staat.
function maakDiagnostiekSectie(titel, groepen, standaardOpen, keuzePrefix = "") {
    const sectie = document.createElement("section");
    sectie.className = "diagnostiekSectie";
    const kop = document.createElement("h3");
    kop.className = "diagnostiekSectieKop";
    kop.textContent = titel;
    sectie.appendChild(kop);
    groepen.forEach(groep => sectie.appendChild(maakDiagnostiekCategorie(
        groep, standaardOpen === null ? categorieStandaardOpen(groep.meldingen) : standaardOpen, keuzePrefix)));
    return sectie;
}

function maakUploadSectie(blok, standaardOpen, keuzePrefix, ookLive = 0) {
    const groepen = groepeerPerCategorie(filterOpNiveaus(blok.meldingen, diagnostiekZichtbareNiveaus));
    const sectie = maakDiagnostiekSectie(blok.titel, groepen, standaardOpen, keuzePrefix);
    const notities = [];
    if (ookLive > 0) notities.push(uploadOokLiveTekst(ookLive));
    if (groepen.length === 0 && ookLive === 0 && blok.meldingen.length > 0) notities.push("Geen meldingen met dit niveau.");
    notities.forEach(tekst => {
        const p = document.createElement("p");
        p.className = "gedempt";
        p.textContent = tekst;
        sectie.appendChild(p);
    });
    return sectie;
}

function toonUploadMeldingen() {
    const plek = document.getElementById("diagnostiekUploads");
    plek.replaceChildren();
    const blokken = uploadBlokken(diagnostiekUploads, diagnostiekMeldingen, diagnostiekMaxUploads);
    if (!blokken.laatste) return;
    const laatste = maakUploadSectie(blokken.laatste, null, "upload0:", blokken.laatste.aantalOokLive);
    const stand = blokken.laatste.standBijUpload;
    if (stand.length > 0) {
        const groep = document.createElement("details");
        groep.className = "diagnostiekStandBijUpload";
        const kop = document.createElement("summary");
        kop.textContent = `Stand bij upload (wordt nu opnieuw berekend) (${stand.length})`;
        groep.appendChild(kop);
        groepeerPerCategorie(filterOpNiveaus(stand, diagnostiekZichtbareNiveaus))
            .forEach(g => groep.appendChild(maakDiagnostiekCategorie(g, false, "upload0stand:")));
        laatste.appendChild(groep);
    }
    plek.appendChild(laatste);
    if (blokken.ouder.length === 0) return;
    const ouder = document.createElement("details");
    ouder.className = "diagnostiekOudereUploads";
    const kop = document.createElement("summary");
    kop.textContent = `Eerdere uploads (${blokken.ouder.length})`;
    ouder.appendChild(kop);
    blokken.ouder.forEach((blok, i) => ouder.appendChild(maakUploadSectie(blok, false, `upload${i + 1}:`)));
    plek.appendChild(ouder);
}

function gaNaarDiagnostiekBlok(regel) {
    diagnostiekOpenKeuze.set(regel.blokSleutel, true);
    if (!diagnostiekZichtbareNiveaus.includes(regel.niveau)) {
        diagnostiekZichtbareNiveaus = wisselNiveau(diagnostiekZichtbareNiveaus, regel.niveau);
    }
    toonDiagnostiek();
    const blok = Array.from(document.querySelectorAll("[data-blok]")).find(el => el.dataset.blok === regel.blokSleutel);
    if (blok) blok.scrollIntoView({ behavior: "smooth", block: "start" });
}

function toonActielijst() {
    const plek = document.getElementById("diagnostiekActies");
    plek.replaceChildren();
    const { regels, meer } = actielijst(diagnostiekMeldingen, diagnostiekUploads, toegestaneViews());
    if (regels.length === 0) return;
    const ul = document.createElement("ul");
    ul.className = "diagnostiekActielijst";
    regels.forEach(regel => {
        const li = document.createElement("li");
        li.className = "diagnostiekActieRegel";
        li.appendChild(maakNiveauIcoon(regel.niveau));
        const knop = document.createElement("button");
        knop.type = "button";
        knop.className = "diagnostiekActieTekst";
        knop.textContent = `${regel.categorie}: ${regel.tekst}`;
        knop.addEventListener("click", () => gaNaarDiagnostiekBlok(regel));
        li.appendChild(knop);
        if (regel.actie) {
            const ga = document.createElement("button");
            ga.type = "button";
            ga.className = "diagnostiekActie";
            ga.textContent = regel.actie.tekst;
            ga.addEventListener("click", () => gaNaarView(regel.actie.tab));
            li.appendChild(ga);
        }
        ul.appendChild(li);
    });
    plek.appendChild(ul);
    if (meer > 0) {
        const p = document.createElement("p");
        p.className = "gedempt diagnostiekActieMeer";
        p.textContent = `... en ${meer} meer`;
        plek.appendChild(p);
    }
}

function toonControleOverzicht(controles) {
    const plek = document.getElementById("diagnostiekControles");
    plek.replaceChildren();
    if (controles.length === 0) return;
    const details = document.createElement("details");
    details.className = "diagnostiekControleOverzicht";
    const kop = document.createElement("summary");
    kop.textContent = controleSamenvatting(controles);
    details.appendChild(kop);
    const ul = document.createElement("ul");
    ul.className = "diagnostiekControleLijst";
    controleRegels(controles).forEach(regel => {
        const li = document.createElement("li");
        li.textContent = `${regel.categorie}: ${regel.naam}, ${regel.tekst}`;
        ul.appendChild(li);
    });
    details.appendChild(ul);
    plek.appendChild(details);
}

function maakLegeCategorieSectie(lege) {
    const sectie = document.createElement("section");
    sectie.className = "diagnostiekSectie";
    const kop = document.createElement("h3");
    kop.className = "diagnostiekSectieKop";
    kop.textContent = "Zonder meldingen";
    sectie.appendChild(kop);
    lege.forEach(categorie => {
        const rij = document.createElement("p");
        rij.className = "diagnostiekLeeg";
        const naam = document.createElement("span");
        naam.className = "diagnostiekNaam";
        naam.textContent = categorie.categorie;
        const tekst = document.createElement("span");
        tekst.className = "gedempt";
        tekst.textContent = categorie.tekst;
        rij.append(naam, tekst);
        sectie.appendChild(rij);
    });
    return sectie;
}

function toonGeenCodeUitleg() {
    if (!huidigeData || huidigeData.code) return;
    const p = document.createElement("p");
    p.className = "gedempt";
    p.textContent = GEEN_CODE_UITLEG;
    document.getElementById("diagnostiekUploads").appendChild(p);
}

// Conclusie + filterchips bovenaan; op desktop links "Aandacht nodig", rechts "In orde" en "Technisch".
function toonDiagnostiek() {
    const teller = document.getElementById("diagnostiekTeller");
    const chips = document.getElementById("diagnostiekChips");
    const lijst = document.getElementById("diagnostiekLijst");
    lijst.replaceChildren();
    lijst.classList.remove("eenKolom");
    document.getElementById("diagnostiekUploads").replaceChildren();
    document.getElementById("diagnostiekActies").replaceChildren();
    document.getElementById("diagnostiekControles").replaceChildren();
    laadUploadMeldingen();

    const meldingen = zichtbareMeldingen(diagnostiekMeldingen, diagnostiekIsOntwikkelaar());
    const voorTelling = meldingenVoorTelling(meldingen, diagnostiekUploads);
    const controles = zichtbareControles(diagnostiekControles, diagnostiekIsOntwikkelaar());
    if (voorTelling.length === 0 && controles.length === 0) {
        teller.textContent = "Nog geen meldingen voor deze laadbeurt.";
        chips.replaceChildren();
        toonGeenCodeUitleg();
        return;
    }
    teller.textContent = diagnostiekConclusie(voorTelling);
    toonActielijst();
    toonControleOverzicht(controles);
    toonDiagnostiekChips(telPerNiveau(voorTelling));
    toonUploadMeldingen();
    toonGeenCodeUitleg();

    const blokken = deelInBlokken(filterOpNiveaus(meldingen, diagnostiekZichtbareNiveaus));
    // Met een eigen keuze van niveaus staat alles open: je hebt dan zelf gekozen wat je wilt zien.
    const metFilter = !isStandaardNiveauKeuze(diagnostiekZichtbareNiveaus);
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
    const lege = legeCategorieen(voorTelling, controles);
    if (lege.length > 0) rechts.appendChild(maakLegeCategorieSectie(lege));
    if (rechts.childElementCount > 0) lijst.appendChild(rechts);
    if (lijst.childElementCount === 0) {
        const leeg = document.createElement("p");
        leeg.className = "gedempt";
        leeg.textContent = "Geen meldingen met dit niveau.";
        lijst.appendChild(leeg);
    }
}
