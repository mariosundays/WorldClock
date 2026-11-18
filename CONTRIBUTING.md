# Contributing to WorldClock

Thanks for your interest! WorldClock is a small Windows desktop widget, and
contributions of all sizes are welcome: bug reports, feature ideas and pull
requests.

## Reporting bugs

Open an [issue](../../issues) and include:

- Your Windows version and monitor setup (how many screens, scaling)
- Style (Dark / Glass), animation and which Glass effects are on
- What you expected vs. what happened (a screen recording helps, in Dark
  style: Glass is hidden from captures by design)
- Your `%APPDATA%\WorldClock\settings.json` if it seems settings-related

## Development setup

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python worldclock.pyw
```

The code:

- `worldclock.pyw`: entry point, window and edge behaviour, tray, autostart, debug CLI
- `ui.py`: painting the pill, columns and hover balloon
- `clockdata.py`: cities, sun / moon, holidays; `cities.json` is built by `tools/build_cities.py`
- `liquid.py` / `liquid_sdf.py`: the liquid drop reveal; `anim_modes.py`: Slide / None
- `glass.py`: Liquid Glass capture and refraction; `glass_fx.py`: Materialize / Merge / Pointer light
- `settings.py`: the settings dialog
- `version.py`: the version number

`--screenshot`, `--frames` and `--settings-shot` render offscreen, so you can
check a visual change without touching the live clock.

## Pull requests

- Keep changes focused; one logical change per PR.
- Match the surrounding code style.
- Describe the before/after behaviour and how you tested it (a render helps).

## Releases (maintainers)

Releases are automated. Bump `__version__` in `version.py`, update
`RELEASE_NOTES.md`, commit, then push a tag:

```bat
git tag v1.0.1
git push origin v1.0.1
```

GitHub Actions builds the installer and zip and publishes a Release.

## License

By contributing, you agree that your contributions will be licensed under the
[GPL-3.0](LICENSE).
