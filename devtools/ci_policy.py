"""Exact approved CI-only projection; job identity and original release gates remain."""

HUNKS=(
    ("  pull_request:\n", "  pull_request:\n  workflow_dispatch:\n    inputs:\n      workers:\n        description: 'Workers: capacity (up to 4), an explicit count, or 0 for serial fallback'\n        default: capacity\n        type: string\n      scheduler:\n        description: 'xdist scheduler; ignored for serial execution'\n        default: worksteal\n        type: choice\n        options: [worksteal, load]\n"),
    ("    runs-on: ubuntu-latest\n", "    runs-on: ubuntu-latest\n    env:\n      CI_TEST_WORKERS: ${{ inputs.workers || vars.CI_TEST_WORKERS || 'capacity' }}\n      CI_TEST_SCHEDULER: ${{ inputs.scheduler || vars.CI_TEST_SCHEDULER || 'worksteal' }}\n"),
    ('        run: python -m pip install -e ".[test]" -r config/openfootball_snapshot_requirements.txt ruff build\n',
     '        run: python -m pip install -e ".[test]" -r config/openfootball_snapshot_requirements.txt ruff build pytest-xdist==3.8.0 psutil==7.0.0\n'),
    ("        run: python -m compileall -q src tests migrations scripts\n", "        run: python -m compileall -q src tests migrations scripts devtools\n"),
    ("        run: python -m pytest\n", '        run: >-\n          python devtools/measure.py\n          --output "${{ runner.temp }}/integration-full-pytest"\n          --temp-root "${{ runner.temp }}"\n          --workers "$CI_TEST_WORKERS"\n          --dist "$CI_TEST_SCHEDULER"\n          tests devtools/tests\n      - name: Retain complete test evidence\n        if: always()\n        uses: actions/upload-artifact@v4\n        with:\n          name: full-pytest-${{ github.sha }}\n          path: ${{ runner.temp }}/integration-full-pytest/\n          if-no-files-found: error\n          retention-days: 30\n'),
    ('          PUSH_AFTER_SHA: ${{ github.event.after }}\n        run: |\n          zero_sha="0000000000000000000000000000000000000000"\n          if [[ "$EVENT_NAME" == "pull_request" ]]; then\n            git diff --check "$PR_BASE_SHA...$PR_HEAD_SHA"\n          elif [[ "$PUSH_BEFORE_SHA" == "$zero_sha" ]]; then\n            empty_tree_sha="$(git hash-object -t tree -w /dev/null)"\n            git diff --check "$empty_tree_sha" "$PUSH_AFTER_SHA"\n          else\n            git diff --check "$PUSH_BEFORE_SHA" "$PUSH_AFTER_SHA"\n          fi\n',
     '          PUSH_AFTER_SHA: ${{ github.event.after }}\n          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}\n        run: |\n          python -m devtools.whitespace\n'),
)


def project_ci(raw):
    value=raw.replace(b"\r\n",b"\n").decode("utf-8")
    for before,after in reversed(HUNKS):
        if value.count(after)!=1:
            raise ValueError("UNREVIEWED_CI_CONFIGURATION_CHANGE")
        value=value.replace(after,before,1)
    return value.encode("utf-8")
