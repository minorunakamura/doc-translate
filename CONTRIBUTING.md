# 開発ガイド

対象はIssue #1の初期基盤、Issue #2のsetup-ai／offline metadata、Issue #3の認証store／login・logout、Issue #4の再利用可能な推論adapter、Issue #5のEPUB安全読込み境界、Issue #6のEPUB原文snapshot抽出、Issue #7のPDF抽出下書き取得・保存、Issue #8の所有範囲を検証したPDF補正chunk保存、Issue #9のPDF原文snapshot組立、Issue #10の共通翻訳unit生成、Issue #11の本文根拠付きcontext／glossary準備、Issue #12の固定入力による翻訳・検証・採用snapshot、Issue #13の安全Markdown・対応表・QA・summaryとoffline render/status、Issue #14のPDF／EPUB新規run接続、Issue #15のresume／障害復旧です。全文機能の計画は[正式Plan](docs/plans/doc-translate-initial.md)、責務は[基本設計](docs/design/doc-translate.md)、現在の公開契約は[artifact契約](docs/reference/artifacts.md)を参照してください。READMEは利用者向けです。

## 構成・環境

- `src/doc_translate/`：PythonのCLI、アプリ所有Node runtime導入／検査、TTY認証UI（`auth.py`）、推論adapter（`inference.py`）、EPUB安全reader（`epub.py`）と原文抽出adapter（`epub_source.py`）、PDF取得adapter（`pdf.py`）と補正chunk境界（`pdf_correction.py`）・原文組立adapter（`pdf_source.py`）、共通unit生成・inline保護・workflow分割（`units.py`）、固定context／glossary準備と根拠・実行記録（`preparation.py`）、翻訳chunk・検証・部分採用と再利用（`translation.py`）、原文snapshot、workspace OS lock、JSONL codec。
- `ai/src/`：TypeScriptのJSONL codec、非秘密metadata／認証／generation helper（`dist/main.js`）、`AuthDialog`、`FileCredentialStore`、独立推論のpreflight／stream接続（`generation.ts`）。`ai/tests/`の`.mjs`は公開APIとcompile済みhelperを使うテスト専用processです。
- `tests/fixtures/`：自作JSONと外部認証fake／PTY process、推論protocolの故障注入proxy。EPUBの人工ZIPは`tests/test_epub.py`で生成し、`tests/test_epub_source.py`でreader→原文snapshot抽出→公開save/loadのround-tripを検査します。`tests/test_pdf.py`はDoclingの公開変換／page backend境界の人工fixtureから、raw／画像／設定の保存・load検証を行います。`tests/test_pdf_correction.py`は人工draft→実Node helper／faux provider→検証済み所有chunkの公開save/loadを検査します。`tests/test_preparation.py`は保存原文→実helper／faux→固定準備artifactの公開prepare/loadを検査します。`tests/test_translation.py`は保存原文／固定準備→実helper／faux→翻訳の公開translate/loadを検査します。`tests/test_render.py`は正本→Markdown／対応表／QAと実CLI render/statusを検査します。`tests/test_run.py`はPDF／EPUBの公開CLIからworkspace・出力・statusまでを検査します。新規`run`と`resume`は実装済みで、`revise`／`review`は未実装です。
- `scripts/check_distribution.py`：sourceと明示compile済みresource／wheelの一致、および別venvにinstallしたwheel CLIからのclean npm／pnpm setupとNode-only catalog smokeを検査します。runtime導入は公開`setup-ai`自身が行います。

実動基準：macOS 26.7.1 arm64、Python 3.14.9、Node 24.21.0、pnpm 12.10.1、npm 11.19.0。CIはGitHub-hosted macos-15 ARM64、Python 3.14／Node 24です。別OSやmacOSの最低versionは未保証です。

## 正式チェック（順序を含む）

必要なtoolは事前に導入します。以下の依存installだけはregistry接続を使います。model重み・認証・LLMは不要です。

```sh
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
pnpm --dir ai install --frozen-lockfile --ignore-scripts
pnpm --dir ai build
uv sync --locked --dev
pnpm --dir ai exec biome format . --error-on-warnings
pnpm --dir ai exec oxlint --type-aware --type-check --deny-warnings .
pnpm --dir ai exec vitest run
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run doc-translate --help
```

Python packageは`ai/dist`を同梱するため、fresh checkoutでは`uv sync`より先にhelperの明示buildが必要です。既存editable環境で同梱resourceを更新する場合はbuild後に`uv sync --locked --dev --reinstall-package doc-translate`を使います。pytestのprocess契約もcompile済み公開`@doc-translate/ai/jsonl`を使います。Python側はbinary file行iterator、Node側は任意binary chunkのcodecです。警告をquiet／包括的disableで隠さず修正します。

