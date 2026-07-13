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
  # Force VALID UTF-8 before escaping: sink text slurped with useBytes=TRUE can
  # carry latin-1/binary bytes, on which a plain gsub raises 'input string is
  # invalid in this locale' — an uncaught error here used to kill the worker.
  # iconv(sub="byte") replaces invalid bytes with visible <xx> escapes.
  s2 <- tryCatch(iconv(s, from = "", to = "UTF-8", sub = "byte"),
                 error = function(e) NA_character_)
  if (is.na(s2)) {
    s2 <- tryCatch(iconv(s, from = "latin1", to = "UTF-8", sub = "byte"),
                   error = function(e) NA_character_)
  }
  s <- if (is.na(s2)) "(unrepresentable output)" else s2
  s <- gsub("\\", "\\\\", s, fixed = TRUE, useBytes = TRUE)
  s <- gsub('"', '\\"', s, fixed = TRUE, useBytes = TRUE)
  s <- gsub("\n", "\\n", s, fixed = TRUE, useBytes = TRUE)
  s <- gsub("\r", "\\r", s, fixed = TRUE, useBytes = TRUE)
  s <- gsub("\t", "\\t", s, fixed = TRUE, useBytes = TRUE)
  for (i in c(1:8, 11L, 12L, 14:31)) {
    s <- gsub(intToUtf8(i), sprintf("\\u%04x", i), s, fixed = TRUE, useBytes = TRUE)
  }
  paste0('"', s, '"')
}

.oai4s_num <- function(x, digits = 4L) {
  if (is.null(x) || length(x) == 0L || is.na(x)) return("0")
  # explicit marks: a user cell running options(OutDec=",") must not turn
  # every usage number into invalid JSON ("wall_s":0,0049) for the session
  formatC(as.numeric(x), format = "f", digits = digits, mode = "double",
          big.mark = "", decimal.mark = ".")
}

# exactly ONE response frame per execute frame: the manager returns on the
# FIRST response it reads, so a duplicate would desync the NEXT cell
.oai4s_responded <- FALSE

