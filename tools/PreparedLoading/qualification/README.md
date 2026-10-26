# Offline prepared loading evidence gate

Run with Python 3.10+ and the standard library only:

```
python tools/PreparedLoading/qualify.py bundle/manifest.json --output-dir qualification-report
```

The command reads local files and writes JSON and Markdown reports. It never launches a game, requests UAC, deploys DLLs, signs releases, or uses the network. Exit 0 requires full release qualification; exit 1 means a gate failed or remains pending. A valid diagnostic collection, a qualified prepared scenario, and a qualified full release are separate results. A partial supported matrix entry can exist while full release qualification remains pending. No entry is inferred for another importer.

`manifest.schema.json` describes the interchange envelope. The executable performs the authoritative semantic validation; no schema dependency is installed. Each reference is `{ "path": "relative/local/file", "sha256": "64 lowercase hex digits" }`. Files must resolve inside the bundle, exist, match their digest and remain at most 64 MiB each. CSVs have at most 250,000 rows. Duplicate JSON keys, missing files, malformed/nonfinite measurements, altered hashes, truncated CSVs, duplicate pairs and mismatched conditions fail closed. Collect bounded separate complete sessions rather than discard records to fit these limits.

The synthetic example is executable contract documentation, **NOT game evidence or qualification**. Reproduce its negative report with:

```
python tools/PreparedLoading/qualify.py tools/PreparedLoading/qualification/examples/synthetic/manifest.json --output-dir /tmp/synthetic-qualification
```

## Run and capture contract

Baseline and candidate each identify source commit SHA, source archive, exact patch, DLL and compiler build record. The compiler JSON binds `source_sha`, `source_archive_sha256`, `patch_sha256`, and `dll_sha256`, plus nonempty `compiler_id`, `flags`, `architecture`, `build_configuration`, and `build_command`. Preserve original build logs in the archived evidence. The tool checks the binding, not whether an archive actually corresponds to a Git commit or the compiler performed the asserted build; independent provenance review remains necessary.

Importer/version, game version, hardware, driver and references for mod manifest, settings, configuration, scenario protocol and instrumentation must be identical in each pair. Include the exact GPU/CPU/RAM/OS, driver version, settings/resolution/frame target, mod paths/content hashes, cache state, capture procedure and instrumentation overhead controls in these artifacts. Different source, patch and DLL identities are allowed and expected. Baseline and candidate DLL digests must differ. This version defines a ZZMI protocol; other importers fail closed and require their own independently reviewed protocol and evidence.

Each full-session native trace has this exact CSV header:

```
stage,kind,capture_session,start_ns,duration_ns,start_frame,end_frame,thread_id,object_id,bytes,result
```

End with exactly one complete footer, newline included:

```
# cumulative_accepted=N;dropped_full=0;dropped_contention=0;drained=N
```

For standalone v1 exports, N must equal all data rows and be nonzero. The two exact native preamble comments are accepted before the CSV header; lifetime loss counters must remain zero. Do not submit cumulative process counters against only a subset of exported records.

Native streaming capture v2 keeps the same 11 columns. It emits these exact first four lines (SESSION is a positive odd uint64):

```
# load_trace_v1; monotonic_ns; frame_ids_are_attribution_only
# activation_and_first_correct_frame_require_explicit_external_evidence
stage,kind,capture_session,start_ns,duration_ns,start_frame,end_frame,thread_id,object_id,bytes,result
# capture_begin=SESSION;accounting=capture_v2
```

Its single final line has this exact field order:

```
# capture_end=SESSION;structural_complete=1;lossless=1;accepted=N;drained=N;written=N;dropped_full=0;dropped_contention=0;stale=0;outstanding=0;foreign_discarded=F;accounting_available=1;reason=stopped
```

The begin/end session and every data row's session must agree. N must equal all current-session rows and be nonzero. Every counter must fit uint64. F counts discarded records belonging to older sessions and may be nonzero: those older records neither count toward N nor poison the current capture's loss accounting. A foreign-session row in the file fails. `structural_complete=1` means only that the footer was formatted; it cannot override any lost/stale/outstanding event, unavailable accounting, lossless=0, count mismatch, or non-stopped reason. Size/time limits, write failures, replaced sessions, missing/partial footers and mixed cumulative/streaming accounting fail closed. Streaming exports use capture-scoped counters; they do not use the standalone lifetime footer.

No incomplete export marker, lost event, malformed row, or size limit is accepted. This parser follows the native `LoadTrace.cpp`/`TraceControl.cpp` grammar, not the untransferred collector 0.1.6 format. Any collector adapter requires separate validation against the actual raw files and must preserve original files, source and command without inventing events. The 0.1.6 reported capture receipt is not an input trace.

Capture JSON binds `trace_sha256`, `samples_sha256`, `loaded_dll_sha256`, and `origin`; records `collector_version`, `capture_id`, ordered `started_utc`/`ended_utc`, positive `process_id`, `loaded_dll_path`, `normalizer_version`, and `normalization_command`; and requires `measurement_scope` equal to `all-activation-path-operations-and-paired-frames`, `size_limit_reached: false`, `malformed_rows: 0`. Loaded DLL identity must come from the running process, not merely the DLL on disk. Capture origins must match the manifest (`actual-game` or `synthetic-test`). Reviewer confirmation must establish that normalization and instrumentation cover all the operations claimed; absence of an instrumented event alone does not prove absence of work.

