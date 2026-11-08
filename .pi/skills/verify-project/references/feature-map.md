# Feature Map: 保存記録status／PDF・EPUB共通進捗

対象：公開CLIの軽量status／明示詳細検査／完全JSON、必要記録破損拒否、保存不変。EPUB／共通表示は末尾節が対象。PDF補正の工程・所有ページ進捗は末尾PDF節が対象。明示範囲reviewの表示は末尾review節が対象。対象外：実LLM品質、実PDF変換／OCR、他OS、remote CI、任意規模の性能保証。性能の別確認は[正式Plan](../../../../docs/plans/progress-and-status.md)の再作成条件・非並行before／after手順を使い、巨大benchmark基盤を追加しない。

Issue #53の統合受入れは下の既存status／EPUB／PDF／review各節を選び、Rootが新しい証拠directoryの別invocationで再実行する。正式チェックとRich直接依存・core-only保存-only CLI／flagsを含む配布検査は[開発ガイドの#53節](../../../../CONTRIBUTING.md#進捗表示statusの統合受入れissue-53)と[wheel手順](../../../../CONTRIBUTING.md#wheelの明示buildと導入検査)に従う。Skillの各入口だけで性能・故障代表・全正式チェック・配布成功を代替しない。各実行の未確認・所有物cleanup・証拠再読を記録し、上記対象外を統合確認の成功へ含めない。

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

入口は公開`run INPUT --workspace DIR`／`resume DIR`。EPUB新表示・共通request観測・出力flagsが対象です。PDF補正の工程／所有ページ集計とreview表示はそれぞれ後続節で扱い、この手順の成功で完成扱いしません。既存status手順は上記のまま維持します。

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

期待／観測／commandとexit、未確認、revision／dirty、必要人工logと保存hashを各runの証拠へ記録し、所有する一時workspace／credential／childだけfinallyでcleanup後、証拠の全参照を再読します。外部parserの実利用、実provider、実LLM品質、実Docling／OCR、他OS、SIGKILLの画面復元は保証しません。正式pytest／品質／配布検査やRootの受入判断を代替しません。

## PDF run／resumeの工程・所有ページ進捗（Issue #51）

対象はdraft検証／再利用・必要時抽出・所有補正・source組立・後段prepare／translation／render、実requestの所有／参照、保存後集計、部分回復／再利用のみ、coverage訂正／length・input_limit分割、候補採用待ちです。実Docling／OCR・モデル・実providerの成功とは扱いません。上のEPUB／status手順は維持します。

既存PDF extra環境・build済みNode helper・pytestが必要です。`scripts/pdf_progress_fixture.py`は既存`test_pdf.conversion/local_models`と`test_run.pdf_input`の自作9ページを再利用する外部入口で、公開Docling converter／page backendだけを置換して実CLIを呼びます。workflow・保存・validatorは実物、Nodeは既存network拒否付き`run-process.mjs`です。新規runの人工原本／modelは所有fixture-dirへ生成し、resumeでは生成しません。既存のfixture-dirは拒否するため、過去入力／workspaceを渡さないでください。依存の同期・setup・model取得は行いません。

下をrepository rootで実行します。Rootも**別invocation**の新しいEVIDENCEで同じ入口を実行し、期待／観測を自ら照合します。SIGINT defaultを戻し、foreground・非並行で操作します。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
EVIDENCE=$(mktemp -d "$PWD/work/test-verification-issue51-pdf-XXXXXX")
export EVIDENCE
.venv/bin/python - <<'PY'
import hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile
from pathlib import Path
signal.signal(signal.SIGINT, signal.default_int_handler)
root, evidence = Path.cwd(), Path(os.environ['EVIDENCE'])
python = str(Path(sys.executable).absolute())
node = shutil.which('node')
assert node, 'existing Node required; no install'
entry = root / '.pi/skills/verify-project/scripts/pdf_progress_fixture.py'
capture = root / '.pi/skills/verify-project/scripts/capture_progress.py'
for name, command in [('revision.txt', ['git', 'rev-parse', 'HEAD']), ('dirty.txt', ['git', 'status', '--short']), ('scope.diff', ['git', 'diff'])]:
    (evidence / name).write_bytes(subprocess.check_output(command))
observations = []

