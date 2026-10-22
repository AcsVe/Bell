/* <table data-table>: sticky header, a filter box (or list) under each column with data-filter="text|select",
   click a header to sort (data-sort="num" for numbers; data-nosort to opt out), <td data-value> for the value
   used by sorting/filtering. Rows hidden by a filter are skipped by «select all» (bulk selection). */
(function () {
  'use strict';
  const AR = document.documentElement.lang !== 'en';
  const norm = (v) => (window.normText ? window.normText(v) : String(v || '').toLowerCase().trim());
  const val = (td) => (td ? (td.hasAttribute('data-value') ? td.getAttribute('data-value') : td.textContent.trim()) : '');
  function setup(table) {
    const head = table.tHead && table.tHead.rows[0], body = table.tBodies[0];
    if (!head || !body) return;
    const cols = [...head.cells], filters = [];
    table.classList.add('dt');
    if (cols.some((th) => th.dataset.filter)) {
      const fr = table.tHead.insertRow(-1);
      fr.className = 'dt-filter';
      cols.forEach((th, i) => {
        const cell = document.createElement('th');
        fr.appendChild(cell);
        if (!th.dataset.filter) return;
        let el;
        if (th.dataset.filter === 'select') {
          el = document.createElement('select');
          el.add(new Option(AR ? 'الكل' : 'All', ''));
          [...new Set([...body.rows].map((r) => val(r.cells[i])).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ar'))
            .forEach((v) => el.add(new Option(v, v)));
        } else {
          el = document.createElement('input');
          el.type = 'search';
          el.placeholder = AR ? 'تصفية…' : 'Filter…';
        }
        el.setAttribute('aria-label', (AR ? 'تصفية: ' : 'Filter: ') + th.textContent.trim());
        el.addEventListener('input', apply);
        cell.appendChild(el);
        filters[i] = el;
      });
    }
    let sc = -1, dir = 1;
    cols.forEach((th, i) => {
      if (th.hasAttribute('data-nosort') || th.querySelector('input')) return;
      th.classList.add('dt-sort');
      th.tabIndex = 0;
      const go = () => {
        dir = sc === i ? -dir : 1; sc = i;
        cols.forEach((c) => c.classList.remove('asc', 'desc'));
        th.classList.add(dir > 0 ? 'asc' : 'desc');
        const num = th.dataset.sort === 'num';
        [...body.rows].sort((a, b) => {
          const x = val(a.cells[i]), y = val(b.cells[i]);
          return (num ? (parseFloat(x) || 0) - (parseFloat(y) || 0) : x.localeCompare(y, 'ar', { numeric: true })) * dir;
        }).forEach((r) => body.appendChild(r));
      };
      th.addEventListener('click', go);
      th.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    });
    function apply() {
      [...body.rows].forEach((tr) => {
        tr.hidden = !filters.every((el, i) => !el || !el.value ||
          (el.tagName === 'SELECT' ? val(tr.cells[i]) === el.value : norm(val(tr.cells[i])).includes(norm(el.value))));
      });
      document.querySelectorAll('.bulk-bar').forEach((b) => window.bulkRefresh && window.bulkRefresh(b.dataset.bulk));
    }
    table.dtApply = apply;
  }
  const init = () => document.querySelectorAll('table[data-table]').forEach(setup);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
