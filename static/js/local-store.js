/** Private, same-browser storage for public-network attraction edits. */

// Keep the legacy database name so saved browser edits survive the product rename.
const DATABASE_NAME = "wanderplan-local-networks";
const DATABASE_VERSION = 2;
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
      if (!database.objectStoreNames.contains(PUBLIC_EDITS_STORE)) {
        database.createObjectStore(PUBLIC_EDITS_STORE, { keyPath: "network_id" });
      }
    });
    request.addEventListener("success", () => resolve(request.result), { once: true });
    request.addEventListener("error", () => reject(request.error), { once: true });
    request.addEventListener("blocked", () => reject(new Error("Magazyn lokalny jest zablokowany przez inną kartę.")), { once: true });
  });
}


export async function requestPersistentLocalStorage() {
  if (navigator.storage?.persist) {
    try { return await navigator.storage.persist(); }
    catch (_) { return false; }
  }
  return false;
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
