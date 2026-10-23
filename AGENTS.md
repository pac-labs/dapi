# Looprd / DAPI source instructions

Read DESIGN.adoc, PROJECT_PLAN.adoc, TESTING.md and the relevant contract docs.
Use this complete source tree; do not replace the server with a planning scaffold.
Match the DESIGN.adoc UTF-8/LF fingerprint recorded in PROJECT_PLAN.adoc.

Keep modules cohesive, target 400 lines and review before exceeding 500.
New behavior requires focused regressions, the full suite and fresh-package checks.
Build UI assets before importing the panel from a source checkout.

Discovery is optional infrastructure. Failure must not block component activation.
Never replace scoped machine intake with an operator/approval credential, infer
public runtime origins from untrusted requests, or fabricate Git provenance.
Keep publisher review and publication in Atrium. No external identity provider
is mandatory. Treat receiver fixtures as tests, not installed production code.

Source ZIPs must retain complete source, project identity, mandatory Workboard
receipt, verification plan and existing decisions. Do not self-mark tasks Done.
No secrets, private deployment configuration, runtime state or node backups in ZIPs.
Push, publication, receiver installation and deployment require separate authority.

For this version the delivery request explicitly prohibits automatic push/deploy.
