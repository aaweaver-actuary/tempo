import assert from 'node:assert/strict';

// A verified stopped generation can precede delivery of the host input marker.
// Keep it until the matching interaction arrives; receipt delivery is not stop time.
export function createInferenceYieldReceipt({kind, readInteraction, publish}) {
  let stopped, acknowledged = false;
  return {
    record(value) {
      assert.equal(value.proof?.verified, true, 'A running=false observation is not verified cancellation');
      assert(Number.isInteger(value.generation) && value.startedAt <= value.stoppedAt);
      stopped = Object.freeze({...value, proof: Object.freeze({...value.proof})});
    },
    async confirm() {
      if (acknowledged) return true;
      const interaction = await readInteraction();
      if (!stopped || interaction?.started !== true || interaction.generations?.[kind] !== stopped.generation ||
          interaction.at < stopped.startedAt || interaction.at > stopped.stoppedAt) return false;
      await publish({at: stopped.stoppedAt, generation: stopped.generation, started_at: stopped.startedAt,
        outcome: stopped.outcome, pre_interaction_deferrals: stopped.preInteractionDeferrals, proof: stopped.proof});
      acknowledged = true;
      return true;
    },
    async waitForTurn({allowed, pause, deadline, now = Date.now, acknowledge = true}) {
      if (acknowledge && await this.confirm()) return false;
      while (!await allowed()) {
        if (acknowledge && await this.confirm()) return false;
        assert(now() < deadline, 'Capped inference did not resume within its bounded rehearsal');
        await pause(25);
      }
      return true;
    },
  };
}
