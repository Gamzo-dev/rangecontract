# Changelog

## 0.1.0 (unreleased)

- Document isolated Python invocation for the installed verifier and test module-shadowing resistance.
- Verify the working-tree inventory and raw Git blob IDs before and after validation commands, including entries skipped by Git status.

- Add Git range and path-policy verification with pinned base commits, rename and copy handling, and file-count limits.
- Add human and JSON reports and explicit execution of required validation commands.
- Add example contracts, real-repository integration tests, and CI for Windows, Linux, and macOS.