`ai/tsconfig.build.json`の`skipLibCheck`はpi-ai 1.4.0配布declarationのJSON import属性欠落と、@google/genaiのoptional MCP SDK型参照不足に限定した互換性対応です。依存declaration自体の型検査は省きますが、自作sourceの型検査とoxlintのtype-aware／type-check／warning 0は維持します。実helperと両managerのclean installでruntime互換性を別途検査します。

上記はcore-onlyのruntime／テスト検証です。全Python sourceの型検査は、PDF adapterとテストのimportも解決できるPDF extra付き環境で行います。

```sh
uv sync --locked --dev --extra pdf
uv run --locked --extra pdf ty check
uv run --locked --extra pdf python -c 'from docling.document_converter import DocumentConverter; from docling.datamodel.pipeline_options import OcrMacOptions; from docling_core.types.doc import DoclingDocument; from ocrmac import ocrmac; print(OcrMacOptions(lang=["en-US"], framework="vision", recognition="accurate"))'
uv run --locked --extra pdf pytest tests/test_pdf.py tests/test_run.py
```

共通テストはcore-onlyで全件実行し、PDF extra付きではDocling依存のテストを含む上記2ファイルに限定して重複実行を抑えます。PDF依存が必要なテストを別ファイルに追加する場合は、CIとこの手順の実行対象にも追加してください。

extraを保持するには`uv run --locked --extra pdf ...`（pytestは`uv run --locked --extra pdf pytest`を推奨）、またはsync済みの`.venv/bin/pytest`／`.venv/bin/ruff`／`.venv/bin/ty`を使い、`.venv/bin/pytest`を直接使う場合はvenvのactivateまたは`.venv/bin`のPATH追加が必要です。これは依存解決・importと人工fixture検証であり、それだけを実Docling変換、Apple Vision OCR、重みを使うlayout／table処理の成功と表示しません。

### PDF取得の検証境界

`acquire_pdf`→`load_pdf_draft`の公開seamで、不足model・不正入力／range、raw item／ref／table cell／provenance、clipしないcharspan不一致、conversion status／errors、全対象ページ／PNG、直接描画scale／unload、artifact hash不一致、writer排他、参照replace失敗時の旧版保持を検査します。Doclingの外部公開`DocumentConverter.convert`と公開page backend境界だけを人工fixtureに置換し、保存・load・workspaceは実物を使います。private helper mockや未依頼CLIは追加しません。core-onlyでのPDF／EPUB API importも検査します。

