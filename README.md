# [dev-skills](https://github.com/ahhatem/dev-skills)

Personal agent skills, packaged as one Claude Code plugin. The repository is also its own
plugin marketplace, so a machine needs two commands to get everything.

| Skill | Use it to |
|---|---|
| `review-branch` | Review a feature branch or pull request with the reason for each change beside the diff, in a local web page (Plannotator) or the terminal (Hunk), then have the agent answer your comments. |

## Install on a machine

Needs `git` and `python3`. `review-branch` also needs one viewer:

- [Plannotator](https://plannotator.ai), the web page and the default:
  `curl -fsSL https://plannotator.ai/install.sh | bash -s -- --minimal`
  (`--minimal` installs only the binary: no hooks and no files in other agents' folders);
- or [Hunk](https://github.com/modem-dev/hunk), the terminal fallback:
  `curl -fsSL https://hunk.dev/install.sh | sh`.

Then add the plugin from a terminal (or run the same as `/plugin ...` inside Claude Code):

```sh
claude plugin marketplace add ahhatem/dev-skills   # or its https:// URL, or a local clone's path
claude plugin install dev-skills@dev-skills
```

For a private repository, git on that machine must be able to clone it.

Restart Claude Code. The skill is then `/dev-skills:review-branch`; asking "review this branch"
finds it too.

Update later with `claude plugin marketplace update dev-skills` and then
`claude plugin update dev-skills@dev-skills`.

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
