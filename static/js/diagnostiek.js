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
    const MAX_ACTIELIJST_REGELS = 5;
    const NIVEAUS_MET_ACTIE = ["FOUT", "LET_OP"];
    // "Goed" is vooral ruis zolang alles klopt; de chip toont wel het aantal.
    const STANDAARD_ZICHTBARE_NIVEAUS = ["FOUT", "LET_OP", "INFO"];
    const UPLOAD_BLOK_VOORVOEGSEL = "upload0:";
    const GOED_CHIP_UITLEG = "Goed-meldingen staan standaard verborgen. Klik om ze te tonen of weer te verbergen.";
    const GEEN_CODE_UITLEG = "Je hebt niet opgeslagen, dus er zijn geen uploadmeldingen bewaard. "
        + "Je ziet alleen de meldingen van nu.";

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

    // Een onbekend niveau telt als INFO, net als in telPerNiveau().
    function filterOpNiveaus(meldingen, zichtbaar) {
        return (meldingen || []).filter(m => zichtbaar.includes(normaalNiveau(m.niveau)));
    }

    // Klik op een chip zet dat niveau aan of uit; de volgorde blijft die van DIAGNOSTIEK_NIVEAUS.
    function wisselNiveau(zichtbaar, aangeklikt) {
        const nieuw = zichtbaar.includes(aangeklikt) ? zichtbaar.filter(n => n !== aangeklikt) : [...zichtbaar, aangeklikt];
        return DIAGNOSTIEK_NIVEAUS.filter(n => nieuw.includes(n));
    }

    function isStandaardNiveauKeuze(zichtbaar) {
        return zichtbaar.length === STANDAARD_ZICHTBARE_NIVEAUS.length
            && STANDAARD_ZICHTBARE_NIVEAUS.every(n => zichtbaar.includes(n));
    }

    // Technische meldingen (Laadtijden, Yahoo-calls) zijn alleen voor de ontwikkelaar.
    function zichtbareMeldingen(meldingen, ontwikkelaar) {
        const lijst = meldingen || [];
        return ontwikkelaar === true ? lijst.slice() : lijst.filter(m => !technischeGroep(m));
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

    function kortTekst(tekst, maxTekens) {
        const hele = String(tekst || "");
        return hele.length > maxTekens ? hele.slice(0, maxTekens - 1).trimEnd() + "…" : hele;
    }

    // De tekst van de ernstigste melding (bij gelijk niveau de eerste), ingekort met een ellips.
    function categorieSamenvatting(meldingen, maxTekens = SAMENVATTING_MAX_TEKENS) {
        const lijst = meldingen || [];
        if (lijst.length === 0) return "";
        const ernstigste = lijst.reduce((beste, m) => (ernstIndex(m.niveau) < ernstIndex(beste.niveau) ? m : beste));
        return kortTekst(ernstigste.tekst, maxTekens);
    }

    // Alleen bij fout en let op, en alleen naar een tabblad dat deze gebruiker nu heeft (toegestaneViews; zonder lijst
    // geen controle). null als label of tab ontbreekt.
    function meldingActie(melding, toegestaneViews) {
        const actie = melding && melding.actie;
        if (!actie || !actie.label || !actie.tab) return null;
        if (!NIVEAUS_MET_ACTIE.includes(normaalNiveau(melding.niveau))) return null;
        if (Array.isArray(toegestaneViews) && !toegestaneViews.includes(actie.tab)) return null;
        return { label: actie.label, tab: actie.tab, tekst: `→ Naar ${actie.label}` };
    }

    // Fouten en let-op-punten (niet de technische), ernstigste eerst; binnen een niveau in de volgorde waarin de
    // categorieën voorkomen. blokSleutel wijst naar het categorieblok: live, of bij een melding die alleen in de laatste
    // upload staat het blok van de upload (voorvoegsel zoals in toonUploadMeldingen).
    function actielijst(live, uploads, toegestaneViews, maxRegels = MAX_ACTIELIJST_REGELS) {
        const liveSleutels = new Set((live || []).map(meldingSleutel));
        const alle = meldingenVoorTelling(live, uploads)
            .filter(m => !technischeGroep(m) && NIVEAUS_MET_ACTIE.includes(normaalNiveau(m.niveau)));
        const categorieVolgorde = [];
        alle.forEach(m => { if (!categorieVolgorde.includes(m.categorie)) categorieVolgorde.push(m.categorie); });
        const gesorteerd = alle.slice().sort((a, b) => ernstIndex(a.niveau) - ernstIndex(b.niveau)
            || categorieVolgorde.indexOf(a.categorie) - categorieVolgorde.indexOf(b.categorie));
        const regels = gesorteerd.slice(0, maxRegels).map(m => ({
            niveau: normaalNiveau(m.niveau),
            categorie: m.categorie,
            tekst: kortTekst(m.tekst, SAMENVATTING_MAX_TEKENS),
            actie: meldingActie(m, toegestaneViews),
            blokSleutel: (liveSleutels.has(meldingSleutel(m)) ? "" : UPLOAD_BLOK_VOORVOEGSEL) + m.categorie,
        }));
        return { regels, meer: Math.max(0, gesorteerd.length - regels.length) };
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

    // De betrokken rijen onder een melding ({kolommen, rijen, meer}); null zonder rijen. Cellen als tekst.
    function diagnostiekRijenBlok(rijenBlok) {
        const tabel = diagnostiekTabelRijen(rijenBlok);
        if (!tabel) return null;
        const meer = Number.isFinite(rijenBlok.meer) && rijenBlok.meer > 0 ? rijenBlok.meer : 0;
        const aantal = tabel.rijen.length + meer;
        return {
            kolommen: tabel.kolommen,
            rijen: tabel.rijen,
            meerTekst: meer ? `... en ${meer} meer` : "",
            titel: `Toon ${aantal === 1 ? "de rij" : `${aantal} rijen`}`,
        };
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

    // Alleen een expliciete false: een oude rij zonder het veld telt veilig als ook_live (telt niet mee).
    function alleenBijUpload(m) {
        return m.ook_live === false;
    }

    // uploads nieuwste eerst, zoals /upload-meldingen. {laatste: {titel, meldingen, standBijUpload, aantalOokLive} of
    // null, ouder: [{titel, meldingen}]}. meldingen: alleen-bij-upload, zonder wat ook live bestaat (de live versie wint);
    // standBijUpload: wat gewoon laden opnieuw berekent (ook_live), zoals het bij de upload was.
    // maxUploads = MAX_BEWAARDE_UPLOADS uit de API; ontbreekt hij, dan komen alle meegestuurde uploads.
    function uploadBlokken(uploads, live, maxUploads) {
        const lijst = uploads || [];
        if (lijst.length === 0) return { laatste: null, ouder: [] };
        const liveSleutels = new Set((live || []).map(meldingSleutel));
        const [laatste, ...ouder] = lijst;
        const alle = laatste.meldingen || [];
        const uploadEigen = alle.filter(alleenBijUpload);
        const eigen = uploadEigen.filter(m => !liveSleutels.has(meldingSleutel(m)));
        return {
            laatste: {
                titel: uploadTitel(laatste, "Laatste upload"), meldingen: eigen,
                standBijUpload: alle.filter(m => !alleenBijUpload(m)), aantalOokLive: uploadEigen.length - eigen.length,
            },
            ouder: (Number.isInteger(maxUploads) && maxUploads > 0 ? ouder.slice(0, maxUploads - 1) : ouder).map(u => ({ titel: uploadTitel(u, "Eerdere upload"), meldingen: u.meldingen || [] })),
        };
    }

    // Voor de conclusiezin en de chips: live plus wat alleen bij de laatste upload ontstond; bij dezelfde sleutel wint live.
    function meldingenVoorTelling(live, uploads) {
        const laatste = (uploads && uploads[0] && uploads[0].meldingen) || [];
        return voegMeldingenSamen(laatste.filter(alleenBijUpload), live);
    }

    function uploadOokLiveTekst(aantal) {
        return `${meervoud(aantal, "melding", "meldingen")} van deze upload ${aantal === 1 ? "staat" : "staan"} `
            + "hierboven, met de stand van nu.";
    }

    function controleSleutel(c) {
        return `${c.categorie}\u0000${c.naam}`;
    }

    // Zoals voegMeldingenSamen(): de nieuwste wint, op de plek van de oude.
    function voegControlesSamen(bestaand, nieuw) {
        const resultaat = (bestaand || []).slice();
        const positie = new Map(resultaat.map((c, i) => [controleSleutel(c), i]));
        (nieuw || []).forEach(c => {
            const sleutel = controleSleutel(c);
            if (positie.has(sleutel)) {
                resultaat[positie.get(sleutel)] = c;
            } else {
                positie.set(sleutel, resultaat.length);
                resultaat.push(c);
            }
        });
        return resultaat;
    }

    // Technische controles (Laadtijden, Yahoo-calls) zijn alleen voor de ontwikkelaar.
    function zichtbareControles(controles, ontwikkelaar) {
        const lijst = controles || [];
        return ontwikkelaar === true ? lijst.slice() : lijst.filter(c => !c.technisch);
    }

    // Bv. "14 controles gedraaid, 2 met bevindingen, 1 overgeslagen"; "" zonder controles.
    function controleSamenvatting(controles) {
        const lijst = controles || [];
        if (lijst.length === 0) return "";
        const gedraaid = lijst.filter(c => c.status === "gedraaid");
        const delen = [
            `${meervoud(gedraaid.length, "controle", "controles")} gedraaid`,
            `${gedraaid.filter(c => c.bevindingen > 0).length} met bevindingen`,
        ];
        const overgeslagen = lijst.filter(c => c.status === "overgeslagen").length;
        const mislukt = lijst.filter(c => c.status === "fout").length;
        if (overgeslagen > 0) delen.push(`${overgeslagen} overgeslagen`);
        if (mislukt > 0) delen.push(`${mislukt} mislukt`);
        return delen.join(", ");
    }

    function controleStatusTekst(c) {
        if (c.status === "overgeslagen") return `overgeslagen: ${c.reden || "voorwaarde ontbreekt"}`;
        if (c.status === "fout") return `mislukt (${c.reden || "onbekende fout"})`;
        return c.bevindingen > 0 ? meervoud(c.bevindingen, "bevinding", "bevindingen") : "niets gevonden";
    }

    // [{categorie, naam, tekst}] op categorie, daarbinnen in de volgorde van binnenkomst.
    function controleRegels(controles) {
        const lijst = (controles || []).map((c, i) => ({ c, i }));
        lijst.sort((a, b) => a.c.categorie.localeCompare(b.c.categorie, "nl") || a.i - b.i);
        return lijst.map(({ c }) => ({ categorie: c.categorie, naam: c.naam, tekst: controleStatusTekst(c) }));
    }

    // Categorieën met wel controles maar zonder enkele melding: [{categorie, tekst}]. Gedraaid wint van overgeslagen;
    // is er van beide, dan staat wat is overgeslagen erachter.
    function legeCategorieen(meldingen, controles) {
        const metMelding = new Set((meldingen || []).map(m => m.categorie));
        const perCategorie = new Map();
        (controles || []).forEach(c => {
            if (metMelding.has(c.categorie)) return;
            if (!perCategorie.has(c.categorie)) perCategorie.set(c.categorie, []);
            perCategorie.get(c.categorie).push(c);
        });
        return Array.from(perCategorie, ([categorie, lijst]) => {
            const gedraaid = lijst.filter(c => c.status === "gedraaid");
            const overgeslagen = lijst.filter(c => c.status === "overgeslagen");
            const reden = c => `${c.naam} (${c.reden || "voorwaarde ontbreekt"})`;
            let tekst;
            if (gedraaid.length === 0) {
                tekst = overgeslagen.length > 0
                    ? `Niet gecontroleerd: ${overgeslagen.map(reden).join(", ")}.` : "Controle mislukt.";
            } else {
                tekst = "Gecontroleerd, niets gevonden."
                    + (overgeslagen.length > 0 ? ` Niet gecontroleerd: ${overgeslagen.map(reden).join(", ")}.` : "");
            }
            return { categorie, tekst };
        });
    }

    const exportsObj = {
        DIAGNOSTIEK_NIVEAUS, DIAGNOSTIEK_NIVEAU_LABEL, DIAGNOSTIEK_NIVEAU_ICOON,
        STANDAARD_ZICHTBARE_NIVEAUS, GEEN_CODE_UITLEG, GOED_CHIP_UITLEG, UPLOAD_BLOK_VOORVOEGSEL, MAX_ACTIELIJST_REGELS,
        actielijst, voegControlesSamen, zichtbareControles, controleSamenvatting, controleRegels, legeCategorieen,
        normaalNiveau, deelInBlokken, filterOpNiveaus, wisselNiveau, isStandaardNiveauKeuze, zichtbareMeldingen,
        diagnostiekConclusie,
        categorieSamenvatting, meldingActie,
        voegMeldingenSamen, telPerNiveau, groepeerPerCategorie, diagnostiekTellerTekst,
        hoogsteNiveau, categorieStandaardOpen, diagnostiekTabelRijen, diagnostiekRijenBlok,
        formatUploadMoment, uploadBlokken, meldingenVoorTelling, uploadOokLiveTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
