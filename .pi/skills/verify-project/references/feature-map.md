# Feature Map: 保存記録status／EPUB共通進捗

対象：公開CLIの軽量status／明示詳細検査／完全JSON、必要記録破損拒否、保存不変。EPUB／共通表示は末尾節が対象。対象外：PDF補正／review新表示の完成、実LLM品質、実PDF変換／OCR、他OS、remote CI、任意規模の性能保証。性能の別確認は[正式Plan](../../../../docs/plans/progress-and-status.md)の再作成条件・非並行before／after手順を使い、巨大benchmark基盤を追加しない。

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

## EPUB run／resumeと共通進捗（Issue #50）

入口は公開`run INPUT --workspace DIR`／`resume DIR`。EPUB新表示・共通request観測・出力flagsが対象です。PDF補正の工程／所有ページ集計とreview新表示は後続Issueであり、この手順の成功で完成扱いしません。既存status手順は上記のまま維持します。

前提：既存`.venv`、Node、`ai/dist`と既存依存が使用可能なこと（`CONTRIBUTING.md`の正式チェック参照）。追加install／setup／実認証／model／実書籍は不要です。実Python CLI→実Node `run-process.mjs`→network拒否付き公開fauxを使います。fixtureの`process_command`は既存の外部process seamであり、内部workflowのmockではありません。

### TTY、狭幅、NO_COLOR、保存との照合

repository rootで下を実行します。`capture_progress.py`はstderrだけを所有PTYへ接続し、stdoutを分離します。`--mirror`を追加すれば自分の端末で直接観測できます。専用terminal tool不要。helperは今回の小さいRich CSIだけをframeへ投影します。`frames.html`は実stderrのcell位置とSGR色を保持したHTML投影です（標準xterm palette、スクリーンショット／汎用terminal emulatorではありません）。既存Chromeで実表示・screenshotを観測し、現在対象と異常理由の読取りを照合します。SGR検出／HTML生成だけを明暗背景の視認性確認と呼びません。

