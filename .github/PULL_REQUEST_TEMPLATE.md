<!--
Thanks for contributing to tts-daemon! Keep changes small and well-reasoned.
Fill in the sections below and tick the checklist before requesting review.
-->

## Summary

<!-- What does this PR change, and why? One or two paragraphs. -->

## Related issue

<!-- e.g. "Closes #123". Larger changes should have an issue discussing the design first. -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] New provider or player
- [ ] Documentation only
- [ ] Breaking change (API/config shape, event names)

## Checklist

- [ ] `make check` passes locally (ruff lint + format check + full pytest suite)
- [ ] New behaviour and failure paths are covered by tests, and tests stay hermetic (no network, sound devices, real user config, or real cache)
- [ ] Documentation updated (`docs/`, `README.md`) where user-facing behaviour changed
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`
- [ ] The gateway's runtime dependency footprint is unchanged (new engines ship as optional extras with lazy imports)
- [ ] `config.example.yaml` regenerated if the config schema changed
