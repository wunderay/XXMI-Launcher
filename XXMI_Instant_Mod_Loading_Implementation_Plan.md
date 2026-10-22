# XXMI Near-Instant In-Game Mod Activation — Implementation Plan

**Date:** October 21, 2026  
**Status:** Source-reviewed implementation proposal; no implementation, deployment, or live performance validation is claimed.  
**Primary codebase:** `SpectrumQT/XXMI-Libs-Package`  
**Source baseline inspected:** `a7847eae82aaac32175c9a7d199673d5c9323bcd`  
**Secondary integration:** `SpectrumQT/XXMI-Launcher`  
**Initial validation target:** Windows, Direct3D 11, ZZMI. Keep the native loading engine game- and character-agnostic; qualify other importers before claiming support.

## 1. Objective and boundaries

Make a prepared mod appear on its first eligible rendered frame without synchronous mod-file reads, image decoding, shader compilation, or GPU resource creation on the activation path. Also remove repeat loading after changing outfits, revisiting characters, and refreshing unchanged resources.

The primary problem is **in-game time until a correct, complete mod appears**, not launcher startup speed. First-use loading is the first release objective. Nonblocking configuration refresh follows; incremental INI compilation is a later optimization, not a prerequisite for the first useful release.

Preparation and activation are separate operations. Report both. A mod that takes seconds to prepare but activates quickly is not an instant cold load. Truly arbitrary unprepared content cannot meet the same target as a compatible prepared working set.

Keep original mod files unchanged. Do not decimate geometry, downscale textures, perform lossy recompression, disable outfit choices, omit LODs, or change facial animation to achieve speed. Derived cache entries must preserve rendering semantics. Do not change injection or anti-cheat behavior.

GPU caches live inside the current game process and device lifetime. They may survive F10 refreshes, but do not survive process exit or device recreation. Only file manifests, intermediate representations, shader bytecode, and decoded asset data may be persisted on disk.

## 2. Acceptance contract

All numerical values below are proposed engineering targets, not measured results. Phase 0 establishes the hardware-specific baseline before these gates are frozen.

| Scenario | Proposed acceptance target |
|---|---|
| Prepared compatible mod selected before the activation cutoff | First eligible frame uses the complete replacement; no disk reads, decode, compile, resource creation, or worker waits on that path |
| Warm activation bookkeeping | p99 CPU time at or below 1 ms on qualified hardware |
| Gameplay while preparing mods | p99 added frame time at or below 1 ms against a paired control; any attributable stall over 8 ms requires investigation |
| Character revisit, outfit toggle, or LOD transition within the prepared working set | No avoidable first-use asset work; correct appearance without partial replacements |
| Newly installed or modified mod | Existing complete generation continues; preparation time and request-to-visible time are both reported |
| No-change fast refresh | Reuse compiled configuration and compatible assets only where the requested initialization semantics permit it |
| Failure, memory pressure, or unsupported behavior | No crash, partial activation, silent quality reduction, or false “instant” result; retain safe previous behavior and report the reason |

Track p50/p95/p99 and maximum values, cache coverage, cold misses, bytes read, CPU/RAM/VRAM use, canceled jobs, and first-correct-frame latency. A “GPU objects ready” flag is not proof of physical residency or absence of driver work; actual frame measurements remain the gate.

## 3. Confirmed code starting points

At the pinned baseline:

- `DirectX11/CommandList.cpp:6407–6469`, `CustomResource::Substantiate()`, defers creation until resource use. Its comments explain the need for the correct device and final binding information. It invokes file loading under the resource-creation-mode mechanism. [S1]
- `DirectX11/CommandList.cpp:6471–6508`, `CustomResource::LoadBufferFromFile()`, performs `CreateFile`, allocation, `ReadFile`, and buffer creation synchronously. [S1]
- `DirectX11/IniHandler.cpp:2179–2207`, `ParseResourceSections()`, clears resource and resource-pool maps before rebuilding them. [S2]
- `DirectX11/IniHandler.cpp:5159–5245`, `ReloadConfig()`, rebuilds configuration and command lists, refreshes shader processing state, and performs initialization work. It must not simply be moved wholesale onto a worker. [S3]

Additional integration points to audit in Phase 0 are resource/view acquisition, `CustomShader::substantiate()`, deferred shader replacement, the active swapchain’s Present path, input dispatch, resource pools, context creation, and device teardown. Recheck locations when rebasing.

## 4. Architecture

### 4.1 Proposed components

Create a native `DirectX11/ModLoading/` subsystem. Names below are proposed, not existing interfaces.

