---
name: review-branch
description: Use when the user wants to review a feature branch, a pull request, or work just finished in this session, especially when they want the reason for each change shown beside the diff, an interactive or visual review, or inline comments the agent can read back.
---

# Review a branch with the reason beside each change

A diff shows what changed; a reviewer needs why. Write a notes file where each note sits on its
lines and says where its reason came from, then open it for the user: a local web page
(Plannotator) by default, or Hunk, a terminal diff viewer the user runs and you never do.

Helper: `python3 <this skill's directory>/scripts/review_branch.py` with `map`, `check` or `open`
(no args prints usage). Below, `review_branch.py` means that full command.

## Steps

1. **Pick the viewer.** `plannotator --version`: the web page, the default. Missing, or the user
   asks for the terminal: `hunk --version` (or `~/.hunk/bin/hunk`). Neither: tell the user to
   install one (Hunk: `curl -fsSL https://hunk.dev/install.sh | sh`) and stop.
2. **Resolve the range.**
   - head: the named branch, else the current one. On origin and not built here: `git fetch origin <branch>`, use `origin/<branch>`. Local only: its local name.
   - base: the branch the PR targets, from the user or the repo's instructions; never assume `main`. Use `origin/<base>` after a fetch.
   - worktree: the one holding head (`git worktree list`), else the repo root. If `git -C <worktree> status --porcelain` is not empty, say those changes are not in the review.
3. **Map.** `review_branch.py map <base> <head> --repo <worktree> > <scratch>/map.json`. It lists every commit message and splits each hunk into blocks credited to the commit that last wrote them. A commit that renames or reformats lines that will carry notes: rerun with `--ignore <sha>`, since its message explains the rename, not the lines.
4. **Find the reasons.** Each note's `author` names the source of its main point, so Hunk shows how far to trust it before it is read:

   | `author` | Source |
   |---|---|
   | `commit <sha>` | that commit's message (in map.json) |
   | `design` | a design doc, spec, plan or PR description; the rationale names which |
   | `session` | what the user asked or decided in this conversation, when the work was built here |
   | `inferred` | your reading of the code; nothing written says why |
   | `review` | a problem or risk you found, not a reason |
   | `<source> + review` | a reason and a risk about the same lines, in one note |

   Review depth: read the diff and the code it calls. Run the repo's existing tests once, with caches and temp files in the scratch directory, unless this session already ran them on this head; cite that run. Nothing that needs credentials. A question only an experiment would settle becomes a `review` note marked unverified.
5. **Write the notes** to the scratch directory, never inside the repo:
   `{"version":1,"summary":str,"files":[{"path":str,"summary":str,"annotations":[{"newRange":[start,end],"summary":str,"rationale":str,"author":str}]}]}`.
   - One note per logical change (a function, a config block, a pipeline, a test class), not per blame block. Boilerplate gets none.
   - `summary`: what changed, one line. `rationale`: the reason in words, then every source by name. Hunk does not show commit messages, so a bare sha explains nothing.
   - `newRange`: changed lines in head's version of the file, never overlapping another note's.
   - Every changed file gets a `summary`. The top `summary` gives what the branch does, the range, a reading order (data, logic, wiring, tests), and any finding that sits on no changed line (scope, PR text, deleted code).
6. **Check.** `review_branch.py check <base> <head> <notes.json> --repo <worktree> --show`. Fix every error and warning, and confirm from the printed first and last lines that each note sits on the code it describes.
7. **Hand off.** Tell the user the range and size, the note count per source (all `commit <sha>`
   notes together as `commit`), and the `review` findings most important first. Then:
   - Plannotator: run `review_branch.py open <base> <head> <notes.json> --repo <worktree>`
     with `run_in_background`. It opens the page in the user's browser; within seconds the task
     output shows `Review page: <url>`, which you give the user in case no tab opened. Tell them to
     comment on lines and press Submit when done, then end your turn: you are woken when it exits.
     An `error:` exit means the notes no longer match the range; go back to step 3.
   - Hunk: give `cd <worktree> && hunk diff <base>...<head> --agent-context <notes.json>`, and ask
     them to comment inline and say when done.
8. **Answer the feedback.**
   - Plannotator: the task output ends with `{"decision", "message"}`, also saved beside the notes
     as `*.feedback-*.json`. `dismissed`: nothing to answer. Otherwise `message` holds the user's
     comments, followed by Plannotator's standing instructions; answer every comment in the chat,
     and change code only where the user asked for a change.
   - Hunk: `hunk session comment list --repo <worktree> --type user --json`. Answer each:
     `hunk session comment add --repo <worktree> --reply-to <id> --summary "..." --author review`.

   After a fix commit, line numbers move: regenerate and re-check the notes, then reopen (rerun
   `review_branch.py open`, or `hunk session reload --repo <worktree> -- diff <base>...<head>`).

## Common mistakes

| Mistake | Effect |
|---|---|
| Base is local `main` when the PR targets another branch | The diff includes commits that are not on the branch |
| A rename commit cited as a line's reason | The reviewer reads "renamed pr" where the reason should be |
| Guessed reasons labelled as fact | The reviewer trusts an inference; use `inferred` |
| A risk moved onto nearby unchanged lines to avoid an overlap | It reads as a note about those lines; use `<source> + review` |
| Running `review_branch.py open` in the foreground, or `hunk diff` at all | Your shell hangs until the user finishes |
| `plannotator review` run directly | It diffs the working tree, so uncommitted edits shift every note; sharing and update checks stay on |
| Notes saved in the repo | They get committed |

Unverified: whether `reload` keeps sidecar notes.
