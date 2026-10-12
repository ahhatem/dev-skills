# [dev-skills](https://github.com/ahhatem/dev-skills)

Personal agent skills, packaged as one Claude Code plugin. The repository is also its own
plugin marketplace, so a machine needs two commands to get everything.

| Skill | Use it to |
|---|---|
| `review-branch` | Review a feature branch or pull request with the reason for each change beside the diff, in a local web page (Plannotator) or the terminal (Hunk), then have the agent answer your comments. |

## Install on a machine

Paste this into a terminal on macOS, Linux or WSL:

```sh
for c in git python3 claude; do command -v $c >/dev/null || echo "Missing: $c (install it first)"; done
command -v plannotator >/dev/null || curl -fsSL https://plannotator.ai/install.sh | bash -s -- --minimal
claude plugin marketplace add ahhatem/dev-skills
claude plugin install dev-skills@dev-skills
```

- [Plannotator](https://plannotator.ai) shows the review as a local web page. `--minimal` installs
  only its binary: no hooks, and no files in other agents' folders.
- [Hunk](https://github.com/modem-dev/hunk) is optional, for reviewing in the terminal instead:
  `curl -fsSL https://hunk.dev/install.sh | sh`.
- `marketplace add` also takes the repository's https:// URL or a local clone's path. For a
  private repository, git on that machine must be able to clone it.

Then restart Claude Code. The skill is then `/dev-skills:review-branch`; asking "review this branch"
finds it too.

Update later with `claude plugin marketplace update dev-skills` and then
`claude plugin update dev-skills@dev-skills`.

### OpenCode

OpenCode does not install Claude Code plugins, but it reads skill folders: its own
`~/.config/opencode/skills/`, plus `~/.claude/skills/` and `~/.agents/skills/`. Clone the repo and
link the skill there:

```sh
for c in git python3 opencode; do command -v $c >/dev/null || echo "Missing: $c (install it first)"; done
command -v plannotator >/dev/null || curl -fsSL https://plannotator.ai/install.sh | bash -s -- --minimal
git clone https://github.com/ahhatem/dev-skills ~/dev-skills
mkdir -p ~/.config/opencode/skills && ln -s ~/dev-skills/skills/review-branch ~/.config/opencode/skills/review-branch
```

Update later with `git -C ~/dev-skills pull`. If `~/.claude/skills/review-branch` already exists
on that machine (see "Work on the skills"), skip the link: OpenCode already sees the skill, and
a second link would list it twice.

The review page wakes the agent when you submit, which needs a shell tool that can run a command
in the background and resume the agent when it exits: Claude Code and OpenCode 2 have one.

## Work on the skills

An installed plugin is a copy, so edits made here reach it only after a push and an update. On
the machine where you edit, link the skill instead of installing the plugin. Changes then apply
from the next session, and the name stays `/review-branch`:

```sh
ln -s ~/dev-skills/skills/review-branch ~/.claude/skills/review-branch
```

Do not install the plugin on that machine as well, or the skill appears twice.

Before committing:

```sh
python3 -m unittest discover -s tests     # the open tests are skipped without plannotator
claude plugin validate .
```

Bump `version` in both `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` when
releasing a change, so installed copies see it as an update.

## Layout

```
.claude-plugin/plugin.json         the plugin
.claude-plugin/marketplace.json    lists the plugin, so this repo is its own marketplace
skills/review-branch/SKILL.md      the instructions the agent follows
skills/review-branch/scripts/review_branch.py   map | check | open (no args prints usage)
tests/                             unit tests on throwaway git repositories
```

## Where review data goes

`review-branch` writes notes and the reviewer's feedback to the agent's scratch directory, or
wherever you point it, never into the repository under review or into this one. A head that is
not checked out is reviewed in a temporary worktree under `~/.cache/review-branch/worktrees`,
which is removed when the review ends.
