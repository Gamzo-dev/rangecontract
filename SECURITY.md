# Security

Launch the installed verifier with a trusted interpreter using `python -I -m rangecontract`. Install into a trusted virtual environment outside the target checkout using a normal installation. Ordinary `python -m rangecontract` can import a checkout-supplied `rangecontract.py` or package through the current directory or `PYTHONPATH`. Python chooses the module before RangeContract starts. Isolated mode excludes those paths and user site-packages.

Supply a contract you obtained from a trusted source and reviewed. With `--run-checks`, keep the contract outside the target checkout under your control. RangeContract never discovers a contract from a Git ref or automatically runs a command declared by a pull request. The SHA-256 identifies the bytes of the selected contract. It does not establish who produced or approved those bytes.

Trust the Python installation, local Git executable, repository metadata and config, validation executables, and process environment. A trusted command can load or execute target code. Use a disposable environment without access to secrets for code that requires isolation.

RangeContract checks exact path spellings, file types, raw Git blob IDs and Git metadata immediately before and after each validation command. It excludes only the root `.git` administrative entry from the filesystem comparison. The checks are observations with no filesystem lock. A concurrent change, a change reverted before the next scan, or a subprocess that outlives a timeout may escape detection. RangeContract provides no process sandbox. It checks the POSIX owner executable bit where applicable and does not attest other permissions, ACLs, or Windows execution rights. The report is local evidence without a cryptographic attestation.

Git's rename and copy heuristics can classify changes differently based on target attributes and ambiguous source candidates. Forbidden paths apply to Git's reported records and do not guarantee information flow or copy provenance.

Report suspected vulnerabilities privately to the repository owner through a private contact channel. Include the version, affected platform and a description with sensitive data removed.
