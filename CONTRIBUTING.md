# 開発ガイド

構成、正式チェック、変更影響からの実行範囲の選び方、各公開境界のテスト入口と直接確認、依存・license・fixture/CIの境界をまとめます。公開契約は[artifact契約](docs/reference/artifacts.md)、責務は[基本設計](docs/design/doc-translate.md)、利用者向け手順はREADMEと[CLI詳細リファレンス](docs/reference/usage.md)です。実動の再実行手順と証拠の扱いは[検証Skill](.pi/skills/verify-project/SKILL.md)と[Feature Map](.pi/skills/verify-project/references/feature-map.md)にあります。

## 検証状況と限界

offlineテストは人工入力・実Node helper・公開faux providerで構造・範囲・保存・再利用の契約を確認します。自作の小入力ではOpenAI OAuth・実LLM・限定viewer表示も個別承認のうえ確認しました。実token refresh、任意の実文書・実スキャン、意味品質・翻訳品質、他viewer・他OSは未保証です。QAの`meaning_quality_approved`／`viewer_display_checked`は常にfalseです。実送信・model重み取得・実認証は毎回個別承認が必要です。

## 構成・環境

- `src/doc_translate/`：PythonのCLI（`cli.py`／`cli_output.py`／`progress*.py`）、アプリ所有Node runtimeの導入／検査（`ai_runtime.py`）、TTY認証UI（`auth.py`）、推論adapter（`inference.py`）、EPUB安全reader（`epub.py`）と原文抽出（`epub_source.py`）、PDF取得（`pdf.py`）・補正chunk（`pdf_correction.py`／`pdf_adoption.py`）・原文組立（`pdf_source.py`）、unit生成（`units.py`）、準備（`preparation.py`）、翻訳（`translation.py`）、render／status（`render.py`／`render_assets.py`／`saved_status.py`）、run／resume（`run.py`）、revise（`revise.py`）、review（`review.py`）、原文snapshot（`snapshot.py`）、workspace lock（`workspace.py`）、JSONL codec（`jsonl.py`）。
- `ai/src/`：TypeScriptのJSONL codec、metadata／認証／generation helper（`dist/main.js`）、`AuthDialog`、`FileCredentialStore`、推論接続（`generation.ts`）。`ai/tests/*.mjs`は公開APIとcompile済みhelperを使うテスト専用process（`run-process.mjs`等は実`runHelper`＋公開faux provider＋network拒否）です。
- `tests/`：公開API／CLIのpytest。`tests/fixtures/`は自作JSON、外部認証fake／PTY process、推論protocolの故障注入proxy、OS replace境界で停止できる`resume_process.py`、保存形式互換用の原byte fixture（`preparation-v1.json`、`translation-v1.json`等）です。EPUBの人工ZIPは`tests/test_epub.py`の`make_epub`、PDFはDoclingの公開converter／page backendを置換する`tests/test_pdf.py`のfixtureで作ります。
- `scripts/check_distribution.py`：wheel配布検査（下記）。

CIはGitHub-hosted macos-15 ARM64、Python 3.14、Node 24.21.0、npm 11.19.0、uv 0.13.5です。別OSやmacOSの最低versionは未保証です。

## 正式チェック（順序を含む）

fresh checkout／全件phaseの順序です。CI（`.github/workflows/ci.yml`）も同じ順序で実行します（加えてarm64確認とnpm 11.19.0の固定installを行います）。依存installだけがregistry接続を使い、model重み・認証・LLMは不要です。install／syncは毎編集の義務ではなく、環境不足・lock変更・同梱resourceの鮮度から必要性と許可を確認します。既存`.venv`を無断でcore／PDFに切り替えません。

```sh
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
npm --prefix ai ci --ignore-scripts --no-audit --no-fund
npm --prefix ai run build
uv sync --locked --dev
npm --prefix ai run format   # biome format --error-on-warnings
npm --prefix ai run lint     # oxlint --type-aware --type-check --deny-warnings
npm --prefix ai run test     # vitest run
uv run ruff check .
uv run ruff format --check .
uv run python -c 'import importlib.util; assert importlib.util.find_spec("docling") is None'
uv run pytest
uv run doc-translate --help
```

Python packageは`ai/dist`を同梱するため、`uv sync`より先にhelperの明示buildが必要です。既存editable環境で同梱resourceを更新するときは、許可確認後にbuildと`uv sync --locked --dev --reinstall-package doc-translate`（PDF環境は`--extra pdf`も）を行います。警告をquiet／包括的disableで隠さず修正します。

`ai/tsconfig.build.json`の`skipLibCheck`は依存配布declarationの型不足への互換対応です。自作sourceの型検査はoxlintのtype-aware／type-checkで維持し、runtime互換性は実helperとclean installで検査します。

型検査とPDF依存のテストはPDF extra付き環境で行います。

```sh
uv sync --locked --dev --extra pdf
uv run --locked --extra pdf ty check
uv run --locked --extra pdf python -c 'from docling.document_converter import DocumentConverter; from docling.datamodel.pipeline_options import OcrMacOptions; from docling_core.types.doc import DoclingDocument; from ocrmac import ocrmac; print(OcrMacOptions(lang=["en-US"], framework="vision", recognition="accurate"))'
uv run --locked --extra pdf pytest tests/test_pdf.py tests/test_run.py tests/test_review.py tests/test_pdf_candidate_acceptance.py
```

全件pytestはcore-onlyで実行し、PDF extra側はDocling依存のテストを含む上記4ファイルに限定します（coreとPDFの重なりはoptional依存の有無を検査する別条件です）。PDF依存が必要なテストを別ファイルに追加する場合はCIとこの手順にも追加します。`.venv/bin/pytest`等を直接使う場合はvenvのactivateか`.venv/bin`のPATH追加が必要です。これらはimportと人工fixtureの検証であり、実Docling変換・Apple Vision OCR・重みを使う処理の成功ではありません。

### 変更影響から正式実行範囲を選ぶ

Rootは毎回、次の順で選定します。ファイル種別だけでは限定可否を決めません。

1. 要求・維持する保証と変わる動作／前提を差分から特定し、公開CLI／API／保存物のどこで観測するかを決めます。
2. 変更した定義の全callerとimportを検索し、関数内import、`pytest_plugins`、fixtureのscopeと全consumerまで辿ります。Python→実Node helper／JSONL→Pythonの接続、compile済みresource、`pyproject.toml`の`testpaths`、収集設定とCIの実行集合も確認します。
3. 影響が閉じるなら、変更caseのfocused実行、影響module全体と必要なconsumer、品質チェック、保証に対応する実入口の直接確認をRoot自身が行います。新／変更fixtureは代表consumer単独のfresh processとmodule実行の両方で確認し、収集node ID／parameter／skip理由で対象漏れがないか照合します。
4. shared fixture／helper、共通schema／hash／保存／lock、JSONL／helper接続、工程間の再利用／状態遷移、依存／lock／build／配布、収集／CI／検証規約の変更は、関連する全件phaseへ戻します。影響不明・consumerを列挙できない場合も同じです。
5. 選んだcommandと選択／除外理由、保証、環境／build鮮度、skip理由、期待／観測／未確認を結果報告に残します。

