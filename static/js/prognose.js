// Pure logica voor de Prognose (zonder DOM, getest onder Node).
// Maandelijks samengesteld; inleg komt ná de groei van die periode.

(function (root) {
    "use strict";

    // Geometrisch, niet /12: over 12 maanden komt dan precies het jaarrendement uit.
    function maandRenteVanJaarPct(rendementPct) {
        return Math.pow(1 + rendementPct / 100, 1 / 12) - 1;
    }

    // Index 0 = startWaarde, index i = waarde na i maanden.
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

    // Niet herbelegd: elke maand 1/12 van de jaaryield over de waarde aan het begin van die maand, zonder groei.
    function berekenDividendCumulatief(waardePad, yieldNettoJaar) {
        const punten = [0];
        for (let m = 1; m < waardePad.length; m++) {
            punten.push(punten[m - 1] + waardePad[m - 1] * yieldNettoJaar / 12);
        }
        return punten;
    }

    // Begint bij vanafIso + 1 maand. UTC, anders kan de tijdzone een dag verschuiven.
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

    // opties.metInleg = false: zonder inlegvelden (tabblad Huidige portfolio).
    function valideerPrognoseInvoer(input, opties) {
        const metInleg = !opties || opties.metInleg !== false;
        const { jaren, rendementPct, laagPct, hoogPct, jaarlijkseInleg, maandelijkseInleg } = input;
        const fouten = [];

        if (!Number.isFinite(jaren) || jaren < 0 || jaren > JAREN_MAX) {
            fouten.push(`Aantal jaren vooruit moet tussen 0 en ${JAREN_MAX} liggen.`);
        }
        [
            ["Verwacht koersrendement", rendementPct],
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
        if (metInleg && (!Number.isFinite(jaarlijkseInleg) || jaarlijkseInleg < 0)) {
            fouten.push("Jaarlijkse inleg moet een positief bedrag zijn.");
        }
        if (metInleg && (!Number.isFinite(maandelijkseInleg) || maandelijkseInleg < 0)) {
            fouten.push("Maandelijkse inleg moet een positief bedrag zijn.");
        }

        let waarschuwing = null;
        if (
            fouten.length === 0 &&
            (rendementPct < laagPct || rendementPct > hoogPct)
        ) {
            waarschuwing = "Het verwachte koersrendement valt buiten de opgegeven bandbreedte.";
        }

        return { geldig: fouten.length === 0, fouten, waarschuwing };
    }

    // chartData als parameter (niet globaal), zodat nooit data van een vorige portfolio meekomt.
    // {x, y}-punten voor de tijd-as; historie en prognose delen het laatste punt.
    // invoer.dividendYield (netto, fractie per jaar): optioneel, geeft een extra lijn waarde + cumulatief dividend.
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
        // pad[0] hoort bij laatsteDatum, zodat de lijn aansluit op historischPad.
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

        if (typeof invoer.dividendYield === "number" && Number.isFinite(invoer.dividendYield)) {
            const cumulatief = berekenDividendCumulatief(prognose.midden, invoer.dividendYield);
            datasets.splice(2, 0, {
                label: "Waarde + verwacht dividend, niet herbelegd (€)",
                data: toekomstPad(prognose.midden.map((w, m) => w + cumulatief[m])),
                borderColor: "#b7791f",
                borderDash: [3, 3]
            });
        }

        return { datasets };
    }

    const exportsObj = {
        maandRenteVanJaarPct,
        berekenPrognosePad,
        berekenGeinvesteerdPad,
        berekenPrognose,
        berekenDividendCumulatief,
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
