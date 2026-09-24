let engine = null
let currentId = null
let cancellingId = null
const pendingRequests = []

function startNextRequest() {
  if (!engine || currentId !== null || pendingRequests.length === 0) return
  const request = pendingRequests.shift()
  currentId = request.id
  const multipv = Math.max(1, Math.min(5, Number(request.multipv ?? 5)))
  engine.uci(`setoption name MultiPV value ${multipv}`)
  const startFen = request.positionStartFen ?? request.fen
  const prefix = request.positionPrefixUci ?? []
  const moves = prefix.length ? ` moves ${prefix.join(' ')}` : ''
  engine.uci(`position fen ${startFen}${moves}`)
  const rootMove = request.rootMoveUci
  engine.uci(`go depth ${request.depth ?? 13}${rootMove ? ` searchmoves ${rootMove}` : ''}`)
}

async function initialize() {
  const assetRoot = new URL('engines/', self.location.href)
  const { default: StockfishFactory } = await import(new URL('sf_19_smallnet.js', assetRoot).href)
  engine = await StockfishFactory({
    locateFile: (file) => new URL(file, assetRoot).href,
    mainScriptUrlOrBlob: new URL('sf_19_smallnet.js', assetRoot).href,
  })
  engine.listen = (line) => {
    if (line.startsWith('bestmove ')) {
      const finishedId = currentId
      const wasCancelled = cancellingId === finishedId
      currentId = null
      cancellingId = null
      postMessage(wasCancelled
        ? { type: 'cancelled', id: finishedId }
        : { type: 'line', id: finishedId, line })
      startNextRequest()
      return
    }
    if (currentId !== null && cancellingId !== currentId)
      postMessage({ type: 'line', id: currentId, line })
  }
  engine.onError = (message) => postMessage({ type: 'error', id: currentId, message })
  const response = await fetch(new URL('nn-61e7af4bb97d.nnue', assetRoot))
  if (!response.ok) throw new Error('Could not load Stockfish evaluation network')
  engine.setNnueBuffer(new Uint8Array(await response.arrayBuffer()))
  engine.uci('uci')
  engine.uci('setoption name Threads value 1')
  engine.uci('setoption name Hash value 32')
  engine.uci('setoption name MultiPV value 5')
  engine.uci('isready')
}

self.onmessage = async (event) => {
  try {
    if (event.data.type === 'init') {
      if (!engine) await initialize()
      postMessage({ type: 'ready' })
    }
    if (event.data.type === 'analyze') {
      if (!engine) await initialize()
      pendingRequests.push(event.data)
      startNextRequest()
    }
    if (event.data.type === 'cancel') {
      const queuedIndex = pendingRequests.findIndex((request) => request.id === event.data.id)
      if (queuedIndex >= 0) {
        pendingRequests.splice(queuedIndex, 1)
        postMessage({ type: 'cancelled', id: event.data.id })
      } else if (currentId === event.data.id) {
        cancellingId = currentId
        engine.uci('stop')
      }
    }
  } catch (error) {
    postMessage({ type: 'error', id: event.data.id, message: error?.message ?? 'Stockfish failed to start' })
  }
}