| Component | Responsibility |
|---|---|
| `LoadTrace` | Low-overhead phase timings, frame attribution, cache and readiness counters |
| `AssetCatalog` | Source identities, descriptors, dependencies, exclusions, observed usage |
| `PreparationScheduler` | Bounded I/O/decode queues, priorities, cancellation, memory backpressure |
| `CpuAssetCache` | Immutable source/decoded bytes with bounded memory and optional derived disk storage |
| `DeviceResourceCache` | Compatible resource/view/shader ownership scoped to a device epoch |
| `PreparationPlanner` | Determines which assets and prerequisites must be ready for an activation unit |
| `MemoryBudgetController` | Process-budget-aware GPU admission and RAM/cache eviction |
| `ChangeMonitor` | File notifications, stable snapshots, overflow recovery, reconciliation |
| `ConfigCompiler` | Detached parse/link/validation without modifying live renderer state |
| `GenerationManager` | Atomic activation, reader ownership, migration, rollback, retirement |

### 4.2 State model

Use explicit states such as:

`Discovered -> SnapshotVerified -> CPUReady -> DeviceObjectsReady -> ValidatedForActivation -> Active -> Retiring -> Released`

`Failed`, `Canceled`, and `CompatibilityOnly` are separate states. Attach a source generation, device epoch, compatibility classification, and failure reason. Never reuse the current single `substantiated` flag as a complete asynchronous state machine.

An activation unit is a dependency-connected group of overrides, resources, shaders, and state—not necessarily a folder. Shared libraries and cross-mod references may enlarge the unit. Publish a complete unit; do not mix old geometry with new textures, or enable `handling = skip` before the replacement is available.

### 4.3 Cache identity and ownership

CPU cache identity includes a digest of the actual snapshot bytes, decoder/cache-format version, effective color-space policy, dimensions, format, mip/array layout, and relevant conversion options. Metadata checks accelerate change detection but do not establish content identity.

GPU cache identity additionally includes the device epoch, effective resource descriptor, bind/miscellaneous flags, creation-mode requirements, and view compatibility. Resource ownership and view ownership must both be explicit.

Share GPU objects only for resources proven read-only under their command dependencies. Identical source files do not make writable runtime resources interchangeable. Mutable buffers, resource pools, UAV outputs, and copy/clear targets need separate state or a conservative compatibility path. Immutable source bytes may still be shared.

## 5. Delivery stages

### Phase 0 — Establish the reproducible baseline

**Tasks**

Build the pinned upstream source before changing behavior. Identify the actual DLL loaded into the game, importer configuration, GPU/driver, creation flags, RAM and GPU budgets, and storage characteristics. Audit the real scope and cost of resource and configuration synchronization.

Instrument discovery, file read, decode, shader preparation, GPU creation, view creation, command-list work, readiness wait, activation, and first correct frame. Use bounded in-memory tracing; export asynchronously rather than writing a verbose line for every draw.

Create a standalone D3D11 fixture host and repeatable in-game scenarios. Measure first character appearance, character revisits, every outfit toggle, LOD transitions, F10, and loading a newly added mod. Capture both long freezes and delayed appearance without a freeze.

**Exit gate:** Baseline report ties each delay to a measured stage or explicitly labels it unresolved. Source inspection alone does not establish the local bottleneck. Tests and images identify a known-correct rendering baseline.

### Phase 1 — CPU-side prewarming

**Tasks**

After successful legacy configuration parsing, collect file-backed resource descriptors into the catalog. Schedule bounded file reading and image decoding in worker threads. Initialize COM appropriately for worker-side WIC use; do not manipulate the immediate context.

Retain source bytes and decoded data separately where useful. Preserve original color-space and alpha behavior, row/slice layout, mip levels, and existing compressed DDS payloads. Do not adopt lossy conversion as a default optimization.

Use versioned, checksummed, bounded cache files outside recursively loaded mod folders. Write entries atomically, treat corruption as a miss, and keep originals untouched. Validate sizes and arithmetic before allocating memory. Handle files changing while they are read, and publish only verified snapshots.

Modify the resource-loading path to consume prepared CPU data when available. Preserve the legacy fallback during rollout, but count every fallback as a preparation miss—not as successful instant loading.

**Exit gate:** Prepared fixture assets incur zero file read/decode work on first use. Rendering matches the baseline. Cancellation, missing files, corruption, memory pressure, and resource resizing cannot corrupt shared cache data.

### Phase 2 — GPU preparation and shader/view warming

**Tasks**

