# Third-party notices

hone-select is licensed under the Apache License 2.0 (see [`LICENSE`](LICENSE)).

## Bundled third-party material

None. The wheel and the source distribution contain only code, documentation, examples and test
fixtures written for this project. No model weights, datasets or third-party source files are included.

## Dependencies (installed separately, not redistributed)

| Package | Needed by | Licence |
|---|---|---|
| [pydantic](https://github.com/pydantic/pydantic) | core | MIT |
| [openai](https://github.com/openai/openai-python) | `openai` extra | Apache-2.0 |
| [langchain-core](https://github.com/langchain-ai/langchain) | `langchain` extra | MIT |
| [hone-models](https://github.com/honeworks/hone-models) | `models` extra | Apache-2.0 (its own optional extras list their licences in its `THIRD_PARTY_NOTICES.md`) |
| [typer](https://github.com/fastapi/typer) | `cli` extra | MIT |

Their transitive dependencies are under permissive licences as far as we know; check them yourself if
you redistribute an environment that includes them. The models you call through a judge (hosted APIs or
local models) come with their own terms of use, which you accept separately.
