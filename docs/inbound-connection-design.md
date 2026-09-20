# Inbound connections — budget and acceptance preparation

[中文](inbound-connection-design_zh.md)

Status: **P2-02 preparation only**. This document classifies the work, fixes
admission/release semantics and defines local acceptance cases. It enables no
new timeout, quota, setting, route or HTTP/WS response behavior. Numeric values
below are a future local-test profile, not proposed production defaults. Real
slow-upload and laboratory-network measurements are required before rollout.

## Observed implementation boundary

The current [`_GatewayHTTPServer`](../openai4s/server/gateway.py) inherits
`ThreadingHTTPServer`: a connection gets a handler thread before its request
line, headers or identity are known. HTTP/1.1 keep-alive is enabled. There is no
gateway-owned pre-header admission limit or inbound phase deadline in this path.
This is a code observation, not a claim of a reproduced attack incident.

| Surface | Current behavior to preserve or distinguish |
| --- | --- |
| Protected HTTP routes | Host/Origin and application authentication precede `_prepare_request_body`. Login/invite and other explicitly public routes have their own authorization rules; do not describe every unauthenticated request as authenticated before reading its body. |
| `Expect: 100-continue` | The Handler does not override `handle_expect_100`. The stdlib can emit 100 during header parsing, **before** `_route` performs application admission. That interim response is not task acceptance. |
| Ordinary bodies | `_read_request_body` validates framing and size, then reads the declared length into memory once; the cache is cleared after dispatch. The shared cap is currently 128 MiB. Duplicate/negative length and nonempty Transfer-Encoding are refused; incomplete input closes the connection. |
| JSON artifact uploads | `POST /api/v1/uploads` carries base64 or text JSON, not multipart. It uses the buffered body path and requires exactly one content field. It is not the streamed file-area route. |
| Raw session archives | `/api/v1/sessions/import` and `/api/v1/sessions/verify` use the 128 MiB buffered archive path; verification is not import or execution. |
| File-area upload | [`file_routes.py`](../openai4s/server/file_routes.py) handles `POST /api/v1/files/upload`, up to 512 MiB, in 1 MiB chunks into a temporary file, then `os.replace`. `_prepare_request_body` deliberately skips the whole-body read for this exact route. |
| WebSocket | `/api/v1/ws` checks Host/Origin/auth before 101 and refuses an upgrade body. [`ws_frames.py`](../openai4s/server/ws_frames.py) bounds a gateway frame to 16 MiB and rejects continuation frames. Existing count/byte queue limits keep a slow subscriber from blocking producers. |
| Resource release | `WSConnection._drop` marks dead and clears its queue but does not shut down the socket. It alone does not unblock a current reader/writer. Both message and `/api/v1/frames/{fid}/kernel/execute` synchronous HTTP paths can wait on `MessageJob.wait_result()` independently of the socket; closing the socket alone does not wake that event wait. |
| Execution ownership | An accepted MessageJob continues without observers. Only an explicit authorized cancellation targets its exact execution/owner. WS reconnect uses the existing epoch/sequence and REST reconciliation; it is not task resubmission. |

## Phase clocks

Use monotonic clocks. Idle budgets measure time since actual byte progress;
absolute phase budgets do not reset when one byte or a heartbeat arrives.
Each blocking I/O operation uses the smaller of its remaining idle and phase
allowances. Accounting must observe bytes actually read, not wait for a buffered
`read(n)` to fill: a legal 1 MiB upload chunk can take longer than the idle budget.
On timeout, close/discard the buffered stream; do not resume reading a poisoned
`makefile` object or interpret leftover body bytes as the next request.

Setting one socket timeout before `read(length)` or `readline()` does not enforce
an absolute deadline against a peer that keeps supplying bytes. The future reader
must recalculate at actual receive boundaries or have an independently expiring,
owner-bound mechanism that interrupts the blocked I/O. Arm/disarm it with both
connection identity and request/phase generation; a late timer cannot close the
next keep-alive request or a replacement socket. Timers themselves must be bounded
and disposed with the connection. Keep gateway-compatible mask tolerance and
relay strict-mask behavior unchanged when touching the shared frame reader.