Identify the game’s actual rendering device and allocate a monotonically increasing device epoch. Do not prepare on an unrelated dummy device. Resolve effective descriptors and bind flags before precreation; unknown dynamic cases remain CPU-ready until safely resolved.

Respect `D3D11_CREATE_DEVICE_SINGLETHREADED`. On eligible devices, check `DriverConcurrentCreates` and audit XXMI’s own wrapper/creation-mode state before allowing device calls from workers. Keep immediate-context and swapchain operations on their owning path. Driver concurrency support alone does not make XXMI’s surrounding code thread-safe. [S4–S6]

Prepare compatible buffers, textures, views, known custom shaders, and independent state objects. Upload data through properly ordered operations. Do not run arbitrary mod commands or game-dependent compute work merely to warm resources.

Use a bounded device-creation queue with a render-owned fallback for devices that cannot safely create concurrently. Time/byte budgets are advisory: an individual driver call cannot be assumed preemptible. Large operations may need a known loading window or a classified compatibility path.

Warm known shader replacements and validate existing caches. Newly encountered game shaders with unresolved runtime dependencies remain an explicit limitation rather than being marked ready prematurely.

**Exit gate:** A qualified prepared mod’s first visible use makes no file/decode/compile/create calls. Cross-device reuse is rejected. Rendering and context state are preserved. Driver stalls and compatibility fallbacks are included in measured results.

### Phase 3 — Working-set prediction and adaptive memory control

**Tasks**

Prioritize the currently observed mod resources, associated outfit choices and LODs, recently used resources, explicitly selected profiles, then other enabled candidates. Record associations from actual dependencies and usage; do not assume a folder name identifies a character or that XXMI knows the future roster.

Provide a strict prepared-profile mode: finish preparing a selected working set before reporting it ready. Also provide adaptive mode, which warms the likely working set while the game runs. Neither mode claims that arbitrarily many mods fit in memory.

Use `QueryVideoMemoryInfo` for the same game process and relevant adapter segments. The process budget includes the game itself, not a separate allowance for XXMI. Admission should consider `Budget - CurrentUsage`, safety headroom, queued allocations, and the estimated simultaneous size of old/new generations. Use conservative fallbacks when budget information is unavailable. [S7–S8]

Protect active resources from cache-policy eviction; apply an LRU-style policy to speculative entries and retain reusable CPU data when affordable. “Protected by our cache” does not mean physically pinned by the OS. Stop speculative work before memory pressure causes paging.

**Exit gate / first useful milestone:** First character appearance, prepared outfit switches, and prepared LOD transitions meet the warm-path gates on the selected ZZMI corpus. Resource use remains bounded in a longer gameplay session. This milestone does not yet promise nonblocking full INI reloads.

### Phase 4 — Preparation on file changes

**Tasks**

Monitor relevant source directories with overlapped `ReadDirectoryChangesW`. Observe creates, changes, deletes, and renames. Use parent monitoring or reattachment logic for watched-root replacement. Watch resolved dependencies, not just one Mods directory. Exclude the subsystem’s own caches and logs.

Coalesce bursts and validate stable snapshots. Debouncing is a heuristic, not proof that a multi-file installation is complete. Cooperating installers should offer an atomic directory replacement or completion manifest; otherwise validate the dependency closure and keep the current generation until a consistent candidate is available.

Recover from notification overflow or unsupported notification behavior by reconciliation. Microsoft documents that notification details can be lost and enumeration is then necessary. [S9]

Cancel obsolete generations, prioritize the newest verified request, and provide a bounded full-rescan action. Asset-only updates can prepare before the detached configuration compiler is complete; INI changes remain queued for compatible reload until Phase 5.

Default behavior prepares automatically but preserves the user’s existing activation workflow. Automatic activation is a separate explicit mode, not a surprise change to F10 behavior.

**Exit gate:** Partial copies, rapid saves, atomic renames, deletions, overflow, and same-size/same-timestamp fixture changes cannot activate stale or incomplete candidates. Document the residual consistency boundary for noncooperating multi-file writers.

### Phase 5 — Detached configuration builds and transactional activation

**Tasks**

Refactor parsing/linking to accept a build context instead of writing live global maps. Remove process-global locale changes from concurrent parsing. Preserve deterministic include order, namespaces, duplicate-key behavior, persistent state, and override precedence.

Represent compiled structure separately from mutable runtime state. Replace raw cross-generation references with generation-owned references or stable handles. Resource/pool maps are rebuilt inside a candidate generation; reusable assets have separate ownership and do not disappear when the old maps are cleared.

