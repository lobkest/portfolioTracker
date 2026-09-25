// Pure logica voor het hamburgermenu (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    function volgendeMenuOpenStatus(huidigOpen) {
        return !huidigOpen;
    }

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