| Phase | Start and completion | Budget contract |
| --- | --- | --- |
| Initial request line + headers | Socket accept to complete headers | `H_idle` plus non-resetting `H_total`; includes a client that sends no first byte. Starts before handler header parsing. Keep existing stdlib framing/size checks. |
| HTTP keep-alive gap | Finished response to next request | Separate `K_idle`; when the next request is ready, atomically acquire a header/admission slot before parsing it, including prefetched bytes. If none is available, close within the cleanup budget. Otherwise start a fresh header phase. An idle socket still consumes capacity. |
| Header-only admission | Complete headers to Host/Origin/framing/identity checks and class transfer | Non-resetting `A_total`, retaining the pre-header permit until transfer or actual exit; includes identity/visibility and Store lock acquisition. Those waits must themselves support bounded refusal, without an abandoned background waiter. Socket shutdown does not unblock a Python lock. Unavailable verification refuses as unavailable, not as a fabricated authentication result. |
| Ordinary buffered body | Framing/admission complete to exactly Content-Length bytes | `B_idle` plus `B_total`; incomplete/over-limit content never dispatches a mutation. End the input clock after complete input, not after model execution. |
| Upload/archive body | Exact allowlisted upload/archive route, valid length and capacity reserved | `U_idle` plus `D(L) = min(U_max, U_grace + ceil(L / U_min_rate))`. `L` is wire Content-Length, so base64 overhead counts. This absolute budget is not refreshed per chunk. JSON/archive routes remain buffered and reserve bytes; file-area upload remains streaming. |
| WS handshake | HTTP header phase through authenticated upgrade | Header/admission budgets apply until 101. Reserve WS capacity before 101 or creating its writer. No body and no later HTTP reuse. |
| WS frame | First frame byte through complete frame | `F_idle` plus `F_total`, including extended length, masking key and payload. An oversized length refuses before reading the payload. A ping cannot excuse an incomplete preceding frame. |
| Quiet WS connection | Between complete frames | `W_quiet` followed by a bounded liveness probe window `W_probe`; record which supported control/app heartbeat supplied evidence. A healthy quiet observer may remain connected indefinitely; no model-call or whole-connection lifetime limit. |
| Accepted synchronous observer | Task admission to HTTP result delivery/detachment | Separate `O_total`; input completion does not grant an unlimited handler wait. Future `wait_result` observation must be interruptible by disconnect/deadline without setting the job's cancellation event. No promise that socket shutdown alone achieves this. |
| Output/drain and close | A pending socket write/flush or close | Independent bounded I/O/cleanup allowance, including an in-flight WS frame and a non-reading status client. Stop accepting output for that observer, wake queue waits and shut down that exact socket; retain task execution and billing/drain ownership. |

The last two rows are prerequisites for claiming that a connection slot is
actually released. They do not impose a scientific task deadline or change LLM
metered draining. A synchronous observer whose result is lost has an unknown
response outcome; the client reads recorded facts. This preparation does not
invent a new 202 fallback for existing `wait:true` callers.

These are network/observation budgets, not deadlines for arbitrary database,
filesystem or CPU operations. Header-only admission also needs a bounded wait
for local checks/locks before a hard reclamation claim is valid; status routes
have the same prerequisite. A stuck local backend remains counted and reported,
not falsely released after a network timer. Client-stall acceptance runs with
responsive local backends; implementation review must enumerate those waits and
platform/filesystem assumptions before choosing production guarantees.
`U_min_rate` sets the upload time allowance; it is not an independently enforced
sliding-window rate. Grace can allow a short transfer below that nominal rate.
Acceptance compares actual completion with `D(L)`, not a guessed rate threshold.

## Admission, capacity and status headroom

