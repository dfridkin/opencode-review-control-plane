# Incident evals

The reviewer is software. It gets a regression suite.

## Add a case

1. Find a PR that caused a revert or an incident (label it `incident-cause` going forward).
2. `mkdir evals/incidents/NNN-short-name`
3. Save the diff that shipped the bug: `git show <sha> > evals/incidents/NNN-short-name/diff.patch`
   (or `gh pr diff <n>`). Trim unrelated files.
4. Write `case.toml`:

```toml
id = "NNN-short-name"
kind = "incident"            # or "control" for a clean diff
source = "INC-123 / PR #456"
description = "What broke, in one sentence."
line_tolerance = 3

[[expected]]
file = "src/path/to/file.py"   # path in the NEW version
line = 42                      # line of the defect in the NEW version
keywords = ["tenant", "scope"] # any of these in the finding's title/evidence also counts
```

A finding matches when it names the right file AND either lands within `line_tolerance` lines
or uses one of the keywords.

Keep roughly one clean `control` case for every two incidents, so the score punishes noise as well
as misses. A control fails if the reviewer reports anything at `major` or above.

## Run

```bash
# live, majority of 3 runs per case
LLM_BASE_URL=http://gpu-box:8000/v1 \
  python3 evals/run_evals.py --live --model openweight/<model> --runs 3

# accept the current score as the new floor (do this in a PR a human approves)
python3 evals/run_evals.py --live --model openweight/<model> --runs 3 --write-baseline

# offline replay of saved responses (used by the unit tests)
python3 evals/run_evals.py --replay evals/fixtures/replay
```

Each model keeps its own baseline in `evals/baselines/<model>.json`, so the same cases double as a
like-for-like comparison between candidate models on your own failures.

In CI, `review-evals.yml` compares against the baseline from the PR's **base** commit, so a PR
can't lower the bar and pass in the same change. Removing incident cases is also flagged.

The three seed cases are illustrative. Replace them.
