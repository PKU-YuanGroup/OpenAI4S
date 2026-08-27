# Skills installer (`npx openai4s-skills`)

[中文说明](README_zh.md)

Copies OpenAI4S's bundled Skill library onto a machine — into Claude Code, into
an OpenAI4S data directory, or into any directory named on the command line.

```bash
npx openai4s-skills list
npx openai4s-skills install --all                 # the 43 curated Skills
npx openai4s-skills install --collection bioskills # the 561 pinned recipes
npx openai4s-skills install alphafold2 boltz --target claude
npx openai4s-skills installed
npx openai4s-skills uninstall --all
```

`npx github:PKU-YuanGroup/OpenAI4S <command>` runs the same CLI straight from
the repository, with no npm publication involved.

## Files

| File | Responsibility |
| --- | --- |
| `cli.mjs` | Argument parsing and the four commands. The only file with a shebang, and the target of the root `package.json`'s `bin`. `--quiet` drops progress chatter and never the answer — the listing, and a `--dry-run` plan, are the deliverable rather than chatter. |
| `catalog.mjs` | Discovery and frontmatter. Restates the Python loader's rule — a directory is a Skill when it holds `SKILL.md`, and a collection when it holds `COLLECTION.json` with its members one level below — so no directory name is hardcoded on either side. A requested name is matched against both the declared frontmatter names and the directory names, and where the two collide the directory wins: only directory names are guaranteed unique, and a name you can see on disk must select its own directory rather than whichever entry happened to claim it as a frontmatter name. |
| `source.mjs` | Where the Skill tree comes from: a checkout that already has `skills/`, or the source tarball at codeload.github.com, cached per ref. |
| `targz.mjs` | A gzip + POSIX-tar reader with no dependencies, including the path validation that keeps a remote archive from writing outside the extraction root. |
| `install.mjs` | Targets, copying, the `.openai4s-skills.json` manifest, and uninstall. |
| `selftest.mjs` | The gate. Covers extraction safety, discovery against this repository's real tree, and the install/overwrite/uninstall decisions. |
| `check_package.mjs` | The second gate, deliberately separate: does `npm pack` still produce a package with the CLI and the Skills in it? A `files` glob that stopped matching publishes a working command with nothing to install. |

## What it refuses to do

Three refusals carry most of this command's value, because each of them is a
way an installer can quietly destroy work that is not its own:

- **It will not overwrite a Skill you have edited.** Every installed file's
  SHA-256 is recorded, so "a copy we wrote" is distinguishable from "a copy you
  changed" and from "a directory that was already there". `--force` overrides,
  and says which files it is overriding.
- **It will not delete a file it did not write.** `uninstall` removes exactly
  the manifest's files and prunes the directories that end up empty; an extra
  file you added blocks removal rather than being swept up with it.
- **It will not extract an archive member that escapes the target.** Absolute
  paths, `..` segments, drive letters, and NUL bytes are rejected outright, and
  a symlink or hardlink member aborts the extraction rather than being skipped —
  a silent skip would report a complete tree it had not produced.

## Provenance

An install records where its bytes came from. For a download that is the ref
requested, the URL, and the SHA-256 of the tarball actually received — not a
commit SHA, because resolving a branch to a commit would be a second request
whose answer nothing here checks against the archive it unpacked. Pass
`--ref <commit-sha>` for an install that is reproducible by construction.

## Where this fits

For an OpenAI4S user this command is mostly redundant: the wheel already ships
all 604 Skills, and `openai4s/skills_loader/loader.py` gives a bundled Skill
precedence over a same-named one in `<data_dir>/user-skills`. Its reason to
exist is the other direction — putting these recipes in front of an agent that
is not OpenAI4S.