Extend the existing server/Handler and focused route owners; keep the stdlib core
and one execution system. Do not introduce a proxy, framework or separate daemon.

1. Reserve an accepted-socket permit **before creating a handler thread**.
   The accept loop never blocks waiting for an application permit and never
   creates an unbounded overflow thread or queue. With no pre-header capacity,
   promptly close the new socket; its unknown request cannot be promised a
   structured authenticated response.
2. Keep separate bounded pre-header, ordinary HTTP, WS and lightweight status
   pools under one total socket limit. Ordinary HTTP includes independent
   upload/waiting-observer/keep-alive sublimits and a declared-body byte budget.
   Buffered JSON/archive requests reserve their full permitted wire length before
   allocation; streaming uploads reserve chunk/workspace resources, not 512 MiB
   of daemon memory. Reserve declared upload bytes against a separate finite
   temporary-disk budget, include existing temporary files and free-space
   headroom, enforce actual bytes too, and retain the reservation if cleanup
   fails. Do not evict other users' files to admit a new upload.
   This accounts for receive staging; decoded/extracted outputs retain their
   existing route-specific limits and are not claimed to fit the wire budget.
   Wire-byte accounting is not a proof of total Python heap
   usage after JSON decoding; measure that separately during calibration.
3. After complete headers and the route's required Host/Origin/auth checks,
   atomically transfer the permit to its destination class. No oversubscription
   window, double release or waiting while holding an unrelated class permit.
   A keep-alive connection first transfers from ordinary/idle back to the bounded
   pre-header pool before parsing a new request, or closes if that pool is full;
   ordinary/idle permits cannot become extra header readers. It is reclassified
   every time and cannot keep a privileged
   status permit for a later mutation or upgrade. Close a status connection after
   its response in the initial design, rather than lending its reserve.
4. Reserve status capacity only for an explicit **GET + exact route + zero body**
   allowlist: `/health`, `/api/v1/diagnostics/status`, and scoped
   `/api/v1/frames/{fid}/{status|execution|admissions/{reservation_id}}` reads.
   Apply each route's real authorization and object visibility; `/health` stays
   public and minimal. A nonzero Content-Length, Transfer-Encoding, an export,
   refresh/probe endpoint, generic GET or a `/status` suffix is not a status
   permit. Existing `/health` currently also goes through body preparation, so
   zero-body admission is a future requirement, not an existing guarantee.
5. Before granting that reserve in production, verify each allowlisted route's
   response bound and state/Store lock wait. Status reads must complete or refuse
   within `R_service`, perform no external refresh/startup and have a bounded
   socket write. Protect fair access among authenticated principals; an address
   or untrusted forwarded header does not establish a team identity. Do not
   expose other sessions when capacity is scarce.
6. A parsed, safely answerable request with no destination/byte capacity receives
   a bounded **503** through the existing redacted error envelope and closes,
   before body reads or task/upload admission. Only a pre-admission inbound
   header/body timeout may return **408**, if no final response began and framing
   permits a safe reply; otherwise close. In particular, `O_total` expiry after
   task acceptance is observer detachment, never a 408 or a claim of non-admission.
   Future stable reason codes need the ordinary contract/schema gates. Preserve
   existing 400/401/403/404/413 framing, identity and size outcomes. `100 Continue`
   is sent only after the header-only checks and capacity reservation available
   for that route; login credentials still require their bounded body to verify.
7. Release connection permits exactly once after the socket is closed and the
   handler, upload cleanup attempt and any WS writer have actually exited. Failed
   file deletion retains its disk reservation, independently of the freed socket.
   A dead flag, cleared
   queue, sent close frame or removal from the hub is insufficient. Shutdown must
   wake both socket I/O and synchronous result observers without calling
   `runner.close()` or `runner.cancel()` for an individual connection. Stale
   cleanup cannot release a replacement connection's permit.

