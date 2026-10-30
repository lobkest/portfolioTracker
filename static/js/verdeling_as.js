// Pure logica voor de as-keuze op Samenstelling > Verdeling (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    const STANDAARD_AS = "fonds";
    const VERDELING_AS_OPSLAG_SLEUTEL = "verdelingAs";

    // view = de tabblad-blok-naam (data-views); dimensie = sleutel van /verdeling-over-tijd, null als die as geen tijdreeks heeft.
    const VERDELING_ASSEN = [
        { as: "fonds", label: "Fonds", view: "verdeling", dimensie: "positie" },
        { as: "land", label: "Land", view: "land", dimensie: "land" },
        { as: "sector", label: "Sector", view: "sector", dimensie: "sector" },
        { as: "valuta", label: "Valuta", view: "valuta", dimensie: "valuta" },
        { as: "beurs", label: "Beurs", view: "beurs", dimensie: "beurs" },
    ];

    function asInfo(as) {
        return VERDELING_ASSEN.find(a => a.as === as) || VERDELING_ASSEN[0];
    }

    function geldigeAs(waarde) {
        return VERDELING_ASSEN.some(a => a.as === waarde) ? waarde : STANDAARD_AS;
    }

    function domViewVoorAs(as) {
        return asInfo(as).view;
    }

    function fondsElementenZichtbaar(as) {
        return geldigeAs(as) === "fonds";
    }

    // assen: optioneel andere lijst (tests); de pagina gebruikt VERDELING_ASSEN.
    function heeftTijdreeks(as, assen = VERDELING_ASSEN) {
        const info = assen.find(a => a.as === as);
        return Boolean(info && info.dimensie);
    }

    function effectieveModus(as, modus, assen = VERDELING_ASSEN) {
        return modus === "tijd" && heeftTijdreeks(as, assen) ? "tijd" : "nu";
    }

    function asVoorDimensie(dimensie) {
        const info = VERDELING_ASSEN.find(a => a.dimensie === dimensie);
        return info ? info.as : STANDAARD_AS;
    }

    // Opslag: object met getItem/setItem, of null; zonder opslag of met een fout gewoon de standaard.
    function leesOpgeslagenAs(opslag) {
        try {
            return geldigeAs(opslag.getItem(VERDELING_AS_OPSLAG_SLEUTEL));
        } catch (_) {
            return STANDAARD_AS;
        }
    }

    function bewaarAs(opslag, as) {
        try {
            opslag.setItem(VERDELING_AS_OPSLAG_SLEUTEL, geldigeAs(as));
        } catch (_) {
            // geen opslag beschikbaar: de keuze geldt alleen voor deze pagina
        }
    }

    const exportsObj = {
        STANDAARD_AS, VERDELING_AS_OPSLAG_SLEUTEL, VERDELING_ASSEN, geldigeAs, domViewVoorAs, fondsElementenZichtbaar,
        heeftTijdreeks, effectieveModus, asVoorDimensie, leesOpgeslagenAs, bewaarAs,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
