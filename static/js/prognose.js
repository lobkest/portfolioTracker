// Rekenkern voor het Prognose-tabblad. Puur JS, geen DOM/Chart.js-afhankelijkheden,
// zodat dit zowel in de browser (index.html) als onder Node (tests/test_prognose.js)
// draait — vandaar de module.exports-omweg onderaan i.p.v. een <script type=module>.
//
// Aanname (bevestigd door gebruiker): rendement wordt MAANDELIJKS samengesteld.
// De maandrente wordt geometrisch afgeleid uit het jaarrendement
// ((1+jaarrendement)^(1/12) - 1) i.p.v. lineair gedeeld door 12 — dat geeft bij
// samenstelling over exact 12 maanden weer precies het opgegeven jaarrendement
// terug (bij lineair delen door 12 zou het effectieve jaarrendement door het
// samengesteld-effect net iets hoger uitvallen dan ingevuld). Inleg (jaarlijks
// en maandelijks) wordt telkens NA de groei van die periode toegevoegd.

(function (root) {
    "use strict";

    function maandRenteVanJaarPct(rendementPct) {
        return Math.pow(1 + rendementPct / 100, 1 / 12) - 1;
    }

    // Waarde-pad met samengestelde groei + inleg, per maand, inclusief startpunt
    // (index 0 = startWaarde, index i = waarde na i maanden).
    function berekenPrognosePad(startWaarde, rendementPct, jaren, jaarlijkseInleg, maandelijkseInleg) {
        const maandRente = maandRenteVanJaarPct(rendementPct);
        const totaalMaanden = Math.round(jaren * 12);
        const punten = [startWaarde];
        let waarde = startWaarde;
        for (let m = 1; m <= totaalMaanden; m++) {
            waarde = waarde * (1 + maandRente) + maandelijkseInleg;
            if (m % 12 === 0) {
                waarde += jaarlijkseInleg;
            }
            punten.push(waarde);
        }
        return punten;
    }

    // Cumulatief geïnvesteerd bedrag, lineair (geen rendement), zelfde inleg-ritme.
    function berekenGeinvesteerdPad(startGeinvesteerd, jaren, jaarlijkseInleg, maandelijkseInleg) {
        const totaalMaanden = Math.round(jaren * 12);
        const punten = [startGeinvesteerd];
        let geinvesteerd = startGeinvesteerd;
        for (let m = 1; m <= totaalMaanden; m++) {
            geinvesteerd += maandelijkseInleg;
            if (m % 12 === 0) {
                geinvesteerd += jaarlijkseInleg;
            }
            punten.push(geinvesteerd);
        }
        return punten;
    }

    function berekenPrognose(input) {
        const {
            startWaarde, startGeinvesteerd, jaren,
            rendementPct, laagPct, hoogPct,
            jaarlijkseInleg, maandelijkseInleg
        } = input;

        return {
            midden: berekenPrognosePad(startWaarde, rendementPct, jaren, jaarlijkseInleg, maandelijkseInleg),
            laag: berekenPrognosePad(startWaarde, laagPct, jaren, jaarlijkseInleg, maandelijkseInleg),
            hoog: berekenPrognosePad(startWaarde, hoogPct, jaren, jaarlijkseInleg, maandelijkseInleg),
            geinvesteerd: berekenGeinvesteerdPad(startGeinvesteerd, jaren, jaarlijkseInleg, maandelijkseInleg)
        };
    }

    // Maandelijkse toekomst-datums ná vanafIso ("YYYY-MM-DD"), dus zonder vanafIso
    // zelf — index 0 van het resultaat is vanafIso + 1 maand. UTC om te voorkomen
    // dat de tijdzone van de browser een dag laat verschuiven.
    function genereerToekomstDatums(vanafIso, aantalMaanden) {
        const basis = new Date(vanafIso + "T00:00:00Z");
        const datums = [];
        for (let m = 1; m <= aantalMaanden; m++) {
            const d = new Date(Date.UTC(basis.getUTCFullYear(), basis.getUTCMonth() + m, basis.getUTCDate()));
            datums.push(d.toISOString().slice(0, 10));
        }
        return datums;
    }

    const RENDEMENT_MIN_PCT = -20;
    const RENDEMENT_MAX_PCT = 30;
    const JAREN_MAX = 60;

    function valideerPrognoseInvoer(input) {
        const { jaren, rendementPct, laagPct, hoogPct, jaarlijkseInleg, maandelijkseInleg } = input;
        const fouten = [];

        if (!Number.isFinite(jaren) || jaren < 0 || jaren > JAREN_MAX) {
            fouten.push(`Aantal jaren vooruit moet tussen 0 en ${JAREN_MAX} liggen.`);
        }
        [
            ["Verwacht rendement", rendementPct],
            ["Lage kant van de bandbreedte", laagPct],
            ["Hoge kant van de bandbreedte", hoogPct]
        ].forEach(([naam, waarde]) => {
            if (!Number.isFinite(waarde) || waarde < RENDEMENT_MIN_PCT || waarde > RENDEMENT_MAX_PCT) {
                fouten.push(`${naam} moet tussen ${RENDEMENT_MIN_PCT}% en ${RENDEMENT_MAX_PCT}% liggen.`);
            }
        });
        if (Number.isFinite(laagPct) && Number.isFinite(hoogPct) && laagPct >= hoogPct) {
            fouten.push("De lage kant van de bandbreedte moet kleiner zijn dan de hoge kant.");
        }
        if (!Number.isFinite(jaarlijkseInleg) || jaarlijkseInleg < 0) {
            fouten.push("Jaarlijkse inleg moet een positief bedrag zijn.");
        }
        if (!Number.isFinite(maandelijkseInleg) || maandelijkseInleg < 0) {
            fouten.push("Maandelijkse inleg moet een positief bedrag zijn.");
        }

        let waarschuwing = null;
        if (
            fouten.length === 0 &&
            (rendementPct < laagPct || rendementPct > hoogPct)
        ) {
            waarschuwing = "Het verwachte rendement valt buiten de opgegeven bandbreedte.";
        }

        return { geldig: fouten.length === 0, fouten, waarschuwing };
    }

    // Bouwt uit historische data (chartData, = de chart_data van de op dat
    // moment actieve portfolio) + berekenPrognose() de labels/datasets voor de
    // gedeelde rendementChart-canvas. Neemt chartData expliciet als parameter
    // i.p.v. een globale variabele te lezen, zodat dit voor twee verschillende
    // portfolio's aantoonbaar verschillende resultaten geeft (zie
    // tests/test_prognose.js) en nooit per ongeluk data van een eerder geladen
    // portfolio kan hergebruiken.
    // Verleden- en toekomst-reeksen delen het laatste historische punt
    // (index H-1) zodat de lijnen zonder gat op elkaar aansluiten.
    // Datasets als {x, y}-punten (x = ISO-datumstring) i.p.v. een gedeelde
    // labels-array — nodig voor de echte tijd-as in de browser.
    function bouwPrognoseGrafiekData(chartData, invoer) {
        const d = chartData;
        const H = d.labels.length;
        const laatsteDatum = d.labels[H - 1];
        const startWaarde = d.waarde[H - 1];
        const startGeinvesteerd = d.geinvesteerd[H - 1];

        const prognose = berekenPrognose({
            startWaarde, startGeinvesteerd,
            jaren: invoer.jaren,
            rendementPct: invoer.rendement,
            laagPct: invoer.laag,
            hoogPct: invoer.hoog,
            jaarlijkseInleg: invoer.jaarlijks,
            maandelijkseInleg: invoer.maandelijks
        });

        const totaalMaanden = Math.round(invoer.jaren * 12);
        const toekomstDatums = genereerToekomstDatums(laatsteDatum, totaalMaanden);

        const historischPad = (reeks) => d.labels.map((iso, i) => ({ x: iso, y: reeks[i] }));
        // pad[0] is het startpunt (== laatsteDatum), dus die slaan we hier over en
        // beginnen bij het boundary-punt zelf om aan te sluiten op historischPad.
        const toekomstPad = (pad) =>
            [{ x: laatsteDatum, y: pad[0] }].concat(toekomstDatums.map((iso, i) => ({ x: iso, y: pad[i + 1] })));

        const datasets = [
            { label: "Waarde (€)", data: historischPad(d.waarde), borderColor: "#2c7a4b" },
            { label: "Waarde — prognose (€)", data: toekomstPad(prognose.midden), borderColor: "#2c7a4b", borderDash: [6, 4] },
            { label: "Bandbreedte (hoog)", data: toekomstPad(prognose.hoog), borderColor: "rgba(44, 122, 75, 0.35)", borderDash: [2, 3], fill: false, _verbergInLegenda: true },
            { label: "Bandbreedte laag–hoog", data: toekomstPad(prognose.laag), borderColor: "rgba(44, 122, 75, 0.35)", borderDash: [2, 3], backgroundColor: "rgba(44, 122, 75, 0.15)", fill: "-1" },
            { label: "Geïnvesteerd (€)", data: historischPad(d.geinvesteerd), borderColor: "#3182bd" },
            { label: "Geïnvesteerd — prognose (€)", data: toekomstPad(prognose.geinvesteerd), borderColor: "#3182bd", borderDash: [6, 4] }
        ];

        return { datasets };
    }

    const exportsObj = {
        maandRenteVanJaarPct,
        berekenPrognosePad,
        berekenGeinvesteerdPad,
        berekenPrognose,
        genereerToekomstDatums,
        valideerPrognoseInvoer,
        bouwPrognoseGrafiekData
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
