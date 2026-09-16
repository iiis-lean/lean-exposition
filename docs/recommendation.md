# Generation-free refinement recommendations

The local application uses the structural policy by default. A deterministic
seeded random policy remains available only as an explicit comparison baseline.

`make_structural_policy(workspace, hierarchy, feature_set, config=None, targets=())`
returns a `policy(context, candidate_ids)` callable for `ReaderService`. Context
requires `expanded` (including hidden descendant expansion memory); optional
`hierarchy_id` validates the bound hierarchy. `structure_id` is the separate content
structure identity. `locale` may be null, English or Chinese. `synopsis_codepoints`
contains only cached current candidate synopses. No future text is requested.

Targets are explicit node/DeclRef seeds, otherwise repository primary outcomes,
closed over internal provider Units. Without outcomes all internal Units are used.
Unresolved outcomes are reported. Each candidate simulates the actual visible
frontier after expanding it, including restored descendant state.

The current structural ordering is lexicographic: ascending cost tier,
descending declaration gain, descending dependency gain, source order, stable ID.
Declaration gain averages `1/N_after - 1/N_before` over target Units, where N is the
number of Units under the finest visible wrapper. Dependency gain averages newly
separated raw pairs over target consumers, each divided by `max(1, fixed internal
cross-Unit incoming pair count)`. Raw pairs are deduplicated; external and same-Unit
edges contribute neither numerator nor denominator. These are structural proxies,
not demonstrated understanding gains.

Material sizes at most 800 / 3000 / larger codepoints plan 240 / 600 / 1200
codepoints for each newly visible node. Restored expanded sections conservatively
receive a whole-section budget for their I/O; still-hidden descendants cost nothing.
Subtract a known current synopsis, then floor the increment at 120. Unknown synopses
are not subtracted. Estimates at most 600 / 1600 / larger are low / medium / high.
Missing required cost material forces high; unrelated missing compiled/type features
do not. All thresholds are uncalibrated engineering defaults, identical for zh/en.

Results retain `policy_id="structural"`, ranked targets, localized reasons and
components with cost basis, coverage, target closure, gains and the canonical
configuration digest. `random_policy` hashes the seed, view ID, and candidate ID
to produce a repeatable ordering with `policy_id="random"`; it explicitly makes
no mathematical-benefit claim. Neither policy generates or mutates text.
