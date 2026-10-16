// Leest de NDJSON-voortgangsstroom van /upload?voortgang=1 (formaat: zie CLAUDE.md: Laadbalk).

(function (root) {
    "use strict";

    // Chunks breken soms midden in een regel af: de onvolledige laatste regel komt terug als `rest`.
    function verwerkStroomBuffer(buffer) {
        const delen = String(buffer).split("\n");
        const rest = delen.pop();
        const regels = delen.filter((deel) => deel.trim() !== "").map((deel) => JSON.parse(deel));
        return { regels, rest };
    }

    // De staptekst, met "(gedaan/totaal)" erachter als de stap telt.
    function voortgangTekst(event) {
        return event.totaal > 0 ? `${event.tekst} (${event.gedaan}/${event.totaal})` : event.tekst;
    }

    // Geeft { status, data } van de `klaar`-regel; zonder stroom (bv. 429) het gewone antwoord.
    async function leesVoortgangsstroom(res, opVoortgang) {
        const type = res.headers.get("Content-Type") || "";
        if (!type.includes("application/x-ndjson") || !res.body) {
            return { status: res.status, data: await res.json() };
        }
        const lezer = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let klaar = null;
        const verwerk = (tekst) => {
            const { regels, rest } = verwerkStroomBuffer(tekst);
            for (const regel of regels) {
                if (regel.type === "klaar") klaar = { status: regel.status, data: regel.data };
                else if (regel.type === "voortgang") opVoortgang(regel);
            }
            return rest;
        };
        while (true) {
            const { done, value } = await lezer.read();
            if (done) break;
            buffer = verwerk(buffer + decoder.decode(value, { stream: true }));
        }
        verwerk(buffer + decoder.decode() + "\n");
        if (!klaar) throw new Error("STROOM_AFGEBROKEN");
        return klaar;
    }

    const exportsObj = { verwerkStroomBuffer, voortgangTekst, leesVoortgangsstroom };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
