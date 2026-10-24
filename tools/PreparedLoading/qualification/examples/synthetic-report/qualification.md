# Prepared loading qualification

Prepared optimization qualified: **False**
Full release qualified: **False**
Diagnostic collection pass: **True**

Synthetic evidence is never real-game qualification.

## Gates

- pass: provenance-and-lossless-paired-collection
- fail: synthetic fixture is NOT game qualification

## Scenario statistics

```json
[
  {
    "name": "outfit-toggle",
    "cache_state": "warm-gpu",
    "bookkeeping_ms": {
      "n": 100,
      "p50": 0.2,
      "p95": 0.2,
      "p99": 0.2,
      "maximum": 0.2,
      "uncertainty": {
        "method": "95% DKW distribution-free CDF band",
        "cdf_epsilon": 0.13581015157406195,
        "p99_lower_ms": 0.2,
        "p99_upper_ms": null,
        "note": "100 samples give a preliminary tail estimate; null upper bound is unbounded."
      }
    },
    "paired_added_frame_ms": {
      "n": 100,
      "p50": 0.40000000000000036,
      "p95": 0.40000000000000036,
      "p99": 0.40000000000000036,
      "maximum": 0.40000000000000036,
      "uncertainty": {
        "method": "95% DKW distribution-free CDF band",
        "cdf_epsilon": 0.13581015157406195,
        "p99_lower_ms": 0.40000000000000036,
        "p99_upper_ms": null,
        "note": "100 samples give a preliminary tail estimate; null upper bound is unbounded."
      }
    },
    "issues": []
  }
]
```

## Limits

- Offline validation checks supplied evidence integrity and consistency; it cannot authenticate a game capture or reviewer identity.
- Performance thresholds use observed empirical p99; uncertainty is reported and may be large.
- Qualification applies only to the exact importer/version, scenarios and conditions listed. No publisher signing or release authorization is performed.
