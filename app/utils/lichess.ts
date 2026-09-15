import { randomUrlSafe } from "./urls";

export async function connectLichess() {
  const verifier = randomUrlSafe();
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  const challenge = btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
  const state = randomUrlSafe(20);
  const redirectUri = `${location.origin}${location.pathname}`;
  sessionStorage.setItem("tempo-lichess-verifier", verifier);
  sessionStorage.setItem("tempo-lichess-state", state);
  sessionStorage.setItem("tempo-return-view", "analysis");
  const url = new URL("https://lichess.org/oauth");
  url.search = new URLSearchParams({
    response_type: "code",
    client_id: "tempo.local.chess.trainer",
    redirect_uri: redirectUri,
    code_challenge_method: "S256",
    code_challenge: challenge,
    state,
  }).toString();
  location.assign(url.toString());
}
