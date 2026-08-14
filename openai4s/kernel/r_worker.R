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
# Frames handled: {"type":"execute","id":...,"code":...,"sink_out":...,
# "sink_err":...} -> one {"type":"response", id, sink_capture, stdout, stderr,
#  error, interrupted, trace:{error_lineno,error_call}, guards:{},
#  usage:{wall_s,cpu_s,peak_rss_kb}} per cell (identical result contract to
# worker.py); {"type":"shutdown"} exits.
#
# The two `sink_*` paths are fifos the HOST drains, and they are required: this
# worker refuses an execute frame without them rather than running the cell
# uncaptured. `stdout`/`stderr` therefore leave here empty and `sink_capture`
# tells the manager to fill them from what it read. Why the capture moved out
# of this file at all is in .oai4s_run and in kernel/sink_drain.py.
# This ANALYSIS kernel never emits host_call frames — there is no `host` object
# in R; completion (host.submit_output) stays on the python control plane.
#
# Inbound JSON is parsed with jsonlite (pinned in envs/r.yml). Outbound JSON is
# hand-escaped so a jsonlite-less R still reports a clean, structured error.

# 1MB head cap (worker.py parity). The two captured streams are now bounded by
# the host at the same number (kernel/sink_drain.CAP_BYTES); what is left for
# this constant is the one string R itself produces — an error message, which
# never travels through a sink.
.oai4s_MAX_OUTPUT <- 1000000L

# Hard backstop for one outbound frame, whatever it carries — the sibling of
# worker.py's `_MAX_FRAME_BYTES`, which this side simply did not have, so an
# oversized response was written and the manager's readline() materialised it
# whole. Derived from the SAME numbers rather than hand-picked, so the two
# workers cannot drift into different contracts: worst-case JSON expansion
# (12 bytes — an astral character costs a `\uXXXX` surrogate PAIR) times the
# two capped streams, plus room for the rest of the frame.
.oai4s_MAX_FRAME_BYTES <- 12L * 2L * .oai4s_MAX_OUTPUT + 2000000L

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
                           lineno, callname, wall, cpu, rss,
                           sink_capture = FALSE) {
  json <- paste0(
    '{"type":"response","id":', .oai4s_esc(id),
    ',"sink_capture":', if (isTRUE(sink_capture)) "true" else "false",
    ',"stdout":', .oai4s_esc(stdout_txt),
    ',"stderr":', .oai4s_esc(stderr_txt),
    ',"error":', if (is.null(error)) "null" else .oai4s_esc(error),
    ',"interrupted":', if (isTRUE(interrupted)) "true" else "false",
    ',"trace":{"error_lineno":',
    if (is.null(lineno)) "null" else sprintf("%d", as.integer(lineno)),
    ',"error_call":', if (is.null(callname)) "null" else .oai4s_esc(callname),
    '},"guards":{},"usage":{"wall_s":', .oai4s_num(wall),
    ',"cpu_s":', .oai4s_num(cpu),
    ',"peak_rss_kb":',
    if (is.null(rss)) "null" else sprintf("%d", as.integer(rss)),
    "}}"
  )
  if (nchar(json, type = "bytes") > .oai4s_MAX_FRAME_BYTES) {
    # worker.py's contract, kept here too: a DROPPED response leaves the
    # manager blocked on an id that never arrives, which reads to the user as a
    # hang rather than as a refusal. So the replacement keeps the type and the
    # id, drops the payload, and says what happened. The caps above are what
    # stop this being reached; this is what stops the next unbounded field
    # being a hang instead of a message.
    json <- paste0(
      '{"type":"response","id":', .oai4s_esc(id),
      # Still true, and still the host's to fill: the payload this drops is
      # the error string, not the cell's output.
      ',"sink_capture":', if (isTRUE(sink_capture)) "true" else "false",
      ',"stdout":"","stderr":"","error":',
      .oai4s_esc(sprintf(
        "R kernel dropped an oversized response frame (>%d bytes)",
        .oai4s_MAX_FRAME_BYTES
      )),
      ',"interrupted":', if (isTRUE(interrupted)) "true" else "false",
      ',"trace":{"error_lineno":null,"error_call":null},"guards":{}',
      ',"usage":{"wall_s":', .oai4s_num(wall),
      ',"cpu_s":', .oai4s_num(cpu),
      ',"peak_rss_kb":',
      if (is.null(rss)) "null" else sprintf("%d", as.integer(rss)),
      "}}"
    )
  }
  .oai4s_responded <<- TRUE
  .oai4s_write_frame(json)
}

# --- capture helpers ---------------------------------------------------------

