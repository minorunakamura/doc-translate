# Feature Map: 保存記録status（初期対象）

対象：公開CLIの軽量status／明示詳細検査／完全JSON、必要記録破損拒否、保存不変。対象外：進捗通知・Rich・TTY、run／resume／review新表示、実LLM品質、実PDF変換／OCR、他OS、remote CI、任意規模の性能保証。性能の別確認は[正式Plan](../../../../docs/plans/progress-and-status.md)の再作成条件・非並行before／after手順を使い、巨大benchmark基盤を追加しない。

## 保存記録と詳細検査

入口は`doc-translate status DIR`、`--verify`、`--json`、`--verify --json`。既存自作`tests/fixtures/render-source.pdf.json`を再利用し、Node／認証／network不要な公開CLIだけを操作する。repository rootで既存環境を使う（自動syncしない）。下の2ブロックを同じshellで順に実行する。

```sh
set -eu
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mkdir -p work
EVIDENCE=$(mktemp -d "$PWD/work/test-verification-status-XXXXXX")
export EVIDENCE
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git status --short > "$EVIDENCE/dirty.txt"
git diff -- src/doc_translate/render.py src/doc_translate/saved_status.py src/doc_translate/cli.py src/doc_translate/cli_output.py > "$EVIDENCE/scope.diff"
# 新規untracked moduleも含め、実際に操作したreader版を残す。
cp src/doc_translate/saved_status.py "$EVIDENCE/saved-status.py.txt"
.venv/bin/python -c 'import sys, pydantic, doc_translate.render; print(sys.version); print("pydantic", pydantic.__version__); print("saved-only imports usable; no sync/install; PATH empty for CLI probes")' > "$EVIDENCE/preflight.txt"
.venv/bin/doc-translate status --help > "$EVIDENCE/help.txt"
```

```sh
.venv/bin/python - <<'PY'
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from doc_translate.snapshot import SourceSnapshot, save_source_snapshot

evidence = Path(os.environ['EVIDENCE'])
cli = str(Path(sys.executable).with_name('doc-translate'))
env = {**os.environ, 'PATH': '', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
observations = []

def files(workspace):
    return {str(p.relative_to(workspace)): [hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns]
            for p in workspace.rglob('*') if p.is_file()}

def invoke(workspace, name, flags, expected, operation='status'):
    before = files(workspace)
    command = [cli, operation, str(workspace), *flags]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    (evidence / (name + '.stdout')).write_text(result.stdout)
    (evidence / (name + '.stderr')).write_text(result.stderr)
    observations.append({'name': name, 'command': command, 'expected_exit': expected, 'exit': result.returncode})
    assert result.returncode == expected, (name, result.returncode, result.stderr)
    if operation == 'status':
        assert files(workspace) == before, 'status changed saved files or mtime'
    return result.stdout

try:
    with tempfile.TemporaryDirectory(prefix='status-owned-', dir=evidence) as temporary:
        workspace = Path(temporary) / 'workspace'
        fixture = Path('tests/fixtures/render-source.pdf.json')
        source = SourceSnapshot.model_validate_json(fixture.read_bytes())
        save_source_snapshot(workspace, source)
        observations.append({'fixture': str(fixture), 'source_pointer': json.loads((workspace / 'source-snapshot.json').read_bytes())})
        rendered = json.loads(invoke(workspace, 'render', [], 1, 'render'))
        initial = files(workspace)
        (evidence / 'initial-files.json').write_text(json.dumps(initial, indent=2))
        light = invoke(workspace, 'light', [], 0)
        assert '保存記録のみ' in light and '成果物未検証' in light and '不明／未集計' in light
        assert '最新' not in light and '有効' not in light
        verified = invoke(workspace, 'verify', ['--verify'], 0)
        assert '最新' in verified
        detailed = json.loads(invoke(workspace, 'json', ['--json'], 0))
        combined = json.loads(invoke(workspace, 'verify-json', ['--verify', '--json'], 0))
        assert detailed == combined and detailed['artifacts'] == rendered['artifacts']
        assert detailed['processing']['status'] == 'partial' and detailed['render_current'] is True
        japanese = workspace / rendered['artifacts']['japanese']
        original = japanese.read_bytes()
        japanese.write_text('artificial output damage')
        assert '成果物未検証' in invoke(workspace, 'damaged-output-light', [], 0)
        invoke(workspace, 'damaged-output-verify', ['--verify'], 2)
        invoke(workspace, 'damaged-output-json', ['--json'], 2)
        japanese.write_bytes(original)
        summary = workspace / rendered['artifacts']['summary']
        summary.write_text('{}')
        invoke(workspace, 'damaged-required-summary', [], 2)
        observations.append({'expected': 'light read leaves outputs unverified; full inspection rejects output damage; required summary hash damage rejected', 'observed': 'matched', 'unverified': 'translation counts/version differences/races are focused tests; large performance and real translated faux workspace are separate Root probes'})
finally:
    # TemporaryDirectory has removed only this invocation's synthetic workspace.
    (evidence / 'observations.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2))
    leftovers = list(evidence.glob('status-owned-*'))
    (evidence / 'cleanup.txt').write_text('owned workspaces removed; short-lived children reaped\n' if not leftovers else 'cleanup incomplete\n')
    assert not leftovers

# Evidence lives outside the deleted workspace; reread every file after cleanup.
for path in evidence.iterdir():
    if path.is_file():
        assert path.read_bytes() is not None
print(evidence)
print('public CLI exits/output/full JSON/read-only/damage boundaries matched; cleanup and evidence reread complete')
PY
```

期待：source-onlyの翻訳総数はunknown、過去renderは保存当時、通常表示は成果物未検証。通常statusと詳細statusの全成功読取りはexit0・file hash／mtime不変。`--json`と併用JSONが等しい。成果物破損は通常statusで未検証のまま、詳細はexit2。必要なsummary破損は通常もexit2。表示だけでなく`initial-files.json`と各operationのexitを`observations.json`で照合する。ログは合成fixtureのみで、実秘密を記録しない。

証拠：`revision.txt/dirty.txt/scope.diff/saved-status.py.txt/preflight.txt/help.txt/initial-files.json/observations.json/cleanup.txt`と各`.stdout/.stderr`。失敗時もobservationsとcleanupを確認し、成功に書き換えない。証拠はworkspace外に残り、末尾でcleanup後の再読を行う。手順変更後はこの公開入口を再実行する。

## 補助のfocused回帰と未確認範囲

`.venv/bin/pytest tests/test_render.py tests/test_cli_output.py`がrun-only／source-only／leaf分類・取消／旧source・preparation・run設定差／candidate／schema・型・hash・path・symlink／限定再読／詳細検査中更新を扱う。生成手順の一機能成立だけで全保証を検証済みとしない。内部workflowやJSON consumerへの影響はCONTRIBUTINGの選定手順に従い別途正式チェックする。
