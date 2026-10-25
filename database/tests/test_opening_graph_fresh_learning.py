"""Pure fresh-start public-workflow proofs; capabilities are synthetic fakes."""

import copy
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "opening_graph_fresh_learning",
    Path(__file__).resolve().parents[1] / "tooling" / "opening_graph_fresh_learning.py")
fresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fresh)


def target():
    return {"target_id": "synthetic-disposable-target", "workspace_id": "synthetic-fresh-workspace",
            "timezone": "Etc/UTC", "disposable": True, "initialized": True,
            "empty_learning": True, "observed_at": "2030-01-02T12:00:00Z"}


def exported():
    # The real injected public Content verifier owns the complete existing
    # SourceSnapshot/Publication/route schemas and chess legality. No duplicate
    # domain model or fake claim of legality is introduced by this boundary test.
    return {"schema_version": 1, "kind": "content_bridge_export", "complete": True,
            "workspace_id": "synthetic-fresh-workspace", "source": {"source_digest": "a" * 64},
            "publication": {"workspace_id": "synthetic-fresh-workspace",
                            "publication_id": "synthetic-publication", "publication_generation": 1,
                            "source_revision": 1, "built_at": "2030-01-02T12:00:00Z",
                            "definitions": [{"synthetic": "owning verifier checks full definition"}]},
            "legacy_aliases": {}, "historical_mapping_unknown": ["synthetic-no-history-required"]}


def initial_status():
    return {"schema_version": 1, "workspace_id": "synthetic-fresh-workspace",
            "publication_id": "synthetic-publication", "status": "unavailable", "accepted": None,
            "publication_generation": None, "activation_revision": 0, "local_date": "2030-01-02"}


def arguments():
    return {"target": target(), "operation_id": "synthetic-approved-operation",
            "content_cli": "/synthetic/bin/tempo-content", "learning_origin": "http://synthetic-disposable.invalid",
            "operator_token_file": "/synthetic/private/operator-token", "initial_status": initial_status(),
            "verify_export": lambda export: True}


def prepare(**changes):
    options = arguments()
    options.update(changes)
    return fresh.prepare_fresh_learning(exported(), **options)


def responses(plan):
    publication = plan["export"]["publication"]
    acknowledgement = {"contract_version": 1, "workspace_id": plan["target"]["workspace_id"],
                       "activation_id": plan["activation_request"]["activation_id"],
                       "publication_id": publication["publication_id"],
                       "publication_generation": publication["publication_generation"],
                       "activation_revision": 1, "status": "activated", "reason": None}
    receipt = {"operation_id": acknowledgement["activation_id"], "state": "complete",
               "workspace_id": acknowledgement["workspace_id"], "command_name": "publication.activate.v2",
               "request": copy.deepcopy(plan["activation_request"]), "response": acknowledgement}
    accepted = {key: publication[key] for key in ("workspace_id", "publication_id",
                                                "publication_generation", "source_revision")}
    accepted.update(source_digest=plan["export"]["source"]["source_digest"], publication_digest=fresh._digest(publication),
                    routes_digest="c" * 64, bundle_schema_version=2, bundle_digest="b" * 64)
    status = {"schema_version": 1, "workspace_id": acknowledgement["workspace_id"],
              "publication_id": publication["publication_id"], "publication_generation": 1,
              "activation_revision": 1, "status": "active", "accepted": accepted, "bundle_digest": "b" * 64}
    return {"operator_result": {"id": plan["operation_id"], "state": "completed"},
            "receipt": receipt, "status": status, "verify_fresh_effects": lambda plan, status: True}