**Headroom has a precise limit.** One listener cannot recognize a status request
before reading its headers. The reserve protects parsed, authorized status reads
from ordinary/upload/WS saturation; it does not guarantee immediate service when
every pre-header slot is occupied. For a finite batch of stalled headers, those
slots must expire by `H_total + cleanup`, after which read-only probes can observe
status. A continuous arrival flood or kernel listen-backlog exhaustion has no
unconditional latency guarantee here. Do not label that as a solved availability
problem or increase pre-header slots without counting threads/FDs.

## Connection loss and accepted work

- Before full body validation and admission: no task, final upload file, artifact
  version or irreversible action. An incomplete streamed upload may have a private
  temporary file; remove it and leave the previous target unchanged.
- After task admission: lose only the observer, preserving `request_id`,
  `execution_id`, owner and job facts. A disconnected `wait:true` handler must
  exit while the task continues; a stopped WS writer cannot stop the turn.
- Reconnect through the existing WS epoch/sequence protocol and read the scoped
  execution/status/admission records. A known annotation reservation can reconcile
  a lost 202; a generic correlation ID alone is not an idempotency guarantee or
  a universal lookup API. Unknown outcomes stay unknown until observed. Never
  automatically repost a message, Cell, tool, upload or remote job.
- If upload `os.replace` succeeded but its response was lost, the file may already
  exist. Reconcile authorized file metadata/bytes against the submitted content;
  do not blindly overwrite or announce failure as proof that nothing was written.
- Explicit authorized Stop/cancel retains its existing execution ownership check;
  connection budgets must not weaken it. Background model billing/drain, kernel
  persistence and remote job lifecycle retain their own existing contracts.

## Local fixture profile and future acceptance

Run only against a temporary loopback server, synthetic content and a real local
socket; no live model, public target or external storage. A future private test
injection selects this profile, not a new production env variable:

| Fixture parameter | Value |
| --- | --- |
| Socket pools | Total 16 = pre-header 4 + ordinary HTTP 6 + WS 4 + status 2; ordinary sublimits: upload 2, synchronous observer 2, idle keep-alive 2. |
| Buffered body reservation | 2 MiB aggregate for fixture-sized bodies; production route byte caps remain unchanged in this preparation. |
| Upload temporary bytes | 1 MiB aggregate for fixture uploads, plus 256 KiB free-space headroom; failed cleanup stays charged. |
| Header/body/keep-alive | `H_idle=0.5 s`, `H_total=2 s`, `B_idle=0.5 s`, `B_total=3 s`, `K_idle=0.5 s`. |
| Header-only admission | `A_total=1.5 s`, including bounded local identity/visibility lock acquisition. |
| Upload | `U_idle=0.5 s`, `U_grace=1 s`, `U_min_rate=64 KiB/s`, `U_max=8 s`; 256 KiB at 128 KiB/s is a legal slow-upload case. |
| WS | `F_idle=0.5 s`, `F_total=2 s`, `W_quiet=3 s`, `W_probe=1 s`. |
| Observation/cleanup | `O_total=2 s`, `R_service=1 s`, output idle 0.5 s / total 2 s, cleanup at most 1 s; scheduling tolerance at most 1 s, recorded separately. |

Use barriers around admission and real byte progress, not arbitrary sleeps to
guess task state. Record monotonic phase timestamps, bytes, connection/handler/
writer counts, permit ownership, temporary/final file hashes and accepted task
IDs/events. Never record Authorization/Cookie values or payload secrets. Assert
bounded resource return, not just an error status. An outer fixture watchdog
closes its own sockets and temporary server after failure, with no user-daemon
cleanup. Fault injection that stubs a service must carry `stubbed_backend`.

All following cases are **planned**, not executed proof of new limits:

