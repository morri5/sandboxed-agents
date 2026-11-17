`compare_versions` in the `versions` package compares numbers as text: `compare_versions("1.10.0", "1.9.0")` returns `-1`, but it should return `1`.

Fix it so that it follows Semantic Versioning 2.0.0 precedence for versions of the form `MAJOR.MINOR.PATCH` with an optional pre-release (`1.0.0-alpha.1`):

- `MAJOR`, `MINOR` and `PATCH` compare numerically.
- A version with a pre-release is lower than the same version without one (`1.0.0-rc.1` < `1.0.0`).
- Pre-releases compare identifier by identifier, split on dots: identifiers of digits only compare numerically, others compare as ASCII text, and a numeric identifier is lower than a non-numeric one. If all shared identifiers are equal, the version with fewer identifiers is lower.

`latest` uses `compare_versions` and should then be right as well.
