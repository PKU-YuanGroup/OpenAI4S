"""Change three frontmatter fields without touching the rest.

Editing a skill in Web Customize rebuilt its frontmatter from scratch:

    ---
    name: <name>
    description: <description>
    origin: <origin>
    ---

Everything else the author had written was gone. Across the 34 bundled skills
that is `license` and `category` on 23 of them, a nested `metadata` block on
17, `requirements` on 13, and `fold_cue` on one — deleted by someone fixing a
typo in a description, with nothing said.

`requirements` is the one that turns a metadata loss into a wrong answer.
Readiness is computed from it, so a skill that declared `[gpu]` and lost it
stops reporting `needs_setup` and starts reporting `ready` — it now claims it
can run on a machine where it cannot.

The obvious repair is to parse the frontmatter, override three keys and
re-emit. It does not work, and the reason is worth stating: `_parse_frontmatter`
is a deliberately small YAML subset that flattens `requirements: [gpu, cuda]`
to a string and *ignores* the indented lines under a nested `metadata:`.
Re-emitting from what it returns would preserve the keys it understands and
silently drop the structure it does not — a narrower version of the same bug,
and harder to notice.

So this edits the raw text. The three owned fields are replaced line by line
(with their block-scalar continuations, if any); every other line survives
byte for byte, whether or not anything in this repo understands it.
"""

from __future__ import annotations

#: The only fields the editor owns. Everything else belongs to whoever wrote
#: the skill, including keys added after this code was written — which is the
#: point: a rebuild-from-known-fields breaks every time the vocabulary grows,
#: and breaks silently.
OWNED_FIELDS = ("name", "description", "origin")

#: Web Customize import always stamps this origin. The author's other
#: frontmatter, including keys this code has never heard of, is the seed.
IMPORT_ORIGIN = "user"


def _is_top_level_key(line: str) -> bool:
    """A `key:` at column 0. Indented lines belong to the block above."""
    if not line or line[:1].isspace() or line.lstrip().startswith("#"):
        return False
    return ":" in line


def _key_of(line: str) -> str:
    """The key of a top-level line, folded the way `_parse_frontmatter` folds it.

    The loader lowercases every key and keeps the *last* occurrence. Matching
    case-sensitively here meant a document written with `Name:` was not
    recognised as owning that field: a fresh `name:` was prepended and the
    capitalised original preserved after it -- which is the one the loader
    then read. An edit that did not take effect, and an import stamped
    `origin: user` that loaded as whatever `Origin:` the author wrote.
    """
    return line.split(":", 1)[0].strip().lower()


def split_document(text: str) -> tuple[list[str], str]:
    """Return the raw frontmatter lines and the body, or `([], text)`.

    Deliberately the same delimiter handling as `_parse_frontmatter`, so a
    document that parser reads as having frontmatter is one this writer edits
    rather than prepends to.
    """
    if not text.startswith("---"):
        return [], text
    end = text.find("\n---", 3)
    if end == -1:
        return [], text
    raw = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    return raw.splitlines(), body


def _drop_field(lines: list[str], key: str) -> list[str]:
    """Remove one top-level field and any continuation lines beneath it.

    The continuation matters: `description: >` is followed by more-indented
    lines that are part of its value. Replacing only the `description:` line
    would leave that text stranded at the top level, where the next parse reads
    it as garbage — or, worse, as a key.
    """
    out: list[str] = []
    skipping = False
    for line in lines:
        if _is_top_level_key(line):
            skipping = _key_of(line) == key
            if skipping:
                continue
        elif skipping and (not line.strip() or line[:1].isspace()):
            # a continuation of the field being removed
            continue
        else:
            skipping = False
        out.append(line)
    return out


def _scalar_lines(key: str, value: str) -> list[str]:
    """One owned field as the lines `_parse_frontmatter` reads back as it.

    A value with newlines -- the loader returns a `description: |` block with
    its line breaks intact -- cannot be written as a bare `key: value`: the
    continuation lines would land at column 0, where the next parse reads
    them as top-level keys. An imported `description: |` whose second line
    said `capabilities: ...` became a real field, and the description lost
    every line but the first. The block scalar the loader already understands
    keeps the value whole.
    """
    text = str(value or "")
    if "\n" not in text:
        return [f"{key}: {text}"]
    return [f"{key}: |", *(f"  {line}" for line in text.splitlines())]


def rewrite(
    original: str, *, name: str, description: str, origin: str, body: str
) -> str:
    """Return a document with the three owned fields set, everything else kept.

    `original` supplies the frontmatter to preserve; `body` is the new prose,
    which comes from the form rather than from the file. Keeping them as
    separate arguments is deliberate — an earlier draft took the body from
    `original` and then needed string surgery at the call site to put the
    edited text back, which is how the preserved frontmatter would have got
    mangled instead of the frontmatter that was being replaced.

    The owned fields are written first so the head of the file stays
    predictable for a human reader, and the author's remaining lines follow in
    the order they were written.
    """
    lines, _old_body = split_document(original or "")
    preserved = lines
    for field in OWNED_FIELDS:
        preserved = _drop_field(preserved, field)
    # Blank lines that only separated removed fields would accumulate on every
    # edit, so leading blanks are dropped; interior ones are the author's.
    while preserved and not preserved[0].strip():
        preserved.pop(0)

    head = [
        *_scalar_lines("name", name),
        *_scalar_lines("description", description),
        *_scalar_lines("origin", origin),
    ]
    frontmatter = "\n".join(head + preserved)
    return f"---\n{frontmatter}\n---\n\n{(body or '').strip()}\n"


def rewrite_import(original: str, *, name: str, description: str, body: str) -> str:
    """Import overlay: origin is always ``user``; every other line is kept.

    ``original`` is the pasted SKILL.md, not an on-disk previous version. A
    new import has no previous file, so passing ``""`` here would rebuild
    from the three owned fields and drop requirements, capabilities,
    license, category, comments, and unknown nested keys.
    """
    return rewrite(
        original,
        name=name,
        description=description,
        origin=IMPORT_ORIGIN,
        body=body,
    )


__all__ = [
    "IMPORT_ORIGIN",
    "OWNED_FIELDS",
    "rewrite",
    "rewrite_import",
    "split_document",
]
