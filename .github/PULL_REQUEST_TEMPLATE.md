## Summary

<!-- What changes, and why. -->

## Type of change

- [ ] Bug fix
- [ ] New tool or new tool argument
- [ ] Refactor / internal change
- [ ] Documentation
- [ ] CI / packaging

## Checklist

- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `pytest --cov=hermes_yandex_disk --cov-fail-under=90` passes, and new error paths are covered
- [ ] `radon cc -s -n C hermes_yandex_disk` prints nothing
- [ ] `client.py` and `paths.py` still import nothing from Hermes
- [ ] Every handler still returns JSON on every path, including failure
- [ ] Every path still goes through `paths.resolve`, so `YANDEX_DISK_ROOT` still holds
- [ ] A new tool is listed in `config.ACTIONS`, an `ACTION_GROUPS` group, `_TOOLS`, `plugin.yaml` and the README table
- [ ] README updated if the user-visible behaviour changed
- [ ] Live e2e run (`pytest -m e2e`) if the change touches the API calls — say what you ran
