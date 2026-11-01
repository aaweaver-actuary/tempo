// Each family owns its durable claim/recovery and callback routes. The ordinary
// journal is shared by all callbacks; only Defense claims have a separate one.
export function createEngineFamilyRequest(durableRequest, defenseClaimRequest) {
  return function request(path, options = {}, deliveryOptions = {}) {
    const defenseClaim = path === "/api/defensive-threats/analysis/claim";
    const journal = defenseClaim ? defenseClaimRequest : durableRequest;
    return journal.send(path, {
      ...options,
      ...(defenseClaim ? { pollAttempts: 20 } : {}),
      headers: { ...options.headers, "X-Tempo-Engine-Worker": "docker" },
    }, deliveryOptions);
  };
}

function callback(request, prefix, job, action, payload, deliveryOptions) {
  const options = { method: "POST", body: JSON.stringify(payload) };
  if (action === "report") return request(`${prefix}/${job.id}/${action}`, options);
  return request(`${prefix}/${job.id}/${action}`, options, deliveryOptions);
}

async function allowed(request, path, predicate) {
  try {
    const control = await request(path, { signal: AbortSignal.timeout(75), requestAttempts: 1 });
    return predicate(control);
  } catch {
    return false;
  }
}

export function createEngineJobFamilies({ request, durableRequest, defenseClaimRequest }) {
  const gamePrefix = "/api/games/analysis/position";
  const defensePrefix = "/api/defensive-threats/analysis";
  const game = {
    kind: "game", workClass: "engine_game",
    allowed: () => allowed(request, "/api/system/foreground-active",
      control => control.active === false && control.available !== false && control.study_only_mode !== true),
    async admit(job) {
      if (await this.allowed(job)) return true;
      await request(`${gamePrefix}/${encodeURIComponent(job.id)}/release`, {
        method: "POST", body: JSON.stringify({ lease_id: job.lease_id }),
      });
      return false;
    },
    async recover() {
      const recovered = await durableRequest.recover();
      return recovered?.path === `${gamePrefix}/claim` ? recovered.result.job : null;
    },
    async claim(preferredGameId) {
      await request("/api/games/analysis/repair-timeout", { method: "POST" });
      await request("/api/games/analysis/repair-provenance", { method: "POST" });
      return (await request(`${gamePrefix}/claim`, { method: "POST",
        ...(preferredGameId ? { body: JSON.stringify({ preferred_game_id: preferredGameId }) } : {}),
      })).job;
    },
    isFinalize: job => job.kind === "finalize",
    finalize: job => request(`${gamePrefix}/finalize`, {
      method: "POST", body: JSON.stringify({ lease_id: job.lease_id }),
    }),
    finalizeFailure: (job, error) => request(`/api/games/analysis/${encodeURIComponent(job.game_id)}/failure`, {
      method: "POST", body: JSON.stringify({ lease_id: job.lease_id,
        error: `Game finalization failed: ${error.message.slice(0, 900)}` }),
    }),
    complete: (job, report, diagnostics) => callback(request, gamePrefix, job, "report", { lease_id: job.lease_id, report, diagnostics }),
    release: (job, error, options) => callback(request, gamePrefix, job, "release", { lease_id: job.lease_id, diagnostics: error.diagnostics }, options),
    fail: (job, error, options) => callback(request, gamePrefix, job, "failure", { lease_id: job.lease_id, error: error.message.slice(0, 1000), diagnostics: error.diagnostics }, options),
  };
  const defense = {
    kind: "defense", workClass: "engine_defense",
    allowed: job => allowed(request,
      `${defensePrefix}/${encodeURIComponent(job.id)}/control?lease_id=${encodeURIComponent(job.lease_id)}`,
      control => control.search_allowed === true && control.foreground_active === false),
    async admit(job) {
      if (await this.allowed(job)) return true;
      await request(`${defensePrefix}/${encodeURIComponent(job.id)}/release`, {
        method: "POST", body: JSON.stringify({ lease_id: job.lease_id }),
      });
      return false;
    },
    async recover() { return (await defenseClaimRequest.recover())?.result; },
    claim: () => request(`${defensePrefix}/claim`, { method: "POST" }),
    // Preserve the existing finalize discriminator even for malformed recovered work.
    isFinalize: game.isFinalize,
    finalize: game.finalize,
    finalizeFailure: game.finalizeFailure,
    complete: (job, report, diagnostics) => callback(request, defensePrefix, job, "report", { lease_id: job.lease_id, report, diagnostics }),
    release: (job, error, options) => callback(request, defensePrefix, job, "release", { lease_id: job.lease_id, diagnostics: error.diagnostics }, options),
    fail: (job, error, options) => callback(request, defensePrefix, job, "failure", { lease_id: job.lease_id, error: error.message.slice(0, 1000), diagnostics: error.diagnostics }, options),
  };
  return { game, defense };
}