.oai4s_write_frame <- function(json) {
  ok <- tryCatch({
    writeLines(json, .oai4s_out, useBytes = TRUE)
    flush(.oai4s_out)
    TRUE
  }, error = function(e) FALSE)
  if (!ok) {
    # user code closed our connection (closeAllConnections() is a common
    # sink-recovery idiom) — the raw process fd 3 is still open: reopen once
    con <- tryCatch(file("/dev/fd/3", open = "wt"), error = function(e) NULL)
    if (!is.null(con)) {
      .oai4s_out <<- con
      ok <- tryCatch({
        writeLines(json, .oai4s_out, useBytes = TRUE)
        flush(.oai4s_out)
        TRUE
      }, error = function(e) FALSE)
    }
  }
  invisible(ok)
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
  .oai4s_responded <<- TRUE
  .oai4s_write_frame(json)
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
  # truncate in the SAME units the gate compares (bytes) — substr counts
  # characters and would keep up to 4x the cap for multibyte output. A split
  # trailing multibyte char is repaired by .oai4s_esc's iconv(sub="byte").
  head_bytes <- charToRaw(s)[seq_len(.oai4s_MAX_OUTPUT)]
  paste0(
    rawToChar(head_bytes),
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

# --- read-only variable inspection ------------------------------------------

# Keep inspection code outside the user namespace's lexical lookup chain.
# Every helper resolves only base functions, and the environment/bindings are
# locked before the first Cell runs.  The protocol writer is captured now so a
# later user variable with the same name is never called by the inspector.
.oai4s_inspector <- new.env(parent = baseenv())
.oai4s_inspector$write_frame <- .oai4s_write_frame
evalq({
  hidden <- c("quit", "q")
  sample_items <- 12L

  esc <- function(s) {
    if (is.null(s) || length(s) == 0L) return('""')
    s <- paste(as.character(s), collapse = "\n")
    s2 <- tryCatch(iconv(s, from = "", to = "UTF-8", sub = "byte"),
                   error = function(e) NA_character_)
    s <- if (is.na(s2)) "(unrepresentable)" else s2
    s <- gsub("\\", "\\\\", s, fixed = TRUE, useBytes = TRUE)
    s <- gsub('"', '\\"', s, fixed = TRUE, useBytes = TRUE)
    s <- gsub("\n", "\\n", s, fixed = TRUE, useBytes = TRUE)
    s <- gsub("\r", "\\r", s, fixed = TRUE, useBytes = TRUE)
    s <- gsub("\t", "\\t", s, fixed = TRUE, useBytes = TRUE)
    for (i in c(1:8, 11L, 12L, 14:31)) {
      s <- gsub(intToUtf8(i), sprintf("\\u%04x", i), s, fixed = TRUE, useBytes = TRUE)
    }
    paste0('"', s, '"')
  }

  bounded <- function(s, n = 240L) {
    if (is.na(s)) return("NA")
    s <- enc2utf8(s)
    if (nchar(s, type = "chars") <= n) s else paste0(substr(s, 1L, n - 1L), "…")
  }

  scalar_token <- function(value) {
    if (is.object(value) || length(value) != 1L) return(NULL)
    kind <- typeof(value)
    if (identical(kind, "logical")) {
      if (is.na(value)) return("logical:NA")
      return(if (isTRUE(value)) "logical:true" else "logical:false")
    }
    if (identical(kind, "integer")) {
      return(if (is.na(value)) "integer:NA" else paste0("integer:", as.character(value)))
    }
    if (identical(kind, "double")) {
      if (is.nan(value)) return("double:NaN")
      if (is.na(value)) return("double:NA")
      if (is.infinite(value)) return(if (value > 0) "double:+Inf" else "double:-Inf")
      return(paste0("double:", sprintf("%.17g", value)))
    }
    if (identical(kind, "character")) {
      return(if (is.na(value)) "character:NA" else paste0("character:", bounded(value, 128L)))
    }
    if (identical(kind, "raw")) return(paste0("raw:", paste(sprintf("%02x", as.integer(value)), collapse = "")))
    NULL
  }

  scalar_preview <- function(value) {
    token <- scalar_token(value)
    if (is.null(token)) return(NULL)
    kind <- typeof(value)
    if (identical(kind, "character")) return(if (is.na(value)) "NA" else bounded(value))
    if (identical(kind, "raw")) return(paste0("0x", paste(sprintf("%02x", as.integer(value)), collapse = "")))
    sub("^[^:]+:", "", token)
  }

  fingerprint <- function(token) {
    # A bounded deterministic fingerprint without R serialization APIs,
    # object methods, attributes, files, or optional packages.
    ints <- utf8ToInt(bounded(token, 4096L))
    h <- 5381
    for (code in ints) h <- (h * 33 + code) %% 2147483647
    sprintf("%08x", as.integer(h))
  }

  binding_value <- function(name) {
    env <- globalenv()
    if (bindingIsActive(name, env)) {
      return(list(active = TRUE, value = NULL))
    }
    # substitute() reads an ordinary binding without invoking repr/print and
    # returns a delayedAssign promise's expression without forcing it.
    symbol <- as.name(name)
    call <- as.call(list(as.name("substitute"), symbol, env))
    list(active = FALSE, value = eval(call, baseenv()))
  }

  inspect_one <- function(name) {
    binding <- binding_value(name)
    if (isTRUE(binding$active)) return(list(name = name, type = "active_binding"))
    value <- binding$value
    kind <- typeof(value)
    entry <- list(name = name, type = kind)
    if (is.object(value)) return(entry)

    if (kind %in% c("logical", "integer", "double", "character", "raw")) {
      n <- length(value)
      entry$kind <- if (identical(kind, "character")) "text" else if (identical(kind, "raw")) "bytes" else "vector"
      entry$length <- n
      take <- min(n, sample_items)
      values <- if (take > 0L) lapply(seq_len(take), function(i) value[[i]]) else list()
      tokens <- lapply(values, scalar_token)
      if (all(vapply(tokens, function(x) !is.null(x), logical(1)))) {
        previews <- vapply(values, scalar_preview, character(1))
        entry$preview <- paste0("[", paste(previews, collapse = ", "), if (n > take) ", …" else "", "]")
        entry$fingerprint <- fingerprint(paste0(kind, ":", n, ":", paste(unlist(tokens), collapse = "|")))
      }
      return(entry)
    }

    if (identical(kind, "list")) {
      n <- length(value)
      entry$kind <- "container"; entry$length <- n
      take <- min(n, sample_items)
      values <- if (take > 0L) lapply(seq_len(take), function(i) value[[i]]) else list()
      tokens <- lapply(values, scalar_token)
      if (all(vapply(tokens, function(x) !is.null(x), logical(1)))) {
        previews <- vapply(values, scalar_preview, character(1))
        entry$preview <- paste0("[", paste(previews, collapse = ", "), if (n > take) ", …" else "", "]")
        entry$fingerprint <- fingerprint(paste0("list:", n, ":", paste(unlist(tokens), collapse = "|")))
      }
    }
    entry
  }

  entry_json <- function(entry) {
    fields <- c(paste0('"name":', esc(entry$name)), paste0('"type":', esc(entry$type)))
    if (!is.null(entry$kind)) fields <- c(fields, paste0('"kind":', esc(entry$kind)))
    if (!is.null(entry$length)) fields <- c(fields, paste0('"length":', sprintf("%.0f", entry$length)))
    if (!is.null(entry$preview)) fields <- c(fields, paste0('"preview":', esc(entry$preview)))
    if (!is.null(entry$fingerprint)) fields <- c(fields, paste0('"fingerprint":', esc(entry$fingerprint)))
    paste0("{", paste(fields, collapse = ","), "}")
  }

  inspect <- function(limit) {
    names <- ls(envir = globalenv(), all.names = TRUE, sorted = TRUE)
    names <- names[!startsWith(names, ".oai4s_") & !(names %in% hidden)]
    selected <- names[seq_len(min(length(names), limit))]
    list(
      variables = lapply(selected, inspect_one),
      truncated = length(names) > length(selected),
      limit = limit
    )
  }

  respond <- function(id, result = NULL, error = NULL) {
    variables <- if (is.null(result)) list() else result$variables
    entries <- vapply(variables, entry_json, character(1))
    json <- paste0(
      '{"type":"variables_response","id":', esc(id),
      ',"variables":[', paste(entries, collapse = ","), "]",
      ',"truncated":', if (!is.null(result) && isTRUE(result$truncated)) "true" else "false",
      ',"limit":', if (is.null(result)) "0" else sprintf("%d", as.integer(result$limit)),
      ',"error":', if (is.null(error)) "null" else esc(error), "}"
    )
    write_frame(json)
  }
}, .oai4s_inspector)
lockEnvironment(.oai4s_inspector, bindings = TRUE)
lockBinding(".oai4s_inspector", globalenv())

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

# parse one line and dispatch it; returns "shutdown" | "ok"
.oai4s_handle_line <- function(line) {
  frame <- NULL
  if (.oai4s_have_jsonlite) {
    frame <- tryCatch(jsonlite::fromJSON(line, simplifyVector = TRUE),
                      error = function(e) NULL)
  }
  if (is.null(frame) || !is.list(frame)) {
    if (grepl('"type"[[:space:]]*:[[:space:]]*"shutdown"', line)) return("shutdown")
    .oai4s_respond(
      .oai4s_regex_id(line), "", "",
      if (.oai4s_have_jsonlite) "invalid JSON request" else
        "openai4s R worker requires the 'jsonlite' package — install.packages(\"jsonlite\") or select the prebuilt 'r' environment",
      FALSE, NULL, NULL, 0, 0, 0L
    )
    return("ok")
  }
  type <- as.character(.oai4s_or(frame$type, "execute"))
  if (identical(type, "shutdown")) return("shutdown")
  if (identical(type, "inspect_variables")) {
    id <- as.character(.oai4s_or(frame$id, "unknown"))
    limit <- frame$limit
    valid_limit <- is.numeric(limit) && length(limit) == 1L && is.finite(limit) &&
      limit == floor(limit) && limit >= 1 && limit <= 500
    if (!isTRUE(valid_limit)) {
      .oai4s_inspector$respond(id, error = "invalid variable inspection limit")
      .oai4s_responded <<- TRUE
      return("ok")
    }
    inspected <- tryCatch(
      .oai4s_inspector$inspect(as.integer(limit)),
      error = function(e) NULL,
      interrupt = function(e) NULL
    )
    if (is.null(inspected)) {
      .oai4s_inspector$respond(id, error = "variable inspection failed closed")
    } else {
      .oai4s_inspector$respond(id, result = inspected)
    }
    .oai4s_responded <<- TRUE
    return("ok")
  }
  if (identical(type, "execute")) {
    .oai4s_run(
      as.character(.oai4s_or(frame$code, "")),
      as.character(.oai4s_or(frame$id, "unknown"))
    )
  }
  # host_response frames only follow a host_call, which this worker never
  # emits — a stray one is stale desync; ignore (worker.py parity).
  "ok"
}

repeat {
  line <- tryCatch(
    readLines(.oai4s_in, n = 1L, warn = FALSE),
    interrupt = function(e) "",       # idle SIGINT: swallow, keep the worker alive
    error = function(e) NULL
  )
  if (is.null(line)) {
    # read failed (user closeAllConnections()): the raw process fd 4 is still
    # open — reopen once; a second failure means the host is really gone
    .oai4s_in <- tryCatch(file("/dev/fd/4", open = "rt", blocking = TRUE),
                          error = function(e) NULL)
    if (is.null(.oai4s_in)) break
    line <- tryCatch(readLines(.oai4s_in, n = 1L, warn = FALSE),
                     interrupt = function(e) "",
                     error = function(e) character(0))
  }
  if (length(line) == 0L) break       # EOF — the host closed the pipe
  if (!nzchar(line)) next

  # In non-interactive Rscript ANY uncaught condition halts the interpreter —
  # including a latched idle SIGINT firing at the next checkpoint (before
  # .oai4s_run's own handlers arm) and internal errors in parse/respond. One
  # frame may fail; the worker itself must survive it, and each execute frame
  # gets exactly ONE response (.oai4s_responded guards the fallback).
  .oai4s_responded <- FALSE
  outcome <- tryCatch(
    .oai4s_handle_line(line),
    interrupt = function(e) "interrupted",
    error = function(e) paste0("internal error: ", conditionMessage(e))
  )
  if (identical(outcome, "shutdown")) break
  if (!identical(outcome, "ok")) {
    .oai4s_unwind_sinks()
    if (!.oai4s_responded) {
      if (identical(outcome, "interrupted")) {
        .oai4s_respond(.oai4s_regex_id(line), "", "", "Interrupted", TRUE,
                       NULL, NULL, 0, 0, 0L)
      } else {
        .oai4s_respond(.oai4s_regex_id(line), "", "",
                       paste0("openai4s r_worker ", outcome), FALSE,
                       NULL, NULL, 0, 0, 0L)
      }
    }
  }
}
