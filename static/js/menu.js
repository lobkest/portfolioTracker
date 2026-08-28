// Rekenkern voor het hamburger-menu op mobiel. Los van DOM-manipulatie
// gehouden (net als prognose.js) zodat het zowel in de browser (index.html)
// als onder Node (tests/test_menu.js) draait.

(function (root) {
    "use strict";

    function volgendeMenuOpenStatus(huidigOpen) {
        return !huidigOpen;
    }

    // Na het kiezen van een tabblad in het opengeklapte menu moet het menu
    // altijd weer dichtklappen, ongeacht de status ervoor.
    function menuOpenStatusNaViewKeuze() {
        return false;
    }

    const exportsObj = { volgendeMenuOpenStatus, menuOpenStatusNaViewKeuze };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
