# Contributing

Start with the README's Python 3.12 setup and native build. Use a separate branch and temporary runtimes with synthetic media. Describe the behavior change, affected workflow, verification performed, and any remaining hardware acceptance limits.

For UI work, retain the 1000×700 layout and keep A/B source takeover and emergency hiding reachable. Check 1000×700 and 1440×900. Draft edits must remain local until explicitly published; selecting a history entry must not immediately resend it.

Do not include songs, third-party application binaries, credentials, complete cloud receipts, personal paths, or production diagnostics. Sanitize screenshots before attaching them to an issue. Cloud and real-device testing must be explicit and separate from routine tests.

Run relevant unit and Qt checks. The full suite is `PYTHONPATH=.:tests .venv/bin/python -m unittest discover -s tests`; native tests run as part of `bash scripts/build_native.sh`. Real DJ / TV acceptance must be reported separately.

Contributions to the project's original code are made under the MIT license in `LICENSE`. Third-party code or assets must retain their own compatible license and attribution.
