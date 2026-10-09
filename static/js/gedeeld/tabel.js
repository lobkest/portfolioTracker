// De tabelbouwer en zijn cellen. Sorteren en pagineren van Transacties komt uit transacties.js.

function maakCel(tekst) {
    const td = document.createElement("td");
    td.textContent = tekst;
    return td;
}

// "€ (pct%)", groen/rood op het €-bedrag.
function maakRendementCel(eurWaarde, pctWaarde) {
    const td = maakCel(`${formatteerEuro(eurWaarde)} (${formatPct(pctWaarde)})`);
    td.className = `rendementCel ${klasseVoorRendement(eurWaarde)}`;
    return td;
}

// kolommen: [{label, sleutel?, waarde?: rij => getal|null, renderTd: rij => td}].
// Zonder opts.staat: begint in de aangeleverde volgorde; een kolom met 'waarde' is sorteerbaar
// (eerste klik aflopend, null altijd onderaan).
// opts.staat {sorteerKolom, sorteerRichting, pagina, paginaGrootte}: van de aanroeper, zodat de keuze
// een tabwissel overleeft; met paginaGrootte komt er paginanavigatie onder de tabel.
// opts.sorteer(rijen, sleutel, richting): eigen sortering; elke kolom met een sleutel is dan sorteerbaar.
// opts.klasse: CSS-klasse van de tabel (standaard dataTabel). opts.rijKlasse: rij => klasse|null, voor <tr> én de mobiele rij.
// kol.uitleg: tooltip op de kolomkop.
// opts.compactOpMobiel: op mobiel (CSS) i.p.v. de tabel een compacte rij per positie die uitklapt, plus een
// "Sorteer op"-keuze i.p.v. de kolomkoppen; die komt in opts.sorteerPlek als die er is, anders boven de rijen.
// Per kolom dan: mobielRol (zie MOBIEL_ROLLEN, standaard "detail"), mobielTekst: rij => tekst voor titel en
// subregel, alleenMobiel: niet in de tabel, alleenTabel: niet in de mobiele rij. Een detail zonder inhoud valt weg.
const MOBIEL_ROLLEN = ["titel", "subregel", "waarde", "subwaarde", "detail"];

function mobielIndeling(kolommen) {
    const indeling = Object.fromEntries(MOBIEL_ROLLEN.map(rol => [rol, []]));
    kolommen.forEach(kol => {
        if (kol.alleenTabel) return;
        const rol = kol.mobielRol || "detail";
        if (!indeling[rol]) throw new Error(`Onbekende mobielRol: ${rol}`);
        indeling[rol].push(kol);
    });
    return indeling;
}

function mobielTekst(kol, rij) {
    return kol.mobielTekst ? kol.mobielTekst(rij) : kol.renderTd(rij).textContent;
}

function voegSubregelSamen(teksten) {
    return teksten.filter(t => t !== null && t !== undefined && t !== "").join(" · ");
}

// Inhoud van een td in een span, met dezelfde klassen (kleur van het rendement).
function celAlsSpan(kol, rij, klasse) {
    const td = kol.renderTd(rij);
    const span = document.createElement("span");
    span.className = `${klasse} ${td.className}`.trim();
    span.append(...td.childNodes);
    return span;
}

function maakMobieleRij(indeling, rij, klasse) {
    const knop = document.createElement("button");
    knop.type = "button";
    knop.className = indeling.waarde.length ? "mobielRij" : "mobielRij mobielRijZonderWaarde";
    knop.setAttribute("aria-expanded", "false");

    const titel = document.createElement("span");
    titel.className = "mobielTitel";
    titel.textContent = indeling.titel.map(kol => mobielTekst(kol, rij)).join(" ");
    const subregel = document.createElement("span");
    subregel.className = "mobielSubregel";
    subregel.textContent = voegSubregelSamen(indeling.subregel.map(kol => mobielTekst(kol, rij)));
    knop.append(titel, subregel);
    indeling.waarde.forEach(kol => knop.appendChild(celAlsSpan(kol, rij, "mobielWaarde")));
    indeling.subwaarde.forEach(kol => knop.appendChild(celAlsSpan(kol, rij, "mobielSubwaarde")));

    const details = document.createElement("div");
    details.className = "mobielDetails";
    details.hidden = true;
    indeling.detail.forEach(kol => {
        const waarde = celAlsSpan(kol, rij, "mobielDetailWaarde");
        if (!waarde.childNodes.length) return;
        const label = document.createElement("span");
        label.className = "mobielDetailLabel";
        label.textContent = kol.label;
        details.append(label, waarde);
    });

    knop.addEventListener("click", () => {
        const open = knop.getAttribute("aria-expanded") !== "true";
        knop.setAttribute("aria-expanded", String(open));
        details.hidden = !open;
    });

    const li = document.createElement("li");
    if (klasse) li.className = klasse;
    li.append(knop, details);
    return li;
}

