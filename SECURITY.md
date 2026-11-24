# Security

Supply a contract you obtained from a trusted source and reviewed. With `--run-checks`, keep the contract outside the target checkout under your control. RangeContract never discovers a contract from a Git ref or automatically runs a command declared by a pull request. The SHA-256 identifies the bytes of the selected contract. It does not establish who produced or approved those bytes.

Trust the local Git executable, repository metadata and config, validation executables, and the environment used to run them. A trusted command may load or execute target code. Run checks against code you trust, or in an isolated, disposable environment without access to secrets. RangeContract is a policy checker and provides no process sandbox, protection against concurrent checkout changes, or guarantee that descendants of a timed-out command are terminated. The validation report is local evidence without a cryptographic attestation.

Report suspected vulnerabilities privately to the repository owner through a private contact channel. Include the version, affected platform and a description with sensitive data removed.