実変換は人工fixtureテストとは別に、明示承認済みの既存local modelだけを使い、自作PDFで公開APIを呼びます。重みをテストやCIが取得することはありません。準備は[README](README.md#pdf取得だけを使う場合のローカル準備)、APIと保存物は[artifact契約](docs/reference/artifacts.md#pdf抽出下書きissue-7)を参照してください。再確認は自作の小さな文字付きPDFと画像のみPDFについて、それぞれ次を実行します（`OWN.pdf`／`MODELS`／workspaceは置換）。

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

Issue #7ではmacOS 26.7.1 arm64／Python 3.14.9、承認済み固定revisionのHeron／TableFormer cache、CPU／Visionで自作文字PDF 2ページと画像のみPDF 1ページを実行し、いずれもsuccess／errorsなし、全対象PNG 1800×1350、取得textと保存hash照合を確認しました。画像のみPDFの自作2行をOCRMacが取得しました。公開page backendで400×300 pointからscale 4.5で直接再描画し、既存rasterのupscaleではありません。通信socketを拒否して実行し、同じAPIで不足model／不正PDFの失敗も確認しました。実LLM／補正／翻訳・既存書籍・一般的スキャン品質・他OSは未検証です。Torch由来のmacOS multiprocessing redirect未対応NOTEは観測しましたが変換は成功しています。

### PDF補正chunkの検証境界

Issue #8の公開seamは`correct_pdf(... draft_sha256, owned_pages, reference_pages, policy, config ...) -> digest`→`load_pdf_correction(workspace, digest)`です。取得draftの所有は送信許可ではありません。明示許可範囲だけのraw text／provenance／table cell投影と全所有PNGを、既存`generate(stage="correction")`へ渡します。回答はkind別の型と実raw provenanceで検証し、図の内部block／cellを採用本文から分離して保持します。`SourceBlock.structure`へ任意JSONを保存するだけで受理しません。API・回答schema・内部保持領域の利用禁止・保存物は[artifact契約](docs/reference/artifacts.md#pdf補正chunkissue-8)を参照してください。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_pdf_correction.py tests/test_pdf.py tests/test_inference.py
```

`tests/test_pdf_correction.py`は自作の小さなraw JSON／PNG／PDFDraftをworkspaceへ保存し、public loadでhash検証したdraftから補正します。`ai/tests/pdf-correction-process.mjs`はnetwork拒否下の公開`fauxProvider`と実`runHelper`を使い、人工回答と実際のrequestを記録する外部processです。`generate`やprivate collaboratorのmockで代用しません。実credential・model重みは使いません。全`.mjs`と同じBiome／oxlint／Vitestチェック対象です。

- `test_correct_pdf_saves_only_owned_content_and_sends_only_authorized_input`で所有1／参照2／未許可3のliteral期待値、画像順、refなし画像復元、raw回答記録を検査します。
- `test_form_image_keeps_membership_without_adopting_internal_text_or_cells`で「form画像がlogo＋text＋tableに分かれた」人工形状を処理します。実際の保存結果は採用IDが`outside/caption/ambiguous/partial/fields/form`、内部保持IDが`logo/internal`、採用table cellが`value`、内部保持cellが`name`です。曖昧／部分所属の原textと図領域／原ページPNG・要確認を残します。
- 全content kind、見出し／list階層、code改行／callout、空cell／span、除外理由・原本欠損、不正ref／wrong-page ref／cell／span、所有外出力、owned画像欠落、rawの安全なspan投影とclipしない省略、bbox重なりだけで本文を消さないことを検査します。
- raw itemの部分所属／共有ref／不確実cellと全table所属の矛盾で外側を削除しないこと、旧chunk保持・chunk保存失敗前のattempt保存、各artifactのhash不正、load時のdomain／送信許可再検証、writer排他を検査します。save障害注入はOSの`replace`境界だけです。

再現用の公開API例はartifact契約を参照してください。offlineの実process検証は補正の構造・範囲・保存に限定され、実LLMの読取り／図分類品質・全文翻訳の成立は示しません。chunkは別artifactに確定するだけで、`source-snapshot.json`を切り替えません。CLI・unit生成・翻訳・resume engine・全chunk所有割当は対象外です。ページ接続は次の独立adapterで行います。

### PDF原文組立の検証境界

Issue #9は受理済みchunk→`assemble_pdf_source(workspace=..., chunk_sha256s=..., snapshot_id=...)`→既存`save_source_snapshot/load_source_snapshot`が公開seamです。API／追加schema／最小入力例は[artifact契約](docs/reference/artifacts.md#pdf原文snapshot組立issue-9)を参照してください。adapterは保存・採用lockを自分で取らず、callerが別途saveします。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_pdf_source.py tests/test_pdf_correction.py tests/test_source_snapshot.py
```

`tests/test_pdf_source.py`は既存の人工draft／evidence／run fixtureと実helper／公開faux providerを再利用し、補正回答から検証済みchunkを作ってから組み立てます。脚注越しの本文接続・分綴・code改行／language・欠損の非補完・callout行対応、別ページlist親子（同名local IDを含む）と未知／複数／循環／kind／深さ／順序のQA、未確定table・図内部非再採用、ページ順・断片／出典／画像のsave/loadをliteral期待値で検査します。同じ原本文書の別draftも、実際の抽出snapshotを残し、draft-localな同名refの内容が違う場合に推測接続しないことを検査します。補正側では接続／親根拠の許可範囲と実raw provenanceも拒否検証します。

所有unionを越える処理完了・意味品質は主張しません。未確定内容は断片とQAを保持しますが、hash不一致・所有重複・異なる原本文書は拒否します。新たな永続管理・private mock・実LLM・model取得・既存書籍処理は追加しません。CLI／unit／翻訳／renderer／自動割当は対象外です。

### 共通翻訳unitの検証境界

Issue #10の公開seamは検証済み`SourceSnapshot`→`doc_translate.units.generate_translation_units`→`split_translation_units`／`validate_unit_text`です。API、marker／PDF inline表現、hash・位置・分割範囲と例外は[artifact契約](docs/reference/artifacts.md#共通翻訳unit生成issue-10)を参照してください。snapshot／adapterの旧round-trip契約は変更せず、このunit API自身にはCLI・翻訳engine・glossary準備・unit保存・rendererを追加しません。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_units.py tests/test_epub_source.py tests/test_pdf_source.py tests/test_source_snapshot.py
```

`tests/test_units.py`は人工PDF snapshotと実EPUB reader→原文抽出から、採用table cell／spanと祖先context、confirmedな図内部の除外（draft-local refを含む）、caption／近接・不確実textの保持、段落／item単位のmixed inline、link／literal／identifier保護、日本語並替え・marker釣合い、独立した原文／context／snapshot hash、両形式のlossless分割・再分割・不可分spanの明示失敗を公開APIで検査します。private collaborator mock・外部通信・重み・実書籍・実LLMは使いません。語義からidentifierを完全同定することや意味品質は保証せず、普通の単語に見えるidentifierは明示inlineを使います。unitのローカルtoken payloadをそのままLLMへ送らない境界を将来のcallerも守る必要があります。

### 文書context・glossary準備の検証境界

Issue #11の公開seamは保存済み原文→`doc_translate.preparation.prepare_document`（生成・検証・保存・採用）→`load_preparation`です。API、回答schema、固定入力hash、候補根拠／全attempt記録の保存形は[artifact契約](docs/reference/artifacts.md#文書contextglossary準備issue-11)を参照してください。PDF／EPUBとも既存unitをmarker投影し、見出し／EPUB resourceごとの収集→文書全体の整理という1方式を使います。CLI・全文翻訳・汎用方式plugin・依存は追加しません。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_preparation.py tests/test_units.py tests/test_inference.py tests/test_source_snapshot.py
```

`tests/test_preparation.py`の自作PDF原文snapshotと実EPUB readerからの人工原文、`ai/tests/preparation-process.mjs`のnetwork拒否下の公開`fauxProvider`＋実`runHelper`を使います。内部`generate`／validator／保存helperをmockしません。保存故障注入はOSの`replace`境界だけです。faux processも通常のBiome／oxlint検査対象です。

- 公開prepare/save/loadの観測で利用者terms優先、複合語・原表記、本文根拠なし候補の非採用、単一policy由来style、固定glossary/contextの保存を確認します。
- 節単位の実送信と整理入力、語がなくても節contextが整理へ届くこと、PDF／EPUB両方のblock／inline code・数式・画像・XHTML全文の非送信、snapshot-boundな用例／出典を検査します。
- 未知unit／捏造引用・根拠なし語の採用、schema不正を拒否し、失敗のraw回答／usage記録と旧版保持、retryの全attempt（失敗分も含む）を確認します。
- 同じ固定版の無送信再利用、準備方式metadataだけの変更で固定翻訳入力hashが変わらないこと、policy／利用者terms改版、全artifactのhash不正、hashを再計算した捏造根拠・採用内容差替え、採用参照replace失敗、writer排他を検査します。

実LLM・実credential・model重み・既存書籍を使いません。この機械検証は候補網羅性・定義の意味や訳語品質の確認ではなく、全文翻訳snapshot／CLI統合の成立も示しません。実送信や取得は別途承認が必要です。

### 翻訳・採用snapshotの検証境界

Issue #12は`doc_translate.translation.translate_document`→`load_translation`の公開seamです。API、回答schema、部分失敗・分割履歴・再利用と保存形は[artifact契約](docs/reference/artifacts.md#翻訳採用snapshotissue-12)を参照してください。保存済みPDF型snapshotと実EPUB readerからの自作原文、固定準備版を使い、`ai/tests/translation-process.mjs`のnetwork拒否下の実`runHelper`／公開faux provider（取消は公開stream provider）を通します。内部generate／validator／saveはmockせず、保存故障注入はOSのreplace境界だけです。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_translation.py tests/test_preparation.py tests/test_units.py tests/test_inference.py
```

- 既定約12,000原文文字・concurrency 2、章節境界、ID全種の不正／空訳／marker inventory・構造の拒否、原位置付き要確認と品質未承認を検査します。
- lengthと公開input上限によるchunk縮小／lossless unit分割・不可分spanの失敗、正常採用を残した部分失敗／再開、有限retryと失敗usage／不明usage生値の保持を検査します。
- marker入力が同じcode変更・準備metadataだけの変更の無送信再利用、原文／context／glossary／policy／実設定改版、artifact破損・再hashした不正attempt、artifact先／採用参照最後のreplace故障と旧版保持、writer排他を検査します。
- main-thread SIGINT／SIGTERMを実processへ送り、2つのchildが取消・usage保持・drain／回収を終えるまで戻らず、未開始chunkを新規送信しないことを検査します。

CLI・renderer・汎用復旧管理・依存は追加しません。実credential・実provider通信・実LLM／意味品質・既存書籍は未検証です。Node fixtureも通常のBiome／type-aware oxlint／Vitest対象で、helper source変更がないためこの変更にbuildは不要です。

## Markdown／対応表／QA・render/statusの検証境界

Issue #13は保存した正本→`doc_translate.render.render_document(workspace)`と実CLI `render DIR`／`status DIR`が公開seamです。`render.py`が出力とQAを、`render_assets.py`が元archive／PNGの検査と安全な表示assetを担います。依存・Node source・取得／翻訳APIは変更しません。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_render.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check  # PDF extra付き環境。未導入なら解決できないimportを区別して報告
.venv/bin/pytest
```

自作source／EPUB・固定した人工訳文を使い、fence内backtick・callout・list・空cell／span・MathML・PNG／crop・notes／anchors・危険markup／外部参照・元text不変・部分／split訳・stale／hash／atomic保存・writer排他を検査します。訳文は既存の実helper／公開faux providerを通し、private collaboratorをmockしません。CLIのcore-only条件は空PATHで検査します。機械検査は意味品質の承認ではなく、viewer実表示は統合受入れまで未確認です。

LLMもNodeも使わない直接CLI確認用の自作fixture（原資料を取得した実績ではありません）：

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
.venv/bin/doc-translate render "$WORKSPACE"  # exit 1: 訳文なしの部分出力、JSONを返す
.venv/bin/doc-translate status "$WORKSPACE"  # exit 0: render_current=true、QA／出力pathを返す
```

日本語側に原文＋`※`、英語側に原文、4-backtickのcode fence、数式、code原本欠損のQAが残ります。source mapから元ページ2へ戻れますが、PDF全文とは表示しません。訳文／分割／失敗usageを含む検証は`tests/test_render.py`の既存prepare/translate公開API＋faux fixtureを参照してください。実LLM・実credential・model取得・書籍・viewer・remote CIは使いません。

## 新規runの検証境界

Issue #14のseamは公開`cli.main(argv, process_command=...)`→workspace／Markdown／statusです。新しいworkflowは既存の取得・補正・準備・翻訳・render APIをつなぐだけで、内部generate／workflowはmockしません。`tests/test_run.py`は実EPUB ZIPと、`tests/test_pdf.py`のDocling公開converter／page backend・人工local model fixtureを使います。`ai/tests/run-process.mjs`は実`runHelper`と公開faux providerを接続し、network transportを拒否、credential directoryは一時pathのみです。helper sourceを変えないのでbuildは不要です。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_run.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check
.venv/bin/pytest
pnpm --dir ai format
pnpm --dir ai lint  # oxlint --type-aware --type-check --deny-warnings
pnpm --dir ai test
```

PDF／EPUBの出力到達、所有5ページ分割／length縮小、許可範囲だけの隣接context・離れた参照範囲の無送信、全ページ指定、指定glossary terms優先、要確認と部分失敗の区別、補正成功保持／sourceなし／準備・翻訳失敗時の部分出力、既存空directory・file・symlinkの拒否、未知modelの無送信／no fallback、実認証metadataの送信前flushを検査します。partial補正の意図範囲／成功／失敗はrunと後続statusの両方で確認します。既存workspaceのrender/statusと同じく、保存-only操作は空PATHでも動きます。

実LLM・実credential・model取得・書籍を使わない、公開CLI直接確認（repository root、開発依存導入済み）：

```sh
RUN_FIXTURE=$(mktemp -d)
export RUN_FIXTURE HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PYTHONPATH=tests .venv/bin/python - <<'PY_RUN'
import json, os, shutil
from pathlib import Path
from test_epub import make_epub
from doc_translate.cli import main
root = Path(os.environ["RUN_FIXTURE"])
source = make_epub(root)  # 自作mixed-inline EPUB、既存書籍ではない
settings = root / "faux.json"
settings.write_text(json.dumps({}))
main(["run", str(source), "--workspace", str(root / "new"),
      "--provider", "faux", "--model", "text", "--thinking", "off"],
     process_command=[shutil.which("node"), str(Path("ai/tests/run-process.mjs").resolve()),
                      str(root / "credentials"), str(settings), str(root / "requests.jsonl")])
PY_RUN
.venv/bin/doc-translate status "$RUN_FIXTURE/new"  # exit0、completed／render_current=true
.venv/bin/doc-translate render "$RUN_FIXTURE/new"  # exit0、LLMなし
# 同じworkspaceへの新規runはexit2（setup／実認証／推論を開始しない）
.venv/bin/doc-translate run "$RUN_FIXTURE/book.epub" --workspace "$RUN_FIXTURE/new" \
  --provider faux --model text --thinking off
```

最初のrunはstderrへprepare×2／translation×1の送信前通知、stdoutへsummaryを表示します。`artifacts`から日本語側の`訳:Original`とinline保持、英語原文、source map、QA／usageを確認できます。PDFのsynthetic直接確認は`.venv/bin/pytest -s tests/test_run.py -k 'owns_requested or partial_pdf or without_pages'`で同じ公開CLIを使い、置き換えるのはDocling外部APIとfaux providerだけです。fixtureの取得成功は実PDFの完全抽出や実LLM意味品質・viewer表示の保証ではありません。汎用resume／revise／review・remote CIの実行はこのIssueでは行いません。

## wheelの明示buildと導入検査

```sh
pnpm --dir ai build
uv build
uv run python scripts/check_distribution.py dist/doc_translate-0.1.0-py3-none-any.whl
```

この検査は一時directoryで再compileして`ai/dist`と比較し、wheel内のdist／manifest／両lockをbyte比較します。core-onlyの別venvへwheelをinstallし、workspace外の別々のclean data directoryで実CLI `setup-ai --package-manager MANAGER`を実行します（依存packageのregistry取得あり）。内部で使用する固定コマンドは次のとおりです。

- npm：`ci --omit=dev --ignore-scripts --no-audit --no-fund`
- pnpm：`install --prod --frozen-lockfile --ignore-scripts`

続くoffline smokeはNodeだけをPATHに置き、source treeのnode_modules／Pi CLI／managerなしで実CLI `providers`／`models --provider anthropic|google`、公開codecのUTF-8分割byte round-trip・末尾不正の拒否とpi-ai／proper-lockfileのimportを確認します。認証・refresh・推論はしません。Vitestの実helper processはnetwork transportを拒否した状態でもcatalogを参照します。pytestは公開CLI→runtimeとPython↔実Node helper境界を検査し、package manager／失敗process境界だけをfake化します。依存不足を自動取得するruntime hookはありません。wheelにはhelper sourceやnode_modulesを含めません。sdistは明示build済みdistと再build用source／manifest／lockを含みます。

Python側の別環境確認（repository rootで実行、`TMP`は新規一時directory）：

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

core-onlyでEPUB用位置のsnapshot保存・読込みと、`read_epub(Path)`による安全読込み・package／spine／fallback／URI解決をpytestで確認しています。`uv run pytest tests/test_epub.py`は人工ZIPでpath・ZIP／XML／fallback上限・entity／DOCTYPE・固定layout・暗号化／font難読化・対象外本文の受理／拒否を検証します。namespace展開名の長さ／累積量・単一tagの属性数、深いpathと祖先衝突の区切り、申告size＋prefix CRCによるstored／deflate切捨て・deflate終端・data descriptor／空member、複数font／manifest alias／暗号化recordも公開`read_epub`経由で検査します。性能回帰は固定の時間閾値やprivate helper mockを使わず、祖先prefix再構築と暗号化recordごとのmanifest全走査を避ける実装と公開受理／拒否の回帰で確認します。`extract_epub_source(book, *, document, snapshot_id)`は受理済みreader結果からresource大のsection blockと階層を持つflat node table（ordered child位置参照）を生成します。XML最大深さ128でも共通JSON契約の再帰上限を超えずsave/loadできます。元archive hash／artifactはcallerが明示供給する真実で、adapterは原資料コピー・hash照合・workspace書込みをしません。spine順／non-linear／alias・fallback provenance、off-spine notes／linkの発見順と循環回避、mixed text／child／tail、見出し・list／table span・caption／alt・code空白、実在IDとsnapshot-bound node位置を保持します。単数／複数のnotes roleを子孫へ継承し、同じnodeのtocを優先します。参照先と共有duplicate候補のlocationは元IDを複製せずsnapshot-bound座標で保持し、長い元IDが繰返しincoming linkごとに増幅しないことを公開save/loadで検査します。全訪問elementのstructured／excluded／inert処置とcoverage、duplicate／missing fragment・非XHTML／active assetの原資料参照と要確認を残します。treeは表示用markupではなく、raw MathML／SVG／script／CSSは不活性な元resourceで保持します。`uv run pytest tests/test_epub_source.py tests/test_source_snapshot.py`で人工EPUB 2／3のliteral比較と既存のatomic save/loadを検査できます。翻訳unit生成は上記の共通APIで行い、このEPUB抽出API自身にはrenderer・CLI接続を含めません。Node／Docling／OCR・外部取得は不要です。画像主体の一般判定は保証せず、本文textがなく画像のみのspineを停止する範囲です。APIと固定上限は[artifact契約](docs/reference/artifacts.md)を参照してください。PDF extraの別環境importはmodel実行ではありません。

## 認証の実装・検証境界

公開storeは`FileCredentialStore(directory?)`の`read/list/modify/delete/getDeviceId/drain`です。既定directoryは`$XDG_CONFIG_HOME/doc-translate`（未設定・空なら`~/.config/doc-translate`）。directoryは0700、credential JSON／installation UUIDは0600です。proper-lockfileの共通identityでファイル全体のread→callback→atomic保存／deleteとUUID初回作成を排他します。`modify`のundefinedは変更なし、OAuthの追加fieldは保持、`list`はprovider／typeだけです。壊れたJSON・保存失敗を空扱いしません。

login／refresh／TTL判定・認証変換はpi-ai 1.4.0の公開APIに任せます。OpenAI OAuthは安定UUIDと`agentName: "doc-translate"`を渡します。`providers[].login_methods`は公開interactive loginの有無だけを示し、ambient-onlyにlogin methodを作りません。OAuth refresh失敗後のAPI key fallback／request overrideは追加しません。

Pythonの`authenticate`はCLIから実helperへJSONLを送り、controlling TTYだけで非echo入力し、認証URL・device codeをterminalへ表示します。認証record／入力／URL・code／raw stderrやprovider例外をログへ残しません。SIGINT／SIGTERM／EOFで取消し、prompt取消・終了で入力queueをflushしてechoを復元します。進行中のstore callback／保存は取消から保護し`drain`、Pythonは強制kill前に30秒待ってchildを回収します。取消後の`storage_failed`も非秘密の保存エラーと再login案内を表示します。SIGKILL／disk障害時の保存・TTY復元は保証できません。

承認済みの公開seamでTDDを行います。`ai/tests/credentials.test.ts`は実storeと公開`createModels/createProvider/getAuth`で同時refresh・失敗時のno fallback・保存維持を検査し、Nodeの実processでも排他・heartbeat・UUID・drainを確認します。`ai/tests/auth.test.ts`は公開provider／実helper境界、`tests/test_auth_cli.py`は公開CLI・実helper／外部fake・PTYで非echo、callbackによるprompt取消、SIGINT／SIGTERM／EOF、未送信secretの破棄、取消中の保存失敗とchild回収を検査します。実credentialを読み込まず、人工credentialとnetwork拒否で検証します。実認証・実token refresh・推論・文書送信は未検証で、別途個別承認が必要です。

## 推論adapterの実装・検証境界

`doc_translate.inference.generate`は文書workflowとは独立したPython APIです。段階指定→共通指定→`PI_PROVIDER/PI_MODEL/PI_REASONING_LEVEL`で設定を解決し、文書を含まないpreflightで正確なmodel／thinking・画像能力・認証sourceを確認してから、明示policy／text／context／glossaryと必要なPNGだけを送ります。各試行は新しいNode processで、Pi CLI設定・履歴・tools・探索・agent loopを使いません。`run/resume`等のCLIは追加していません。APIの型・固定code・JSONLは[artifact契約](docs/reference/artifacts.md)を参照してください。

Pythonは回答JSONを別に解析してcallerのPydantic model（domain validatorを含む）で検証します。ID／根拠・送信許可範囲・座標説明の意味検証、artifact保存・採用は文書callerが所有します。PDFの補正chunk callerはIssue #8で追加済みですが、文書全体のsource採用や翻訳callerは後続です。画像は完全decode・実寸・hash等を検証して元byte列と順序を保持し、縮小・再encode・保存しません。公表入力上限超過は`input_limit`で停止します。`inputLimits.images.resize.maxBytes`はraw PNGではなくcanonical base64 payloadのASCII byte量に適用します。

成功にはNode terminal／stop reason・送信先・toolCallなし・Python schema検証・正常process終了がすべて必要です。workflow retryは最終`generation_failed`のlibrary heuristicによるretryableなerrorだけに初回込み最大3試行・待機3／6秒です。schema／認証／保存／timeout／取消はretryしません。libraryへ`maxRetries:0`を渡しますが、内部retryは観測できないため不明のままです。未観測usageも0ではなく不明とします。timeout／取消はchildへ伝え、stream終了を最大30秒待ち、協調的providerのterminal／最終message／usageを失敗のまま保持します。終了すれば直ちに待機を終え、credential refresh／保存のdrainも維持します。親は原則30秒のgraceでchildを回収し、非協調providerのterminal／usageは保証しません。drainで上書きされた失敗は、保持されたretry metadataだけを理由に再試行しません。

`ai/tests/generation.test.ts`と`generation-process.mjs`は公開fauxProvider／createProviderと実HTTP adapter＋injected fetchで実効入力・terminal・usage・retry設定・取消／refresh drainを検査します。`tests/test_inference.py`は実helperを使ってPython側の独立validation・domain validator・有限retry・画像保持・timeout／signal取消を検査し、`ai/tests/inference-provider.mjs`は人工認証／取消process、`tests/fixtures/inference_process.py`はprotocol故障注入だけを担当します。全Node fixtureは上記Biome／oxlintの対象です。実provider通信・実credential・実token refresh・account利用権・課金・翻訳品質・全adapterの内部retry回数は未検証です。offlineテストやwheelのimport成功をこれらの成功と扱いません。

## lock・依存更新とlicense

Pythonは`pyproject.toml`変更後に`uv lock`、Nodeは共通`ai/package.json`を変更して`pnpm --dir ai install --lockfile-only --ignore-scripts`と`npm --prefix ai install --package-lock-only --ignore-scripts --no-audit --no-fund`で両lockを更新します。依存更新はregistry取得の許可範囲内で行います。既存node_modulesへ別managerを上書きせず、上のwheel clean install検査を両managerで再実行してください。両managerの推移依存が完全一致するとは仮定しません。

現在の主依存：Pydantic／Docling／OCRMac／pi-ai／proper-lockfileはMIT、PillowはMIT-CMU。推移依存にはcertifiのMPL-2.0、Torchの複合license、Node側のMIT／Apache-2.0／BSD等があります。lockは解決versionの記録でありlicense承認の代替ではありません。今回のnative tool確認はmacOS arm64に限定しています。更新時は実際の配布物のlicense／NOTICEとnative条件を再確認します。Apple VisionのOS条件やmodel重みをpackageのMITに含めません。Heron重みはApache-2.0、TableFormer重みはCDLA-Permissive-2.0です。Issue #7の実動検証は承認済みの既存local cacheを使い、新規取得・同梱はしていません。project全体のlicense宣言・releaseは今回の対象外です。

## fixture・CIの境界

fixtureは自作の小文書／人工応答だけを使い、書籍の切出し、実credential、token、重みを追加しません。`sample/`はローカル資料としてignoreします。テストはofflineで、依存installだけをネットワーク処理と区別します。Hub／Transformersのoffline設定は他の全通信まで遮断するfirewallではありません。実provider送信・大型取得は別途個別承認が必要です。

CIはpush／pull_requestで上記checksとwheel導入を実行する設定です。secrets、pull_request_target、continue-on-errorは使いません。今回および後続Work Itemではremote CIの実行・成功確認・実行ref／run URLの取得・記録を受け入れ条件にせず、そのためのcommit／pushも必須ではありません。既存CI設定・smoke検査と、各Work Itemの責務に対応するローカル正式チェック・build／別環境導入検証は維持します。remote CIの未実行だけでは未完了・blockedにせず、未実行をremote成功とも表示しません。§18の過去実験はpi-ai直接経路の検証実績ではありません。

## resume／障害復旧の検証境界（Issue #15）

`tests/test_resume.py`は公開CLI→workspace／status／出力と実Node `runHelper`＋公開faux providerで検査します。`tests/fixtures/resume_process.py`は実Python CLI入口で、試験専用に**OS `replace`だけ**をbefore／afterで停止できます。productionに故障注入flagはありません。source／準備／翻訳pointerの両側でSIGINTとSIGKILLを送り、旧原本・snapshot・出力hash不変、accepted request再送なし、未受理応答の再送とusage不明／重複課金開示、API失敗、hash破損の事前拒否、interstageを含むwriter排他、保存設定の優先順位を確認します。`tests/test_run.py`にPDF部分所有回復、chunk確定後・進捗replace前の強制kill、未完了draftの再取得、glossaryによる取得／補正非無効化を含めます。PDF fixtureの外部公開変換境界以外にworkflowのmockは使いません。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_resume.py tests/test_run.py tests/test_preparation.py tests/test_pdf_correction.py tests/test_translation.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check
pnpm --dir ai format
pnpm --dir ai lint
pnpm --dir ai test
```

pytestとは別の直接entry再現例です。model／credential／networkは使わず、`process_command`を実Node helper／public fauxへ明示します。`BOUNDARY=source-snapshot.json|preparation-snapshot.json|translation-snapshot.json`、`EDGE=before|after`、`STOP_SIGNAL=SIGINT|SIGKILL`で各境界を選べます。SIGINTでは正常取消drainを待ち、SIGKILLではOS lock自動解放と保存済みartifactの再検証だけを確認します。OAuth refreshの強制kill安全性や実請求・意味品質は証明しません。

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
initial = subprocess.run([*entry, '--', 'run', str(source), '--workspace', str(workspace),
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
resumed = subprocess.run([*entry, '--', 'resume', str(workspace)], capture_output=True, text=True, check=True)
summary = json.loads(resumed.stdout)
assert summary['processing']['status'] == 'completed'
assert len((work / 'requests.jsonl').read_text().splitlines()) == 5
assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in old.items())
status = subprocess.run([str(Path(sys.executable).with_name('doc-translate')), 'status', str(workspace)],
                        capture_output=True, text=True, check=True)
assert json.loads(status.stdout)['render_current'] is True
print(work, 'completed; 5 requests; old hashes preserved; saved status current')
PY
```

残る一時fileは採用対象でなく、試験workspaceも自動削除しません。実provider／実認証・OCRの新たな実行やmodel取得はこの検証に含めません。