Normalized samples use the exact header:

```
pair_id,scenario,cache_state,activation_ms,frame_ms,eligible_frame,visible_frame,file_op,read,decode,compile,create,worker_wait
```

Each baseline/candidate sample is matched by `(scenario, cache_state, pair_id)` under an identical protocol. Times are finite, nonnegative milliseconds; frames and operation counts are nonnegative integers. Keep one independent activation/window per pair and document how frames/windows are selected. Do not treat multiple draws of a single activation as independent activations. Warm qualification requires `cache_state` = `warm-gpu`, at least 100 activations per claimed scenario, observed bookkeeping p99 <= 1 ms, observed paired added-frame p99 <= 1 ms, no candidate operation counters, and exact equality of first eligible and first complete visible frames. Other cache conditions remain separate diagnostic/release matrix cases, not substitutes for warm evidence. Pairwise frame deltas are computed before quantiles; the gate does not subtract independent p99 values. Any positive pair delta over 8 ms remains pending investigation; this version conservatively requires recollection after resolving/excluding an independently established unrelated cause rather than accepting a self-asserted exception.

Reports include n, nearest-rank p50/p95/p99, maximum, and a distribution-free 95% DKW CDF band translated to p99 bounds. The upper p99 bound is unbounded for small n (including n=100); 100 is only an initial tail estimate. This band assumes independent representative samples. Reports cannot establish that assumption. Preserve cold misses, bytes read, cache coverage, CPU/RAM/VRAM, canceled jobs, preparation time and request-to-visible latency in the instrumentation/condition evidence; independent review must assess those plan requirements.

## Recovery, independent review and release

`recovery` references JSON binding candidate and prior DLL digests and the origin. It references a JSONL `event_log`, nonempty raw `captures`, and `capture_ids`. Two actual observation event types are required: `candidate-failure-retained-generation` and `rollback-restored-prior`. Each event records UTC, reason, observation, capture ID/digest, previous/observed generation and observed DLL digest. Failure retains the previous complete generation while the candidate DLL runs; rollback restores the prior DLL and generation. Capture digests must bind the referenced raw files. A `passed: true` field alone proves nothing and is rejected. Review the raw observations, persisted settings/mod preservation, crash/partial-activation behavior and exact rollback procedure.

`review` JSON follows `review.schema.json`: it names the independent reviewer, review UTC and independence basis, binds baseline/candidate DLL digests, candidate trace/sample digests and recovery digest, lists every claimed scenario, and provides written `visual_observation` and `first_eligible_frame_observation`. It also requires `evidence_sha256`, a noncircular binding to the complete reviewed evidence. To compute it, remove only the top-level `review` member from the manifest, serialize the remaining object with Python `json.dumps(evidence, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)`, encode that string as UTF-8, and take its lowercase SHA256 hexadecimal digest. `qualify.py:review_evidence_sha256` implements this exact contract. No newline is appended; object key order is canonical, while array order remains significant. Nonfinite values are rejected.

This digest covers schema/origin, the complete baseline and candidate run records (including source/build identities, samples, traces, capture metadata references and all paired conditions), scenario/cache claims, recovery and any release coverage reference. Each referenced file and its nested evidence are separately checked against their recorded SHA256, so the binding extends to those bytes. The only excluded reference is the review itself: finalize all other evidence before review, then record `evidence_sha256`, save the review JSON and hash it into the manifest's `review` reference. Any evidence change, including a baseline normalization, capture provenance, shared settings/protocol, scenario claim or addition/change of release coverage, requires fresh independent review and a new binding. Updating artifact/capture hashes alone cannot reuse an earlier acceptance. Existing reviews without this binding fail closed.

Qualification requires verdict `accept`, appearance `correct-complete` and timing `first-eligible-frame`. Screenshots/video are optional supporting evidence; a resource-ready flag or timing counter cannot replace the explicit visual/timing attestation. Hashes protect archived bytes, not honesty or reviewer identity. An authorized independent human must authenticate the captures, observe the game and attest the result; the importer is not a digital signing or identity verification service. Recomputing a digest is not itself a review or evidence of an authenticated reviewer.

A full release additionally requires `release_coverage` JSON binding origin/candidate digest, with a `cases` object containing every case in `qualify.py:RELEASE_CASES` (no omissions or extras). Every case must record `verdict: accept`, observation, conditions, reviewer, reviewed UTC and at least one hashed evidence reference. The independent review must bind its digest as `release_coverage_sha256`. These cover mod classes, caches, startup/device recreation, resolution/frame targets/memory pressure and adversarial/failure scenarios from the plan. They are observation records, not boolean checkboxes; independent review must judge their adequacy. This gate does not sign or authorize publisher deployment. Real captures, independent acceptance and the authorized publisher's private signing key remain external requirements.
