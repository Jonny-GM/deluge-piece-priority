# Working on deluge-piece-priority

Guidance for agents (and humans) making changes here.

## The spec is load-bearing

`docs/spec/` is the project's contract, not documentation written after the
fact. The rule:

> **Any change to externally observable behavior lands in the same PR as a
> spec update.** If the spec and the code disagree, that is a bug in one of
> them — decide which, and fix that one.

Externally observable means:

- The Core plugin's RPC surface: every `@export`-decorated method, its
  parameters, return shape, and error semantics — this is the contract
  external callers (`deluge-console`, a caller embedding Deluge as a
  streaming backend, any other RPC client) drive the plugin through.
- The exact libtorrent calls issued and their semantics: priority vs.
  deadline behavior, how they interact with Deluge's own `sequential_download`
  and "prioritize first/last piece" features, and what happens across
  torrent restarts/re-adds.
- WebUI-exposed behavior: endpoints/JS surface and visible UI behavior.
- GTK3 UI-exposed behavior: visible UI behavior and user-facing controls.
- Config/preferences schema: keys, defaults, and persistence behavior.
- Supported Deluge and libtorrent version matrix, and plugin packaging
  (entry points under `[deluge.plugin.*]`).

Below spec level (change freely, no spec edit needed):

- Log output — messages, levels, fields.
- Internal refactors, test infrastructure, CI mechanics, packaging scripts
  that don't change the installed plugin's behavior.
- Performance work that doesn't change contracts or defaults.

When unsure, err toward updating the spec: a one-line spec edit is cheap,
and a reader (or a programmatic consumer) discovering unspecced behavior is
expensive.

## Spec style

- Specs contain **decisions, not open questions**. When a choice comes up,
  make the call (or ask the maintainer), write down the outcome and the
  one-sentence why. No "TBD" sections.
- Keep each doc's scope: overview = boundaries and principles; numbered
  specs = their layer. New surface area gets a new numbered doc.

## Development environment

Dependencies and the dev venv are managed with `uv` (`pyproject.toml` +
`uv.lock`), not pip/`requirements.txt`. One wrinkle: `uv sync` doesn't
support `--system-site-packages`, which the venv needs to see the
system-installed `libtorrent` package (its compiled extension is tied to a
specific Python ABI — building it from source via PyPI instead is a much
heavier ask). So the venv is created manually with
`uv venv --system-site-packages` and populated with `uv pip install`
rather than `uv sync` — see the README's Development section for the exact
commands. Don't "fix" this back to plain `uv sync`; it's a deliberate
workaround, not an oversight.

## Development conventions

- This plugin is consumed programmatically as much as it's used
  interactively — treat the RPC surface with the same rigor as a public
  API, not as an implementation detail of the GTK3/WebUI frontends.
- Reproduce reported bugs as automated tests before fixing them, so the fix
  is demonstrated rather than asserted.
- All Python code is fully type-hinted — function parameters, return types,
  and non-obvious variables. Run `ty check` before considering a change
  done; this project doesn't ship code `ty` would flag. Type checker is
  `ty` (Astral's, same vendor as `uv`), not `mypy` — chosen deliberately
  for the fastest checker over the most mature one, with a known trade-off:
  `ty` is still in beta and has real gaps (e.g. no per-module "ignore
  missing import" override, unlike mypy — see the comment in
  `deluge_piecepriority/core.py` on the `libtorrent` import for the
  concrete case this hit). A clean `ty` run is not the same strength of
  guarantee a mature checker would give; don't treat it as one.
  Third-party libraries without stubs (`deluge`, `libtorrent`) are
  suppressed at the specific import site that needs it, not exempted
  wholesale — `ty` has no global/per-module equivalent to reach for here.