def immutable(workspace):
    return {p.relative_to(workspace): p.read_bytes() for directory in ('originals', 'drafts', 'corrections', 'attempts', 'snapshots')
            for p in (workspace / directory).rglob('*') if p.is_file()}

def inputs(log):
    return [json.loads(json.loads(line)['messages'][1]['content'][0]['text']) for line in log.read_text().splitlines()]

try:
    with tempfile.TemporaryDirectory(prefix='pdf-progress-owned-') as owned:
        temporary = Path(owned)
        for name, settings, tty, columns, no_color in [
            ('dark', {'delay_ms': 600}, True, 80, False),
            ('narrow-no-color', {'correction_fail_page': 7, 'delay_ms': 600}, True, 36, True),
            ('coverage', {'correction_omit_pages': [7]}, False, 80, False),
            ('length', {'correction_length_above': 2}, False, 80, False),
            ('input-limit', {'image_limit': 4}, False, 80, False),
            ('candidate', {'correction_fail_page': 7}, False, 80, False),
        ]:
            case = temporary / name
            case.mkdir()
            fixture, workspace, log = case / 'fixture', case / 'workspace', case / 'requests.jsonl'
            config = case / 'settings.json'
            config.write_text(json.dumps(settings))
            helper = [node, str(root / 'ai/tests/run-process.mjs'), str(case / 'credentials'), str(config), str(log)]
            prefix = [python, str(entry), '--fixture-dir', str(fixture), '--helper', json.dumps(helper), '--']
            env = {**os.environ, 'TERM': 'xterm-256color', 'COLORFGBG': '15;0'}
            env.pop('NO_COLOR', None)
            if no_color:
                env['NO_COLOR'] = ''
            directory = evidence / name
            operations = []

            def invoke(label, arguments, expected=0, use_tty=False, empty_path=False):
                command = [*prefix, *arguments]
                actual_env = {**env, **({'PATH': ''} if empty_path else {})}
                if use_tty:
                    command = [python, str(capture), '--evidence', str(directory / label), '--columns', str(columns), '--rows', '24' if columns == 80 else '10', '--', *command]
                    result = subprocess.run(command, env=actual_env, check=False)
                    out = (directory / label / 'stdout.txt').read_text()
                    err = (directory / label / 'stderr.ansi').read_text()
                else:
                    directory.mkdir(parents=True, exist_ok=True)
                    result = subprocess.run(command, env=actual_env, text=True, capture_output=True, check=False)
                    out, err = result.stdout, result.stderr
                    (directory / (label + '.stdout')).write_text(out)
                    (directory / (label + '.stderr')).write_text(err)
                operations.append({'label': label, 'command': command, 'exit': result.returncode, 'expected_exit': expected})
                (directory / 'operations.json').write_text(json.dumps(operations, indent=2))
                assert result.returncode == expected, (name, label, result.returncode, err)
                return out, err

            failed = 'correction_fail_page' in settings
            out, err = invoke('run', ['run', str(fixture / 'own.pdf'), '--workspace', str(workspace), '--pages', '2-7', '--reference-pages', '9-9', '--artifacts-path', str(fixture / 'models'), '--provider', 'faux', '--model', 'text', '--thinking', 'off', '--correction-model', 'vision', '--json'], 1 if failed else 0, tty)
            summary = json.loads(out)
            assert summary['processing']['status'] == ('partial' if failed else 'completed')
            assert all(word in err for word in ('保存データ検証', 'PDF補正', 'PDF source組立', '翻訳準備', 'Markdown出力'))
            assert 'ONLY_PAGE' not in err
            if name == 'candidate':
                assert '所有 7ページ／参照専用 6ページ' in err
            corrections = [i['context'] for i in inputs(log) if 'owned_pages' in i['context']]
            allowed = {2,3,4,5,6,7,9}
            assert all(set(c['owned_pages']).isdisjoint(c['reference_pages']) and set(c['owned_pages'] + c['reference_pages']) <= allowed for c in corrections)
            if name in ('dark', 'narrow-no-color', 'coverage', 'candidate'):
                assert corrections[0]['owned_pages'] == [2,3,4,5,6] and corrections[0]['reference_pages'] == [7]
            if name == 'coverage':
                assert [c['owned_pages'] for c in corrections] == [[2,3,4,5,6], [7], [7]]
                assert '回答訂正' in err and '通信再試行' not in err
            if name in ('length', 'input-limit'):
                assert '入力分割' in err and ('length' if name == 'length' else 'input_limit') in err
                assert not any('総数更新中' in line for line in err.splitlines() if line.startswith('PDF補正:'))
            if no_color:
                assert not re.search(r'\x1b\[[0-9;]*m', err)
            if tty:
                frames = json.loads((directory / 'run/frames.json').read_text())
                assert any('PDF補正' in frame['text'] for frame in frames)
                if name == 'dark':
                    assert any('AI応答待ち 1件' in frame['text'] and '所有 2–6ページ／参照専用 7ページ' in frame['text'] for frame in frames)
            previous = immutable(workspace)
            before_count = len(inputs(log))
            if name == 'candidate':
                source = json.loads((workspace / 'source-snapshot.json').read_text())['sha256']
                from doc_translate.snapshot import load_source_snapshot
                replacement = load_source_snapshot(workspace).model_dump(mode='json')
                next(b for b in replacement['blocks'] if b['kind'] == 'body')['text'] = 'Self-authored manual correction.'
                envelope = case / 'manual.json'
                envelope.write_text(json.dumps({'schema_version': 1, 'base_source_sha256': source, 'source': replacement}))
                invoke('revise', ['revise', str(workspace), '--source', str(envelope), '--reason', 'Self-authored check'])
                pointers = {p: (workspace / p).read_bytes() for p in ('source-snapshot.json', 'preparation-snapshot.json', 'translation-snapshot.json', 'render.json')}
            if failed:
                config.write_text('{}')
                out, err = invoke('recover', ['resume', str(workspace), '--json'], 1 if name == 'candidate' else 0)
                assert '成功 5/6 ページ（うち再利用 5）' in err and '成功 6/6 ページ（うち再利用 5）' in err
                new_corrections = [i['context'] for i in inputs(log)[before_count:] if 'owned_pages' in i['context']]
                assert [(c['owned_pages'], c['reference_pages']) for c in new_corrections] == [([7], [6])]
                assert all((workspace / p).read_bytes() == data for p, data in previous.items())
                if name == 'candidate':
                    assert json.loads(out)['source_adoption']['status'] == 'pending'
                    assert '候補の明示採用待ち' in err and '翻訳準備:' not in err
                    assert all((workspace / p).read_bytes() == data for p, data in pointers.items())
            previous = immutable(workspace)
            before = log.read_bytes()
            out, err = invoke('reuse', ['resume', str(workspace), '--json'], 1 if name == 'candidate' else 0, empty_path=True)
            assert log.read_bytes() == before and all((workspace / p).read_bytes() == data for p, data in previous.items())
            assert '成功 6/6 ページ（うち再利用 6）' in err
            assert 'AI応答待ち' not in err
            if name == 'dark':
                out, err = invoke('reuse-events', ['resume', str(workspace), '--json', '--events-json'], empty_path=True)
                assert err == '' and json.loads(out)['processing']['status'] == 'completed'
            (directory / 'requests.jsonl').write_bytes(log.read_bytes())
            (directory / 'immutable-hashes.json').write_text(json.dumps({str(p): hashlib.sha256(data).hexdigest() for p, data in previous.items()}, indent=2))
            observations.append({'case': name, 'operations': operations, 'owned_total': 6, 'reused': 6, 'old_bytes_unchanged': True, 'requests': len(inputs(log)), 'expected': 'stage order, real owned/reference scopes, published page counts, no resend on empty-PATH reuse; candidate stops before downstream', 'observed': 'matched'})
