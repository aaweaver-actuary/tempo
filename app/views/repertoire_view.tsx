import { useState, useCallback, useEffect } from "react";
import { LocalRepertoire, PieceColor, RepertoireItem } from "../types";
import { usesLocalApi } from "../utils/local";
import { API_URL } from "../const";

export default function RepertoireView({ imported, onImport, onBrowse, onDeleteLocal, onRenameLocal, onQueueChanged }: { imported: LocalRepertoire[]; onImport: () => void; onBrowse: () => void; onDeleteLocal: (id: string, sourceName?: string) => void; onRenameLocal: (id: string, name: string) => void; onQueueChanged: () => Promise<void> }) {
  const [samples, setSamples] = useState<RepertoireItem[]>([
    { id: 'sample-white', side: 'white', title: '1. e4 Main Lines', sourceName: 'Tempo examples', detail: '4 example lines', progress: 76, due: 0, pgn: '[Event "1. e4 Main Lines"]\n[Result "*"]\n\n1. e4 c5 2. Nf3 d6 3. d4 cxd4 *' },
    { id: 'sample-black', side: 'black', title: 'Sicilian Defense', sourceName: 'Tempo examples', detail: '2 example lines', progress: 58, due: 0, pgn: '[Event "Sicilian Defense"]\n[Result "*"]\n\n1. e4 c5 2. Nf3 d6 *' },
  ]);
  const [backendItems, setBackendItems] = useState<RepertoireItem[]>([]);

  const loadBackend = useCallback(async () => {
    if (!usesLocalApi()) return;
    try {
      const response = await fetch(`${API_URL}/api/repertoires`);
      if (!response.ok) return;
      const body = await response.json() as { repertoires: { id: string; name: string; source_name: string; line_count: number; card_count: number; due_count: number; trained_color?: PieceColor }[] };
      setBackendItems(body.repertoires.map((item) => ({ id: item.id, side: item.trained_color === 'black' ? 'black' : 'white', title: item.name, sourceName: item.source_name, detail: `${item.line_count} unique ${item.line_count === 1 ? 'line' : 'lines'} · ${item.card_count} cards`, progress: 0, due: item.due_count, backend: true })));
    } catch { /* Browser-local repertoires remain available. */ }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadBackend();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadBackend]);
  const backendSources = new Set(backendItems.map((item) => item.sourceName));
  const importedItems: RepertoireItem[] = imported.filter((item) => !backendSources.has(item.sourceName)).map((item) => ({ id: item.id, side: item.side, title: item.title, sourceName: item.sourceName, detail: `${item.cards.length} unique ${item.cards.length === 1 ? 'line' : 'lines'} · stored in this browser`, progress: 0, due: 0, pgn: item.pgn }));
  const repertoires = [...samples, ...backendItems, ...importedItems];

  async function rename(item: RepertoireItem) {
    const value=window.prompt('Repertoire nickname',item.title)?.trim();
    if(!value) return;
    if (item.backend) {
      const response = await fetch(`${API_URL}/api/repertoires/${item.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: value }) });
      if (response.ok) await loadBackend();
    } else if (item.id.startsWith('sample-')) setSamples((current) => current.map((entry) => entry.id === item.id ? { ...entry, title: value } : entry));
    else onRenameLocal(item.id, value);
  }

  async function remove(item: RepertoireItem) {
    if (!window.confirm(`Delete “${item.title}”? Its cards and review history will also be removed.`)) return;
    if (item.backend) {
      const response = await fetch(`${API_URL}/api/repertoires/${item.id}`, { method: 'DELETE' });
      if (!response.ok) return;
      onDeleteLocal(item.id, item.sourceName);
      await onQueueChanged();
      await loadBackend();
    } else if (item.id.startsWith('sample-')) setSamples((current) => current.filter((entry) => entry.id !== item.id));
    else onDeleteLocal(item.id, item.sourceName);
  }

  function exportPgn(item?: RepertoireItem) {
    if ((item?.backend || (!item && backendItems.length)) && usesLocalApi()) {
      const link=document.createElement('a'); link.href=item ? `${API_URL}/api/repertoires/${item.id}/export.pgn` : `${API_URL}/api/repertoires/export.pgn`; link.click(); return;
    }
    const text = item?.pgn ?? repertoires.flatMap((entry) => entry.pgn ? [entry.pgn] : []).join('\n\n');
    const url=URL.createObjectURL(new Blob([text],{type:'application/x-chess-pgn'}));
    const link=document.createElement('a'); link.href=url; link.download=item?`${item.title}.pgn`:'tempo-repertoires.pgn'; link.click(); URL.revokeObjectURL(url);
  }
  return (
    <section className="library-page" id="repertoire">
      <div className="page-heading">
        <div><p className="eyebrow">Your source material</p><h1>Repertoire</h1><p>Upload PGNs once. Tempo turns transpositions and shared prefixes into one clean set of cards.</p></div>
        <div className="heading-actions"><button onClick={()=>exportPgn()}>⇩ Export all PGN</button><button className="primary-button" onClick={onImport}>＋ Import PGN</button></div>
      </div>
      <div className="library-grid">
        {repertoires.map((item) => (
          <article className="repertoire-card" key={item.id}>
            <div className="repertoire-top"><span className="side-badge">{item.side}</span><span>{item.due ? `${item.due} due` : 'Up to date'}</span></div>
            <div className="mini-board" aria-hidden="true">{Array.from({ length: 16 }).map((_, index) => <i key={index} />)}</div>
            <div className="repertoire-name"><h2>{item.title}</h2><button onClick={()=>void rename(item)} title="Rename repertoire">✎</button></div><p>{item.detail}</p><small className="source-name">{item.sourceName}</small>
            <div className="maturity-row"><span>Maturity</span><strong>{item.progress}%</strong></div>
            <div className="maturity-track"><span style={{ width: `${item.progress}%` }} /></div>
            <div className="repertoire-actions"><button className="browse-button" onClick={onBrowse}>Browse tree</button><button onClick={()=>exportPgn(item)}>⇩ PGN</button><button className="delete-repertoire" onClick={()=>void remove(item)}>Delete</button></div>
          </article>
        ))}
        <button className="new-repertoire-card" onClick={onImport}><span>＋</span><strong>Add a repertoire</strong><small>PGN files stay on this computer</small></button>
      </div>
    </section>
  );
}