```sh
EVIDENCE=$(mktemp -d "$PWD/work/test-verification-progress-XXXXXX")
export EVIDENCE HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PYTHONPATH=tests .venv/bin/python - <<'PY'
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from test_epub import make_epub
from doc_translate.translation import load_translation
root, evidence = Path.cwd(), Path(os.environ['EVIDENCE'])
python = str(Path(sys.executable).absolute())
(evidence / 'revision.txt').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True))
(evidence / 'dirty.txt').write_text(subprocess.check_output(['git', 'status', '--short'], text=True))
(evidence / 'scope.diff').write_text(subprocess.check_output(['git', 'diff'], text=True))
observations = []
try:
    with tempfile.TemporaryDirectory(prefix='progress-owned-') as owned:
        temporary = Path(owned)
        source = make_epub(temporary)  # 既存の自作mixed-inline入力のみ
        for name, columns, rows, background, no_color, fail in [
            ('dark', 80, 24, 'dark', False, False),
            ('light', 80, 24, 'light', False, False),
            ('light-narrow', 36, 10, 'light', False, True),
            ('dark-narrow', 36, 10, 'dark', False, True),
            ('no-color', 36, 10, 'dark', True, False),
        ]:
            case = temporary / name
            case.mkdir()
            workspace = case / 'workspace'
            settings = case / 'settings.json'
            settings.write_text(json.dumps({'delay_phase': 'collect', 'delay_ms': 800, **({'fail_stage': 'translation'} if fail else {})}))
            helper = [shutil.which('node'), str(root / 'ai/tests/run-process.mjs'),
                      str(case / 'credentials'), str(settings), str(case / 'requests.jsonl')]
            entry = [python, str(root / 'tests/fixtures/resume_process.py'), '--helper', json.dumps(helper), '--']
            run = [*entry, 'run', str(source), '--workspace', str(workspace), '--provider', 'faux', '--model', 'text', '--thinking', 'off', '--json']
            env = {**os.environ, 'TERM': 'xterm-256color', 'COLORFGBG': '0;15' if background == 'light' else '15;0'}
            env.pop('NO_COLOR', None)
            if no_color:
                env['NO_COLOR'] = ''  # 空値も「存在」
            command = [python, str(root / '.pi/skills/verify-project/scripts/capture_progress.py'),
                       '--evidence', str(evidence / name), '--columns', str(columns), '--rows', str(rows),
                       '--background', background, '--', *run]
            result = subprocess.run(command, env=env, check=False)
            assert result.returncode == (1 if fail else 0)
            saved = json.loads((evidence / name / 'stdout.txt').read_text())
            assert saved['processing']['status'] == ('partial' if fail else 'completed')
            transcript = (evidence / name / 'stderr.ansi').read_text()
            assert 'PDF' not in transcript and 'ページ' not in transcript
            assert 'Original' not in transcript and 'PRIVATE' not in transcript
            assert all(word in transcript for word in ['collect', 'organize', '翻訳', 'Markdown出力'])
            if no_color:
                import re
                assert not re.search(r'\x1b\[[0-9;]*m', transcript)
            frames = json.loads((evidence / name / 'frames.json').read_text())
            assert any('AI応答待ち' in f['text'] and '対象' in f['text'] for f in frames)
            if fail:
                assert '現在:' in frames[-1]['text'] and 'translation_rejected' in frames[-1]['text']
            request_count = len((case / 'requests.jsonl').read_text().splitlines())
            assert request_count == 3
            if fail:
                assert 'translation_rejected' in transcript
                partial_immutable = {p.relative_to(workspace): p.read_bytes() for directory in ['originals', 'snapshots', 'attempts'] for p in (workspace / directory).rglob('*') if p.is_file()}
                settings.write_text('{}')
                recovered = subprocess.run([*entry, 'resume', str(workspace), '--json'], env=env, capture_output=True, text=True, check=True)
                (evidence / name / 'resume.stdout.json').write_text(recovered.stdout)
                (evidence / name / 'resume.stderr.txt').write_text(recovered.stderr)
                assert json.loads(recovered.stdout)['processing']['status'] == 'completed'
                assert all((workspace / p).read_bytes() == data for p, data in partial_immutable.items())
                request_count = len((case / 'requests.jsonl').read_text().splitlines())
                assert request_count == 4  # 失敗翻訳だけ再送、prepareは再利用
            adopted = load_translation(workspace)
            assert len(adopted.translations) == 1
            from doc_translate.render import workspace_status
            current = workspace_status(workspace)
            assert '訳:Original' in (workspace / current['artifacts']['japanese']).read_text()
            assert any('color:#' in frame['html'] for frame in frames) == (not no_color)
            immutable = {p.relative_to(workspace): p.read_bytes() for directory in ['originals', 'snapshots', 'attempts'] for p in (workspace / directory).rglob('*') if p.is_file()}
            resume = subprocess.run([*entry, 'resume', str(workspace), '--json', '--events-json'],
                                    env={**env, 'PATH': ''}, capture_output=True, text=True, check=True)
            assert json.loads(resume.stdout)['processing']['status'] == 'completed' and resume.stderr == ''
            assert all((workspace / p).read_bytes() == data for p, data in immutable.items())
            assert len((case / 'requests.jsonl').read_text().splitlines()) == request_count
            (evidence / name / 'summary.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2))
            (evidence / name / 'requests.jsonl').write_bytes((case / 'requests.jsonl').read_bytes())
            observations.append({'case': name, 'exit': result.returncode, 'requests': request_count,
                'saved_units': len(adopted.translations), 'reuse_attempt_increase': 0, 'immutable_unchanged': True,
                'expected': 'EPUB stage order; wait with target; completed saved output; empty-PATH reuse',
                'unverified': 'Chrome visual inspection is a separate recorded step; real provider/model/meaning quality'})
finally:
    (evidence / 'observations.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2))
# owned tempとそのchildだけ回収済み。証拠の全参照をcleanup後に再読。
assert len(observations) == 5
for file in evidence.rglob('*'):
    if file.is_file():
        file.read_bytes()
print(evidence, '5 isolated CLI cases; cleanup complete; evidence reread')
PY
```

