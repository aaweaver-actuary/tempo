const pendingKey = "tempo-pending-game-sync-command-v1";

export function readPendingGameSync(): string | null {
  return localStorage.getItem(pendingKey);
}

export function storePendingGameSync(pending: object): void {
  localStorage.setItem(pendingKey, JSON.stringify(pending));
}

export function clearPendingGameSync(): void {
  localStorage.removeItem(pendingKey);
}

const productPendingKey = (scope: string) => `tempo-pending-product-game-sync-v1:${encodeURIComponent(scope)}`;

export function readPendingProductGameSync(scope: string): string | null {
  return localStorage.getItem(productPendingKey(scope));
}

export function storePendingProductGameSync(scope: string, pending: object): void {
  localStorage.setItem(productPendingKey(scope), JSON.stringify(pending));
}

export function clearPendingProductGameSync(scope: string): void {
  localStorage.removeItem(productPendingKey(scope));
}

const refreshKey = (scope: string, provider: string) => `tempo-pending-game-refresh-v1:${encodeURIComponent(scope)}:${provider}`;
export function readPendingGameRefresh(scope: string, provider: string): string | null {
  return localStorage.getItem(refreshKey(scope, provider));
}
export function storePendingGameRefresh(scope: string, provider: string, pending: object): void {
  localStorage.setItem(refreshKey(scope, provider), JSON.stringify(pending));
}
export function clearPendingGameRefresh(scope: string, provider: string): void {
  localStorage.removeItem(refreshKey(scope, provider));
}
