"""Pure synthetic v2 model measurement; no database, network, or user data."""
import json, resource, sys, time
from pathlib import Path
sys.path.insert(0, str(Path('services/learning/runtime/tests').resolve()))
from test_content_bridge import content_bundle
from tempo_learning_runtime.publication_bridge import PublicationBundleV2, canonical
count = int(sys.argv[1])
start = time.perf_counter()
payload = content_bundle(count)
generated = time.perf_counter()
bundle = PublicationBundleV2.model_validate(payload)
validated = time.perf_counter()
print(json.dumps({'definitions':len(bundle.publication.definitions),'facts':len(bundle.facts),'routes':len(bundle.routes),'bindings':sum(len(r.bindings) for r in bundle.routes),'normalized_bytes':len(bundle.model_dump_json().encode()),'canonical_stage_bytes':len(canonical(bundle.model_dump(mode='json')).encode()),'generation_seconds':generated-start,'validation_seconds':validated-generated,'peak_rss_bytes_macos':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'atomic_activation':'not exercised','fixture':'existing seeded content_bundle: one unique position, route, binding and move per definition'},sort_keys=True))