Rootは**別invocation**で実行し、以下を直接観測・記録します。

- `frames.html`のindexから各`frame-N.html`を直接指定できます。frames.jsonから`AI応答待ち`＋`対象`のframe、narrowから`translation_rejected`のframeを選び、既存Chromeでlocal HTMLを表示／screenshotし、dark／lightで現在工程・実unit対象・送信先・異常理由が文字として読めることを照合・記録します。色／cell幅を保持した人工投影の確認であり、全terminal／実font同一性の保証ではありません。物理端末切替を利用者へ要求しません。必要なら`--mirror`で自身の端末へ直接再生できます。

  macOSの既存Chromeをheadlessで使う例（人工HTMLのみ、専用profileは自分のものだけfinally cleanup）：
  ```sh
  CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  PROFILE=$(mktemp -d)
  # Nはframes.jsonで選んだindexへ置換。Rootはscreenshotの実画像を開いて読取りを照合。
  # 長いindex HTML+fragmentのheadless captureでは空白を観測したため単一frameを開く。
  "$CHROME" --headless --disable-background-networking --disable-extensions \
    --no-first-run --no-default-browser-check --user-data-dir="$PROFILE" \
    --window-size=1100,900 --screenshot="$EVIDENCE/dark-frame.png" \
    "file://$EVIDENCE/dark/frame-N.html"
  rm -rf "$PROFILE" # このmktempで所有したChrome profileだけ。失敗時も同様に回収。
  ```

- TTY工程一覧が固定更新され、最新3件の履歴が残る。非TTYは同じ`entry`を`subprocess.run(..., capture_output=True)`で実行して変更行だけになること、`--json`と`--events-json`の4組合せが独立することを照合する。JSON指定でも人向けstderrは残り、events指定だけではstdoutをJSONへ変えない。
- 新規runを`settings={'fail_stage':'translation'}`で部分失敗させ、保存summary／旧immutable／attempt数を退避。settingsを`{}`へ変更して同じ`entry`のresumeを実行し、失敗対象だけのrequest増加とcompletedを照合する。完成後に空PATHでresumeし、attempt増加0・旧immutable byte不変を照合する。`--events-json`は従来送信前通知だけ（全工程streamではない）。
- 並列待機／split：`tests/test_translation.py`の`prepare_blocks`＋`translate`は実helperの再利用入口。`{'barrier':True}`と3×6500文字、`{'length_above':80}`と人工`'word '*40`でobserverを記録し、複数waitingの識別／request終端、splitから採用まで総数更新中、各`adopted`時点の公開`load_translation`のleaf件数を照合する。別jobのsplitが残る状態は`test_progress_state_keeps_other_split_pending_until_its_adoption`の小さいstate境界も参照する。新たなleaf正本は作らない。
- retry／domain repair／取消／例外：既存`test_inference`の実3/6秒`http-retry`、`test_preparation`のevidence repair、`test_translation`のmarker repair／parallel cancel、`test_resume`のOS replace境界を再利用する。まず既存fixtureの引数をreadし、Rootが別workspaceで選んだ代表を操作する。`retry`と`repair`、Event観測時の取消受付とchild drain・保存終了を別に照合し、送信前をprovider到達と記録しない。signal handlerのI/Oやretry／30秒drain変更で試験を短縮しない。全TTY条件×全signalの恒久E2E化はしない。

期待／観測／commandとexit、未確認、revision／dirty、必要人工logと保存hashを各runの証拠へ記録し、所有する一時workspace／credential／childだけfinallyでcleanup後、証拠の全参照を再読します。外部parserの実利用、実provider、実LLM品質、実PDF補正／review完成、他OS、SIGKILLの画面復元は保証しません。正式pytest／品質／配布検査やRootの受入判断を代替しません。
