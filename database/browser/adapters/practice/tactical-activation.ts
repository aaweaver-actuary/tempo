/** Existing durable browser persistence; application decisions stay with callers. */
type ActivationIdentity = { packIds: string[]; active: boolean };
const activationKey = ({ packIds, active }: ActivationIdentity) =>
  `tempo-tactics-activation-pending-v1:${[...packIds].sort().join(",")}:${active}`;
export function readPendingPackActivation(identity: ActivationIdentity) { return localStorage.getItem(activationKey(identity)); }
export function writePendingPackActivation(identity: ActivationIdentity, operationId: string) { localStorage.setItem(activationKey(identity), operationId); }
export function removePendingPackActivation(identity: ActivationIdentity) { localStorage.removeItem(activationKey(identity)); }
