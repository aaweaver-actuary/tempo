/** Fixed IndexedDB operations for the legacy classic Maia worker. */
(() => {
const DB_NAME = 'MaiaModels'
const STORE_NAME = 'models'
const MODEL_KEY = 'maia-rapid-model'

function isCompatibleModelCache(data, expectedUrl, expectedVersion) {
  return data.url === expectedUrl && data.version === expectedVersion
}

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)
    request.onerror = () => reject(request.error)
    request.onsuccess = () => resolve(request.result)
    request.onupgradeneeded = (event) => {
      const db = event.target.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id' })
      }
    }
  })
}

async function getCachedModel(modelUrl, modelVersion) {
  const db = await openDB()
  const tx = db.transaction([STORE_NAME], 'readonly')
  const store = tx.objectStore(STORE_NAME)

  const data = await new Promise((resolve, reject) => {
    const req = store.get(MODEL_KEY)
    req.onsuccess = () => resolve(req.result || null)
    req.onerror = () => reject(req.error)
  })

  if (!data) return null

  if (!isCompatibleModelCache(data, modelUrl, modelVersion)) {
    const rwTx = db.transaction([STORE_NAME], 'readwrite')
    rwTx.objectStore(STORE_NAME).delete(MODEL_KEY)
    return null
  }

  return await data.data.arrayBuffer()
}

async function storeModel(modelUrl, modelVersion, buffer) {
  const db = await openDB()
  const tx = db.transaction([STORE_NAME], 'readwrite')
  const store = tx.objectStore(STORE_NAME)

  await new Promise((resolve, reject) => {
    const req = store.put({
      id: MODEL_KEY,
      url: modelUrl,
      version: modelVersion,
      data: new Blob([buffer]),
      timestamp: Date.now(),
      size: buffer.byteLength,
    })
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
  })
}

self.tempoMaiaModelCache = { getCachedModel, storeModel }
})()
