# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.2] - 2026-09-18

Maintained-fork release. The original project is by **Edd Mann**
([eddmann/garmin-connect-mcp](https://github.com/eddmann/garmin-connect-mcp));
all original work is his. This fork continues maintenance while upstream is
inactive, and every change here is offered back as a pull request.

### Fixed

- `manage_weight_data` **add** passed the date to `add_weigh_in` in the
  `unitKey` position, so the call always raised
  `ValueError: unitKey must be one of {'kg', 'lbs'}`. It now uses
  `add_weigh_in_with_timestamps`.
- `manage_weight_data` **delete** passed comma-separated `samplePk` ids to
  `delete_weigh_ins`, which expects a date, so the requested entries were never
  the ones removed. Entries are now resolved against the day and deleted
  individually by `samplePk`.
- Date-only additions are anchored to local **noon** rather than midnight.
  Garmin re-buckets entries using the *account* timezone, so a midnight anchor
  lands in the previous calendar day whenever that timezone sits behind UTC.

### Added

- Optional `unit`, `local_timestamp`, `gmt_timestamp` and `delete_all`
  parameters on `manage_weight_data`.
- `tests/test_weight.py` covering every add and delete path.

## [1.0.1] - 2026-05-19

### Changed

- Document published `uvx garmin-connect-mcp` usage as the primary setup path
- Simplify Claude Desktop configuration for published `uvx` usage
- Store interactive setup credentials in `~/.garminconnect.env` by default

## [1.0.0] - 2026-05-19

### Added

- Initial Garmin Connect MCP server release
- Garmin Connect activity, health, training, profile, device, gear, weight, workout, and women's health tools
- MCP resources for athlete profile, training readiness, and daily health context
- MCP prompts for training analysis, sleep quality, readiness checks, activity analysis, run comparison, and health summaries
- `garmin-connect-mcp` server entrypoint
- `garmin-connect-mcp auth` interactive authentication setup with MFA token persistence
- Docker image support via GitHub Container Registry

[1.0.1]: https://github.com/eddmann/garmin-connect-mcp/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/eddmann/garmin-connect-mcp/releases/tag/v1.0.0
