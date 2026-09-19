let engine = null
let currentId = null
let cancellingId = null

async function initialize() {
  const assetRoot = new URL('engines/', self.location.href)
  const { default: StockfishFactory } = await import(new URL('sf_19_smallnet.js', assetRoot).href)
  engine = await StockfishFactory({
    locateFile: (file) => new URL(file, assetRoot).href,
    mainScriptUrlOrBlob: new URL('sf_19_smallnet.js', assetRoot).href,
  })
  engine.listen = (line) => {
    if (cancellingId === currentId && line.startsWith('bestmove ')) {
      const cancelledId = currentId
      currentId = null
      cancellingId = null
      postMessage({ type: 'cancelled', id: cancelledId })
      return
    }
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
      currentId = event.data.id
      engine.uci('stop')
      engine.uci(`position fen ${event.data.fen}`)
      engine.uci(`go depth ${event.data.depth ?? 13}`)
    }
    if (event.data.type === 'cancel' && currentId === event.data.id) {
      cancellingId = currentId
      engine.uci('stop')
    }
  } catch (error) {
    postMessage({ type: 'error', id: event.data.id, message: error?.message ?? 'Stockfish failed to start' })
  }
}