finally:
    (evidence / 'observations.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2))
    (evidence / 'cleanup.txt').write_text('owned temporary input/workspaces/credentials removed; synchronous children reaped; evidence retained\n')
assert len(observations) == 6
for p in evidence.rglob('*'):
    if p.is_file():
        p.read_bytes()
print(evidence, '6 isolated PDF cases matched; cleanup complete; evidence reread')
PY
```

期待：所有分母6はreference／splitで増減せず、再利用は成功内訳。部分resumeで[7]のみ参照[6]付き送信し旧byte保持、空PATHで再利用のみは補正・準備・翻訳の送信ゼロ。coverage修復は同じ全所有requestの再回答で、通信retryとは別。候補待ちはvalidate部分終了でprepare以後へ進まない。request／hash照合とexitは`observations.json`、stdout／stderr／frameは各case、人工requestとhashはworkspace外の同caseに残します。失敗はassert／非ゼロとして扱い、成功に書き換えません。

TTYの直接視認は上の共通capture／Chrome手順を再利用し、`dark/run`の80桁と`narrow-no-color/run`の36桁frameで所有／参照・現在工程・異常理由を実画像または`--mirror`で照合してください。NO_COLORのSGRなしと視認性は別の証拠です。全形式×全条件の積、実terminal fontの同一性は保証しません。

保存前成功ゼロ・通知時のchunk／run実体・固定分母と翻訳split分離・transport retryとcoverage repairのrequest終端は`tests/test_run.py -k pdf_progress`、`test_pdf_correction.py::test_correct_pdf_preserves_transport_attempts_without_cumulative_usage`、`test_cli_output.py::test_pdf_progress_split_keeps_owned_denominator_and_translation_state_separate`で別途照合します。取消受付／child終了は既存inference／resumeのEvent・signal・OS replace境界とPDF補正のrecord-save／cancelを再利用し、retry待機／drainを短縮しません。正式チェックとRoot／Reviewer判断はこの入口だけで代替しません。


## 明示範囲reviewの共通表示（Issue #52）

入口は公開`review DIR --units unit-2`／PDF `--pages 3-3`。既存の`resume_process.py`は任意CLI引数を受け付け、`pdf_progress_fixture.py`はrun時だけ人工Docling境界を設定しreviewはそのままmainへ渡すため、追加helperは不要です。実Python→実Node helper→network拒否付きfauxと自作入力のみを使います。既存`.venv`（PDF extra付き）とNode／build済みhelperが必要で、無断install／sync／実provider／実認証／model取得はしません。

以下は新規の所有TemporaryDirectoryに人工EPUB（選択外sentinel付き）とPDFを作り、runで必要な保存訳だけ準備してからreviewを操作します。新しいpipeline基盤や後続のrevise／renderは不要です。4つのstdout／stderr flag組合せ、TTYの指摘あり、空指摘、狭幅NO_COLORの失敗、無効選択、終了時詳細検査と保存hash／旧byteを照合します。指摘全文は通常画面へ出さず、空でも品質未承認です。

```sh
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
EVIDENCE=$(mktemp -d "$PWD/work/test-verification-issue52-review-XXXXXX")
export EVIDENCE
PYTHONPATH=tests .venv/bin/python - <<'PY'
import hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile
from pathlib import Path
from test_epub import make_epub
signal.signal(signal.SIGINT, signal.default_int_handler)
root, evidence = Path.cwd(), Path(os.environ['EVIDENCE'])
python = str(Path(sys.executable).absolute())
node = shutil.which('node')
assert node, 'existing Node required; no install'
capture = root / '.pi/skills/verify-project/scripts/capture_progress.py'
for name, command in [('revision.txt', ['git', 'rev-parse', 'HEAD']), ('dirty.txt', ['git', 'status', '--short']), ('scope.diff', ['git', 'diff'])]:
    (evidence / name).write_bytes(subprocess.check_output(command))
observations = []
try:
    with tempfile.TemporaryDirectory(prefix='review-progress-owned-') as owned:
        temporary = Path(owned)
        for format in ('epub', 'pdf'):
            case = temporary / format
            case.mkdir()
            directory = evidence / format
            directory.mkdir()
            fixture, workspace, settings, log = [case / name for name in ('fixture', 'workspace', 'settings.json', 'requests.jsonl')]
            settings.write_text('{}')
            helper = [node, str(root / 'ai/tests/run-process.mjs'), str(case / 'credentials'), str(settings), str(log)]
            entry = ([python, str(root / '.pi/skills/verify-project/scripts/pdf_progress_fixture.py'), '--fixture-dir', str(fixture)] if format == 'pdf' else [python, str(root / 'tests/fixtures/resume_process.py')]) + ['--helper', json.dumps(helper), '--']
            source = fixture / 'own.pdf' if format == 'pdf' else make_epub(case, extra={'OPS/text/ch.xhtml': b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>UNSELECTED SECRET</p><p>SELECTED TEXT</p><p>OTHER SECRET</p></body></html>'})
            env = {**os.environ, 'TERM': 'xterm-256color'}
            env.pop('NO_COLOR', None)
            operations = []

            def invoke(name, arguments, expected=0, tty=False, narrow=False):
                command = [*entry, *arguments]
                actual_env = {**env, **({'NO_COLOR': ''} if narrow else {})}
                if tty:
                    command = [python, str(capture), '--evidence', str(directory / name), '--columns', '36' if narrow else '100', '--rows', '10' if narrow else '32', '--', *command]
                    result = subprocess.run(command, env=actual_env, check=False)
                    out = (directory / name / 'stdout.txt').read_text()
                    err = (directory / name / 'stderr.ansi').read_text()
                else:
                    result = subprocess.run(command, env=actual_env, capture_output=True, text=True, check=False)
                    out, err = result.stdout, result.stderr
                    (directory / (name + '.stdout')).write_text(out)
                    (directory / (name + '.stderr')).write_text(err)
                operations.append({'name': name, 'command': command, 'exit': result.returncode, 'expected_exit': expected})
                (directory / 'operations.json').write_text(json.dumps(operations, indent=2))
                assert result.returncode == expected, (format, name, result.returncode, err)
                return out, err

            invoke('run', ['run', str(source), '--workspace', str(workspace), '--provider', 'faux', '--model', 'text', '--thinking', 'off', '--json', '--events-json', *(['--pages', '2-4', '--correction-model', 'vision', '--artifacts-path', str(fixture / 'models')] if format == 'pdf' else [])])
            old = {p: p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
            scope = ['--pages', '3-3'] if format == 'pdf' else ['--units', 'unit-2']
            for name, flags, findings, failure, tty, narrow in [
                ('tty-findings', [], [{'target_kind': 'unit', 'target_id': 'unit-2', 'reason': 'PRIVATE FINDING'}], False, True, False),
                ('empty-json', ['--json'], [], False, False, False),
                ('events-human', ['--events-json'], [], False, False, False),
                ('both-json', ['--json', '--events-json'], [], False, False, False),
                ('failure-narrow', [], [], True, True, True),
            ]:
                settings.write_text(json.dumps({'delay_phase': 'review', 'delay_ms': 800, 'review_findings': findings, **({'fail_stage': 'review'} if failure else {})}))
                before_requests = len(log.read_text().splitlines())
                out, err = invoke(name, ['review', str(workspace), *scope, *flags], 1 if failure else 0, tty, narrow)
                assert len(log.read_text().splitlines()) == before_requests + 1
                sent = json.loads(log.read_text().splitlines()[-1])
                inputs = json.loads(sent['messages'][1]['content'][0]['text'])
                assert inputs['context']['unit_ids'] == ['unit-2']
                assert inputs['context']['pages'] == ([3] if format == 'pdf' else [])
                assert inputs['context']['block_ids'] == (['pdf-2'] if format == 'pdf' else [])
                assert all(word not in json.dumps(sent) for word in ('UNSELECTED SECRET', 'OTHER SECRET', 'Page 2 source.', 'Page 4 source.'))
                assert 'PRIVATE FINDING' not in err and 'SELECTED TEXT' not in err and 'Page 3 source.' not in err
                if '--json' in flags:
                    saved = json.loads(out)
                    assert saved['review']['status'] == 'validated'
                    assert saved['content_review']['meaning_quality_approved'] is False
                    record_path = workspace / saved['review']['artifact']
                    record = json.loads(record_path.read_bytes())
                    assert hashlib.sha256(record_path.read_bytes()).hexdigest() == saved['review']['sha256']
                    assert record['unit_ids'] == inputs['context']['unit_ids'] and record['pages'] == inputs['context']['pages']
                    (directory / (name + '.record.json')).write_bytes(record_path.read_bytes())
                elif failure:
                    assert '確認失敗（確認記録は未保存）' in out and '未確定' in out
                else:
                    assert f'{len(findings)}件' in out and '未承認' in out and 'reviews/' in out and 'PRIVATE FINDING' not in out
                if '--events-json' in flags:
                    notice = json.loads(err)
                    assert notice['transmission']['unit_ids'] == ['unit-2'] and notice['transmission']['pages'] == inputs['context']['pages']
                else:
                    assert all(word in err for word in ('保存データ・選択範囲の検証', 'AIによる内容確認', '確認結果の保存', '終了時の保存データ検査', '送信先 faux', 'api_key'))
                    assert '所有意図' not in err and '参照専用' not in err
                if tty:
                    frames = json.loads((directory / name / 'frames.json').read_text())
                    assert any('AI応答待ち 1件' in f['text'] and 'unit-2' in f['text'] for f in frames)
                    assert 'AI応答待ち 0件' in frames[-1]['text']
                    if failure:
                        assert 'review_rejected' in err and '失敗' in err
                    if narrow:
                        assert not re.search(r'\x1b\[[0-9;]*m', err)
                assert all(p.read_bytes() == data for p, data in old.items())
                observations.append({'format': format, 'case': name, 'exit': 1 if failure else 0, 'expected': 'explicit scope only; current-record findings count; quality not approved; independent flags; no pending wait at exit; old bytes unchanged', 'observed': 'matched'})
            before = log.read_bytes()
            invoke('invalid-scope', ['review', str(workspace), *(['--pages', '1-1'] if format == 'pdf' else ['--units', 'missing'])], 2)
            assert log.read_bytes() == before
            settings.write_text('{}')
            inspected, _ = invoke('inspection', ['status', str(workspace), '--json'])
            status = json.loads(inspected)
            assert status['content_review']['meaning_quality_approved'] is False
            history = status['content_review']['review_history']
            assert sorted(len(r['findings']) for r in history) == [0, 0, 0, 1]
            for record in history:
                data = (workspace / record['artifact']).read_bytes()
                assert hashlib.sha256(data).hexdigest() == record['sha256']
                (directory / Path(record['artifact']).name).write_bytes(data)
            (directory / 'requests.jsonl').write_bytes(log.read_bytes())
            (directory / 'old-hashes.json').write_text(json.dumps({str(p.relative_to(workspace)): hashlib.sha256(data).hexdigest() for p, data in old.items()}, indent=2))
            observations.append({'format': format, 'case': 'invalid-scope-and-inspection', 'expected': 'invalid scope exit2 without send; four accepted records including empty findings, each hash verified; quality false; saved outputs not silently rerendered', 'observed': 'matched'})
finally:
    (evidence / 'observations.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2))
    (evidence / 'cleanup.txt').write_text('owned temporary input/workspaces/credentials removed; synchronous children reaped; evidence retained\n')
assert len(observations) == 12
for p in evidence.rglob('*'):
    if p.is_file():
        p.read_bytes()
print(evidence, '12 review cases matched; cleanup complete; evidence reread')
PY
```

期待：各形式のreviewは4件受理・1件推論失敗・1件無効選択、選択unitはunit-2、PDF画像／blockは3ページ／pdf-2だけ。reviewは出力を再生成せず、今回の指摘は1件または0件、失敗は件数未確定、品質は常にfalse。重い検証前→AI内容確認→保存→終了検査の順がstderrへ現れ、終了frameの待ちは0件。eventsは従来送信前noticeだけで最終stdoutとは独立です。invalidはexit2でrequest増加なし、失敗はexit1で受理記録増加なし。旧file全byteは保持します。

証拠はrevision／dirty／scope.diff、各形式operations.json（command＋exit）、stdout／stderr、TTY frame／HTML、人工request、受理record／hashとold-hashes、observations／cleanupです。workspace外へ退避済みで、末尾でcleanup後全参照を再読します。`tty-findings`の待機frameと`failure-narrow`の異常frameを上記Chrome手順で実表示／画像readし、unit／block／画像ページ・送信先・異常理由の文字が読めることを記録してください。HTML生成／SGR検出だけを視認性合格としません。Rootは別invocationで公開CLIを操作・照合します。

保存拒否／旧byte維持は`test_review_atomic_save_failure_keeps_old_review_snapshots_and_observed_usage`、preflight拒否／request終端・保存前後・詳細検査接続は`tests/test_review.py`、共通表示の安全escape／stage順とrun／resume回帰は`tests/test_cli_output.py`を使います。retry／timeout／取消の共通generate境界は既存`test_inference`／`test_resume`を再利用し、reviewの推論前ローカル検証のsignal境界を変更して即停止を保証しません。実LLM品質・全signal×全工程・SIGKILL・全terminalは未確認です。
