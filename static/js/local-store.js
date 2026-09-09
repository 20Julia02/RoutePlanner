/** Private, same-browser storage for local networks and public-network edits. */

const DATABASE_NAME = "wanderplan-local-networks";
const DATABASE_VERSION = 2;
const DATA_STORE = "network-data";
const METADATA_STORE = "network-metadata";
const PUBLIC_EDITS_STORE = "public-network-edits";


function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.addEventListener("success", () => resolve(request.result), { once: true });
    request.addEventListener("error", () => reject(request.error), { once: true });
  });
}


function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.addEventListener("complete", resolve, { once: true });
    transaction.addEventListener("abort", () => reject(transaction.error), { once: true });
    transaction.addEventListener("error", () => reject(transaction.error), { once: true });
  });
}


function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.addEventListener("upgradeneeded", () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(DATA_STORE)) {
        database.createObjectStore(DATA_STORE, { keyPath: "id" });
      }
      if (!database.objectStoreNames.contains(METADATA_STORE)) {
        database.createObjectStore(METADATA_STORE, { keyPath: "id" });
      }
      if (!database.objectStoreNames.contains(PUBLIC_EDITS_STORE)) {
        database.createObjectStore(PUBLIC_EDITS_STORE, { keyPath: "network_id" });
      }
    });
    request.addEventListener("success", () => resolve(request.result), { once: true });
    request.addEventListener("error", () => reject(request.error), { once: true });
    request.addEventListener("blocked", () => reject(new Error("Magazyn lokalny jest zablokowany przez inną kartę.")), { once: true });
  });
}


export function createLocalNetworkId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID().replaceAll("-", "");
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
}


export async function requestPersistentLocalStorage() {
  if (navigator.storage?.persist) {
    try { return await navigator.storage.persist(); }
    catch (_) { return false; }
  }
  return false;
}


export async function listLocalNetworks() {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(METADATA_STORE, "readonly");
    const done = transactionDone(transaction);
    const items = await requestResult(transaction.objectStore(METADATA_STORE).getAll());
    await done;
    return items.sort((first, second) => String(second.updated_at).localeCompare(String(first.updated_at)));
  } finally {
    database.close();
  }
}


export async function loadLocalNetwork(id) {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(DATA_STORE, "readonly");
    const done = transactionDone(transaction);
    const result = await requestResult(transaction.objectStore(DATA_STORE).get(String(id)));
    await done;
    if (!result) throw new Error("Nie znaleziono lokalnego zestawu w tej przeglądarce.");
    return result;
  } finally {
    database.close();
  }
}


export async function saveLocalNetwork(metadata, data) {
  const database = await openDatabase();
  try {
    const transaction = database.transaction([METADATA_STORE, DATA_STORE], "readwrite");
    transaction.objectStore(METADATA_STORE).put(metadata);
    transaction.objectStore(DATA_STORE).put({ id: metadata.id, ...data });
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}


export async function deleteLocalNetwork(id) {
  const database = await openDatabase();
  try {
    const transaction = database.transaction([METADATA_STORE, DATA_STORE], "readwrite");
    transaction.objectStore(METADATA_STORE).delete(String(id));
    transaction.objectStore(DATA_STORE).delete(String(id));
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}


export async function loadPublicNetworkEdits(networkId) {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(PUBLIC_EDITS_STORE, "readonly");
    const done = transactionDone(transaction);
    const result = await requestResult(
      transaction.objectStore(PUBLIC_EDITS_STORE).get(String(networkId))
    );
    await done;
    return Array.isArray(result?.edits) ? result.edits : [];
  } finally {
    database.close();
  }
}


export async function savePublicNetworkEdits(networkId, edits) {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(PUBLIC_EDITS_STORE, "readwrite");
    transaction.objectStore(PUBLIC_EDITS_STORE).put({
      network_id: String(networkId),
      edits,
      updated_at: new Date().toISOString()
    });
    await transactionDone(transaction);
  } finally {
    database.close();
  }
}
