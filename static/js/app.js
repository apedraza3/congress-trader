/* Congress Trade Copier — shared JS */

function apiFetch(url, opts) {
    opts = opts || {};
    return fetch(url, opts).then(function(r) {
        if (r.status === 401) { window.location = '/login'; return; }
        return r.json();
    });
}

function esc(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function formatMoney(n) {
    if (n == null) return '--';
    return '$' + Number(n).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function formatPct(n) {
    if (n == null) return '--';
    return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

function formatDate(s) {
    if (!s) return '--';
    return s.substring(0, 10);
}
