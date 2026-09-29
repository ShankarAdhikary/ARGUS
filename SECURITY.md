# Security policy

ARGUS is a demonstration platform that runs on **synthetic data only**. It is not yet certified or approved for live
law-enforcement data (see "Before real use" in the README). Please do not load real case data into a stack you have not
hardened and had independently assessed.

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue:

- Use GitHub's **"Report a vulnerability"** button on the repository's Security tab (private vulnerability reporting).
- Include what you found, how to reproduce it, and the impact you see. A proof of concept against the demo stack is ideal.

You will get an acknowledgement, and the issue will be handled before any public write-up. Please give the maintainer a
reasonable time to fix it before disclosing.

## What is in scope

Authentication and sessions, role and jurisdiction access control (including the Postgres row-level security), the audit
log's integrity, injection and file-upload handling, the biometric image endpoint, and secret handling in this repository.

## Known and intentional

- The demo accounts documented in the README exist only when `SEED_DEMO_USERS=true` (the local-development default); the
  API refuses to start in production mode with them enabled, with placeholder secrets, or with a weak `JWT_SECRET`.
- `.env.example` contains only `CHANGE_ME` placeholders. `python ARGUS-Pipeline/scripts/init_env.py` generates real ones.
- The station-to-jurisdiction mapping in `ARGUS-Pipeline/jurisdictions.py` is synthetic.
