/** Concrete Storage operations for the Workbench's one unresolved mutation per authority. */
const legacyKey = 'tempo-analysis-command-v1';
// Retain the existing authority slot: commands and run creation must never compete.
const prefix = 'tempo-analysis-command-v2:';

export function authorityKey(client:{connection:{origin:string;workspace:string}}) {
  return prefix + JSON.stringify([client.connection.origin,client.connection.workspace]);
}

export function persistPendingMutation(storage:Storage,key:string,serialized:string) {
  storage.setItem(key,serialized);
  if(storage.getItem(key)!==serialized)throw new Error('Unable to retain the request. Nothing was sent.');
}

export function readLegacyPendingCommand(storage:Storage) {
  return storage.getItem(legacyKey);
}

export function readPendingMutation(storage:Storage,storageKey:string) {
  return storage.getItem(storageKey);
}

export function removeLegacyPendingCommand(storage:Storage) {
  storage.removeItem(legacyKey);
}

export function clearPendingMutation(storage:Storage,storageKey:string,saved:unknown) {
  if(storage.getItem(storageKey)!==JSON.stringify(saved))throw new Error('Saved request changed during recovery. Pending evidence retained.');
  storage.removeItem(storageKey);
}
