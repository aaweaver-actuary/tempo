# Training on an iPhone

Tempo's PostgreSQL database stays on the computer. The phone keeps one prepared daily queue and an unsynced review journal in its own browser storage.

1. Run `docker compose up -d --build` on the computer and sign in to Tailscale on both devices.
2. On the computer, run `tailscale serve --bg 3000`. Use the private HTTPS address shown by `tailscale serve status`; do not use Funnel. Docker binds Tempo's web and API ports to localhost, and Serve proxies the web port into your tailnet.
   After a Tempo update, run `npm run verify:phone-deployment` on the computer. The check confirms that the running web app and API both include phone queue support. Set `TEMPO_PHONE_URL` to the private HTTPS address to check that route too.
3. Open that address in iPhone Safari. Choose **Share → Add to Home Screen**, then open Tempo from its Home Screen icon.
4. Open **Train** while the computer is reachable. Wait for **Phone queue prepared for YYYY-MM-DD** before leaving.
5. Train from the prepared queue without the computer. Reviews say **Saved on phone** until replay reaches PostgreSQL. Open the Home Screen app with the computer reachable to sync.

The phone will not invent a new day's queue. Reconnect to prepare each day. Defensive exercises need the computer's grading service and are identified separately when offline. If another device reviewed the same card first, Tempo keeps the PostgreSQL result and shows the unsynced phone conflict. Keep the Home Screen app installed until every review has synced; browser storage can be evicted under device storage pressure.

While Train is open and visible, Tempo checks the live queue every 30 seconds and refreshes the complete phone copy every minute. It also refreshes on reconnection or when you return to the app. An **Offline queue** notice shows when the copy was prepared and offers **Retry sync**. Its card count can differ from the computer's until the phone reconnects and saved reviews replay.

Prepared authored Study exercises with a supported rubric snapshot can be answered offline. Tempo saves the actual move, square set, route, choice, or explanation response in the phone journal and validates it again against the pinned revision when syncing. Unsupported rubric versions, stale revisions, and another device's completed review remain visible as unavailable or conflicted evidence rather than an incorrect answer. An offline explanation or unrecognized open move requires an explicit self-assessment after the rubric is shown.

If Tempo says an update is ready, finish the current attempt and reopen the Home Screen app while connected. Do not clear Safari website data or uninstall Tempo while reviews are saved on the phone.

Tailscale Serve must remain enabled, and the computer and Docker Tempo must be running for a fresh queue or sync. See the [Tailscale Serve documentation](https://tailscale.com/docs/features/tailscale-serve) for HTTPS and access controls.