Python変更時のRuff check／formatは全体で実行し、型検査はPDF extra環境で行います。Node変更時はBiome／type-aware oxlintを維持し、局所Node test本体なら関連Vitest、shared helper／source／依存の変更はVitest全件です。helper source変更時は明示buildと同梱resourceの鮮度確認も必要です。build／配布に影響するときはwheel検査も対象にし、必要なinstallの許可がなければ未確認のまま成功とせずBLOCKEDとします。

#### 現在の依存に基づく選定例

- **閉じたEPUB test本体だけ**：例えば`test_revise.py`のpatch拒否case内だけなら、そのcaseと`pytest tests/test_revise.py`、品質と実CLI reviseの受理／拒否を確認します。`malformed_source_seed`／workspaceはmodule-localです。export（`pointer`等）やfixtureを変えていなければ、import元の`test_run.py`／`test_pdf_candidate_acceptance.py`を再実行する必要はありません。`test_review.py`にはPDF caseもあるので、PDFなしのskipだけを成功にしません。
- **閉じたPDF test本体だけ**：`test_pdf_candidate_acceptance.py`のtable-cell／list-depth case内だけなら該当caseとmodule、品質と実CLI reviseを確認します。`pdf_evidence_seed`／workspaceはmodule-localです。ただし同moduleの`start_pdf`は`test_review.py`でもimportされます。
- **局所seedと共有helperを区別**：`review_scope_seed`は`test_review.py`専用ですが、`test_run.helper/run_args`には`test_revise`／`test_resume`／`test_cli_output`／`test_pdf_candidate_acceptance`等のconsumerがあり、`test_run`は`test_pdf` pluginを読みます。共有helperやconverter fixtureの変更を「1ファイルだけ」として限定しません。
- **共有保存／fixture・影響不明**：core全pytestとPDF4ファイルなど関連full phasesで確認し、Node接続へ及ぶなら全VitestとNode品質、helper sourceならbuild／resource鮮度も含めます。

### 検証の分担・再実行・証拠

各節のコマンドは境界ごとの再現入口であり、毎回の全コマンド実行義務ではありません。上の手順で必要な範囲を選び、Approved Planが追加で要求する検証は省略しません。

- **Worker**：変更consumerの代表単独とmodule実行、関連公開seamのfocused regression、必要な品質確認を担当し、保証・前提・除いた工程・command／exit／未確認を報告します。
- **Root**：差分を直接読み、選んだ正式チェックと実入口の直接確認を自ら実行します。既存環境／helper build鮮度を確認し、Workerの成功で代替しません。
- **Reviewer**：差分とPlan、Rootの結果・選定根拠・未確認／cleanupから、不足する具体的保証だけを独立検証します。修正後はRootが影響した保証を再確認し、共有部分なら関連full phasesへ戻します。

### 保証・境界を決めてからTDDを選ぶ

要求／保証→公開境界→必要な整合前提→検査する最小経路の順で決めた後、対象ごとにTDD採否を判断します。Refactoring・収集漏れ修正・規約更新は非TDDで、既存テストと最小のcharacterization checkで等価性を確認します。Approved Planに明示されたTDDは勝手に解除しません。

### 前提共有・比較・cleanup

前提の再利用は、同じ入力・同じ整合条件の今回の実生成物をinvocation内で作り、全file（原本／draft／画像／artifact／evidence／attempt／履歴／pointer／run／render等）と初期request logを独立コピーした場合に限ります。module-local seed（`test_revise.py`の`malformed_source_seed`、`test_review.py`の`review_scope_seed`、`test_pdf_candidate_acceptance.py`の`pdf_evidence_seed`）と専用consumerだけで使い、session共通fixtureへ昇格しません。各moduleの`*_workspace_is_relocatable_and_isolated`が元seed root退避中の公開load、コピー間の変更非伝播、seed byte不変を検査します。永続cache、hardlink、generate／validator／saveのmockは使いません。入力改変・取消・故障・再開の検査対象操作は実経路で行い、Nodeの30秒grace、Pythonの3／6秒retryは短縮しません。

直接確認はrepository root、既存の開発環境とbuild済みhelperから開始し、依存の自動取得や既存環境の同期はしません。証拠は今回専用の新規directoryへ残します。

```sh
set -eu
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mkdir -p work
EVIDENCE=$(mktemp -d "$PWD/work/test-verification-XXXXXX")
export EVIDENCE
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git diff --stat > "$EVIDENCE/diff-scope.txt"
{ uname -sm; .venv/bin/python --version; node --version; npm --version; uv --version; } > "$EVIDENCE/tools.txt"
```

条件（core/PDF extra、tool version、同期の有無、Node build状態、PATH/offline、signal handler、入力）と、各操作のcommand・exit・raw所要時間・期待／観測・未確認を証拠に対応付けます。raw環境変数・credential・秘密入りstderrは保存しません。過去の成功を今回のrevisionの成功に流用しません。

時間比較は他のtest processと**非並行・foreground**、同じ環境／extra／入力／build／PATH/offline／signal条件で行います。SIGINTはdefault handlerを確認し（`python -c 'import signal; assert signal.getsignal(signal.SIGINT) == signal.default_int_handler'`）、ignoreを継承する場合はPython親processで`signal.signal(signal.SIGINT, signal.default_int_handler)`へ戻してからsubprocessを起動します。変更前後各1回のfresh processから始め、ばらつきで判断できない部分だけ双方を追加2回とし、raw値と中央値・min/maxを報告します。品質、install／build／配布、CI待ちはテスト時間と分けます。固定秒数・短縮率の合格閾値は置かず、生成回数削減と時間短縮を同一視しません。

完了後、作ったprocessを回収し、自分が今回`mktemp`で作った`WORKSPACE`／`RUN_FIXTURE`／pytestの`--basetemp`だけを削除します（人工credentialもその内側）。`--basetemp`は既存directoryを消すため既存workspaceを指定しません。`work/`全体、他人のworkspace、既存venv／credential／modelは削除しません。証拠は残し、cleanup後に再読して参照を確認します。

## 公開境界ごとの検査入口

