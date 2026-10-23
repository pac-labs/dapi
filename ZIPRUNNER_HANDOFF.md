# ZIPRUNNER_HANDOFF v1

- Project: `Looprd-DAPI`
- Version: `0.4.0`
- ZIP filename: `Looprd-DAPI-0.4.0-ziprunner.zip`
- Kind: complete source update, not an overlay/patch or publishing blueprint.
- Scope: independent nested components, retaining generic registrations and existing Atrium v2 delivery.
- Release/deployment: not requested or authorized by this package.

The accompanying external handoff supplies the verified ZIP link and SHA-256.
See `evidence/subcomponents-0.4.0/SUMMARY.adoc` for this version’s checks and omissions.

## Import and Workboard preservation

The existing registered project is `Looprd-DAPI`. Its existing `define-next-pass`
card, criteria, suggested status and manager-review-needed decision are preserved
in the source progress proposal. New source tasks await independent review; none
is marked Done. The consumer's trusted local Workboard remains authoritative and
must merge by ID, preserving any decisions made after this snapshot.

The currently registered source destination/build commands came from the earlier
publishing planning scaffold. Review the new `project.zr` recipe and use a **clean
complete-source directory** under the same project identity. Do not blindly overlay
the scaffold: obsolete scaffold tests/validators may otherwise remain. No active
ZipRunner configuration or destination paths are shipped or changed automatically.
The recipe builds/tests only; it does not start services, push, publish or deploy.

The functional verification plan uses `dapi-source-pytest` as a declarative runner
reference. Map it to the trusted recipe's pytest gate through normal consumer
review; the plan cannot authorize execution. Producer logs are not consumer-owned
verification reports or operator acceptance.

## Matching Atrium required

**2026-10-19 update:** the actual Atrium 0.5.21 v2 receiver passed the Looprd 0.2.0
compatibility gate. See `docs/ATRIUM_COMPATIBILITY_GATE_20261019.adoc`. The original
source-delivery assessment below describes the earlier pinned receiver, not a
remaining implementation blocker. Live provisioning/acceptance is tracked separately.

The reviewed Atrium commit `264a109681e3c8a24039c3356d9f7fb556197fed` still has
legacy, operator-only intake. Do not enable this sender against that endpoint.
Receiver owners must implement the separate v2 contract in
`contracts/atrium-discovery-v2/README.adoc`, machine deployment/component/source
binding, persistent replay/tombstones and candidate review semantics.

The supplied receiver fixture is synthetic test code, not an installed receiver.
Real receiver integration, staging, independent QA and operator acceptance remain
open. Generic public deployment metadata and a scoped mounted machine credential
are configured only after that matching release is independently reviewed.

No GitHub push, container publication, receiver installation, node configuration
change, application migration or production deployment was performed for this ZIP.

## 0.3.0 consumer boundary

See docs/service-registrations.adoc. Unknown services are labels, not dependencies.
Consumer pull implementations and their host reader keys are separate work; this
package does not disable existing Atrium delivery, push or deploy anything.

## 0.4.0 subcomponents

Complete independent child specifications and nested mounts; see docs/subcomponents.adoc
and evidence/subcomponents-0.4.0/SUMMARY.adoc. Upgrade before adding declarations.
No application receiver, production config, push or deployment is included.
