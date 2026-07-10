# Persistent R kernel worker for openai4s.
#
# Speaks the SAME JSON-per-line frame protocol as kernel/worker.py, driven by
# the same host-side manager (kernel/manager.py) — the R sibling of the python
# worker, so the host executes exactly two kinds of instructions: python cells
# and R cells.
#
# fd discipline (the shell-redirection equivalent of worker.py's dup2 swap —
# see kernel/r_kernel.py, which spawns this file as
#   sh -c 'exec "$0" --vanilla "$1" 3>&1 4<&0 </dev/null 1>&2' Rscript r_worker.R):
#   protocol OUT  = fd 3  (the pipe the manager reads)
#   protocol IN   = fd 4  (the pipe the manager writes)
#   fd 0          = /dev/null  (user code reading stdin cannot eat frames)
#   fd 1          = aliased to stderr (stray C-level prints never hit the wire)
#
# Frames handled: {"type":"execute","id":...,"code":...} -> one
# {"type":"response", id, stdout, stderr, error, interrupted,
#  trace:{error_lineno,error_call}, guards:{}, usage:{wall_s,cpu_s,peak_rss_kb}}
# per cell (identical result contract to worker.py); {"type":"shutdown"} exits.
# This ANALYSIS kernel never emits host_call frames — there is no `host` object
# in R; completion (host.submit_output) stays on the python control plane.
#
# Inbound JSON is parsed with jsonlite (pinned in envs/r.yml). Outbound JSON is
# hand-escaped so a jsonlite-less R still reports a clean, structured error.

.oai4s_MAX_OUTPUT <- 1000000L  # 1MB head cap per captured stream (worker.py parity)

.oai4s_or <- function(a, b) if (is.null(a) || length(a) == 0L) b else a

# --- outbound JSON (dependency-free) ----------------------------------------

.oai4s_esc <- function(s) {
  if (is.null(s) || length(s) == 0L) return('""')
  s <- paste(as.character(s), collapse = "\n")
  s <- gsub("\\", "\\\\", s, fixed = TRUE)
  s <- gsub('"', '\\"', s, fixed = TRUE)
  s <- gsub("\n", "\\n", s, fixed = TRUE)
  s <- gsub("\r", "\\r", s, fixed = TRUE)
  s <- gsub("\t", "\\t", s, fixed = TRUE)
  for (i in c(1:8, 11L, 12L, 14:31)) {
    s <- gsub(intToUtf8(i), sprintf("\\u%04x", i), s, fixed = TRUE)
  }
  paste0('"', s, '"')
}

.oai4s_num <- function(x, digits = 4L) {
  if (is.null(x) || length(x) == 0L || is.na(x)) return("0")
  formatC(as.numeric(x), format = "f", digits = digits, mode = "double")
}

.oai4s_respond <- function(id, stdout_txt, stderr_txt, error, interrupted,
                           lineno, callname, wall, cpu, rss) {
  json <- paste0(
    '{"type":"response","id":', .oai4s_esc(id),
    ',"stdout":', .oai4s_esc(stdout_txt),
    ',"stderr":', .oai4s_esc(stderr_txt),
    ',"error":', if (is.null(error)) "null" else .oai4s_esc(error),
    ',"interrupted":', if (isTRUE(interrupted)) "true" else "false",
    ',"trace":{"error_lineno":',
    if (is.null(lineno)) "null" else sprintf("%d", as.integer(lineno)),
    ',"error_call":', if (is.null(callname)) "null" else .oai4s_esc(callname),
    '},"guards":{},"usage":{"wall_s":', .oai4s_num(wall),
    ',"cpu_s":', .oai4s_num(cpu),
    ',"peak_rss_kb":', sprintf("%d", as.integer(.oai4s_or(rss, 0L))),
    "}}"
  )
  writeLines(json, .oai4s_out, useBytes = TRUE)
  flush(.oai4s_out)
}

# --- capture helpers ---------------------------------------------------------

.oai4s_slurp <- function(path) {
  if (!file.exists(path)) return("")
  sz <- file.info(path)$size
  if (is.na(sz) || sz <= 0) return("")
  tryCatch(readChar(path, sz, useBytes = TRUE), error = function(e) "")
}

.oai4s_cap <- function(s) {
  if (is.null(s) || !nzchar(s)) return("")
  if (nchar(s, type = "bytes") <= .oai4s_MAX_OUTPUT) return(s)
  paste0(
    substr(s, 1L, .oai4s_MAX_OUTPUT),
    sprintf("\n...(truncated at %d bytes)", .oai4s_MAX_OUTPUT)
  )
}

.oai4s_rss_kb <- function() {
  status <- "/proc/self/status"
  if (file.exists(status)) {
    lines <- tryCatch(readLines(status, warn = FALSE), error = function(e) character(0))
    hw <- grep("^VmHWM:", lines, value = TRUE)
    if (length(hw) == 1L) {
      kb <- suppressWarnings(as.integer(gsub("[^0-9]", "", hw)))
      if (!is.na(kb)) return(kb)
    }
  }
  0L  # non-Linux; best-effort like worker.py
}

.oai4s_unwind_sinks <- function() {
  tryCatch({
    while (sink.number() > 0L) sink()
  }, error = function(e) NULL)
  tryCatch({
    while (sink.number(type = "message") != 2L) sink(type = "message")
  }, error = function(e) NULL)
}

# --- one cell ----------------------------------------------------------------

