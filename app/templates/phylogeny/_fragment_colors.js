// Shared fragment-combination coloring (included inside a <script> block).
// A "combo" is a '+'-joined set of gene fragments a species/sequence carries,
// e.g. '18S', '18S+ITS', '28S+COI+COII'. fragmentColor maps any combo to a
// stable, deterministic colour so the same combination looks identical on every
// tree/table across the app.
const FRAG_ORDER = ['18S', 'ITS', '28S', 'COI', 'COII'];

function canonCombo(combo) {
    if (!combo) return '';
    const parts = String(combo).split('+').map(s => s.trim()).filter(Boolean);
    parts.sort((a, b) => {
        const ia = FRAG_ORDER.indexOf(a), ib = FRAG_ORDER.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    });
    return parts.join('+');
}

// Deterministic HSL from the canonical combo string. More fragments → darker,
// so richer taxa read as stronger colours; hue distinguishes the combination.
function fragmentColor(combo) {
    const c = canonCombo(combo);
    if (!c) return null;
    let h = 0;
    for (let i = 0; i < c.length; i++) h = (h * 33 + c.charCodeAt(i)) >>> 0;
    const n = c.split('+').length;
    const light = Math.max(28, 52 - (n - 1) * 7);
    return `hsl(${h % 360}, 62%, ${light}%)`;
}

// Distinct canonical combos actually present, sorted by fragment count then name,
// each with its colour — for building a legend.
function fragmentLegendItems(tipMarkers) {
    const seen = new Set();
    Object.values(tipMarkers || {}).forEach(v => { const c = canonCombo(v); if (c) seen.add(c); });
    return [...seen]
        .sort((a, b) => a.split('+').length - b.split('+').length || a.localeCompare(b))
        .map(c => ({ combo: c, color: fragmentColor(c) }));
}