以下のfocused pytestは境界の再現入口です。各テストは自作入力、実Node helper／公開faux provider（network拒否）を使い、内部generate／workflow／validator／saveをmockしません。保存故障の注入はOSの`replace`境界だけです。

### PDF取得の検証境界

`acquire_pdf`→`load_pdf_draft`の公開seamで、不足model・不正入力／range、raw item／ref／table cell／provenance、clipしないcharspan不一致、conversion status／errors、全対象ページ／PNG、直接描画scale／unload、artifact hash不一致、writer排他、参照replace失敗時の旧版保持を検査します（`tests/test_pdf.py`）。Doclingの`DocumentConverter.convert`と公開page backendだけを人工fixtureに置換します。core-onlyでのPDF／EPUB API importも検査します。APIと保存物は[artifact契約](docs/reference/artifacts.md#pdf抽出下書き)を参照してください。

実変換の確認は人工fixtureテストとは別に、明示承認済みの既存local modelだけを使い、自作PDFで公開APIを呼びます。重みをテストやCIが取得することはありません。準備は[READMEのPDF手順](README.md#pdfを翻訳する)を参照してください。

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --locked --extra pdf python - <<'PY'
from pathlib import Path
from doc_translate.pdf import acquire_pdf, load_pdf_draft
workspace = Path('/tmp/own-pdf-workspace')
digest = acquire_pdf(Path('OWN.pdf'), workspace=workspace, artifacts_path=Path('MODELS'))
draft = load_pdf_draft(workspace, digest)
print(draft.document.sha256, draft.target_pages, draft.conversion_status)
print([check.model_dump() for check in draft.checks])
print([(page.page, page.width_pixels, page.height_pixels) for page in draft.pages])
PY
```

Torch由来のmacOS multiprocessing redirect未対応NOTEは非致命です。

### PDF補正chunkの検証境界

公開seamは`correct_pdf`→`load_pdf_correction`です（[artifact契約](docs/reference/artifacts.md#pdf補正chunk)）。`tests/test_pdf_correction.py`は自作のraw JSON／PNG／PDFDraftを保存し、`ai/tests/pdf-correction-process.mjs`（実`runHelper`＋公開faux）で補正します。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_pdf_correction.py tests/test_pdf.py tests/test_inference.py
```

- 所有／参照／未許可ページのliteral期待値、画像順、refなし画像復元、raw回答記録（`test_correct_pdf_saves_only_owned_content_and_sends_only_authorized_input`）。
- 図所属：form画像がlogo＋text＋tableに分かれた人工形状で、内部block／cellを採用本文から分離して保持し、曖昧／部分所属の原textと図領域・要確認を残すこと（`test_form_image_keeps_membership_without_adopting_internal_text_or_cells`）。
- 全content kind、見出し／list階層、code改行／callout、空cell／span、除外理由、不正ref／wrong-page ref／cell／span、所有外出力、owned画像欠落、安全なspan投影、bbox重なりだけで本文を消さないこと。
- 旧chunk保持、chunk保存失敗前のattempt保存、各artifactのhash不正、load時のdomain／送信許可再検証、writer排他。
- 全所有結果の要求と白紙ページ（`test_correct_pdf_requests_full_owned_results_and_image_confirmed_blank_pages`）、不足ページのcoverage訂正（`test_correct_pdf_repairs_missing_pages_with_complete_chunk_and_retains_usage`）、3回上限での停止（`test_correct_pdf_stops_after_three_incomplete_whole_chunk_answers`）、chunk保存前の訂正成功の無送信回収（`test_correct_pdf_recovers_repaired_success_before_chunk_save_without_resending`）、CLI resumeでの失敗所有だけの訂正（`tests/test_run.py::test_resume_pdf_repairs_coverage_only_for_failed_ownership_and_retains_all_usage`）。

consumerの回帰は`tests/test_pdf_source.py`、`tests/test_units.py::test_accepted_pdf_figure_membership_is_not_reclassified_by_units`、`tests/test_review.py::test_review_exclusion_only_chunk_uses_adopted_images_and_revalidates_offline`、`tests/test_run.py`です。現行以外の採用方式で保存された記録がresumeの送信前に案内付きで止まることと、所有本文の採用・保存再読の直接操作は[Feature Map](.pi/skills/verify-project/references/feature-map.md#所有本文の採用保存再読97)を参照してください。

標準依存`pypdfium2==5.14.0`はDoclingを起動しないfresh loadでも局所PDF根拠を読むためのものです。native PDFiumはthread-safeではないため、原本のopen／局所read／closeだけをlock内に置きます。

PDF補正の制限付き並列（`--correction-concurrency 1|2|4`、manifest保存・resume継承、EPUB明示1の拒否、分割後のactive数、逆順完了時のchunk／source順、局所失敗と全体停止、取消／保存失敗→旧pointer保持・成功回収）は`pytest tests/test_run.py -k correction_concurrency`、代表単独は`pytest 'tests/test_run.py::test_pdf_correction_concurrency_split_reverse_order_and_resume[None-4]'`です。計測は[性能reference](docs/reference/performance.md#pdf補正の制限付き並列issue-61)と[Feature Map](.pi/skills/verify-project/references/feature-map.md#pdf補正の制限付き並列issue-61)です。

### PDF原文組立の検証境界

公開seamは受理済みchunk→`assemble_pdf_source`→`save_source_snapshot/load_source_snapshot`です（[artifact契約](docs/reference/artifacts.md#pdf原文snapshot組立)）。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_pdf_source.py tests/test_pdf_correction.py tests/test_source_snapshot.py
```

`tests/test_pdf_source.py`は脚注越しの本文接続・分綴・code改行／language・欠損の非補完・callout行対応、別ページlist親子（同名local IDを含む）と未知／複数／循環／kind／深さ／順序のQA、未確定table・図内部非再採用、ページ順・断片／出典／画像のsave/load、同じ原本文書の別draftでdraft-localな同名refを推測接続しないことをliteral期待値で検査します。

### 共通翻訳unitの検証境界

公開seamは`SourceSnapshot`→`generate_translation_units`→`split_translation_units`／`validate_unit_text`です（[artifact契約](docs/reference/artifacts.md#共通翻訳unit生成)）。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_units.py tests/test_epub_source.py tests/test_pdf_source.py tests/test_source_snapshot.py
```

`tests/test_units.py`は人工PDF snapshotと実EPUB reader→原文抽出から、採用table cell／spanと祖先context、confirmedな図内部の除外、caption／不確実textの保持、mixed inline、link／literal／identifier保護、日本語並替え・marker釣合い、hash、lossless分割・再分割・不可分spanの明示失敗を検査します。

### 文書context・glossary準備の検証境界

公開seamは保存済み原文→`prepare_document`→`load_preparation`です（[artifact契約](docs/reference/artifacts.md#文書contextglossary準備)）。`ai/tests/preparation-process.mjs`を使います。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_preparation.py tests/test_units.py tests/test_inference.py tests/test_source_snapshot.py
```

- 利用者terms優先、複合語・原表記、本文根拠なし候補の非採用、単一policy由来style、固定glossary/contextの保存。
- 節単位の実送信と整理入力、PDF／EPUB両方のblock／inline code・数式・画像・XHTML全文の非送信、snapshot-boundな用例／出典。
- 未知unit／捏造引用・根拠なし語・schema不正の拒否、失敗のraw回答／usage記録と旧版保持、根拠不一致の最大3回答までの再生成と上限停止・訂正済みrequestの再利用。
- 無送信再利用、方式metadataだけの変更で固定翻訳入力hashが変わらないこと、policy／terms改版、hash不正・再hashした捏造根拠、採用参照replace失敗、writer排他。
- wire v2とbinding：前方追加・重複本文・疎／複数引用・実heading／本文変更、原文からのbinding再構築、未知version／再hash改ざん、v1完全一致の無送信reuseと混在。`tests/fixtures/preparation-v1.json`は旧v1の原byteをpath→UTF-8文字列で保持するfixtureです。代表単独は`pytest tests/test_preparation.py::test_preparation_v2_rebinds_shifted_groups_and_keeps_real_context_misses tests/test_preparation.py::test_preparation_loads_byte_original_v1_and_only_generates_missing_v2`、共有fixtureのconsumer確認は`pytest tests/test_preparation.py tests/test_translation.py tests/test_render.py tests/test_performance_verification.py`です。
- collectの制限付き並列：`pytest tests/test_preparation.py -k 'parallel or invalid_concurrency or parent_signal or non_main_thread'`と`pytest tests/test_run.py -k preparation_concurrency`、代表単独は`pytest tests/test_preparation.py::test_preparation_parallel_same_wire_miss_shares_raw_success_or_failure`です。親main threadへのSIGINT／SIGTERM、旧pointer保持、子の回収、handler復元、非main thread呼出しを含みます。

公開prepare→loadの直接手順は[Feature Map](.pi/skills/verify-project/references/feature-map.md#準備request内idと追跡bindingissue-58)、並列の計測は[性能reference](docs/reference/performance.md#準備collectの制限付き並列issue-60)と[Feature Map](.pi/skills/verify-project/references/feature-map.md#準備collectの制限付き並列issue-60)を参照してください。秒数閾値はpytestに入れません。

### 翻訳・採用snapshotの検証境界

公開seamは`translate_document`→`load_translation`です（[artifact契約](docs/reference/artifacts.md#翻訳採用snapshot)）。`ai/tests/translation-process.mjs`（取消は公開stream provider）を使います。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_translation.py tests/test_preparation.py tests/test_units.py tests/test_inference.py
```

- 既定約12,000原文文字・concurrency 2、章節境界、ID不正／空訳／marker inventory・構造の拒否、原位置付き要確認と品質未承認。
- length／input上限によるchunk縮小・lossless分割・不可分spanの失敗、部分失敗／再開、有限retryとusage保持、marker違反の最大3回答の訂正生成と無送信再利用・中断回復、実CLI resumeで失敗chunkだけを訂正すること。
- marker入力が同じcode変更・準備metadataだけの変更の無送信再利用、原文／context／glossary／policy／設定改版、artifact破損・再hashした不正attempt、replace故障と旧版保持、writer排他。
- main-thread SIGINT／SIGTERMで、childの取消・usage保持・drain／回収を待ち、未開始chunkを送信しないこと。
- `auth_failed`／`storage_failed`の全体停止と保存例外：`pytest tests/test_translation.py -k "stop or save_failure"`。直接操作は[Feature Map](.pi/skills/verify-project/references/feature-map.md#翻訳の全体障害停止issue-104)です。
- wire v2とbinding：prefix追加、raw alias→現在ID、実context／本文変更miss、manual優先／非転写、旧immutableとbyte-original v1（`tests/fixtures/translation-v1.json`）、splitの親hash＋元範囲、同一v2 requestの並列共有・usage一度・保存例外・取消drain。consumerを含めて`pytest tests/test_translation.py tests/test_revise.py tests/test_resume.py tests/test_review.py tests/test_pdf_candidate_acceptance.py tests/test_performance_verification.py`、直接手順は[Feature Map](.pi/skills/verify-project/references/feature-map.md#翻訳request内idと追跡bindingissue-59)です。

## Markdown／対応表／QA・render/statusの検証境界

公開seamは保存した正本→`render_document`と実CLI `render DIR`／`status DIR`です。`render.py`が出力とQA、`render_assets.py`が元archive／PNGの検査と安全な表示assetを担います。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_render.py tests/test_cli_output.py
```

自作source／EPUBと固定した人工訳文で、fence内backtick・callout・list・空cell／span・MathML・PNG／crop・notes／anchors・危険markup／外部参照・元text不変・部分／split訳・stale／hash／atomic保存・writer排他を検査します。CLIのcore-only条件は空PATHで検査します。通常`status DIR`の保存記録readerと共有`not_processed`分類は`tests/test_render.py`、CLI／formatterは`tests/test_cli_output.py`です。

LLMもNodeも使わない直接CLI確認（上の`EVIDENCE`を作成済みであること）：

```sh
WORKSPACE=$(mktemp -d)
export WORKSPACE
.venv/bin/python - <<'PY'
import os
from pathlib import Path
from doc_translate.snapshot import SourceSnapshot, save_source_snapshot
source = SourceSnapshot.model_validate_json(Path('tests/fixtures/render-source.pdf.json').read_bytes())
save_source_snapshot(Path(os.environ['WORKSPACE']), source)
PY
if PATH='' "$PWD/.venv/bin/doc-translate" render "$WORKSPACE" > "$EVIDENCE/render.json"; then code=0; else code=$?; fi
printf '%s\n' "$code" > "$EVIDENCE/render.exit"
test "$code" -eq 1  # 期待する部分出力
PATH='' "$PWD/.venv/bin/doc-translate" status "$WORKSPACE" --json > "$EVIDENCE/render-status.json"
.venv/bin/python - <<'PY'
import json, os, shutil
from pathlib import Path
root, evidence = Path(os.environ['WORKSPACE']), Path(os.environ['EVIDENCE'])
result = json.loads((evidence / 'render.json').read_bytes())
status = json.loads((evidence / 'render-status.json').read_bytes())
assert status['render_current'] is True
assert result['processing']['status'] == 'partial'
assert result['artifacts'] == status['artifacts']
assert all((root / path).is_file() for path in result['artifacts'].values())
ja = (root / result['artifacts']['japanese']).read_text()
en = (root / result['artifacts']['english']).read_text()
qa = json.loads((root / result['artifacts']['qa']).read_bytes())
assert '※' in ja and '````' in en
assert any(item['reason'] == 'Original code is unfinished; do not invent its ending'
           for item in qa['uncertainties'])
mapping = json.loads((root / result['artifacts']['source_map']).read_bytes())
assert mapping['blocks'][0]['locations'][0]['page'] == 2
assert qa['content_review']['meaning_quality_approved'] is False
shutil.copytree((root / result['artifacts']['japanese']).parent, evidence / 'render-artifacts')
print('partial exit1; saved artifacts/QA and current status matched')
PY
```

status／EPUB／PDFの進捗表示とreviewの共通表示（TTY、狭幅、明暗背景、NO_COLOR、部分resume、空PATH再利用）の直接操作は[検証Skill](.pi/skills/verify-project/SKILL.md)と[Feature Map](.pi/skills/verify-project/references/feature-map.md)の「保存記録と詳細検査」「EPUB run／resumeと共通進捗」「PDF run／resumeの工程・所有ページ進捗」「明示範囲reviewの共通表示」を使います。HTML生成だけを視認性確認とせず、実表示を確認します。

### 進捗表示・statusの統合受入れ

統合確認では上記のSkill／Map各節をRootが別invocationで直接操作し、正式範囲は「正式チェック」のcore全pytest・PDF4ファイル・Ruff check／format・PDF環境ty・CLI helpと、[wheelの明示buildと導入検査](#wheelの明示buildと導入検査)です。Rich直接依存・core-only空PATHの保存-only status全4指定、run／resume／reviewのJSON flagsは配布smokeで確認します。Node source／protocolが不変なら全Node phaseの反復は不要です。進捗の回帰は`tests/test_run.py -k pdf_progress`、`tests/test_pdf_correction.py::test_correct_pdf_preserves_transport_attempts_without_cumulative_usage`、`tests/test_cli_output.py::test_pdf_progress_split_keeps_owned_denominator_and_translation_state_separate`、observer境界は`tests/test_inference.py`／`test_preparation.py`／`test_translation.py`／`test_run.py`／`test_resume.py`です。`test_run.helper/run_args`は`--json --events-json`を指定する機械向けfixtureで、人向け既定と4組合せは`tests/test_cli_output.py`で検査します。

## 新規runの検証境界

公開seamは`cli.main(argv, process_command=...)`→workspace／Markdown／statusです。`tests/test_run.py`は実EPUB ZIPと、`tests/test_pdf.py`のDocling公開converter／page backend・人工local model fixture、`ai/tests/run-process.mjs`を使います。credential directoryは一時pathのみです。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_run.py
```

PDF／EPUBの出力到達、所有5ページ分割／length縮小、許可範囲だけの隣接context・離れた参照範囲の無送信、全ページ指定、glossary terms優先、要確認と部分失敗の区別、補正成功保持／sourceなし／準備・翻訳失敗時の部分出力、既存空directory・file・symlinkの拒否、未知modelの無送信、認証metadataの送信前flushを検査します。PDFのsynthetic直接確認は`.venv/bin/pytest -s tests/test_run.py -k 'owns_requested or partial_pdf or without_pages'`です。

公開CLIの直接確認（上の`EVIDENCE`を作成済みであること）：

```sh
RUN_FIXTURE=$(mktemp -d)
export RUN_FIXTURE HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PYTHONPATH=tests .venv/bin/python - <<'PY_RUN' > "$EVIDENCE/run.json"
import json, os, shutil
from pathlib import Path
from test_epub import make_epub
from doc_translate.cli import main
root = Path(os.environ["RUN_FIXTURE"])
source = make_epub(root)  # 自作mixed-inline EPUB
settings = root / "faux.json"
settings.write_text(json.dumps({}))
main(["run", "--json", str(source), "--workspace", str(root / "new"),
      "--provider", "faux", "--model", "text", "--thinking", "off"],
     process_command=[shutil.which("node"), str(Path("ai/tests/run-process.mjs").resolve()),
                      str(root / "credentials"), str(settings), str(root / "requests.jsonl")])
PY_RUN
.venv/bin/doc-translate status "$RUN_FIXTURE/new" --json > "$EVIDENCE/run-status.json"
.venv/bin/doc-translate render "$RUN_FIXTURE/new" > "$EVIDENCE/run-render.json"
.venv/bin/python - <<'PY'
import json, os, shutil
from pathlib import Path
from doc_translate.snapshot import load_source_snapshot
from doc_translate.preparation import load_preparation
from doc_translate.translation import load_translation
root, evidence = Path(os.environ['RUN_FIXTURE']), Path(os.environ['EVIDENCE'])
workspace = root / 'new'
result = json.loads((evidence / 'run.json').read_bytes())
status = json.loads((evidence / 'run-status.json').read_bytes())
assert result['processing']['status'] == 'completed' and status['render_current'] is True
assert load_source_snapshot(workspace).document.format == 'epub'
load_preparation(workspace)
assert load_translation(workspace).processing.status == 'completed'
assert len((root / 'requests.jsonl').read_text().splitlines()) == 3
assert all((workspace / path).is_file() for path in result['artifacts'].values())
assert '訳:Original' in (workspace / result['artifacts']['japanese']).read_text()
qa = json.loads((workspace / result['artifacts']['qa']).read_bytes())
assert qa['content_review']['meaning_quality_approved'] is False
shutil.copytree((workspace / result['artifacts']['japanese']).parent, evidence / 'run-artifacts')
print('completed; public loads; 3 requests; Japanese output and QA matched')
PY
# 同じworkspaceへの新規runはexit2（setup／認証／推論を開始しない）
if .venv/bin/doc-translate run "$RUN_FIXTURE/book.epub" --workspace "$RUN_FIXTURE/new" \
    --provider faux --model text --thinking off; then code=0; else code=$?; fi
printf '%s\n' "$code" > "$EVIDENCE/existing-run.exit"
test "$code" -eq 2
```

コピー前提・拒否の直接確認は上のrun直後に行います。元workspaceを一時退避した状態で別pathから公開loadし、不正envelopeを実CLIへ渡します。

```sh
.venv/bin/python - <<'PY' > "$EVIDENCE/copy-reject.txt"
import json, os, shutil
from pathlib import Path
from doc_translate.cli import main
from doc_translate.snapshot import load_source_snapshot
from doc_translate.preparation import load_preparation
from doc_translate.translation import load_translation
root = Path(os.environ['RUN_FIXTURE'])
seed = root / 'new'
a = shutil.copytree(seed, root / 'copy-a')
b = shutil.copytree(seed, root / 'copy-b')
seed.rename(root / 'seed-held')
try:
    for workspace in (a, b):
        load_source_snapshot(workspace)
        load_preparation(workspace)
        assert load_translation(workspace).processing.status == 'completed'
finally:
    (root / 'seed-held').rename(seed)
before = {p: p.read_bytes() for p in a.rglob('*') if p.is_file()}
requests = (root / 'requests.jsonl').read_bytes()
revision = root / 'invalid-source.json'
revision.write_text(json.dumps({
    'schema_version': 1,
    'base_source_sha256': json.loads((a / 'source-snapshot.json').read_bytes())['sha256'],
    'source': load_source_snapshot(a).model_dump(mode='json'), 'unexpected': True}))
try:
    main(['revise', str(a), '--source', str(revision), '--reason', 'invalid'])
except SystemExit as error:
    assert error.code == 2
else:
    raise AssertionError('expected revise exit2')
assert {p: p.read_bytes() for p in a.rglob('*') if p.is_file()} == before
assert (root / 'requests.jsonl').read_bytes() == requests
pointer = 'source-snapshot.json'
original = (seed / pointer).read_bytes()
(a / pointer).write_bytes(b'changed independent copy')
assert (seed / pointer).read_bytes() == (b / pointer).read_bytes() == original
print('relocated public loads; revise exit2; all bytes/3 requests unchanged; isolated copies')
PY
```

run／resumeの送信前JSONを解析するときは`--events-json`、resumeの最終JSONを解析するときは`--json`を明示します。

## 明示範囲reviewの検証境界

公開seamは`review DIR --units IDS`／`--pages A-B`→workspace・QA/statusです。`tests/test_review.py`は`test_run.py`のhelper/run_args・人工PDF converter、`test_pdf_correction.py`の保存draft／PNGと公開補正・組立・準備・翻訳API、人工EPUBを再利用します。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_review.py tests/test_cli.py tests/test_render.py tests/test_revise.py tests/test_run.py
```

- unit／pages排他・必須、未知／空ID、EPUB pages、保存範囲外・跨り・未訳／staleの無送信。PDF表のblock位置とcell evidenceが異なる形状でも選択外cell本文を送らないこと。
- 非選択context・他unit本文の非送信、split/table/attributeのID・親範囲・元位置、選択PNGのbyte/hash/順/scale/座標、非翻訳block／ページへの指摘。
- 正常／空findings／未知対象拒否／provider失敗とusage observed/unknown、参照と旧byte hash不変、明示overrideとambient非適用、writer lock、保存失敗時の旧版・usage保持。
- 保存-only status/renderの確認／未確認範囲、QAの位置・anchor、旧reviewの履歴保持、candidate待ちでも採用hashに結ぶこと、空findingsでも`meaning_quality_approved=false`。
- 進捗通知の順序、`--json`／`--events-json`の4組合せ、今回記録だけの指摘件数。

上の`RUN_FIXTURE/new`作成後の直接確認（faux限定）：

```sh
PYTHONPATH=tests .venv/bin/python - <<'PY_REVIEW'
import os
from pathlib import Path
from test_run import helper
from doc_translate.cli import main
root = Path(os.environ['RUN_FIXTURE'])
main(['review', str(root / 'new'), '--units', 'unit-1'], process_command=helper(root))
PY_REVIEW
.venv/bin/doc-translate status "$RUN_FIXTURE/new" --json  # review履歴・未確認範囲、品質承認false
.venv/bin/doc-translate render "$RUN_FIXTURE/new"         # offline QA更新
```

TTY実表示を含む手順は[Feature Mapのreview節](.pi/skills/verify-project/references/feature-map.md#明示範囲reviewの共通表示issue-52)です。

## 基点付き修正・PDF補正候補採用の検証境界

公開seamは`revise`／`resume`／`status`／`render`→参照・保存hash・出力です（[artifact契約](docs/reference/artifacts.md#基点付き修正pdf補正候補採用)、操作は[CLI詳細リファレンス](docs/reference/usage.md#原文訳文を修正する推論なし)）。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_cli.py tests/test_revise.py tests/test_run.py tests/test_pdf_candidate_acceptance.py
```

- 完了後の修正と入力不変、glossary・文体改版、partialの不足ページだけの補正、補正設定の明示変更で不要な取得をしないこと。
- 候補待ちで下流requestが0件、H/B/Cと新旧scope分離、旧参照／hash保持、IDずれ・接続／分割／統合・削除／並替え・table／list／caption／図所属の変更で訂正を自動転写しないこと。
- Cそのまま／編集Kの採用、複数revision後の機械基点継承、stale source／candidate・別入力世代・不正構造／ref／hashの拒否、候補公開前／採用参照切替前後のSIGINT／SIGKILL相当中断と排他（`tests/fixtures/resume_process.py`）。
- 採用後の旧訳／手修正訳の実request全体一致での再利用、code-only無再送と不一致の再翻訳、QA／出力更新、旧byte列hashの互換読込み、EPUB回帰。

PDF候補の直接入口は`.venv/bin/pytest -s tests/test_run.py -k 'candidate_offline_adoption or revised_partial_pdf'`です。上の`RUN_FIXTURE/new`を使う訳文patchの直接確認とcleanup：

```sh
.venv/bin/python - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ['RUN_FIXTURE'])
workspace = root / 'new'
source = json.loads((workspace / 'source-snapshot.json').read_bytes())['sha256']
translation = json.loads((workspace / 'translation-snapshot.json').read_bytes())['sha256']
snapshot = json.loads((workspace / f'snapshots/translation/{translation}.json').read_bytes())
item = snapshot['chunks'][0]['answer']['translations'][0]
item['text'] = item['text'].replace('Original', '手動確認')
(root / 'patch.json').write_text(json.dumps({
    'schema_version': 1, 'base_source_sha256': source,
    'base_translation_sha256': translation, 'translations': [item]}))
PY
PATH='' "$PWD/.venv/bin/doc-translate" revise "$RUN_FIXTURE/new" \
  --translations "$RUN_FIXTURE/patch.json" --reason '人工訳文patchの確認'
PATH='' "$PWD/.venv/bin/doc-translate" render "$RUN_FIXTURE/new"
PATH='' "$PWD/.venv/bin/doc-translate" status "$RUN_FIXTURE/new" --json > "$EVIDENCE/patch-status.json"
.venv/bin/python - <<'PY'
import json, os, shutil
from pathlib import Path
from doc_translate.translation import load_translation
root, evidence = Path(os.environ['RUN_FIXTURE']), Path(os.environ['EVIDENCE'])
workspace = root / 'new'
status = json.loads((evidence / 'patch-status.json').read_bytes())
assert status['render_current'] is True
assert '手動確認' in load_translation(workspace).translations[0].text
assert '手動確認' in (workspace / status['artifacts']['japanese']).read_text()
assert len((root / 'requests.jsonl').read_text().splitlines()) == 3
shutil.copytree((workspace / status['artifacts']['japanese']).parent, evidence / 'patch-artifacts')
print('offline patch/render/status exit0; manual Japanese text; no new request')
PY
# WORKSPACEとRUN_FIXTUREが今回のmktemp値であることを確認してから削除する
rm -rf -- "$WORKSPACE" "$RUN_FIXTURE"
printf '%s\n' 'owned temporary workspaces/credentials removed' > "$EVIDENCE/cleanup.txt"
test -s "$EVIDENCE/revision.txt" && test -s "$EVIDENCE/run.json"
cat "$EVIDENCE/copy-reject.txt" "$EVIDENCE/cleanup.txt"
```

cleanup後の出力は`$EVIDENCE/{render,run,patch}-artifacts/`で再読できます。

## resume／障害復旧の検証境界

`tests/test_resume.py`は公開CLI→workspace／status／出力で、`tests/fixtures/resume_process.py`（実Python CLI入口、試験専用に**OS `replace`だけ**をbefore／afterで停止）を使います。source／準備／翻訳pointerの両側でSIGINTとSIGKILLを送り、旧原本・snapshot・出力hash不変、受理済みrequestの再送なし、未受理応答の再送とusage不明／重複課金の開示、API失敗、hash破損の事前拒否、writer排他、保存設定の優先順位を確認します。`tests/test_run.py`はPDF部分所有回復、chunk確定後・進捗replace前の強制kill、未完了draftの再取得、glossaryによる取得／補正の非無効化を含みます。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_resume.py tests/test_run.py tests/test_preparation.py tests/test_pdf_correction.py tests/test_translation.py
```

直接entry再現例です。`BOUNDARY=source-snapshot.json|preparation-snapshot.json|translation-snapshot.json`、`EDGE=before|after`、`STOP_SIGNAL=SIGINT|SIGKILL`で境界を選びます。SIGINTでは取消drainを、SIGKILLではOS lock解放と保存済みartifactの再検証だけを確認します。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
BOUNDARY=preparation-snapshot.json EDGE=before STOP_SIGNAL=SIGKILL .venv/bin/python - <<'PY'
import hashlib, json, os, shutil, signal, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path('tests').resolve()))
from test_epub import make_epub
root = Path.cwd()
work = Path(tempfile.mkdtemp(prefix='doc-translate-resume-'))
source = make_epub(work)
workspace = work / 'workspace'
settings = work / 'faux.json'
settings.write_text('{}')
helper = [shutil.which('node'), str(root / 'ai/tests/run-process.mjs'),
          str(work / 'credentials'), str(settings), str(work / 'requests.jsonl')]
entry = [sys.executable, str(root / 'tests/fixtures/resume_process.py'),
         '--helper', json.dumps(helper)]
subprocess.run([*entry, '--', 'run', str(source), '--workspace', str(workspace),
                '--provider', 'faux', '--model', 'text', '--thinking', 'off'],
               capture_output=True, text=True, check=True)
old = {p: hashlib.sha256(p.read_bytes()).hexdigest()
       for directory in ('originals', 'snapshots', 'renders')
       for p in (workspace / directory).rglob('*') if p.is_file()}
glossary = work / 'terms.json'
glossary.write_text(json.dumps({'version':'2', 'book':'ignored', 'style':'ignored',
                                'terms':{'Original':'指定'}}))
marker = work / 'boundary.marker'
with (work / 'stop.out').open('w') as out, (work / 'stop.err').open('w') as err:
    child = subprocess.Popen([*entry, '--boundary', os.environ.get('BOUNDARY', 'preparation-snapshot.json'),
                              '--edge', os.environ.get('EDGE', 'before'), '--marker', str(marker),
                              '--', 'resume', str(workspace), '--glossary', str(glossary)],
                             stdout=out, stderr=err)
    try:
        deadline = time.monotonic() + 15
        while not marker.exists():
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(.01)
        stop = getattr(signal, os.environ.get('STOP_SIGNAL', 'SIGKILL'))
        child.send_signal(stop)
        marker.with_suffix('.release').touch()
        code = child.wait(timeout=35)
        assert code in (0, 1) if stop == signal.SIGINT else code == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
resumed = subprocess.run([*entry, '--', 'resume', str(workspace), '--json'], capture_output=True, text=True, check=True)
assert json.loads(resumed.stdout)['processing']['status'] == 'completed'
assert len((work / 'requests.jsonl').read_text().splitlines()) == 5
assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in old.items())
status = subprocess.run([str(Path(sys.executable).with_name('doc-translate')), 'status', str(workspace), '--json'],
                        capture_output=True, text=True, check=True)
assert json.loads(status.stdout)['render_current'] is True
print(work, 'completed; 5 requests; old hashes preserved; saved status current')
PY
```

試験workspaceは自動削除しません。確認後、自分が作った`work`だけを削除します。

## wheelの明示buildと導入検査

```sh
npm --prefix ai run build
uv build
uv run python scripts/check_distribution.py dist/doc_translate-0.1.0-py3-none-any.whl
```

`check_distribution.py`は一時directoryで再compileして`ai/dist`と比較し、wheel内のdist／manifest／lockをbyte比較します。core-onlyの別venvへwheelをinstallし、Nodeもnpmもない空PATHで自作EPUB ZIPのreader→source保存／load→unit生成→実CLI render/statusを検査します。wheel metadataのRich直接依存（`>=15,<16`）と実import、run／resume／review helpの`--json`／`--events-json`も確認します。訳文なしのrenderはexit1・部分出力・`※`、通常statusはexit0・保存記録のみ、`--verify`／`--json`／`--verify --json`はexit0です。その後、workspace外のclean data directoryで実CLI `setup-ai`を実行します（installed npmの`ci --omit=dev --ignore-scripts --no-audit --no-fund`、registry取得あり）。続くoffline smokeはNodeだけをPATHに置き、`providers`／`models --provider anthropic|google`、公開codecのUTF-8分割byte round-trip・末尾不正の拒否、pi-ai／proper-lockfileのimportを確認します。認証・推論はしません。wheelにはhelper sourceやnode_modulesを含めず、sdistは明示build済みdistと再build用source／manifest／lockを含みます。

Python側の別環境確認（`TMP`は新規一時directory）：

```sh
TMP=$(mktemp -d)
WHEEL="$PWD/dist/doc_translate-0.1.0-py3-none-any.whl"
uv venv --python 3.14 "$TMP/core"
uv pip install --python "$TMP/core/bin/python" "$WHEEL"
(cd "$TMP" && core/bin/doc-translate --help)
(cd "$TMP" && core/bin/python -c 'import importlib.util; import doc_translate.snapshot, doc_translate.jsonl, doc_translate.inference, doc_translate.epub, doc_translate.epub_source; assert importlib.util.find_spec("docling") is None')
uv venv --python 3.14 "$TMP/pdf"
uv pip install --python "$TMP/pdf/bin/python" "$WHEEL[pdf]"
(cd "$TMP" && pdf/bin/python -c 'from docling.document_converter import DocumentConverter; from docling.datamodel.pipeline_options import OcrMacOptions; from docling_core.types.doc import DoclingDocument; from ocrmac import ocrmac')
```

EPUB readerと原文抽出はcore-onlyで`uv run pytest tests/test_epub.py tests/test_epub_source.py tests/test_source_snapshot.py`が検査します。`tests/test_epub.py`は人工ZIPでpath・ZIP／XML／fallback上限・entity／DOCTYPE・固定layout・暗号化／font難読化・対象外本文の受理／拒否、namespace展開名の長さ／累積量・属性数、stored／deflate切捨て・data descriptor／空memberを公開`read_epub`経由で検査します。`tests/test_epub_source.py`は人工EPUB 2／3を公開reader→adapter→save/loadへ通し、mixed order、階層／span／caption／alt／code空白、inert保持、位置・duplicate／missing ID、長い元IDへの繰返し参照の保存量、spine／non-linear／alias／fallback、off-spine notes／循環、notes roleの継承とtoc優先、空resource、unsafe／external URI、XML最大深さ128での保存を検査します。性能回帰に固定の時間閾値は使いません。

## 認証の実装・検証境界

storeとhelperの契約は[artifact契約の認証helper](docs/reference/artifacts.md#認証helper)です。Pythonの`authenticate`はCLIから実helperへJSONLを送り、controlling TTYだけで非echo入力し、認証URL・device codeをterminalへ表示します。認証record／入力／URL・code／raw stderrをログへ残しません。

`ai/tests/credentials.test.ts`は実storeと公開`createModels/createProvider/getAuth`で同時refresh・失敗時のno fallback・保存維持を、Node実processで排他・heartbeat・UUID・drainを検査します。`ai/tests/auth.test.ts`は公開provider／実helper境界、`tests/test_auth_cli.py`は公開CLI・実helper／外部fake・PTYで非echo、prompt取消、SIGINT／SIGTERM／EOF、未送信secretの破棄、取消中の保存失敗とchild回収を検査します。人工credentialとnetwork拒否だけを使い、実認証・実token refresh・推論は含みません。

## 推論adapterの実装・検証境界

`doc_translate.inference.generate`は文書workflowとは独立したPython APIです（契約は[artifact契約](docs/reference/artifacts.md#python-inference-adapter)）。`ai/tests/generation.test.ts`と`generation-process.mjs`は公開fauxProvider／createProviderと実HTTP adapter＋injected fetchで実効入力・terminal・usage・retry設定・取消／refresh drainを検査します。`tests/test_inference.py`は実helperを使ってPython側の検証・domain validator・有限retry・画像保持・timeout／signal取消を検査し、`ai/tests/inference-provider.mjs`は人工認証／取消process、`tests/fixtures/inference_process.py`はprotocol故障注入だけを担当します。実provider通信・実credential・課金・翻訳品質・adapter内部retry回数は検証しません。

## 性能計測

形式別run／resume・render負荷、準備／翻訳の保存attempt探索、準備collect・PDF補正の並列、snapshot履歴の容量の計測recipeと結果は[性能reference](docs/reference/performance.md)、再実行は[Feature Map](.pi/skills/verify-project/references/feature-map.md#形式別runresume性能とrender負荷issue-54)の該当節です。他のtestと非並行で、通常3回のraw／median／min／maxと診断計数を分け、実request増加0・旧immutable byte保持・cleanup後の証拠再読を確認します。秒数閾値のpytestは追加しません。小さいselfcheckは`tests/test_performance_verification.py`です。

## PDF原文品質の評価

代表標本の準備、隔離offline実験、固定PDF原文評価の送信前準備の入口・境界・証拠は[PDF原文品質の評価設計](docs/design/pdf-source-quality-feasibility.md)とverify-projectの該当節を使います。実送信・認証は別承認で、試行回数の上限と停止条件は設計に従います。通常製品の実AI評価ではありません。

## lock・依存更新とlicense

Pythonは`pyproject.toml`変更後に`uv lock`、Nodeは`ai/package.json`を変更して`npm --prefix ai install --package-lock-only --ignore-scripts --no-audit --no-fund`でlockを更新します。依存更新はregistry取得の許可範囲内で行い、更新後はwheel検査を再実行します。

project本体のlicenseはMITです（[LICENSE](LICENSE)、`pyproject.toml`と`ai/package.json`に記載）。

主依存：Pydantic／Rich／Docling／OCRMac／pi-ai／proper-lockfileはMIT、PillowはMIT-CMU、pypdfium2はBSD-3-Clause／Apache-2.0、defusedxmlはPSFL。推移依存にはcertifiのMPL-2.0、Torchの複合license、Node側のMIT／Apache-2.0／BSD等があります。pypdfium2は対象platformのPDFium・同梱依存の`licenses/data/*/BUILD_LICENSES`も確認します。lockは解決versionの記録でありlicense承認の代替ではありません。更新時は実際の配布物のlicense／NOTICEとnative条件を再確認します。Apple VisionのOS条件やmodel重みはpackageのMITに含みません。Heron重みはApache-2.0、TableFormer重みはCDLA-Permissive-2.0で、同梱しません。

## fixture・CIの境界

fixtureは自作の小文書／人工応答だけを使い、書籍の切出し、実credential、token、重みを追加しません。`sample/`はローカル資料としてignoreします。テストはofflineで、依存installだけをネットワーク処理と区別します。Hub／Transformersのoffline設定は全通信を遮断するfirewallではありません。

CIはpush（main）／pull_requestで上記checksとwheel導入を実行します。secrets、pull_request_target、continue-on-errorは使いません。Work Itemではremote CIの実行・成功確認を受入れ条件にせず、ローカル正式チェックとbuild／別環境導入検査で確認します。remote CIの未実行を未完了とはしませんが、remote成功とも表示しません。