.oai4s_cap_message <- function(s) {
  # Bound a string R ITSELF produced — an error message — before it is pasted
  # into anything larger.
  #
  # The two captured streams are bounded by the host that drains them, but the
  # error string never travels that way: a cell doing stop(strrep("x", 2e8))
  # built a 200 MB message, pasted it into a bigger one, escaped it character
  # by character in .oai4s_esc, and put the result on the wire. worker.py caps
  # its error; this did not.
  #
  # Cut in CHARACTERS, and the marker says so rather than borrowing the byte
  # wording the host's stream cap uses. charToRaw() allocates a raw vector as
  # large as the whole string, which is precisely the allocation being avoided
  # here; substr() is safe on a string R constructed because R made it valid,
  # and the raw path stays as the fallback for one it did not.
  if (is.null(s) || length(s) == 0L) return("")
  s <- paste(as.character(s), collapse = "\n")
  n <- tryCatch(nchar(s, type = "chars"), error = function(e) NA_integer_)
  if (is.na(n)) n <- nchar(s, type = "bytes")
  if (n <= .oai4s_MAX_OUTPUT) return(s)
  head <- tryCatch(substr(s, 1L, .oai4s_MAX_OUTPUT), error = function(e) NULL)
  if (is.null(head)) head <- rawToChar(charToRaw(s)[seq_len(.oai4s_MAX_OUTPUT)])
  paste0(head, sprintf("\n...(truncated at %d characters)", .oai4s_MAX_OUTPUT))
}