class FreshLearningBoundaryTests(unittest.TestCase):
    def test_public_plan_uses_complete_existing_apply_cli(self):
        plan = prepare()
        self.assertEqual(plan["apply_argv"], ["tempo-a-bridge", "--content-cli", "/synthetic/bin/tempo-content",
            "--learning-origin", "http://synthetic-disposable.invalid", "--operator-token-file",
            "/synthetic/private/operator-token", "apply", "synthetic-approved-operation"])
        self.assertEqual(plan["activation_request"], {"publication_id": "synthetic-publication",
            "publication_generation": 1, "expected_activation_revision": 0,
            "activation_id": "synthetic-approved-operation:activate"})
        self.assertEqual(plan["initial_due_date"], "2030-01-02")
        self.assertNotIn("stage", plan)  # Existing bridge alone owns V2 bundling.

    def test_live_uninitialized_or_nonempty_target_cannot_be_planned(self):
        for field in ("disposable", "initialized", "empty_learning"):
            observed = target()
            observed[field] = False
            with self.subTest(field=field), self.assertRaises(fresh.FreshLearningError):
                prepare(target=observed)

    def test_explicit_workspace_effective_timezone_and_observation_required(self):
        for field, value in (("workspace_id", ""), ("target_id", ""), ("timezone", "local"),
                             ("observed_at", "2030-01-02T12:00:00")):
            observed = target()
            observed[field] = value
            with self.subTest(field=field), self.assertRaises(fresh.FreshLearningError):
                prepare(target=observed)
        observed = target()
        observed.pop("empty_learning")
        with self.assertRaises(fresh.FreshLearningError):
            prepare(target=observed)

    def test_workspace_and_initial_learning_date_are_never_guessed(self):
        observed = target()
        observed["workspace_id"] = "synthetic-other-workspace"
        with self.assertRaises(fresh.FreshLearningError):
            prepare(target=observed)
        for due in (None, "today", "20300102", "2030-02-31"):
            status = initial_status()
            status["local_date"] = due
            with self.subTest(due=due), self.assertRaises(fresh.FreshLearningError):
                prepare(initial_status=status)

    def test_complete_public_graph_closure_verification_required(self):
        for answer in (False, 1, None):
            with self.subTest(answer=answer), self.assertRaises(fresh.FreshLearningError):
                prepare(verify_export=lambda export: answer)
        export = exported()
        export["complete"] = False
        with self.assertRaises(fresh.FreshLearningError):
            fresh.prepare_fresh_learning(export, **arguments())

    def test_old_history_unknown_mapping_is_intentionally_not_a_gate(self):
        self.assertTrue(exported()["historical_mapping_unknown"])
        self.assertEqual(prepare()["activation_request"]["expected_activation_revision"], 0)

    def test_existing_publication_or_staged_state_is_not_an_empty_target(self):
        for mutation in (lambda status: status.update(status="active", activation_revision=1),
                         lambda status: status.update(accepted={"synthetic": "already staged"}),
                         lambda status: status.update(workspace_id="synthetic-other")):
            status = initial_status()
            mutation(status)
            with self.subTest(mutation=mutation), self.assertRaises(fresh.FreshLearningError):
                prepare(initial_status=status)

    def test_empty_target_cannot_adopt_old_physical_objects(self):
        export = exported()
        export["legacy_aliases"] = {"synthetic-content": "synthetic-old-physical"}
        with self.assertRaises(fresh.FreshLearningError):
            fresh.prepare_fresh_learning(export, **arguments())

    def test_cli_arguments_require_explicit_origin_and_do_not_embed_credentials(self):
        for origin in ("", "http://user:secret@synthetic.invalid", "http://synthetic.invalid/api", "file:///synthetic"):
            with self.subTest(origin=origin), self.assertRaises(fresh.FreshLearningError):
                prepare(learning_origin=origin)
        with self.assertRaises(fresh.FreshLearningError):
            prepare(operation_id="x" * 128)

    def test_public_capability_failure_is_sanitized(self):
        def unavailable(export):
            raise RuntimeError("synthetic-private-database-url")
        with self.assertRaises(fresh.FreshLearningError) as error:
            prepare(verify_export=unavailable)
        self.assertNotIn("synthetic-private", str(error.exception))

    def test_completed_content_and_exact_learning_receipt_return_existing_ack(self):
        plan = prepare()
        observed = responses(plan)
        self.assertEqual(fresh.verify_fresh_learning(plan, **observed), observed["receipt"]["response"])
        observed["operator_result"] = {"operation_id": plan["operation_id"], "state": "pending_activation"}
        with self.assertRaises(fresh.FreshLearningError):
            fresh.verify_fresh_learning(plan, **observed)

    def test_pending_failed_or_foreign_learning_receipts_never_complete(self):
        plan = prepare()
        for mutation in (lambda receipt: receipt.update(state="queued"),
                         lambda receipt: receipt.update(state="failed"),
                         lambda receipt: receipt.update(workspace_id="synthetic-other"),
                         lambda receipt: receipt["request"].update(activation_id="synthetic-other"),
                         lambda receipt: receipt["response"].update(activation_revision=True)):
            observed = responses(plan)
            mutation(observed["receipt"])
            with self.subTest(mutation=mutation), self.assertRaises(fresh.FreshLearningError):
                fresh.verify_fresh_learning(plan, **observed)

    def test_active_status_must_bind_exact_source_definitions_and_bundle(self):
        plan = prepare()
        for field in ("publication_digest", "source_digest", "bundle_digest"):
            observed = responses(plan)
            observed["status"]["accepted"][field] = "f" * 64
            with self.subTest(field=field), self.assertRaises(fresh.FreshLearningError):
                fresh.verify_fresh_learning(plan, **observed)
        observed = responses(plan)
        observed["status"]["status"] = "retired"
        with self.assertRaises(fresh.FreshLearningError):
            fresh.verify_fresh_learning(plan, **observed)

    def test_independent_graph_and_new_default_effects_check_is_required(self):
        plan = prepare()
        observed = responses(plan)
        packets = []
        observed["verify_fresh_effects"] = lambda plan, status: packets.append((plan, status)) or False
        with self.assertRaisesRegex(fresh.FreshLearningError, "independent fresh-target"):
            fresh.verify_fresh_learning(plan, **observed)
        self.assertEqual(packets, [(plan, observed["status"])])

    def test_repeated_planning_and_verification_do_not_mutate_inputs(self):
        export, options = exported(), arguments()
        before_export = copy.deepcopy(export)
        plan = fresh.prepare_fresh_learning(export, **options)
        observed = responses(plan)
        before = copy.deepcopy((plan, observed["operator_result"], observed["receipt"], observed["status"]))
        fresh.verify_fresh_learning(plan, **observed)
        fresh.verify_fresh_learning(plan, **observed)
        self.assertEqual(export, before_export)
        self.assertEqual((plan, observed["operator_result"], observed["receipt"], observed["status"]), before)


if __name__ == "__main__":
    unittest.main()
