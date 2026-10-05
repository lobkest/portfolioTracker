// Pure logica voor de hoofdtabs en subtabs (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    // Instellingen is geen hoofdtab maar het tandwiel in de header.
    const MENU_GROEPEN = [
        {
            id: "overzicht", label: "Overzicht", icoon: "⌂",
            views: [
                { view: "portfolio", label: "Home" },
                { view: "transacties", label: "Transacties" },
                { view: "dividend", label: "Dividend" },
            ],
        },
        {
            id: "rendement", label: "Rendement", icoon: "↗",
            views: [
                { view: "rendement", label: "Rendement" },
                { view: "statistieken", label: "Statistieken" },
                { view: "prognose", label: "Prognose" },
            ],
        },
        {
            id: "samenstelling", label: "Samenstelling", icoon: "◔",
            views: [
                { view: "verdeling", label: "Verdeling" },
                { view: "land", label: "Land" },
                { view: "sector", label: "Sector" },
                { view: "valuta", label: "Valuta" },
                { view: "bedrijven", label: "Top 10 bedrijven" },
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
            id: "instellingen", label: "Instellingen", icoon: "⚙", tandwiel: true,
            views: [
                { view: "instellingen", label: "Algemeen" },
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

    const exportsObj = { MENU_GROEPEN, groepVanView, eersteView, zichtbareGroepen, zichtbareViews };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
