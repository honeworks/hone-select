# 0007: Pairwise judging over images

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building explainer-channel, a honeworks demo app. The first demo episode picked its
thumbnail with `PromptScorer(images_from="image")` and the audience panel; the two best thumbnails tied
exactly (0.867 each), and the first one was kept by order. For text the app breaks such ties with
`PromptPairwise` (`escalate = "pairwise"`), but `PromptPairwise` has no `images_from`: it only sends the
two candidates' text (`candidate_state(a, field)`), so it cannot compare two pictures. The same happens
for illustration picks (two candidates per beat, discrete 0 / 0.5 / 1 scores tie often).

## Problem

Ties between image candidates cannot be escalated to a pairwise judge; the winner is decided by order.

## Options

1. **`PromptPairwise(images_from="image")`**: sends `[a.files[k], b.files[k]]` as the two images (in the
   order asked, A then B, and swapped for the second order, as today). The instructions say "the first
   image is A, the second is B". Mirrors `PromptScorer`; the config key works the same.
2. **Side-by-side composites** made by the app (one image, "left is A"): works today, shrinks both
   pictures, costs a file per pair.
3. Keep order as the tie-break; document it.

## Decision

Proposed: option 1 (with 0005's sequence form it would also allow a shared reference image).

## Consequences

- The decision client must accept two images (hone-models vision models do).
- Records: the pairwise span notes the image keys sent; position-bias handling is unchanged (both orders).

## Implementation notes

`PromptPairwise(images_from=...)` takes a key or a sequence of keys like `PromptScorer`
([0005](0005-prompt-scorer-with-several-images.md)) and sends A's images, then B's. A file both
candidates share (the same path, e.g. a reference sheet) is sent once, in its first position, so
`images_from=("reference", "image")` sends reference, A, B. The pairwise span records
`hone.select.image_keys`. `PromptPairwise` has no config form, so there is no config key. Checked on
fakes only; a real vision judge on two images is validated later on the GPU.
