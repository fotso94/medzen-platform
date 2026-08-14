# ASR base-model AWS change packet 2026-002V — VPC-resolver-aligned attempt 23

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002V only, authorizing numbered attempt 23 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

This draft authorizes nothing. After independent review PASS and that exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002V` must bind this
packet's final SHA-256. A committed read-only
`deadline_identity_and_acceptance` validation must then PASS against the
actual authorization, bindings and packet before any attempt envelope or AWS
mutation.

## Attempt-22 terminal result and confirmed cause

Attempt 22 is consumed. Eight of eleven stages passed; the GPU node existed
for at most 1,491 seconds; Torch was not imported and zero evaluation rows
started. The stage refused with `NETWORK_PROBE_REFUSED` /
`POSITIVE_NETWORK_CONVERGENCE_TIMEOUT`, after five DNS lookups returned
`gaierror` errno -3. Status-keyed cleanup passed and independent read-back
confirmed both node groups at desired zero, no temporary endpoint, security
group, volume, deadline action or evaluation namespace, the prior ECR scan
configuration restored, VPC CNI mode restored to standard, production
untouched and no object under `approved/asr`.

The immutable refusal record is SHA-256
`c364a1516e3e69add301444c090d14e996444bc66dd5840ad7871efdcdef8161`.
The diagnosis/correction record is SHA-256
`6a5374db68ea763deaf5437a9022d69d084cd6e57de6bf326ec95f03d95f5c4f`.
It binds the retained live pod and NetworkPolicy plus a read-only cluster
read-back. The live pod used `ClusterFirst`; strict egress permitted DNS only
to the VPC resolver at `172.31.0.2/32`; and kube-dns had zero ready endpoints
while both managed node groups were at desired zero. This is a confirmed pod
DNS-policy/available-resolver mismatch, not an endpoint-policy or model
failure.

## Complete DNS-isolation correction

Both the pilot and inbound-control pod now bind:

- `dnsPolicy: None`;
- `dnsConfig.nameservers: [172.31.0.2]`;
- the unchanged strict DNS egress rule, TCP/UDP 53 only to
  `172.31.0.2/32`.

Before the pilot Job is launched, the controller creates a policy-selected
DNS-control pod from the same rendered pod context and exact digest-pinned
evaluation image. That pod verifies its effective `/etc/resolv.conf`, resolves
every hostname in `network-binding.json` through the VPC resolver, and emits
one bounded pre-Torch receipt. The controller independently parses the actual
rendered `asr-eval-private-egress` NetworkPolicy and refuses unless every
resolved IP is inside its TCP/443 CIDR allowlist. Resolved IPs, effective
resolver, attempts and the control-pod spec hash are persisted. The pod has a
600-second terminal-state poll to include the known large first image pull and
uses twelve five-second DNS attempts once started.

Typed terminal refusals include `DNS_RESOLVER_UNREACHABLE`,
`DNS_EFFECTIVE_RESOLVER_DIFFERS`, `DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST` and
`DNS_CONTROL_POD_TIMEOUT`. No failure is treated as a network-isolation PASS.
Torch import before this gate and the existing positive/negative endpoint
probe both pass remains prohibited. Refusal diagnostics now discover the
actual `aws-eks-nodeagent` container from the live DaemonSet response, with
bounded historical fallbacks; no invented container name is accepted.

## Exact unchanged image and scoped risk continuation

There is no image rebuild. Attempt 22 live-proved the following exact image in
ECR and the successor uses it by immutable child digest:

- local/ECR tag `pilot-7efa6e8c`;
- OCI index `sha256:f14fe88a7ebb2c68bf2ed772ad2ce8913c1fa8117b2da5305af55298f1d15505`;
- linux/amd64 child `sha256:4d1ccde955f5ae074ed6470d7edb6d74f9d49cc6a6f44f9f0a2b7397a0cd3841`;
- config `sha256:2938427027f22b10f9dc5c89b3305b5689ea5c44b088839b85583e1575feeda3`;
- attestation `sha256:8d96d7c4b5b6f4a3c1677dc93301e2829afd0923b20eb269272b5e19dbf57e23`.

Attempt 23 skips publication by the existing exact-digest check. The security
gate remains mandatory and unchanged: ECR Basic is a supplementary OS gate at
zero critical/high, and the child is pulled by digest, verified byte-for-byte,
then scanned with pinned Docker Scout 1.18.3 at zero critical and exactly the
four accepted PyTorch high tuples. No registry scanning mutation is needed.

`ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003`, SHA-256
`43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034`,
continues only because the image and frozen public inputs are unchanged. Its
scope remains one offline evaluation window, no PHI, no untrusted inputs, no
inbound network, private S3/ECR-only egress, one GPU maximum and mandatory
destruction. It remains non-precedential for serving, training, traffic,
production, promotion or later evaluation windows.

## Cost reconciliation and allowance request

`COST-REGISTRY-2026-018`, SHA-256
`a8a9935b3281cba3fb7cde6fc6cfa343cf5540a9dee55a86a64fdf4b866f6b2c`,
conservatively commits the full attempt-22 ceiling because the current-day
Cost Explorer row is estimated and not attempt-attributable:

- aggregate project ceiling: $300;
- recognized committed guardrail: $194.4286064216;
- active reservations before this request: $0;
- headroom before request: $105.5713935784;
- requested attempt-23 ceiling: $10;
- headroom if approved: $95.5713935784.

No pending or zero estimated charge expands headroom. Attempt 23 receives one
fresh, non-transferable ceiling; no earlier attempt allowance is reused.

## Exact execution scope

Only after review PASS and exact owner approval:

1. remeasure all pre-envelope local prerequisites, including 40 GiB host-free
   and GPU-root capacity gates, and validate the committed real-artifact
   stage-one receipt;
2. create the deadline action before other mutations and execute numbered
   attempt 23 once, for at most 10,800 seconds and one GPU node;
3. verify the exact existing ECR image and run the unchanged digest-bound dual
   security gate without upload or registry mutation;
4. verify the 9-object, 13,116,686,091-byte frozen model/audio bundle by hash
   and version ID, with zero upload bytes;
5. create only the temporary encrypted input volume, endpoint/security-group,
   strict NetworkPolicy, namespace, DRA, claim and workload resources in the
   exact machine plan; scale the existing GPU ASG to one then zero;
6. pass stable GPU/DRA/sampler and numeric-UID node staging;
7. pass the new VPC-resolver pod-spec and resolved-IP consistency gate before
   launching the pilot Job;
8. run the existing pre-Torch positive/negative endpoint isolation proof, then
   the frozen 540-row, 47-language comparison of Whisper large-v3, Meta
   Omnilingual CTC-1B-v2 and Meta Omnilingual LLM-1B-v2;
9. persist every stage receipt immediately and run status-keyed cleanup on
   every terminal path.

Machine-plan counts: zero permanent creates, zero permanent updates, eighteen
temporary create-then-delete resources and one bounded capacity change.

Prohibited: reuse or extension of attempts 1-22; IAM or KMS changes;
Inspector Enhanced or registry-wide scanning changes; internet or inbound
access; training; serving; promotion; `approved/asr`; production SSM;
MLflow/model registration; language-registry changes; image rebuild or upload;
or any resource not in the exact machine plan.

## Binding and rehearsal gates

Bindings `ASR-BASE-MODEL-PILOT-BINDINGS-2026-002V` pin executor source commit
`d1e019f49871577b790e615c31cae761b17b6f7c`, all 32 executor modules including
the new shared DNS module, exact image and scan chain, risk record, cost
registry, diagnosis and all prior write-once artifacts.

The receipt-last cold rehearsal must be generated twice byte-identically from
the actual committed 002V bindings and actual `LiveOperations` composition,
with fakes only at paid external boundaries. In addition to all standing
scenarios, it must execute the actual rendered pod specs and prove:

- pilot and inbound-control pods use `dnsPolicy: None` with only
  `172.31.0.2` as nameserver;
- resolve-as-pod aligned PASS records only allowlisted IPs;
- an unreachable VPC resolver refuses with its typed code;
- any resolved IP outside the rendered TCP/443 allowlist refuses;
- the stale attempt-5 Scout-auth regression still refuses before an attempt
  envelope.

The committed receipt path is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002V-COLD/cold-rehearsal.json`.
Its SHA-256 and this packet's final SHA-256 are published in the independent
review package. No source or bindings edit is permitted after receipt
generation.

## Post-approval order

1. write and commit authorization 002V quoting the exact approval phrase;
2. run and commit the complete real-artifact stage-one dry validation;
3. run every pre-envelope prerequisite; only PASS may consume attempt 23;
4. execute once, persist every receipt and always clean up;
5. commit terminal evidence and reconcile actual billing when it lands.

No AWS execution has occurred under this packet. Local qualification does not
claim that any of the 540 inference rows has passed.