function maakSorteerbareTabel(kolommen, rijen, opts) {
    opts = opts || {};
    if (!rijen || rijen.length === 0) {
        if (opts.sorteerPlek) opts.sorteerPlek.replaceChildren();
        const p = document.createElement("p");
        p.className = "grijsTekst";
        p.textContent = opts.legeTekst || "Geen data beschikbaar.";
        return p;
    }

    const staat = opts.staat || { sorteerKolom: null, sorteerRichting: "desc" };

    const tabel = document.createElement("table");
    tabel.className = opts.klasse || "dataTabel";
    if (opts.compactOpMobiel) tabel.classList.add("alleenBreed");
    const thead = document.createElement("thead");
    const kopRij = document.createElement("tr");
    const tbody = document.createElement("tbody");
    const koppen = [];

    const nav = staat.paginaGrootte ? document.createElement("div") : null;
    if (nav) nav.className = "paginaNavigatie";

    const heeftSorteerbareKolom = kolommen.some(kol => !kol.alleenMobiel && isSorteerbaar(kol));
    const sorteerSelect = opts.compactOpMobiel && heeftSorteerbareKolom ? document.createElement("select") : null;
    const indeling = opts.compactOpMobiel ? mobielIndeling(kolommen) : null;
    const mobieleLijst = indeling ? document.createElement("ul") : null;
    if (mobieleLijst) mobieleLijst.className = "mobieleLijst";
    const tabelKolommen = kolommen.filter(kol => !kol.alleenMobiel);
    const rijKlasse = rij => (opts.rijKlasse ? opts.rijKlasse(rij) : null);

    function isSorteerbaar(kol) {
        return Boolean(kol.waarde || (opts.sorteer && kol.sleutel));
    }

    function sorteer() {
        if (staat.sorteerKolom === null) return rijen;
        if (opts.sorteer) return opts.sorteer(rijen, staat.sorteerKolom, staat.sorteerRichting);
        const kol = kolommen[staat.sorteerKolom];
        const metWaarde = [];
        const zonderWaarde = [];
        rijen.forEach(rij => {
            const w = kol.waarde(rij);
            (w === null || w === undefined ? zonderWaarde : metWaarde).push(rij);
        });
        metWaarde.sort((a, b) => {
            const wa = kol.waarde(a), wb = kol.waarde(b);
            return staat.sorteerRichting === "asc" ? wa - wb : wb - wa;
        });
        return metWaarde.concat(zonderWaarde);
    }

    function gaNaarPagina(pagina) {
        staat.pagina = pagina;
        teken();
    }

    function maakPaginaKnop(tekst, pagina, uit) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = tekst;
        btn.disabled = uit;
        btn.addEventListener("click", () => gaNaarPagina(pagina));
        return btn;
    }

    function tekenPaginaNavigatie(totPag) {
        const knoppen = [maakPaginaKnop("Vorige", staat.pagina - 1, staat.pagina === 1)];
        for (let p = 1; p <= totPag; p++) {
            const btn = maakPaginaKnop(String(p), p, p === staat.pagina);
            if (p === staat.pagina) btn.className = "actief";
            knoppen.push(btn);
        }
        knoppen.push(maakPaginaKnop("Volgende", staat.pagina + 1, staat.pagina === totPag));
        nav.replaceChildren(...knoppen);
    }

    function teken() {
        koppen.forEach(({ th, label, sleutel }) => {
            const indicator = staat.sorteerKolom === sleutel ? (staat.sorteerRichting === "asc" ? " ▲" : " ▼") : "";
            th.textContent = label + indicator;
        });
        if (sorteerSelect) {
            sorteerSelect.value = staat.sorteerKolom === null ? "" : `${staat.sorteerKolom}|${staat.sorteerRichting}`;
        }

        let getoond = sorteer();
        if (nav) {
            const totPag = totaalPaginas(getoond.length, staat.paginaGrootte);
            if (staat.pagina > totPag) staat.pagina = totPag;
            getoond = pagineer(getoond, staat.paginaGrootte, staat.pagina);
            tekenPaginaNavigatie(totPag);
        }

        tbody.replaceChildren(...getoond.map(rij => {
            const tr = document.createElement("tr");
            const klasse = rijKlasse(rij);
            if (klasse) tr.className = klasse;
            tabelKolommen.forEach(kol => tr.appendChild(kol.renderTd(rij)));
            return tr;
        }));
        if (mobieleLijst) mobieleLijst.replaceChildren(...getoond.map(rij => maakMobieleRij(indeling, rij, rijKlasse(rij))));
    }

    kolommen.forEach((kol, index) => {
        if (kol.alleenMobiel) return;
        const th = document.createElement("th");
        const sleutel = kol.sleutel ?? index;
        const sorteerbaar = isSorteerbaar(kol);
        if (kol.uitleg) th.title = kol.uitleg;
        if (sorteerbaar) {
            th.className = "sorteerbaar";
            th.addEventListener("click", () => {
                if (staat.sorteerKolom === sleutel) {
                    staat.sorteerRichting = staat.sorteerRichting === "asc" ? "desc" : "asc";
                } else {
                    staat.sorteerKolom = sleutel;
                    staat.sorteerRichting = "desc";
                }
                if (nav) staat.pagina = 1;
                teken();
            });
        }
        kopRij.appendChild(th);
        koppen.push({ th, label: kol.label, sleutel, sorteerbaar });
    });

    const sorteerKeuze = sorteerSelect ? maakSorteerKeuze() : null;

    thead.appendChild(kopRij);
    tabel.appendChild(thead);
    tabel.appendChild(tbody);
    teken();

    // Wrapper: op smalle schermen scrolt de tabel zelf, niet de hele pagina.
    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";
    wrapper.appendChild(tabel);
    if (!nav && !sorteerKeuze && !mobieleLijst) return wrapper;

    const geheel = document.createElement("div");
    if (sorteerKeuze && opts.sorteerPlek) {
        opts.sorteerPlek.replaceChildren(sorteerKeuze);
    } else if (sorteerKeuze) {
        geheel.appendChild(sorteerKeuze);
    }
    geheel.appendChild(wrapper);
    if (mobieleLijst) geheel.appendChild(mobieleLijst);
    if (nav) geheel.appendChild(nav);
    return geheel;

    // Waarde "<sleutel>|<richting>"; zet dezelfde staat als een klik op de kolomkop.
    function maakSorteerKeuze() {
        const opties = [new Option("Standaardvolgorde", "")];
        koppen.forEach(({ label, sleutel, sorteerbaar }) => {
            if (!sorteerbaar) return;
            opties.push(new Option(`${label} (hoog → laag)`, `${sleutel}|desc`));
            opties.push(new Option(`${label} (laag → hoog)`, `${sleutel}|asc`));
        });
        sorteerSelect.replaceChildren(...opties);
        sorteerSelect.addEventListener("change", () => {
            if (sorteerSelect.value === "") {
                staat.sorteerKolom = null;
            } else {
                const [sleutel, richting] = sorteerSelect.value.split("|");
                const kop = koppen.find(k => String(k.sleutel) === sleutel);
                staat.sorteerKolom = kop.sleutel;
                staat.sorteerRichting = richting;
            }
            if (nav) staat.pagina = 1;
            teken();
        });

        const label = document.createElement("label");
        label.className = "sorteerKeuze";
        label.append("Sorteer op ", sorteerSelect);
        return label;
    }
}

if (typeof module !== "undefined") module.exports = { maakSorteerbareTabel, maakCel, mobielIndeling, voegSubregelSamen, MOBIEL_ROLLEN };
