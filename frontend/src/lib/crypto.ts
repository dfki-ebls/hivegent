/**
 * Web Crypto API utilities for encrypting the LLM API key at rest.
 *
 * Uses a non-extractable AES-GCM CryptoKey stored in IndexedDB.
 * The key cannot be read by scripts, so an XSS attacker cannot silently
 * exfiltrate the API key from a static localStorage dump.
 *
 * Encrypted values use the format: `enc:<base64-iv>:<base64-ciphertext>`
 */

const DB_NAME = "hivegent-crypto";
const STORE_NAME = "keys";
const KEY_ID = "master";
const ENCRYPTED_PREFIX = "enc:";

/** Cached CryptoKey to avoid repeated IndexedDB lookups. */
let cachedKey: CryptoKey | null = null;

/** Open (or create) the IndexedDB database for key storage. */
function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE_NAME);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

/**
 * Retrieve the existing CryptoKey from IndexedDB, or generate and store a
 * new one on first use.
 */
async function getOrCreateKey(): Promise<CryptoKey> {
  if (cachedKey) return cachedKey;

  const db = await openDB();

  try {
    // Try to load an existing key.
    const existing = await new Promise<CryptoKey | undefined>(
      (resolve, reject) => {
        const tx = db.transaction(STORE_NAME, "readonly");
        const request = tx.objectStore(STORE_NAME).get(KEY_ID);
        request.onsuccess = () =>
          resolve(request.result as CryptoKey | undefined);
        request.onerror = () => reject(request.error);
      },
    );

    if (existing) {
      cachedKey = existing;
      return existing;
    }

    // Generate a new non-extractable AES-GCM key.
    const key = await crypto.subtle.generateKey(
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );

    // Persist to IndexedDB.
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readwrite");
      const request = tx.objectStore(STORE_NAME).put(key, KEY_ID);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
    });

    cachedKey = key;
    return key;
  } finally {
    db.close();
  }
}

/** Check whether a string looks like an encrypted value. */
export function isEncrypted(value: string): boolean {
  return value.startsWith(ENCRYPTED_PREFIX);
}

/** Encrypt a plain-text API key, returning the `enc:iv:ciphertext` string. */
export async function encryptApiKey(plaintext: string): Promise<string> {
  const key = await getOrCreateKey();
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encoded = new TextEncoder().encode(plaintext);

  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    encoded,
  );

  const ivB64 = btoa(String.fromCharCode(...iv));
  const ctB64 = btoa(String.fromCharCode(...new Uint8Array(ciphertext)));

  return `${ENCRYPTED_PREFIX}${ivB64}:${ctB64}`;
}

/** Decrypt an `enc:iv:ciphertext` string back to the plain-text API key. */
export async function decryptApiKey(encrypted: string): Promise<string> {
  if (!isEncrypted(encrypted)) return encrypted;

  const key = await getOrCreateKey();
  const payload = encrypted.slice(ENCRYPTED_PREFIX.length);
  const separatorIndex = payload.indexOf(":");
  if (separatorIndex === -1) throw new Error("Invalid encrypted format");

  const iv = Uint8Array.from(atob(payload.slice(0, separatorIndex)), (c) =>
    c.charCodeAt(0),
  );
  const ciphertext = Uint8Array.from(
    atob(payload.slice(separatorIndex + 1)),
    (c) => c.charCodeAt(0),
  );

  const decrypted = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv },
    key,
    ciphertext,
  );

  return new TextDecoder().decode(decrypted);
}

/**
 * Delete the encryption key database.
 *
 * Fire-and-forget: callers do not need to await completion.
 */
export function deleteCryptoDatabase(): void {
  cachedKey = null;
  indexedDB.deleteDatabase(DB_NAME);
}
