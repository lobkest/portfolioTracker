// Pure logica voor de hoofdtabs en subtabs (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    // Het tandwiel krijgt U+FE0E (tekstvariant), anders tonen telefoons een kleur-emoji. Instellingen is geen hoofdtab maar het tandwiel in de header.
    const MENU_GROEPEN = [
        {
            id: "overzicht", label: "Overzicht", icoon: "⌂",
            views: [
                { view: "portfolio", label: "Samenvatting" },
                { view: "statistieken", label: "Statistieken" },
                { view: "dividend", label: "Dividend" },
                { view: "box3", label: "Box 3", inOntwikkeling: true },
                { view: "transacties", label: "Transacties" },
            ],
        },
        {
            id: "rendement", label: "Rendement", icoon: "↗",
            views: [
                { view: "rendement", label: "Rendement" },
                { view: "prognose", label: "Prognose" },
                { view: "prognose-huidig", label: "Huidige portfolio" },
            ],
        },
        {
            id: "samenstelling", label: "Samenstelling", icoon: "◔",
            views: [
                { view: "verdeling", label: "Verdeling" },
                { view: "bedrijven", label: "Top 10 bedrijven" },
                { view: "land", label: "Land" },
                { view: "sector", label: "Sector" },
                { view: "valuta", label: "Valuta" },
                { view: "beurs", label: "Beurs" },
                { view: "etfoverlap", label: "ETF-overlap" },
            ],
        },
        {
            id: "posities", label: "Posities", icoon: "≡",
            views: [
                { view: "peraandeel", label: "Per aandeel" },
                { view: "peraandeelaankoop", label: "Aankopen" },
            ],
        },
        {
            id: "instellingen", label: "Instellingen", icoon: "⚙︎", tandwiel: true,
            views: [
                { view: "instellingen", label: "Algemeen" },
                { view: "instellingen-bestanden", label: "Bestanden bijwerken" },
                { view: "instellingen-bijnamen", label: "Bijnamen" },
                { view: "instellingen-ticker", label: "Ticker-zekerheid" },
                { view: "instellingen-diagnostiek", label: "Diagnostiek" },
            ],
        },
    ];

    function groepVanView(view) {
        const groep = MENU_GROEPEN.find(g => g.views.some(v => v.view === view));
        return groep ? groep.id : null;
    }

    function zichtbareViews(groep, toegestaneViews) {
        return groep.views.filter(v => toegestaneViews.includes(v.view));
    }

    function eersteView(groepId, toegestaneViews) {
        const groep = MENU_GROEPEN.find(g => g.id === groepId);
        const eerste = groep && zichtbareViews(groep, toegestaneViews)[0];
        return eerste ? eerste.view : null;
    }

    function zichtbareGroepen(toegestaneViews) {
        return MENU_GROEPEN.filter(g => zichtbareViews(g, toegestaneViews).length > 0);
    }

    // Onderdelen binnen een tabblad (geen view); het id mag geen view-naam zijn.
    const ONTWIKKEL_ONDERDELEN = [
        { id: "rendement-pct", label: "Rendement in %" },
        { id: "verdeling-over-tijd", label: "Verdeling over tijd" },
    ];

    // Per browser (localStorage): JSON-lijst van de aangezette views en onderdeel-id's in ontwikkeling.
    const ONTWIKKEL_OPSLAG_SLEUTEL = "ontwikkelTabsAan";

    function ontwikkelViews() {
        return MENU_GROEPEN.flatMap(g => g.views.filter(v => v.inOntwikkeling).map(v => ({ view: v.view, label: v.label })));
    }

    function uitgeschakeldeViews(aanGezet) {
        return ontwikkelViews().map(v => v.view).filter(v => !aanGezet.includes(v));
    }

    function ontwikkelItems() {
        return [...ontwikkelViews().map(v => ({ id: v.view, label: v.label })), ...ONTWIKKEL_ONDERDELEN];
    }

    function ontwikkelAan(id, aanGezet) {
        return aanGezet.includes(id);
    }

    function leesAanGezet(tekst) {
        try {
            const lijst = JSON.parse(tekst);
            return Array.isArray(lijst) ? lijst.filter(v => typeof v === "string") : [];
        } catch (_) {
            return [];
        }
    }

    // Een uitgeschakelde ontwikkel-view staat niet in toegestaneViews maar blijft als (grijze) chip zichtbaar.
    function menuViews(groep, toegestaneViews, uitgeschakeld) {
        return groep.views
            .filter(v => toegestaneViews.includes(v.view) || (v.inOntwikkeling && uitgeschakeld.includes(v.view)))
            .map(v => ({ ...v, uitgeschakeld: uitgeschakeld.includes(v.view) }));
    }

    // 1 px marge: scrollLeft is op telefoons met zoom vaak een gebroken getal.
    function scrollFades(scrollLeft, scrollWidth, clientWidth) {
        return {
            links: scrollLeft > 1,
            rechts: scrollWidth - clientWidth - scrollLeft > 1,
        };
    }

    const exportsObj = {
        MENU_GROEPEN, ONTWIKKEL_ONDERDELEN, ONTWIKKEL_OPSLAG_SLEUTEL, groepVanView, eersteView, zichtbareGroepen,
        zichtbareViews, ontwikkelViews, ontwikkelItems, ontwikkelAan, uitgeschakeldeViews, leesAanGezet, menuViews,
        scrollFades,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
