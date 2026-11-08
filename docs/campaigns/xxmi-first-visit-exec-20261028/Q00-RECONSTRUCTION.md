# Portable native reconstruction check

The tracked inputs now reconstruct in an isolated local checkout. The aggregate
patch produces exactly the pinned bundled parent tree
`30e935aa00088be3f8493c143f8c6fa70ec59e6e`. Both aggregate and bundle routes are
supported by `tools/PreparedLoading/reconstruct-native.py`. The tool checks native
input hashes, the public base and parent tree, then applies speed, loading,
startup and retention patches in that order. It copies the exact generated
version header and records every reconstructed source file.

The retention patch has CRLF line endings while the reconstructed source uses LF.
Its application view replaces only CRLF terminators with LF. The raw evidence
patch remains unchanged. The receipt records both its original hash and the
derived application hash `e92e888270f2aae3e39383912310ee6e470bfdec4206c6b15b93ba2987a70dfc`.
No whitespace/context fuzzing or source-content repair is used.

Run with Python 3.10+ and Git, using a local upstream checkout that contains the
pinned base. Output and receipt paths must be new, task-owned locations outside
the two input repositories:

```text
python tools/PreparedLoading/reconstruct-native.py --native <upstream-checkout> --output <new-native-checkout> --verify-bundle --receipt <new-receipt.json>
```

The command currently exits **1** after writing its receipt. Reconstructed bytes
over the historical archive's member-name scope do not match the development
record; they must not be treated as a verified reconstruction of the original
ignored host source. The complete new source digest is a separate identity and
is not compared to a historical digest computed over a different file set.
This does not prevent inspection of the newly reconstructed source, but it leaves
Q00's historical-source equivalence and qualified-executor gates open.

| Check | Result |
| --- | --- |
| Pinned aggregate/bundled parent tree | Matched |
| Ordered source patches | Applied with the recorded retention terminator transform |
| Reconstructed Git tree | `7d70f3839746dbf27753dd03990d284cdfd30702` |
| Complete source files, including exact version header | 2,160 |
| Complete source digest | `e168b7f0cc83108ed5fe65991417d7b747b20561d4528e5d7f4581c202537b64` |
| Historical recorded source digest | `7bc377b215fedea252bfafc7f7aebcfe48c83186858cf353583d5ce80bd723bd` — **not matched** |
| Original ZIP membership scope | All 2,130 named files are present; scoped digests also fail to match the historical record |
| Reconstruction contract tests | 11 passed |
| Windows compiler/SDK, clean production/fixture builds and WARP | Not run in this Linux environment |

The archived `corresponding-source.zip` was inspected only as data to recover its
original member names. The reconstructed bytes over that exact scope hash to
`5875145201bc32d9c05c0cfb95fb7a25f89bec8d8a886ffd9dcdf5e1f524baf1`
using archive names including `native/`, or
`d3e72ec0690dcfdb5c7dfbdccac52b9e302ade724c65946a895c91b6fc3ff70e`
using relative native paths. Neither reproduces the historical receipt. The
original full local source/manifest is needed to explain that difference. No
claim about equivalence is inferred from matching individual historical hashes.
`historical_source_matches` uses only the original archive-name scope including
`native/`; the relative-name digest is diagnostic. If the archive is unavailable,
the match is `null` (unproven) and the command fails. Even a matching historical
scope covers only those archived names; the complete reconstructed file set
retains its separate manifest and digest.

[The compact receipt](q00-reconstruction.json) binds the exact inputs, transforms,
archive name scope and [complete source manifest](q00-source-files.json). It
separates current helper identities from their historical pins, so authorized
collector/build-tool changes cannot silently inherit old verification. Native
patches, bundle, generated header, candidate metadata and historical build record
remain strict inputs. Existing destinations and malformed, changed or reordered
native inputs fail closed.

No source evidence, tracked binary or normal installation was changed. No binary
was run, game launched, elevation requested, package signed or release published.
