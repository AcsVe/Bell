/* Tiny IndexedDB key-value store for the cached schedule (survives restarts,
 * works offline). Falls back to localStorage if IndexedDB is unavailable. */
(function (root) {
  'use strict';
  const DB = 'bell-app', STORE = 'kv';
  let dbp = null;

  function open() {
    if (dbp) return dbp;
    dbp = new Promise((resolve, reject) => {
      if (!('indexedDB' in root)) return reject(new Error('no idb'));
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => r.result.createObjectStore(STORE);
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
    return dbp;
  }

  function tx(mode, fn) {
    return open().then((db) => new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const req = fn(t.objectStore(STORE));
      t.oncomplete = () => resolve(req && req.result);
      t.onerror = () => reject(t.error);
    }));
  }

  const ls = {
    get: (k) => Promise.resolve(JSON.parse(localStorage.getItem('bell:' + k) || 'null')),
    set: (k, v) => Promise.resolve(localStorage.setItem('bell:' + k, JSON.stringify(v))),
    del: (k) => Promise.resolve(localStorage.removeItem('bell:' + k)),
  };

  root.BellStore = {
    get: (k) => tx('readonly', (s) => s.get(k)).catch(() => ls.get(k)),
    set: (k, v) => tx('readwrite', (s) => s.put(v, k)).catch(() => ls.set(k, v)),
    del: (k) => tx('readwrite', (s) => s.delete(k)).catch(() => ls.del(k)),
  };
})(self);
