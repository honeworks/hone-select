# 0005: A prompt scorer that shows the judge several images

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building teacher-persona, a honeworks demo app. To keep a recurring character consistent,
the app picks, for every scene, the candidate picture that shows the same person as a reference sheet.
The natural judge question compares two pictures: "is the person in this picture the same as in the
reference?". `PromptScorer(images_from="image")` sends exactly one image per candidate
(`[candidate.files[self.images_from]]`), although the `DecisionClient.decide(..., images=...)` port
already takes a sequence and hone-models' vision models accept several images.

The app works around it by pasting the reference and the candidate side by side into one new image per
candidate (`pictures.side_by_side`) and telling the judge "left is the reference, right is the
candidate". This costs a file per candidate, shrinks both pictures, and the judge sometimes reads the two
halves as one scene.

## Options

1. **`images_from` accepts a sequence of file keys**: `images_from=("reference", "image")` sends
   `[candidate.files[k] for k in keys]` in that order. Reference images that are the same for every
   candidate are put into each candidate's `files`. Smallest change; the config form
   `images_from = ["reference", "image"]` works the same.
2. **A separate `reference_images=[...]` argument** on `PromptScorer` (fixed paths sent before the
   candidate's image). Clearer for the reference case, but a second way to pass images.
3. Keep one image; document the side-by-side workaround.

## Decision

Proposed: option 1. The instructions should say which image is which ("the first image is the
reference"); the scorer records the list of keys in its span attributes as it records the one key today.

## Consequences

- Backwards compatible: a string still means one key.
- teacher-persona would drop `side_by_side` and send the sheet and the candidate at full resolution.

## Implementation notes

`images_from: str | Sequence[str] | None` on `PromptScorer` (config: a string or a list). A key missing
from a candidate's `files` makes that call fail with a message naming the key and the files it has, so
the score is `None` with the error. The keys are recorded as `hone.select.image_keys` on the score span.
Implemented together with [0007](0007-pairwise-over-images.md), which shares the helper.
