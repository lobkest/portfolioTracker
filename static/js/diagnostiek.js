// Pure logica voor Instellingen > Diagnostiek: meldingen samenvoegen, tellen en groeperen.

(function (root) {
    "use strict";

    const { formatPct } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    // Ernstigste eerst -- zelfde niveaus als in diagnostiek.py.
    const DIAGNOSTIEK_NIVEAUS = ["FOUT", "LET_OP", "INFO", "GOED"];

    const DIAGNOSTIEK_NIVEAU_LABEL = {
        FOUT: "Fout",
        LET_OP: "Let op",
        INFO: "Info",
        GOED: "Goed",
    };

    const DIAGNOSTIEK_NIVEAU_ICOON = {
        FOUT: "✗",
        LET_OP: "⚠",
        INFO: "i",
        GOED: "✓",
    };

    // Technische meldingen staan altijd onderaan, onder een eigen groepsnaam.
    const TECHNISCHE_GROEP_LAADTIJDEN = "Laadtijden";
    const TECHNISCHE_GROEP_YAHOO = "Koersen (Yahoo-calls)";

    const SAMENVATTING_MAX_TEKENS = 120;
    // De backend bewaart er 5 (MAX_BEWAARDE_UPLOADS): de laatste plus 4 oudere.
    const MAX_OUDERE_UPLOADS = 4;

    function ernstIndex(niveau) {
        const i = DIAGNOSTIEK_NIVEAUS.indexOf(niveau);
        return i === -1 ? DIAGNOSTIEK_NIVEAUS.indexOf("INFO") : i;
    }

    function meldingSleutel(m) {
        return `${m.categorie}\u0000${m.sleutel}`;
    }

    // Ontdubbelt op categorie + sleutel; de nieuwste wint, op de plek van de oude.
    function voegMeldingenSamen(bestaand, nieuw) {
        const resultaat = (bestaand || []).slice();
        const positie = new Map(resultaat.map((m, i) => [meldingSleutel(m), i]));
        (nieuw || []).forEach(m => {
            const sleutel = meldingSleutel(m);
            if (positie.has(sleutel)) {
                resultaat[positie.get(sleutel)] = m;
            } else {
                positie.set(sleutel, resultaat.length);
                resultaat.push(m);
            }
        });
        return resultaat;
    }

    function telPerNiveau(meldingen) {
        const telling = {};
        DIAGNOSTIEK_NIVEAUS.forEach(n => { telling[n] = 0; });
        (meldingen || []).forEach(m => {
            telling[DIAGNOSTIEK_NIVEAUS[ernstIndex(m.niveau)]] += 1;
        });
        return telling;
    }

    // [{categorie, meldingen}], ernstigste eerst (binnen en tussen categorieën).
    function groepeerPerCategorie(meldingen) {
        const groepen = [];
        const perCategorie = new Map();
        (meldingen || []).forEach(m => {
            if (!perCategorie.has(m.categorie)) {
                const groep = { categorie: m.categorie, meldingen: [] };
                perCategorie.set(m.categorie, groep);
                groepen.push(groep);
            }
            perCategorie.get(m.categorie).meldingen.push(m);
        });
        groepen.forEach(g => {
            g.meldingen.sort((a, b) => ernstIndex(a.niveau) - ernstIndex(b.niveau));
        });
        const ernstigste = g => ernstIndex(g.meldingen[0].niveau);
        groepen.sort((a, b) => ernstigste(a) - ernstigste(b));
        return groepen;
    }

    // null bij een lege lijst; een onbekend niveau telt als INFO.
    function hoogsteNiveau(meldingen) {
        const lijst = meldingen || [];
        if (lijst.length === 0) return null;
        const ernstigste = Math.min(...lijst.map(m => ernstIndex(m.niveau)));
        return DIAGNOSTIEK_NIVEAUS[ernstigste];
    }

    function categorieStandaardOpen(meldingen) {
        const niveau = hoogsteNiveau(meldingen);
        return niveau === "FOUT" || niveau === "LET_OP";
    }

    // Bv. "1 fout, 2 let op"; lege string als er niets is.
    function diagnostiekTellerTekst(telling) {
        return DIAGNOSTIEK_NIVEAUS
            .filter(n => (telling[n] || 0) > 0)
            .map(n => `${telling[n]} ${DIAGNOSTIEK_NIVEAU_LABEL[n].toLowerCase()}`)
            .join(", ");
    }

    function normaalNiveau(niveau) {
        return DIAGNOSTIEK_NIVEAUS[ernstIndex(niveau)];
    }

    // De Yahoo-calls herken je aan de sleutel (yahoo_kern/yahoo_verrijking in yahoo_client.py); de rest van Koersen is inhoudelijk.
    function technischeGroep(m) {
        if (m.categorie === "Laadtijden") return TECHNISCHE_GROEP_LAADTIJDEN;
        if (m.categorie === "Koersen" && String(m.sleutel || "").startsWith("yahoo_")) return TECHNISCHE_GROEP_YAHOO;
        return null;
    }

    // {aandacht, inOrde, technisch}, elk [{categorie, meldingen}] zoals groepeerPerCategorie().
    function deelInBlokken(meldingen) {
        const gewoon = [];
        const technisch = [];
        (meldingen || []).forEach(m => {
            const groep = technischeGroep(m);
            if (groep) technisch.push(Object.assign({}, m, { categorie: groep }));
            else gewoon.push(m);
        });
        const groepen = groepeerPerCategorie(gewoon);
        return {
            aandacht: groepen.filter(g => categorieStandaardOpen(g.meldingen)),
            inOrde: groepen.filter(g => !categorieStandaardOpen(g.meldingen)),
            technisch: groepeerPerCategorie(technisch),
        };
    }

    // null = geen filter; een onbekend niveau telt als INFO, net als in telPerNiveau().
    function filterOpNiveau(meldingen, niveau) {
        const lijst = meldingen || [];
        if (!niveau) return lijst.slice();
        return lijst.filter(m => normaalNiveau(m.niveau) === niveau);
    }

    // Nog een keer op dezelfde chip zet het filter uit.
    function wisselNiveauFilter(huidig, aangeklikt) {
        return huidig === aangeklikt ? null : aangeklikt;
    }

    function meervoud(aantal, enkel, meer) {
        return `${aantal} ${aantal === 1 ? enkel : meer}`;
    }

    // Technische meldingen (bv. een trage laadtijd) tellen niet mee: die staan niet onder "Aandacht nodig".
    function diagnostiekConclusie(meldingen) {
        const telling = telPerNiveau((meldingen || []).filter(m => !technischeGroep(m)));
        const fouten = telling.FOUT;
        const punten = telling.LET_OP;
        const foutTekst = meervoud(fouten, "fout", "fouten") + " gevonden";
        const puntTekst = meervoud(punten, "punt", "punten") + " om naar te kijken";
        if (fouten > 0 && punten > 0) return `${foutTekst}, ${puntTekst}`;
        if (fouten > 0) return foutTekst;
        if (punten > 0) return puntTekst;
        return "Alles in orde";
    }

    // De tekst van de ernstigste melding (bij gelijk niveau de eerste), ingekort met een ellips.
    function categorieSamenvatting(meldingen, maxTekens = SAMENVATTING_MAX_TEKENS) {
        const lijst = meldingen || [];
        if (lijst.length === 0) return "";
        const ernstigste = lijst.reduce((beste, m) => (ernstIndex(m.niveau) < ernstIndex(beste.niveau) ? m : beste));
        const tekst = String(ernstigste.tekst || "");
        return tekst.length > maxTekens ? tekst.slice(0, maxTekens - 1).trimEnd() + "…" : tekst;
    }

    // null als label of tab ontbreekt.
    function meldingActie(melding) {
        const actie = melding && melding.actie;
        if (!actie || !actie.label || !actie.tab) return null;
        return { label: actie.label, tab: actie.tab, tekst: `→ Naar ${actie.label}` };
    }

    // Cellen als tekst; de kolom "Weging" (getal in %) met 1 decimaal, bv. "76,3%". null zonder rijen.
    function diagnostiekTabelRijen(tabel) {
        if (!tabel || !Array.isArray(tabel.rijen) || tabel.rijen.length === 0) return null;
        const kolommen = tabel.kolommen || [];
        const wegingIndex = kolommen.indexOf("Weging");
        const rijen = tabel.rijen.map(rij => rij.map((cel, i) => {
            if (i === wegingIndex && typeof cel === "number") return formatPct(cel, 1);
            return cel === null || cel === undefined ? "" : String(cel);
        }));
        return { kolommen, rijen };
    }

    // ISO-tijdstip -> "dd-mm-jjjj hh:mm" in lokale tijd; "" als het geen geldige datum is.
    function formatUploadMoment(iso) {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return "";
        const tweeCijfers = n => String(n).padStart(2, "0");
        return `${tweeCijfers(d.getDate())}-${tweeCijfers(d.getMonth() + 1)}-${d.getFullYear()} `
            + `${tweeCijfers(d.getHours())}:${tweeCijfers(d.getMinutes())}`;
    }

    function uploadTitel(upload, voorvoegsel) {
        const bijwerken = upload.soort === "bijwerken" ? ", bestanden bijgewerkt" : "";
        return `${voorvoegsel} (${formatUploadMoment(upload.geupload_op)}${bijwerken})`;
    }

    // uploads nieuwste eerst, zoals /upload-meldingen. {laatste: {titel, meldingen, aantalOokLive} of null,
    // ouder: [{titel, meldingen}]}; uit de laatste upload valt weg wat ook live bestaat (de live versie wint).
    function uploadBlokken(uploads, live) {
        const lijst = uploads || [];
        if (lijst.length === 0) return { laatste: null, ouder: [] };
        const liveSleutels = new Set((live || []).map(meldingSleutel));
        const [laatste, ...ouder] = lijst;
        const alle = laatste.meldingen || [];
        const eigen = alle.filter(m => !liveSleutels.has(meldingSleutel(m)));
        return {
            laatste: { titel: uploadTitel(laatste, "Laatste upload"), meldingen: eigen, aantalOokLive: alle.length - eigen.length },
            ouder: ouder.slice(0, MAX_OUDERE_UPLOADS).map(u => ({ titel: uploadTitel(u, "Eerdere upload"), meldingen: u.meldingen || [] })),
        };
    }

    // Voor de conclusiezin en de chips: live plus de laatste upload; bij dezelfde categorie + sleutel wint live.
    function meldingenVoorTelling(live, uploads) {
        const laatste = (uploads && uploads[0] && uploads[0].meldingen) || [];
        return voegMeldingenSamen(laatste, live);
    }

    function uploadOokLiveTekst(aantal) {
        return `${meervoud(aantal, "melding", "meldingen")} van deze upload ${aantal === 1 ? "staat" : "staan"} `
            + "hierboven, met de stand van nu.";
    }

    const exportsObj = {
        DIAGNOSTIEK_NIVEAUS, DIAGNOSTIEK_NIVEAU_LABEL, DIAGNOSTIEK_NIVEAU_ICOON,
        normaalNiveau, deelInBlokken, filterOpNiveau, wisselNiveauFilter, diagnostiekConclusie,
        categorieSamenvatting, meldingActie,
        voegMeldingenSamen, telPerNiveau, groepeerPerCategorie, diagnostiekTellerTekst,
        hoogsteNiveau, categorieStandaardOpen, diagnostiekTabelRijen,
        formatUploadMoment, uploadBlokken, meldingenVoorTelling, uploadOokLiveTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