| ID | Controlled case | Required observation |
| --- | --- | --- |
| C01 | No first byte, slow request line, unterminated headers and one-byte drips | Idle/absolute header deadline closes the socket and returns handler/permit counts within cleanup+tolerance; drip never renews the total. |
| C02 | Keep-alive gap, then normal requests and a body-bearing status lookalike; activate keep-alive while header slots are full | Idle capacity expires; fresh phase on each admitted request; no extra header reader from an ordinary/idle permit, no status permit for the lookalike and no stale timer affecting a replacement request. |
| C03 | Valid Content-Length with stalled/trickled ordinary body | Finite idle/total convergence, no dispatch/job/version, body cache freed; leftover bytes are not a next request. |
| C04 | Duplicate/negative length, Transfer-Encoding, oversize, invalid auth/Origin, and Expect:100 | Existing framing/auth/size outcomes preserved; a header-only refusal waits for no body and the wire contains zero 100 responses. Public login still validates its bounded credentials body. |
| C05 | Legal slow streamed upload, buffered JSON/base64 upload and raw archive | Each completes under its upload budget with matching bytes; raw route uses bounded chunks, buffered routes reserve wire length including base64 overhead. |
| C06 | Upload just inside/outside `D(L)`, idle pause, truncated input, temporary-space exhaustion and cleanup failure | Connection resources return after actual exits; failed file cleanup retains its disk charge and is reported; old final target/version unchanged before replace; transfers within total and idle budgets succeed. |
| C07 | Saturate ordinary/upload/wait/WS classes and byte budget; concurrent zero-body status reads; separately hold the actual identity/Store lock past admission deadline | No extra handler/writer beyond the pool contract; parsed excess receives 503 before work; authorized status completes or refuses within admission/service budgets without external refresh. A blocked check returns with no abandoned waiter or false permit release while the fixture still holds the lock. |
| C08 | Fill pre-header pool with a finite stalled batch, then probe status | Initial probe may be refused; after bounded header expiry and cleanup, status succeeds. Record this narrower guarantee; no fabricated continuous-flood availability claim. |
| C09 | Healthy quiet WS, active pings, partial header/mask/payload and oversized/fragmented frames | Quiet liveness works beyond a model timeout; malformed/oversized frames remain refused; partial-frame total cannot be refreshed by activity. |
| C10 | Non-reading status client; separately, a non-reading WS peer whose constrained TCP buffers demonstrably stall its writer, and a WS reader blocked on partial input | Status connection closes within its budget, without claiming a small response stalled in TCP. For the proven WS stall, queue bounds preserve producer progress; shutdown wakes actual reader/writer and counts return without the peer reading or a test manually releasing the blocked writer. |
| C11 | Both synchronous message and `POST /api/v1/frames/{fid}/kernel/execute` tasks accepted, then observer disconnects or expires | Handler/permit exit while exactly one task continues to its real terminal state; zero cancellation/resubmission, no 408/non-admission claim after acceptance, result reads retain old identity. |
| C12 | Lost 202, lost post-replace upload response, WS reconnect with old/current epoch | Read-only reconciliation, honest unknown outcome where identity is insufficient, no automatic mutation; no duplicate task, artifact or accepted external request. |
| C13 | Concurrent admission/release/upgrade, repeated reconnect and late cleanup | Atomic class transfer, exactly-once release, global/subpool bounds and no replacement-connection release; rejected WS creates no writer or subscription. |
| C14 | Revoke team identity during an existing stream or overload | Existing visibility/cancel ownership checks remain effective; no data leak through status reserve or replay and no cleanup of another user's task. |

Run the actual implementation with macOS and Linux sockets, then the existing
Chromium/Firefox/WebKit suites. Calibrate real permissible upload sizes/rates,
laboratory latency, browser background-tab heartbeat behavior, thread/FD counts,
memory and status-lock latency before selecting production numbers. Follow the
existing auth/body/WS regression suites, full offline tests, response captures
and harness. The future rollout must separately review synchronous-observer
compatibility and failure projection. T8 is complete when this preparation is
reviewed and current behavior remains unchanged; C01–C14 and calibration remain
gates for a later implementation.