.oai4s_run <- function(code, id) {
  out_file <- tempfile("oai4s-out-")
  msg_file <- tempfile("oai4s-msg-")
  out_con <- file(out_file, open = "wt")
  msg_con <- file(msg_file, open = "wt")
  sink(out_con, type = "output")
  sink(msg_con, type = "message")

  err <- NULL; lineno <- NULL; callname <- NULL; interrupted <- FALSE
  t0 <- Sys.time(); p0 <- proc.time()

  parsed <- tryCatch(parse(text = code, keep.source = TRUE), error = function(e) e)
  if (inherits(parsed, "error")) {
    msg <- conditionMessage(parsed)
    err <- paste0("ParseError: ", msg)
    m <- regmatches(msg, regexec("<text>:([0-9]+):", msg))[[1]]
    if (length(m) == 2L) lineno <- suppressWarnings(as.integer(m[2]))
  } else {
    srcrefs <- attr(parsed, "srcref")
    for (i in seq_along(parsed)) {
      state <- tryCatch(
        list(kind = "ok", v = withCallingHandlers(
          withVisible(eval(parsed[[i]], globalenv())),
          # print the warning WITHOUT this eval frame leaking into its call
          warning = function(w) {
            message("Warning: ", conditionMessage(w))
            invokeRestart("muffleWarning")
          }
        )),
        error = function(e) list(kind = "error", e = e),
        interrupt = function(e) list(kind = "interrupt")
      )
      if (identical(state$kind, "interrupt")) {
        interrupted <- TRUE
        err <- "Interrupted"
        break
      }
      if (identical(state$kind, "error")) {
        e <- state$e
        cl <- conditionCall(e)
        err <- paste0(
          "Error",
          if (!is.null(cl)) paste0(" in ", deparse(cl)[1]) else "",
          ": ", conditionMessage(e)
        )
        if (!is.null(srcrefs) && length(srcrefs) >= i && !is.null(srcrefs[[i]])) {
          lineno <- suppressWarnings(as.integer(srcrefs[[i]][1]))
        }
        if (!is.null(cl)) {
          callname <- tryCatch(deparse(cl[[1]])[1], error = function(e2) NULL)
        }
        break
      }
      if (isTRUE(state$v$visible)) {
        tryCatch(print(state$v$value), error = function(e) {
          message("print failed: ", conditionMessage(e))
        })
      }
    }
  }

  .oai4s_unwind_sinks()
  tryCatch(close(out_con), error = function(e) NULL)
  tryCatch(close(msg_con), error = function(e) NULL)

  wall <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  dp <- proc.time() - p0
  cpu <- sum(dp[c("user.self", "sys.self", "user.child", "sys.child")], na.rm = TRUE)

  stdout_txt <- .oai4s_cap(.oai4s_slurp(out_file))
  stderr_txt <- .oai4s_cap(.oai4s_slurp(msg_file))
  unlink(c(out_file, msg_file))

  .oai4s_respond(id, stdout_txt, stderr_txt, err, interrupted, lineno, callname,
                 wall, cpu, .oai4s_rss_kb())
}

# --- protocol channels + main loop -------------------------------------------

.oai4s_out <- tryCatch(file("/dev/fd/3", open = "wt"), error = function(e) NULL)
if (is.null(.oai4s_out)) {
  message("openai4s r_worker: protocol fd 3 unavailable — spawn via kernel/r_kernel.py")
  quit(save = "no", status = 2)
}
.oai4s_in <- tryCatch(file("/dev/fd/4", open = "rt", blocking = TRUE),
                      error = function(e) NULL)
if (is.null(.oai4s_in)) {
  message("openai4s r_worker: protocol fd 4 unavailable — spawn via kernel/r_kernel.py")
  quit(save = "no", status = 2)
}

.oai4s_have_jsonlite <- requireNamespace("jsonlite", quietly = TRUE)

.oai4s_regex_id <- function(line) {
  m <- regmatches(line, regexec('"id"[[:space:]]*:[[:space:]]*"([^"]*)"', line))[[1]]
  if (length(m) == 2L) m[2] else "unknown"
}

# Print warnings as they happen so they land in the cell's message sink instead
# of accumulating for a top-level that never returns; shadow quit()/q() so an R
# cell cannot silently kill the worker (worker.py traps SystemExit the same way).
options(warn = 1)
assign("quit", function(...) stop("quit() is disabled inside openai4s R cells; the kernel stays alive"),
       envir = globalenv())
assign("q", function(...) stop("q() is disabled inside openai4s R cells; the kernel stays alive"),
       envir = globalenv())

repeat {
  line <- tryCatch(
    readLines(.oai4s_in, n = 1L, warn = FALSE),
    interrupt = function(e) "",       # idle SIGINT: swallow, keep the worker alive
    error = function(e) character(0)
  )
  if (length(line) == 0L) break       # EOF — the host closed the pipe
  if (!nzchar(line)) next

  frame <- NULL
  if (.oai4s_have_jsonlite) {
    frame <- tryCatch(jsonlite::fromJSON(line, simplifyVector = TRUE),
                      error = function(e) NULL)
  }
  if (is.null(frame) || !is.list(frame)) {
    if (grepl('"type"[[:space:]]*:[[:space:]]*"shutdown"', line)) break
    .oai4s_respond(
      .oai4s_regex_id(line), "", "",
      if (.oai4s_have_jsonlite) "invalid JSON request" else
        "openai4s R worker requires the 'jsonlite' package — install.packages(\"jsonlite\") or select the prebuilt 'r' environment",
      FALSE, NULL, NULL, 0, 0, 0L
    )
    next
  }

  type <- as.character(.oai4s_or(frame$type, "execute"))
  if (identical(type, "shutdown")) break
  if (identical(type, "execute")) {
    .oai4s_run(
      as.character(.oai4s_or(frame$code, "")),
      as.character(.oai4s_or(frame$id, "unknown"))
    )
  }
  # host_response frames only follow a host_call, which this worker never
  # emits — a stray one is stale desync; ignore (worker.py parity).
}