Define migration policy explicitly: persistent variables take their latest valid runtime values at commit; nonpersistent variables and initialization commands retain documented upstream reload semantics; pools and mutable resources are reset or migrated only under a proven rule. Do not treat every unchanged reload as a harmless no-op.

Build a migration plan, validate the complete candidate, and prepare its activation dependencies before publication. Recheck source/device versions and relevant runtime revisions at commit.

Activate at a verified safe boundary. A raw pointer swap in Present is insufficient if other hooks or deferred command lists retain references. Readers must pin a generation; defer publication when quiescence or bounded migration cannot be established. Retire old generations only after all CPU readers and relevant GPU work are safe. Never busy-wait for a GPU query on the render-critical path.

For a failed replacement, keep the last-good complete generation. For a brand-new unsupported override, maintain original drawing only where the dependency boundary makes that safe. Otherwise use an explicit compatibility route. Do not partially enable skip/draw/resource overrides.

Classify arbitrary initialization side effects and global overrides. Unsupported semantics remain on the legacy path and do not receive the instant-readiness claim. Keep forced reload/reset behavior distinct from a validated fast refresh.

**Exit gate / second milestone:** Prepared configuration changes activate without global rebuild work on the critical path. Stress tests find no mixed generations, dangling references, lost settings, incorrect reinitialization, or partial draws. The previous generation survives candidate failure intact.

### Phase 6 — Incremental configuration and cache refinement

**Tasks**

Cache versioned parsed intermediate representations and dependency graphs. Rebuild changed files plus transitive dependents; use deterministic linking. Include parser/ABI/schema versions in cache invalidation. Expand to a conservative full candidate rebuild for ambiguous global dependencies.

Shader cache identity must include source/include content, compile definitions and flags, compiler/cache version, and applicable patching rules. Device/driver-specific artifacts require appropriate invalidation. Avoid declaring new game shaders supported before they are observed and qualified.

Compare incremental and clean builds structurally and behaviorally. Keep a full-rebuild switch for diagnosis.

**Exit gate:** Incremental output matches a clean build across test fixtures, and measurements show lower preparation cost. This stage must not delay the first-use milestone.

### Phase 7 — Launcher integration and release qualification

**Tasks**

Add version-gated launcher controls for off/adaptive/prepared-profile behavior, preparation progress, memory limits, and diagnostics. Show “preparing,” “ready,” and compatibility reasons separately from mod enabled/disabled status. New options are proposed; they are not current XXMI settings.

Use an isolated development package and a supported update/deployment path. Record the loaded DLL identity, preserve package authenticity checks, and prevent upstream updates from silently replacing the test binary. Do not overwrite an active library, broaden trust settings, or require mod authors to rewrite their packages.

Roll out as off -> CPU cache -> GPU prewarming -> adaptive preparation -> transactional refresh. Each feature remains independently disableable. Rollback restores the previous DLL/configuration; derived caches are expendable.

**Exit gate:** Verified rollback, source-linked build artifacts, supported-version matrix, reproducible benchmark evidence, and independent review. Qualify other importers individually rather than inferring them from ZZMI success.

## 6. Guild-ready work packages

These are proposed assignments; no quests or coding sessions were launched by this plan.

| ID | Work package | Dependencies | Required evidence |
|---|---|---|---|
| IM-01 | Pinned baseline, loaded-DLL verification, timing instrumentation | None | Baseline traces and bottleneck attribution |
| IM-02 | D3D11 fixture host and rendering corpus | IM-01 interfaces | Reproducible resource, shader, LOD, and toggle cases |
| IM-03 | Asset catalog, snapshots, dependency identity | IM-01 | Snapshot and invalidation tests |
| IM-04 | CPU read/decode cache and bounded scheduler | IM-03 | Zero warm-path file reads/decodes |
| IM-05 | Device epochs, GPU/view/shader preparation | IM-02, IM-04 | No invalid cross-device reuse; warm frame evidence |
| IM-06 | Working-set policy and memory admission | IM-05 | Pressure/revisit/LOD tests; bounded usage |
| IM-07 | Change monitor, cancellation, reconciliation | IM-03, IM-04 | Partial-write, overflow, rename, delete tests |
| IM-08 | Detached compiler and generation ownership | IM-02, IM-03 | No live-state mutation during build |
| IM-09 | Transactional activation, migration, retirement | IM-05–IM-08 | Race, rollback, persistent-state, and atomicity tests |
| IM-10 | Incremental builds and shader-cache refinement | IM-08, IM-09 | Equivalence to clean rebuild |
| IM-11 | Launcher controls, version gating, packaging | IM-06, IM-09 | Update/disable/rollback verification |
| IM-12 | Independent acceptance and release report | Relevant milestone quests | Signed verdict with trace/build/source provenance |

