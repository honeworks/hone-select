# 0002: Pass the trace context to components that accept it

## Status

`implemented in 0.1.0`

## Context

Found while using hone-select together with hone-taste, a package of ready-made scorers that record
their own spans (its `for_select` wrappers). Such a scorer plugs in through the scorer shape (a plain callable, see
[`../current.md`](../current.md) §7.7), and records each scoring call as a span in its own store. Its
spans started a new trace: the scorer keeps its own trace context, and nothing told it which selection it
was working for.

## Problem

The records of a selection and the records of the scorers it called were not linked. Someone reading the
traces could not get from a selection decision to the scorer calls behind it, which is the point of
recording both. The design already said that a package calling another one passes its trace context
explicitly, but the scorer shape was defined as `scorer(candidate)` with no way to receive it.

## Options

1. **Leave it to users**: they wrap each scorer and read `current_trace()` themselves. Easy to forget, and
   every user writes the same wrapper.
2. **Always pass `trace=`** to every component. Breaks every plain `fn(candidate)` scorer.
3. **Pass `trace=current_trace()` when the callable has a `trace` parameter**, and call it without
   otherwise.

## Decision

Option 3, for every kind of component: gates, scorers, pairwise judges and generators. The context
passed is the one of the component's own span (gate, score, pairwise or generate), so the callee's spans
nest under it. The scorer shape in [`../current.md`](../current.md) §7.1 and §7.7 now says a scorer may
take a `trace` keyword.

## Consequences

- A scorer from another package joins the selection's trace without any user code.
- Plain callables are unaffected.
- The engine inspects each callable's signature once, when the component is registered.

## Migration and compatibility

Backwards compatible: existing scorers keep working unchanged. A scorer that already had an unrelated
parameter named `trace` would now receive the trace context; none is known.
