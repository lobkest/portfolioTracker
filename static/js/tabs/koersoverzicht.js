// Tabblad Koersen: koersontwikkeling van alle huidige posities in één grafiek, met ranglijst.

let koersPeriode = STANDAARD_KOERS_PERIODE;
let koersWeergave = "pct";
let koersData = null;
let koersOphaalNr = 0;

function resetKoersoverzicht() {
    koersPeriode = STANDAARD_KOERS_PERIODE;
    koersWeergave = "pct";
    koersData = null;
    koersOphaalNr++;
}

function bouwKoersPeriodeKnoppen() {
    const keuze = document.getElementById("koersPeriodeKeuze");
    if (keuze.childElementCount > 0) return;
    keuze.replaceChildren(...KOERS_PERIODES.map(p => {
        const knop = document.createElement("button");
        knop.type = "button";
        knop.className = "segmentKnop";
        knop.dataset.periode = p.id;
        knop.textContent = p.label;
        knop.addEventListener("click", () => {
            koersPeriode = p.id;
            haalKoersoverzichtOp();
        });
        return knop;
    }));
}

function werkKoersKnoppenBij() {
    document.querySelectorAll("#koersPeriodeKeuze .segmentKnop").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.periode === koersPeriode);
    });
    document.querySelectorAll("#koersWeergaveKeuze .segmentKnop").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.weergave === koersWeergave);
    });
}

function toonKoersMelding(tekst, isFout) {
    const msg = document.getElementById("koersMelding");
    msg.textContent = tekst || "";
    msg.classList.toggle("foutTekst", Boolean(isFout));
    msg.style.display = tekst ? "block" : "none";
}

function toonKoersoverzicht() {
    if (chart) { chart.destroy(); chart = null; }
    bouwKoersPeriodeKnoppen();
    werkKoersKnoppenBij();
    if (koersData) tekenKoersoverzicht();
    else haalKoersoverzichtOp();
}

async function haalKoersoverzichtOp() {
    const nr = ++koersOphaalNr;
    werkKoersKnoppenBij();
    toonKoersMelding("");
    toonLaadOverlay("Koersen ophalen...");
    let res, data;
    try {
        res = await fetchMetTimeout(`/api/portfolio/${huidigeData.code}/koersoverzicht?periode=${koersPeriode}`);
        data = await res.json();
    } catch (e) {
        if (nr === koersOphaalNr) toonKoersMelding("Kon de koersen niet ophalen (netwerkfout).", true);
        return;
    } finally {
        verbergLaadOverlay();
    }
    if (nr !== koersOphaalNr) return;
    if (!res.ok) {
        toonKoersMelding(data.error || "De koersen konden niet opgehaald worden.", true);
        return;
    }
    koersData = data;
    tekenKoersoverzicht();
}

function tekenKoersoverzicht() {
    if (chart) { chart.destroy(); chart = null; }
    const d = koersData;
    const procent = koersWeergave === "pct";
    const labelsNL = d.labels.map(formatDatum);
    const formatteer = procent ? (x => formatPct(x, 2, true)) : (x => formatteerEuro(x));

    const datasets = d.reeksen.map(reeks => ({
        label: reeks.naam,
        data: procent ? reeks.pct : reeks.eur,
        borderColor: kleurVoorTicker(reeks.ticker),
        pointRadius: 0,
        pointHoverRadius: 4,
        borderWidth: 1.5,
        spanGaps: false,
        segment: { borderDash: ctx => isGestippeld(reeks.in_bezit, ctx.p1DataIndex) ? [4, 4] : undefined },
        inBezit: reeks.in_bezit,
    }));

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "line",
        data: { labels: labelsNL, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            locale: "nl-NL",
            interaction: { mode: "nearest", axis: "x", intersect: false },
            scales: {
                y: { title: { display: true, text: procent ? "Verandering (%)" : "Koers (€)" } },
            },
            plugins: {
                legend: { display: true },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const bezit = ctx.dataset.inBezit[ctx.dataIndex] ? "in bezit" : "niet in bezit";
                            return `${ctx.dataset.label}: ${formatteer(ctx.parsed.y)} (${bezit})`;
                        },
                    },
                },
                zoom: zoomOpties(),
                datalabels: { display: false },
                annotation: {
                    annotations: procent
                        ? { nulLijn: { type: "line", yMin: 0, yMax: 0, borderColor: "#6b6b66", borderWidth: 1 } }
                        : {},
                },
            },
        },
    });

    toonKoersRanglijst(d);
}

function toonKoersRanglijst(d) {
    const kolommen = [
        {
            label: "Positie",
            mobielRol: "titel",
            mobielTekst: r => r.naam,
            renderTd: r => maakCel(r.naam),
        },
        {
            label: "Verandering in de periode",
            mobielRol: "waarde",
            waarde: r => r.pct,
            renderTd: r => {
                const td = maakCel(r.pct === null ? "–" : formatPct(r.pct, 2, true));
                td.className = `rendementCel ${klasseVoorRendement(r.pct)}`;
                return td;
            },
        },
        { label: "Ticker", alleenMobiel: true, renderTd: r => maakCel(r.ticker) },
    ];
    document.getElementById("koersRanglijst").replaceChildren(
        maakSorteerbareTabel(kolommen, ranglijstRijen(d.reeksen), { legeTekst: "Geen huidige posities.", compactOpMobiel: true }));

    const ontbreekt = d.ontbreken.map(o => o.naam);
    toonKoersMelding(ontbreekt.length ? `Geen koers beschikbaar voor: ${ontbreekt.join(", ")}.` : "");
}

document.querySelectorAll("#koersWeergaveKeuze .segmentKnop").forEach(knop => {
    knop.addEventListener("click", () => {
        koersWeergave = knop.dataset.weergave;
        werkKoersKnoppenBij();
        if (koersData) tekenKoersoverzicht();
    });
});