Use Adventurers for implementation, fixtures, local commands, builds, evidence collection, and bounded repairs. Independent reviewers should own acceptance. Parallelize isolated cache/test/monitor work; serialize changes to shared parser/runtime ownership interfaces. Escalate only genuine authorization, missing credentials, required physical interaction, or product decisions. Do not launch Codex instances without the existing per-instance approval.

## 7. Validation matrix and release evidence

Test small and large libraries, mesh-heavy and texture-heavy mods, many alternate outfits, LOD0/1/2 transitions, facial/eyebrow/mouth animation, compute/morph features, shared libraries, duplicate overrides, and persistent settings. Prioritize the user's existing difficult ZZMI cases rather than assuming static geometry covers the corpus.

Include cold OS cache, warm OS cache with empty XXMI cache, warm CPU cache with absent GPU objects, and warm GPU objects as distinct conditions. Test immediately after process start and device recreation, not only repeat toggles.

Measure multiple resolutions/frame-rate targets and high-memory-pressure conditions, including the temporary old-plus-new-generation peak. Retained objects are not a guarantee against OS paging. [S7–S8]

Exercise rapid F10, no-change refresh, force reset, deletion, symlinks/junctions and path aliases, Unicode names, huge/corrupt assets, cache tampering, disk full, cancellation, allocation failure, stale completion, root replacement, and device loss. Cache parsing must enforce bounds and never treat data as executable code.

Use paired runs of pinned upstream and the modified build with identical configurations and scenarios. Record at least 100 warm activations per qualified scenario for an initial p99 estimate, and identify sample size and uncertainty; repeat cold-start scenarios separately. Performance instrumentation must not itself introduce synchronous logging stalls.

Static tests and the standalone host are necessary but insufficient. Release acceptance requires actual in-game evidence of correct appearance and timing. Do not claim compatibility for untested mods/importers. Archive source SHA, compiler configuration, DLL digest, hardware/driver, mod manifests, settings, traces, screenshots/video where appropriate, and the review verdict.

## 8. Recommended cut

The first release is **measured CPU/GPU prewarming with bounded memory and exact rendering compatibility** (Phases 0–3). It directly targets delayed first appearance and repeat outfit/LOD stalls.

The second release adds **continuous preparation and safe transactional refresh** (Phases 4–5). The final optimization layer makes preparation itself cheaper through incremental compilation (Phase 6).

Success means a prepared, supported working set appears correctly without waiting on loading work—not merely that a loading indicator disappears sooner.

## Sources

[S1] XXMI native resource creation and buffer loading, pinned source: https://github.com/SpectrumQT/XXMI-Libs-Package/blob/a7847eae82aaac32175c9a7d199673d5c9323bcd/DirectX11/CommandList.cpp#L6407-L6508

[S2] XXMI resource-map rebuild, pinned source: https://github.com/SpectrumQT/XXMI-Libs-Package/blob/a7847eae82aaac32175c9a7d199673d5c9323bcd/DirectX11/IniHandler.cpp#L2179-L2207

[S3] XXMI configuration reload, pinned source: https://github.com/SpectrumQT/XXMI-Libs-Package/blob/a7847eae82aaac32175c9a7d199673d5c9323bcd/DirectX11/IniHandler.cpp#L5159-L5245

[S4] Microsoft, Introduction to Multithreading in Direct3D 11: https://learn.microsoft.com/en-us/windows/win32/direct3d11/overviews-direct3d-11-render-multi-thread-intro

[S5] Microsoft, Object Creation with Multithreading: https://learn.microsoft.com/en-us/windows/win32/direct3d11/overviews-direct3d-11-render-multi-thread-create

[S6] Microsoft, D3D11_CREATE_DEVICE_FLAG: https://learn.microsoft.com/en-us/windows/win32/api/d3d11/ne-d3d11-d3d11_create_device_flag

[S7] Microsoft, IDXGIAdapter3::QueryVideoMemoryInfo: https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/nf-dxgi1_4-idxgiadapter3-queryvideomemoryinfo

[S8] Microsoft, DXGI_QUERY_VIDEO_MEMORY_INFO: https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/ns-dxgi1_4-dxgi_query_video_memory_info

[S9] Microsoft, ReadDirectoryChangesW: https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-readdirectorychangesw

[S10] Microsoft, D3D11_FEATURE_DATA_THREADING: https://learn.microsoft.com/en-us/windows/win32/api/d3d11/ns-d3d11-d3d11_feature_data_threading