.oai4s_deparse1 <- function(cl) {
  # deparse() renders the WHOLE call before anything looks at the result, so
  # `deparse(cl)[1]` threw away every line but the first only after paying for
  # all of them — a call quoting a large literal cost its own size right here.
  # nlines stops the deparser after one line and width.cutoff bounds that line.
  tryCatch(deparse(cl, nlines = 1L, width.cutoff = 500L)[1],
           error = function(e) "<call>")
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
  # NULL, not 0L. `0` is a measurement -- "this cell used no memory" -- and it
  # is one this worker cannot make: there is no /proc on macOS, which is the
  # platform this project is developed on, so every R cell reported a peak RSS
  # of zero and the usage row said so. Absent is the true answer, and the
  # column is nullable precisely so it can be given.
  #
  # The Linux value is left as VmHWM deliberately, and it is worth naming what
  # that is: a process-lifetime high-water mark, not a per-cell peak. One
  # memory-hungry cell raises the number every later cell reports. Resetting it
  # per cell needs /proc/self/clear_refs and a Linux run to verify, so it is
  # recorded rather than guessed at from here.
  NULL
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

.oai4s_run <- function(code, id, sink_out, sink_msg) {
  # The two streams go to fifos the HOST drains, not to tempfiles this worker
  # reads back.
  #
  # R gives no hook inside a single top-level expression -- it is single
  # threaded, addTaskCallback does not fire mid-expression, and a connection
  # callback cannot be written in R -- so every bound this side could enforce
  # only ran *between* expressions. One expression printing 300 MB wrote all
  # 300 MB to a tempfile (a tmpfs on much of Linux, so RAM) and this worker
  # then read 1 MB of it and discarded the rest. A reader on the other end of
  # a pipe bounds the writer instead of auditing it afterwards: it keeps the
  # first cap bytes and drops the rest as they arrive, and the same cell now
  # materialises nothing. kernel/sink_drain.py records what was measured.
  #
  # blocking = TRUE is load-bearing. R's fifo() defaults to non-blocking, and
  # a non-blocking writer silently drops everything that does not fit the pipe
  # buffer -- measured at 1.5 MB retained out of 300 MB written, with no error
  # and status 0. Blocking makes the host's reader the thing that paces the
  # cell, which is the whole design.
  out_con <- fifo(sink_out, open = "wb", blocking = TRUE)
  msg_con <- fifo(sink_msg, open = "wb", blocking = TRUE)
  sink(out_con, type = "output")
  sink(msg_con, type = "message")

  err <- NULL; lineno <- NULL; callname <- NULL; interrupted <- FALSE
  t0 <- Sys.time(); p0 <- proc.time()

  parsed <- tryCatch(parse(text = code, keep.source = TRUE), error = function(e) e)
  if (inherits(parsed, "error")) {
    msg <- conditionMessage(parsed)
    err <- paste0("ParseError: ", .oai4s_cap_message(msg))
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
          if (!is.null(cl)) paste0(" in ", .oai4s_deparse1(cl)) else "",
          ": ", .oai4s_cap_message(conditionMessage(e))
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
      # Flushed between top-level expressions so a chatty multi-statement cell
      # reaches the host as it goes. It is no longer the only cadence the host
      # gets -- the drain sees bytes the moment R's connection buffer empties,
      # inside an expression as well -- but an expression that ends without
      # filling that buffer would otherwise sit here until the next one did.
      tryCatch(flush(out_con), error = function(e) NULL)
    }
  }

  .oai4s_unwind_sinks()
  # Closed BEFORE the response frame, so that by the time the host learns the
  # cell is over the fifos are already at EOF and its readers end on that
  # rather than on the grace period they fall back to. Only the fallback is
  # load-bearing for correctness -- moving these two lines below the respond
  # keeps the output, because R closes microseconds later and the reader is
  # still waiting. Stated that way because a mutation proved it: swapping the
  # order changes nothing a test can see, and a comment claiming otherwise
  # would be describing a guarantee this code does not make.
  tryCatch(close(out_con), error = function(e) NULL)
  tryCatch(close(msg_con), error = function(e) NULL)

  wall <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
  dp <- proc.time() - p0
  cpu <- sum(dp[c("user.self", "sys.self", "user.child", "sys.child")], na.rm = TRUE)

  # Empty, and `sink_capture` says why: the host holds this cell's output and
  # fills both fields in. A worker that does not set the flag keeps its own.
  .oai4s_respond(id, "", "", err, interrupted, lineno, callname,
                 wall, cpu, .oai4s_rss_kb(), sink_capture = TRUE)
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
      return(list(active = TRUE, value = NULL, lazy = FALSE))
    }
    # substitute() is tried first because it is the only thing here that can
    # see an unforced promise without running it.  On builds where it exposes
    # a promise's body we get a language object back and stop right there.
    symbol <- as.name(name)
    call <- as.call(list(as.name("substitute"), symbol, env))
    probed <- eval(call, baseenv())
    if (is.language(probed) && !is.symbol(probed)) {
      return(list(active = FALSE, value = probed, lazy = TRUE))
    }
    # Otherwise substitute told us nothing.  It does not substitute bindings
    # from .GlobalEnv -- which is the only environment this inspector reads --
    # so it returned the bare symbol, and using that as the answer reported
    # EVERY ordinary variable as an opaque `symbol` with no type, length or
    # preview.  The inspector was, in effect, a list of names.
    #
    # get0() reads the binding for real.  The cost, stated plainly: a
    # `delayedAssign` binding that substitute did not expose is forced here,
    # which runs user code during inspection.  Only the binding being
    # inspected, and only on builds where the probe above cannot tell -- but
    # it is a real side effect and it is the reason the old code chose the
    # safe, useless answer.
    #
    # And it is wrapped, because forcing is not merely a side effect: a promise
    # whose body raises turns one variable's read into a failure of the WHOLE
    # inspection, which is how this change first showed up -- the entire
    # variable list came back "failed closed" because one `delayedAssign` in
    # the session called stop(). A binding that cannot be read safely degrades
    # to the same opaque answer an unforced promise gets.
    tryCatch(
      list(active = FALSE, value = get0(name, envir = env, inherits = FALSE),
           lazy = FALSE),
      error = function(e) list(active = FALSE, value = NULL, lazy = TRUE),
      condition = function(e) list(active = FALSE, value = NULL, lazy = TRUE)
    )
  }

  inspect_one <- function(name) {
    binding <- binding_value(name)
    if (isTRUE(binding$active)) return(list(name = name, type = "active_binding"))
    if (isTRUE(binding$lazy)) {
      # An unforced promise stays opaque on purpose: reporting its body would
      # be reporting code the user has not run.
      return(list(name = name, type = "language"))
    }
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
    id <- as.character(.oai4s_or(frame$id, "unknown"))
    sink_out <- .oai4s_or(frame$sink_out, NULL)
    sink_msg <- .oai4s_or(frame$sink_err, NULL)
    if (is.null(sink_out) || is.null(sink_msg) ||
        !nzchar(sink_out) || !nzchar(sink_msg)) {
      # Refused rather than run uncaptured. The host is this worker's only
      # caller and always supplies both fifos; running the cell anyway would
      # execute it for real and then report no output at all, which reads as
      # "the code printed nothing" rather than as the protocol break it is.
      .oai4s_respond(
        id, "", "",
        "R kernel received an execute frame with no host capture sinks",
        FALSE, NULL, NULL, 0, 0, NULL
      )
      return("ok")
    }
    .oai4s_run(
      as.character(.oai4s_or(frame$code, "")),
      id,
      as.character(sink_out),
      as.character(sink_msg)
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
                       NULL, NULL, 0, 0, 0L, sink_capture = TRUE)
      } else {
        .oai4s_respond(.oai4s_regex_id(line), "", "",
                       paste0("openai4s r_worker ", outcome), FALSE,
                       NULL, NULL, 0, 0, 0L, sink_capture = TRUE)
      }
    }
  }
}
