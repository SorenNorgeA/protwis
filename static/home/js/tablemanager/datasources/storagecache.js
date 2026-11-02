// Browser storage cache for the client-side data table's AJAX response.
// Entries are written to local storage, falling back to session storage; if neither is usable the cache is skipped.

class StorageCache {

  /**
   * @param {string} key - The storage key the entry is kept under.
   * @param {number} durationDays - How many days an entry stays valid after it was stored.
   */
  constructor(key, durationDays) {
    this.key = key;
    this.durationMs = durationDays * 24 * 60 * 60 * 1000;
  }

  /**
   * Gets the available storage areas, in order of preference. Accessing a storage area can throw (e.g. when blocked by browser settings).
   * @returns {Storage[]} The storage areas that can be accessed.
   */
  _storages() {
    const storages = [];
    for (const name of ["localStorage", "sessionStorage"]) {
      try {
        storages.push(window[name]);
      } catch {
        // storage blocked, skip it
      }
    }
    return storages;
  }

  /**
   * Gets the cached data. An expired or unreadable entry is cleared.
   * @returns {*} The cached data, or undefined if there is no valid entry.
   */
  get() {
    for (const storage of this._storages()) {
      try {
        const raw = storage.getItem(this.key);
        if (raw === null) {
          continue;
        }
        const entry = JSON.parse(raw);
        if (StorageCache._isEntry(entry) && Date.now() <= entry.expires) {
          return entry.data;
        }
        storage.removeItem(this.key); // expired
      } catch {
        this._remove(storage); // corrupt entry or inaccessible storage
      }
    }
    return undefined;
  }

  /**
   * Stores the data in the first storage area that accepts it (e.g. local storage may be full or disabled).
   * @param {*} data - The JSON serialisable data to cache.
   * @returns {boolean} True if the data was stored.
   */
  set(data) {
    let serialised;
    try {
      serialised = JSON.stringify({ tablemanagerCache: true, expires: Date.now() + this.durationMs, data: data });
    } catch {
      return false;
    }

    for (const storage of this._storages()) {
      try {
        storage.setItem(this.key, serialised);
        this._clearOthers(storage);
        return true;
      } catch {
        // Storage is probably full: free the space held by expired entries and try once more
        try {
          this._evictExpired(storage);
          storage.setItem(this.key, serialised);
          this._clearOthers(storage);
          return true;
        } catch {
          this._remove(storage); // don't leave a stale entry behind when the new one does not fit
        }
      }
    }
    return false;
  }

  /**
   * Whether a parsed storage value is a cache entry written by this class.
   * @param {*} entry - The parsed value.
   * @returns {boolean} True if it has the cache entry shape.
   */
  static _isEntry(entry) {
    return !!entry && entry.tablemanagerCache === true && typeof entry.expires === "number";
  }

  /**
   * Removes every expired cache entry (of any key) from a storage area. Values not written by this class are left alone.
   * @param {Storage} storage - The storage area to sweep.
   */
  _evictExpired(storage) {
    const now = Date.now();
    const expiredKeys = [];
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i);
      try {
        const entry = JSON.parse(storage.getItem(key));
        if (StorageCache._isEntry(entry) && now > entry.expires) {
          expiredKeys.push(key);
        }
      } catch {
        // not JSON, so not ours
      }
    }
    expiredKeys.forEach((key) => storage.removeItem(key)); // removed after iterating, as removal shifts the indexes
  }

  /** Removes the entry from every storage area. */
  clear() {
    this._storages().forEach((storage) => this._remove(storage));
  }

  _clearOthers(keep) {
    this._storages().filter((storage) => storage !== keep).forEach((storage) => this._remove(storage));
  }

  _remove(storage) {
    try {
      storage.removeItem(this.key);
    } catch {
      // nothing more can be done
    }
  }
}

export { StorageCache };
