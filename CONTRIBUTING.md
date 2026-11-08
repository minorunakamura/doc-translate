# 開発ガイド

PDF／EPUBの取得から準備・翻訳・出力、resume／revise／局所reviewまで実装済みです。以下に構成、正式チェック、各公開境界の再現手順と限界をまとめます。Issue番号付きの節は各実装時の検証範囲であり、後続機能の未実装や統合受入れ完了を意味しません。実認証・実LLM・viewer表示は自作小入力で限定確認済みです。実token refreshは未観測で、意味品質やIssue全体の承認完了とは区別します。

全体の計画は[正式Plan](docs/plans/doc-translate-initial.md)、責務は[基本設計](docs/design/doc-translate.md)、現在の公開契約は[artifact契約](docs/reference/artifacts.md)を参照してください。READMEは利用者向けです。

## 統合受入れの限定確認（Issue #18）

[Issue #18](https://github.com/minorunakamura/doc-translate/issues/18)の2026-10-28時点の実動・表示記録です。正式PlanのSHA-256は`32443e61e096e44f655014e4433a2db087fb23fa49babd381f4a9f5a0279982e`。本節は限定実動・表示の記録であり、独立ReviewやCode Review gateの判定・最終承認を代用しません。基本設計§18の過去のPi実験とも別の記録です。

### 入力・認証・再現条件

入力・hash・所有／参照範囲・provider／model／thinking・OAuth認証sourceを個別提示し、ユーザーの明示承認後に実行しました。すべて自作で、既存書籍・許可外文書・外部referenceは使っていません。

| 入力 | SHA-256 | 対象 |
| --- | --- | --- |
| `queue-journal-electronic.pdf` | `655f5ed52ae969151b101ebd71ddc61303267e9e8f3342baa8a087446e1b9756` | 2ページ、所有1–2／参照なし。元textは988／1046文字 |
| `queue-journal-image-only.pdf` | `262c466841879b9baf2e6aff6b06418dfb62f7632f6c94d62e1416e9552dd6a8` | 2ページ、所有1–2／参照なし。元textは0／0文字 |
| `queue-journal.epub` | `79303b621458296c43dd7eebfefa2856a61bb6b2a78bb734706d61d3986381a0` | 全体、3 XHTML・34 unit |

- 実行環境は下記「構成・環境」のmacOS arm64／Python 3.14.9／Node 24.22.1。PDFはDocling 2.142.1／core 2.104.0／OCRMac 1.0.1、CPU／Apple Vision `en-US`・`vision`・`accurate`。既存local cacheのHeron `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`／TableFormer `fc0f2d45e2218ea24bce5045f58a389aed16dc23`を固定し、Hub／Transformers offlineで新規重み取得なし。
- 全段階は`openai/gpt-6.1-sol`・`high`。ユーザー手元の専用OAuth loginが成功し、Rootのpreflightと全45 attemptで`auth.source='OAuth'`／`type='oauth'`を確認。API key環境変数を除外し、別認証へのfallbackなし。Pi CLI／Pi authの読込み・コピーなし。実run／resumeのPATHはNodeと`/usr/bin:/bin`のみで、Pi／managerなし。
- ユーザー追加承認でOAuth URL／codeの人間向けTTY表示とmacOS既定browser自動起動を追加しました（HTTP(S)のみ、起動失敗時はTTY手動案内、秘密非log、取消維持）。Rootが公開CLI→PTY→人工通知→外部fake `open` argvで表示・起動呼出し・秘密stdoutなしを確認しました。実login成功はauto-open追加前で、実OAuthページでのauto-open再loginとforced token refreshは未確認です。期限改変等による強制試験はしていません。

### 実動結果・標本照合・限界

初回は電子版の図所属ref不足による`answer_invalid`、画像版／EPUBの準備literal根拠拒否とpromptの日本語target欠落を観測しました。旧回答を保持し、validation・schema・retryを緩めずpromptだけを最小補足。各workspaceを1回resumeし、すべてexit 0・`completed`、`not_processed`／`failed`は空になりました。

| workspace | 出力到達unit | 補正attempt | 準備attempt | 翻訳attempt | review attempt | QA要確認件数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| electronic | 32 | 2 | 6 | 5 | 0 | 11 |
| image-only | 32 | 1 | 8 | 5 | 0 | 9 |
| epub | 34 | 0 | 11 | 7 | 0 | 3 |

画像版の成功済み補正は再開で再送されませんでした。失敗分を含む全45 attemptの参考catalog価格合算は**$0.699300**です（reasoningの二重加算なし）。実請求・subscription残枠・親Agent作業費ではありません。

Root assistantが原資料→採用source→日本語／英語Markdownを標本照合しました。codeの4行・4 space・操作順と末尾`write_record(`の欠損非補完、literal `queue_id`／URL、colspan 2／rowspan 2／空cell、図内部Queue／Journal／Readerの本文非重複とcaption別訳、list 3階層、EPUBのlink／noteref／backlink／em／strong／codeを確認。公開`load_preparation`で全evidence対応を検証し、journal／commit point／checksum／backpressureの本文根拠と日本語glossary・ですます調を標本確認しました。commit pointは電子版「コミット点」、他2件「コミットポイント」のように文書別固定で、3入力間の訳語完全一致は保証しません。

表示はPandoc 3.7.0.1（`gfm+tex_math_dollars`→`html5 --mathml`）＋Google Chrome 156.0.8078.48 headless、1100×4200で確認。HTML有効・ローカルCSS／相対assets、CSP `default-src 'none'`、remote解決遮断・背景通信disableの条件で生成HTMLと3 PNGをRootが直接readし、結合表・図・code・list・`※`を確認しました。PDF数式はPandoc変換のMathMLで表示。EPUBの`math id='rate'`は現rendererの許可属性外で不活性source dataへfallbackし、raw MathMLの`mfrac`、原文位置・QA・source data linkの実在を確認しました。表示のために安全判定は緩めていません。

残る観測・未確認範囲：

- Docling charspan不正はclipせずQAに保持。EPUBのblock単位uncertaintyは多くのunitへ広く`※`が付きます。PDFのa／b sublistはbullet表示ですが親子3階層は保持しました。
- closing argumentを「議論／主張」とする不自然さも観測しました（人工原文自体に多義性あり）。原文／訳文を無断修正していません。
- これはRoot assistantの標本確認で、人間の全文査読・意味品質全体の承認ではありません。QAの`meaning_quality_approved=false`／unreviewedを維持し、外部viewer確認を保存済みQAの`viewer_display_checked`へ書き戻していません。実LLMの局所`review`精度、任意文書／実スキャン、Marked等の他viewer、他OS、統計的品質は未検証です。

### ローカル正式チェック・保存根拠

Rootの最新チェックは別core環境の`uv sync --locked --dev`と全pytest **762 passed／115 skipped**（Doclingなし）、PDF extra環境の全pytest **877 passed**、Vitest **151 passed／5 files**。Biome 26 files、oxlint（type-aware／type-check／deny-warnings）、Ruff 53 files・format、tyはすべてexit 0・lint警告なし。helper明示build、`uv build`、`scripts/check_distribution.py`全文もexit 0で、resource鮮度、別core wheelのEPUB reader→source→units→空PATH render/status、両managerのclean setupとNode-only catalog／codecを確認しました。別core／PDF wheel install・importに加え、最新build wheelを別PDF extra venvへinstallし、Docling／OCR／application importもexit 0で再確認しました（ログ：`/tmp/doc-translate-issue18-checks.8dhrqb/final-wheel-pdf.log`）。pnpm更新案内と既存TorchのmacOS redirect NOTEは非致命メッセージです。remote CIは不要・未実行です。再現用の開発コマンドは本書の「正式チェック」「wheelの明示buildと導入検査」を使い、実送信・重み取得は新たな個別承認なしに行いません。

恒久保存先の3 workspaceでも、PATH空の実CLI `status`→`render`→`resume`→`status`はすべてexit 0・completedでした。`originals`／`snapshots`／`attempts`／`drafts`／`corrections`の旧hashはすべて不変、attempt増加は0です。`content_review.meaning_quality_approved`／`viewer_display_checked`は書き換えず、Rootの外部標本・表示確認と区別しています。

保存先は`/Users/minoru/.agent/experiments/doc-translate/issue18-20261028-xrqkk55t/`。`inputs/`、`workspaces/{electronic,image-only,epub}/`、workspace名の`*.stdout.json`／`*.stderr.log`と`*.resume.stdout.json`／`*.resume.stderr.log`、`viewer/{name}/index.html`と`viewer/{name}.png`、`checks/`のログを参照できます。入力とworkspaceは元`/tmp`からbyte hash全一致でコピーし、原本`/tmp`も保持、credential／model重みはコピーしていません。one-off作成記録中の旧絶対pathは過去記録であり、再実行指示ではありません。ローカル保存先の恒久可用性は保証しません。

## 構成・環境

- `src/doc_translate/`：PythonのCLI、アプリ所有Node runtime導入／検査、TTY認証UI（`auth.py`）、推論adapter（`inference.py`）、EPUB安全reader（`epub.py`）と原文抽出adapter（`epub_source.py`）、PDF取得adapter（`pdf.py`）と補正chunk境界（`pdf_correction.py`）・原文組立adapter（`pdf_source.py`）、共通unit生成・inline保護・workflow分割（`units.py`）、固定context／glossary準備と根拠・実行記録（`preparation.py`）、翻訳chunk・検証・部分採用と再利用（`translation.py`）、原文snapshot、基点付きJSON修正と候補採用（`revise.py`）、明示範囲の独立内容照合とhash付き不変履歴（`review.py`）、workspace OS lock、JSONL codec。
- `ai/src/`：TypeScriptのJSONL codec、非秘密metadata／認証／generation helper（`dist/main.js`）、`AuthDialog`、`FileCredentialStore`、独立推論のpreflight／stream接続（`generation.ts`）。`ai/tests/`の`.mjs`は公開APIとcompile済みhelperを使うテスト専用processです。
- `tests/fixtures/`：自作JSONと外部認証fake／PTY process、推論protocolの故障注入proxy。EPUBの人工ZIPは`tests/test_epub.py`で生成し、`tests/test_epub_source.py`でreader→原文snapshot抽出→公開save/loadのround-tripを検査します。`tests/test_pdf.py`はDoclingの公開変換／page backend境界の人工fixtureから、raw／画像／設定の保存・load検証を行います。`tests/test_pdf_correction.py`は人工draft→実Node helper／faux provider→検証済み所有chunkの公開save/loadを検査します。`tests/test_preparation.py`は保存原文→実helper／faux→固定準備artifactの公開prepare/loadを検査します。`tests/test_translation.py`は保存原文／固定準備→実helper／faux→翻訳の公開translate/loadを検査します。`tests/test_render.py`は正本→Markdown／対応表／QAと実CLI render/statusを検査します。`tests/test_run.py`はPDF／EPUBの公開CLIからworkspace・出力・statusまでを検査します。新規`run`／`resume`／`revise`／`review`は実装済みです。`tests/test_review.py`はCLI→実helper／faux→局所findings保存→保存-only QA/statusのseamを検査します。`tests/test_revise.py`はsource全置換・訳文patchとpolicy改版、`tests/test_pdf_candidate_acceptance.py`は実CLIから候補比較・採用・後続更新を検査します。
- `scripts/check_distribution.py`：sourceと明示compile済みresource／wheelの一致、およびcore-only別venvの実EPUB取得→原文／unit→保存-only CLI出力・status、wheel CLIからのclean npm／pnpm setupとNode-only catalog smokeを検査します。runtime導入は公開`setup-ai`自身が行います。

今回のローカル環境：macOS 26.7.1 arm64、Python 3.14.9、Node 24.22.1、pnpm 12.10.1、npm 11.19.1、uv 0.13.5。以前の基準Node 24.21.0／npm 11.19.0とは異なります。CI設定はGitHub-hosted macos-15 ARM64、Python 3.14／Node 24.21.0／npm 11.19.0で、今回のためにtoolやlockを変更しません。別OSやmacOSの最低versionは未保証です。

## 正式チェック（順序を含む）

以下はfresh checkout／全件phaseの再現順序です。CIはcore全pytest、PDF4ファイル、全Node、品質、build／配布検査の現行契約を維持します。ローカルの変更ごとの正式実行範囲は次項で選び、必要なphase内の順序・失敗時停止は維持します。限定実行を理由にCIの対象を減らしません。

必要なtoolは事前に導入します。以下の依存installだけはregistry接続を使います。model重み・認証・LLMは不要です。install／syncは毎編集の義務ではなく、環境不足・lock変更・同梱resourceの鮮度から必要性と許可を先に確認します。既存`.venv`を無断でcore／PDFに切り替えず、同期済みの別環境を使えます。

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
uv run python -c 'import importlib.util; assert importlib.util.find_spec("docling") is None'
uv run pytest
uv run doc-translate --help
```

Python packageは`ai/dist`を同梱するため、fresh checkoutでは`uv sync`より先にhelperの明示buildが必要です。既存editable環境で同梱resourceの更新が必要なら、許可確認後にbuildと`uv sync --locked --dev --reinstall-package doc-translate`を行います（PDF環境は`--extra pdf`も保持）。pytestのprocess契約もcompile済み公開`@doc-translate/ai/jsonl`を使います。Python側はbinary file行iterator、Node側は任意binary chunkのcodecです。警告をquiet／包括的disableで隠さず修正します。

`ai/tsconfig.build.json`の`skipLibCheck`はpi-ai 1.4.0配布declarationのJSON import属性欠落と、@google/genaiのoptional MCP SDK型参照不足に限定した互換性対応です。依存declaration自体の型検査は省きますが、自作sourceの型検査とoxlintのtype-aware／type-check／warning 0は維持します。実helperと両managerのclean installでruntime互換性を別途検査します。

上記はcore-onlyのruntime／テスト検証です。全Python sourceの型検査は、PDF adapterとテストのimportも解決できるPDF extra付き環境で行います。

```sh
uv sync --locked --dev --extra pdf
uv run --locked --extra pdf ty check
uv run --locked --extra pdf python -c 'from docling.document_converter import DocumentConverter; from docling.datamodel.pipeline_options import OcrMacOptions; from docling_core.types.doc import DoclingDocument; from ocrmac import ocrmac; print(OcrMacOptions(lang=["en-US"], framework="vision", recognition="accurate"))'
uv run --locked --extra pdf pytest tests/test_pdf.py tests/test_run.py tests/test_review.py tests/test_pdf_candidate_acceptance.py
```

共通テストの全件phaseはcore-onlyで実行し、CIのPDF extra付き検査はDocling依存のテストを含む上記4ファイルに限定しています。coreとPDFの重なりはoptional依存の有無を検査する異なる条件であり、単純な重複として除きません。統合受入れでPDF付き全pytestも要求する既存Approved Planは維持します。PDF依存が必要なテストを別ファイルに追加する場合は、CIとこの手順の実行対象にも追加してください。

extraを保持するには`uv run --locked --extra pdf ...`（pytestは`uv run --locked --extra pdf pytest`を推奨）、またはsync済みの`.venv/bin/pytest`／`.venv/bin/ruff`／`.venv/bin/ty`を使い、`.venv/bin/pytest`を直接使う場合はvenvのactivateまたは`.venv/bin`のPATH追加が必要です。これは依存解決・importと人工fixture検証であり、それだけを実Docling変換、Apple Vision OCR、重みを使うlayout／table処理の成功と表示しません。

### 変更影響から正式実行範囲を選ぶ

Rootは毎回、次の順で選定します。test／doc／productionというファイル種別だけでは限定可否を決めません。局所production変更も、同じ根拠で影響を閉じて説明できれば限定できます。

1. 要求・維持する保証と、変わる動作／前提を差分から特定します。公開CLI／API／保存物のどこで観測するかを決めます。
2. 変更した定義の全callerとimportを検索し、関数内import、`pytest_plugins`、fixtureのscopeと全consumerまで辿ります。Python→実Node helper／JSONL→Pythonの接続、compile済みresource、`pyproject.toml`の`testpaths`、収集設定とCIの実行集合も確認します。単に同じdirectoryにあるか、module名にPDFを含むかでは判定しません。
3. 影響が閉じるなら、変更ケースのfocused実行、影響module全体と必要なconsumer、品質チェック、保証に対応する実入口の直接確認をRoot自身が行います。新／変更fixtureは代表consumer単独のfresh processとmodule実行の両方で成立を確認します。収集node ID／parameter／skip理由で、限定した結果に対象漏れがないか照合します。
4. shared fixture／helper、共通schema／hash／保存／lock、JSONL／helper接続、工程間の再利用／状態遷移、依存／lock／build／配布、収集／CI／検証規約の変更は広い影響を前提に、関連する全件phaseへ戻します。影響不明・consumerを列挙できない場合も同じです。狭くできる根拠がなければ省略しません。
5. 選んだcommandと選択／除外理由、保証、環境／build鮮度、skip理由、期待／観測／未確認を既存の結果報告へ残します。自動selector、新runner、新しい台帳は作りません。

Python変更時のRuff check／formatは上記の安価な全体コマンドを維持し、型検査は既存PDF extra環境で行います。Node変更時は既存Biome／type-aware oxlintを維持します。局所Node test本体なら関連Vitestを選べますが、shared helper／source／依存変更はVitest全件へ戻します。helper source変更時は明示buildとPython同梱resourceの鮮度確認も必要です。build／配布に影響するときは下のwheel検査も対象にし、必要なinstallの許可不足は未確認のまま成功とせずBLOCKEDとします。

#### 現在の依存に基づく選定例

以下は実行範囲を決める例であり、架空のproduction変更を実装する指示ではありません。既存PDF extra環境とbuild済みhelperを使い、実際の差分で依存を再検索します。

- **閉じたEPUB test本体だけ**：例えば`test_revise.py`のpatch拒否case内だけなら、そのcaseと`pytest tests/test_revise.py`、品質と実CLI reviseの対応する受理／拒否を確認します。`malformed_source_seed`／workspaceはmodule-localで、malformed source拒否・patch拒否・QA target・移設checkがconsumerです。`pointer`等のexportやfixtureを変えていなければ、`pointer`をimportする`test_run.py`／`test_pdf_candidate_acceptance.py`をそれだけで再実行せず、PDF／全Nodeも非変更のため除外できます。reviewの局所test本体なら`pytest tests/test_review.py`を選びます。同moduleはPDF caseもあるので、EPUB変更という理由でPDFなしのskipだけを成功にしません。
- **閉じたPDF test本体だけ**：table-cell／list-depth case内だけなら該当caseと`pytest tests/test_pdf_candidate_acceptance.py`、品質と実CLI reviseの受理／拒否を確認します。`pdf_evidence_seed`／workspaceはmodule-localで、この22件と移設checkだけがconsumerです。`pytest_plugins = ["test_run"]`、`test_revise.pointer`、`test_run.helper/run_args`等のimportは前提として必要ですが、import元が不変なら元moduleの全testを自動的に追加する必要はありません。非変更のcore全件／PDF4全体／全Nodeは除外できます。ただし同moduleの`start_pdf`は`test_review.py`内でもimportされるため、それを変えた場合は局所test本体の例には該当しません。
- **局所seedと共有helperを区別**：`review_scope_seed`／workspaceは`test_review.py`内のscope拒否・empty／rejected answers・移設check専用です。一方で同moduleの`pytest_plugins`は`test_pdf`／`test_pdf_correction`／`test_run`を読み、`test_run.helper/run_args`もimportします。`test_run`自身も`test_pdf` pluginを読みます。`test_run.helper/run_args`には`test_revise`／`test_resume`／`test_cli_output`／`test_pdf_candidate_acceptance`等のconsumerもあり、共有helperやconverter fixtureの変更を「1ファイルだけ」として限定しません。module-local seed変更でも全consumerと移設／分離checkを含め、共有前提や保存不変条件へ影響したら全件へ戻します。
- **共有保存／fixture・影響不明**：共通snapshot保存／hash／lockや上記共有fixture／helperの変更は、core全pytestとPDF4ファイルなど関連full phasesで確認します。Node接続へ及ぶなら全VitestとNode品質も含め、helper sourceならbuild／resource鮮度も確認します。収集／CI／検証規約の変更である今回（Issue #43）は、この限定方針で自身の全体確認を省略せず、Rootがcore全pytest、PDF4ファイル、全Vitest、既存品質／CLI help／PDF公開importを実行します。CIのbuild／配布契約は変更しません。

### 検証の分担・再実行・証拠

準備／翻訳／render／run／review／revise／resume各節のコマンドは境界ごとの再現入口です。全pytestやNode品質／testを併記した過去の例も、同じ担当への毎回の全コマンド実行義務ではありません。上の手順で必要な範囲を選び、既存Approved Planが追加で要求する検証は勝手に省略しません。

- **Worker**：変更consumerの代表単独とmodule実行、関連公開seamのfocused regression、必要な品質確認を担当し、保証・前提・除いた工程・残した実経路とcommand／exit／未確認を報告します。文書改訂では選定根拠・更新commandの関係確認とfocused検証を担当し、Rootと同じ全件一式を機械的に反復しません。
- **Root**：現在差分を直接読み、選んだ正式チェックと実入口の直接確認を自ら実行します。既存環境／helper build鮮度を確認し、Workerの成功で代替しません。今回の最終受入れは上記全件一式に加え、pytestとは別に人工EPUB／PDFの実生成→元root退避→移設コピーの公開load／seed byte不変・非伝播、不正reviseのexit2／byte・request不変、正常採用／旧版保持、review範囲拒否／無送信と正常選択request／品質未承認を確認します。PDFの外部converter／page-backend fixtureと実Node／fauxを使い、全parameterを直接確認で重複再生する必要はありません。
- **Reviewer**：差分と正式Plan、Rootの正式／直接結果、raw時間、選定根拠、未確認／cleanupから、不足する具体的保証だけを独立検証します。担当交代だけで同じ差分・条件の全suiteを反復しません。修正後はRootが影響した保証・正式チェック・直接確認を再実行し、共有部分なら関連full phasesへ戻します。計測へ影響する差分が変わればその部分も再計測します。

### 保証・境界を決めてからTDDを選ぶ

要求／保証→公開境界→必要な整合前提→検査する最小経路の順で決めた後、対象ごとにTDD採否を判断します。今回のfixture Refactoring・収集漏れ修正・規約更新は非TDDで、既存テストと最小のcharacterization checkで等価性を確認します。機能／不具合修正も一律の種別指定でTDDへ送らず、採用する対象で既存TDD手順を使います。過去のTDD実装を理由に既存テストや歴史的Approved Planを削除せず、既存Planに明示されたTDDを解除する必要があれば再計画します。

### 前提共有・比較・cleanup

前提再利用は同じ入力・同じ整合条件の今回の実生成物をinvocation内で作り、全file（原本／draft／画像／artifact／evidence／attempt／履歴／pointer／run／render等）と初期request logを独立コピーした場合に限定します。既存のmodule-local seed／専用consumerを使い、session共通fixtureへ昇格しません。初期logのbyte一致、公開loadの整合、元root退避中の移設load、seed／他コピーへの変更非伝播とseed byte不変を検査します。別入力へのhashの後付け、永続cache、hardlink、generate／validator／saveのmockは使いません。実準備→翻訳の対照、入力改変、取消、故障、再開の検査対象操作は実行します。Nodeの30秒grace／10.5秒以上のheartbeat、Pythonの実3／6秒retryは短縮しません。

直接例はrepository root、既存の開発環境とbuild済みhelperから開始します。依存の自動取得や既存環境の同期は行わず、必要なら正式手順と許可範囲を先に確認します。以下は新しい証拠directoryだけを作ります。

```sh
set -eu
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mkdir -p work
EVIDENCE=$(mktemp -d "$PWD/work/test-verification-XXXXXX")
export EVIDENCE
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git diff --stat > "$EVIDENCE/diff-scope.txt"
git diff -- CONTRIBUTING.md tests/test_revise.py tests/test_review.py tests/test_pdf_candidate_acceptance.py .github/workflows/ci.yml > "$EVIDENCE/verification.diff"
{ uname -sm; .venv/bin/python --version; node --version; pnpm --version; uv --version; } > "$EVIDENCE/tools.txt"
```

`conditions.txt`へcore/PDF extra・依存／tool version・同期の有無・Node build状態・PATH/offline条件・signal handler・入力・cold/warm状態を記録します（raw環境変数やcredentialは記録しません）。各操作のcommand、exit、時刻／raw所要時間、期待／観測、未確認事項を証拠と対応付けます。過去Issueの成功は今回のrevisionの成功に上書きしません。JSON／assert成功も実LLM意味品質、実Docling/OCR、viewer、account利用権の保証ではなく、remote未実行は明記します。stderr保存はnetwork拒否付き人工fixtureに限り、実認証・秘密入りstderrは保存しません。

比較は他のtest processと**非並行・foreground**、同じ環境／extra／入力／build／PATH/offline／signal条件で行います。SIGINTはdefault handlerを確認し、background shell由来のignoreを持ち込みません。必要ならPython親processで`signal.signal(signal.SIGINT, signal.default_int_handler)`へ戻してからsubprocessを起動します。Planning値や別条件の過去の成功を新差分のbaselineへ流用しません。

現在の[Approved Plan](docs/plans/verification-design-right-sizing.md)の代表49件は次の6関数のparameter集合です。同じPDF extra環境で変更前後各1回のfresh processを基本とし、seed生成・コピー・公開load／追加checkも含めます（下の49件選択自体には別の移設checkを含めません）。終了コードも保存し、失敗値を成功時間に混ぜません。

```sh
python -c 'import signal; assert signal.getsignal(signal.SIGINT) == signal.default_int_handler'
CASES='source_revision_validates_table_cell_evidence_against_original_scope or source_revision_validates_list_parent_depth_across_pages or review_invalid_scope_never_sends or review_empty_and_rejected_answers_keep_usage_without_quality_approval or translation_patch_rejects_invalid_inputs_without_changing_workspace or epub_source_validates_qa_targets_before_adoption'
if /usr/bin/time -p .venv/bin/pytest tests/test_pdf_candidate_acceptance.py tests/test_review.py tests/test_revise.py \
    -k "$CASES" -q --durations=0 --durations-min=0 > "$EVIDENCE/focused49.log" 2>&1; then code=0; else code=$?; fi
printf '%s\n' "$code" > "$EVIDENCE/focused49.exit"
test "$code" -eq 0
.venv/bin/pytest tests/test_pdf_candidate_acceptance.py tests/test_review.py tests/test_revise.py \
  -k "$CASES" --collect-only -q > "$EVIDENCE/collection.log"
```

core全件／PDF4ファイル／全Nodeも同条件の前後各1回を基本とし、正式チェックと一致すれば兼用します。ばらつき・外乱で判断不能な部分だけ双方を追加2回確認し、raw値と中央値・min/maxを報告します。node別setup/call/teardown、collectionのnode ID／parameter／skip理由も照合し、未実行phaseは0でなく理由を記録します。品質、install／build／配布、CI待ちはテスト時間と分け、ローカル結果からCI短縮率を推定しません。固定秒数・短縮率の合格閾値は置きません。

同じ保証範囲の時間、49件の編集直後の確認、PDF3→4ファイルの48件追加による保証増分、無関係な全件再実行を除ける条件は分けて報告します。生成回数削減と時間短縮も同一視せず、短縮未確認を成功と表示しません。Issue #43の前後は#41／#42実装済みの文書改訂のみであり、その比較を過去のfixture軽量化効果にしません。

**歴史的条件（Issue #39専用）**：当時は候補を前後3回計測して中央値beforeの90%以下を要求し、正式phaseは各1回、after/beforeが1.05超または外乱で判断不能なら双方追加2回、5%超悪化は不受理としました。これは過去の別契約であり、今後の一律3回／10%短縮ルールではありません。[過去Plan](docs/plans/test-verification-speedup.md)や実行記録は書き換えません。

直接確認の証拠はworkspace外の`$EVIDENCE`へ残します。完了後、作ったprocessをwait／回収し、自分がこの例の`mktemp`で作った`WORKSPACE`／`RUN_FIXTURE`だけを削除します（人工credentialもその内側）。`work/`全体、他人のworkspace、既存venv／credential／modelは削除しません。証拠は削除せず、cleanup対象／結果と未確認範囲を記録し、`test -s "$EVIDENCE/revision.txt"`と各ログの再読込みで参照維持を確認します。障害復旧例末尾の一時directoryも、自分が今回作成したpathを確認してから同じ扱いにします。

### EPUB revise／reviewの前提再利用（Issue #41）

本項目は[Approved Plan](docs/plans/verification-design-right-sizing.md)の実装方針1のEPUB部分だけです。正式チェック全体・PDF fixture／CI・上のIssue #39の歴史的計測条件は変更しません。

- `test_revise.py`のpatch拒否7件とQA target9件だけを、既存の`malformed_source_seed`／`malformed_source_workspace`へ接続します。同じdefault `make_epub`の実CLI runをmodule invocation内で1回生成し、既存malformed source拒否群とも共有します。
- `test_review.py`のscope拒否7件とempty／rejected answers4件だけは、chosen／secret／heading入りの同一人工EPUBから専用`review_scope_seed`を実生成し、各caseへ独立コピーします。他の`epub_workspace` caller・異なる入力・PDFへは転用しません。
- workspace全file（原本・artifact・evidence・attempt・履歴・pointer・run・render等）と初期3 requestのbyte一致を確認し、コピー上で公開source／preparation／translation loadを行います。各moduleの`*_workspace_is_relocatable_and_isolated`は元seed root全体を一時退避して2コピーをloadし、pointer／request logの変更とconsumer側の`helper`設定書込みがseed／他コピーへ伝播しないことを確認します。seedのworkspace全byteとrequest logはteardownでも不変をassertします。
- 各caseの実CLI revise／review、実Node helper／公開faux、受理／拒否、byte／hash／pointer、無送信、usage、品質未承認のassertは残します。reader→run、patch→resume、旧訳再利用、取消／復旧も従来の実経路のままです。毎caseの前提作成（prepare2件＋translation1件）だけを除きます。

次は既存PDF extra環境・build済みhelperからの再現例です。追加install／sync／model取得をせず、repository rootで**非並行・foreground**実行します。SIGINTのdefault handlerを確認し、background shell由来のignoreを持ち込みません。offline変数だけでなく`ai/tests/run-process.mjs`のnetwork拒否と人工credentialを使います。

```sh
set -eu
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mkdir -p work
EVIDENCE=$(mktemp -d "$PWD/work/issue41-epub-XXXXXX")
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git diff -- CONTRIBUTING.md tests/test_revise.py tests/test_review.py > "$EVIDENCE/verification.diff"
python -c 'import signal; assert signal.getsignal(signal.SIGINT) == signal.default_int_handler'
# 新fixtureは代表consumer単独のfresh processでも生成できることを確認
pytest 'tests/test_revise.py::test_translation_patch_rejects_invalid_inputs_without_changing_workspace[stale]' \
  --basetemp="$EVIDENCE/pytest-revise" -q > "$EVIDENCE/single-revise.log" 2>&1
pytest 'tests/test_review.py::test_review_empty_and_rejected_answers_keep_usage_without_quality_approval[findings0-None-None]' \
  --basetemp="$EVIDENCE/pytest-review" -q > "$EVIDENCE/single-review.log" 2>&1
# 27対象＋移設／分離check、続いて変更moduleの全回帰
pytest tests/test_revise.py tests/test_review.py \
  -k 'translation_patch_rejects_invalid_inputs_without_changing_workspace or epub_source_validates_qa_targets_before_adoption or review_invalid_scope_never_sends or review_empty_and_rejected_answers_keep_usage_without_quality_approval or workspace_is_relocatable_and_isolated' \
  --basetemp="$EVIDENCE/pytest-targets" -q --durations=0 --durations-min=0 > "$EVIDENCE/targets.log" 2>&1
pytest tests/test_revise.py tests/test_review.py --basetemp="$EVIDENCE/pytest-modules" \
  -q --durations=0 --durations-min=0 > "$EVIDENCE/modules.log" 2>&1
# 成功時の終了コードは0。失敗時は止め、command／exitと原因を記録してから所有物だけcleanup
printf '%s\n' 'all four pytest commands exited 0' > "$EVIDENCE/exits.txt"
rm -rf -- "$EVIDENCE/pytest-revise" "$EVIDENCE/pytest-review" "$EVIDENCE/pytest-targets" "$EVIDENCE/pytest-modules"
printf '%s\n' 'owned pytest workspaces/artificial credentials removed; evidence retained' > "$EVIDENCE/cleanup.txt"
test -s "$EVIDENCE/revision.txt" && test -s "$EVIDENCE/modules.log"
cat "$EVIDENCE/exits.txt" "$EVIDENCE/cleanup.txt"
```

今回のcommand／exit／条件／raw時間／node ID比較／cleanup記録は追跡外の今回専用`work/`配下へ保存します。27対象だけなら実生成は従来27回から2回、両module全体では既存revise seedの分を考慮して26回のrun反復を除きます。追加characterization checkも同じseedを使います。時間比較は同条件のfresh process前後各1回から始め、ばらつきが判断を左右するときだけ追加2回とし、固定秒数目標は置きません。Planの49件比較には未変更のPDF22件も含めますが、その前提生成は本項目では変更しません。工程削減を時間短縮率へ外挿せず、seed生成・コピー・公開load・追加checkを含む実測と区別します。Rootの正式チェック・別の実入口からの直接照合・独立Reviewはこの再現例だけで代替しません。実LLM意味品質、実Docling／OCR、viewer、他OS、remote CIはこのoffline確認の対象外です。

### PDF evidence／list reviseの前提再利用（Issue #42）

[Approved Plan](docs/plans/verification-design-right-sizing.md)のPDF部分です。`test_pdf_candidate_acceptance.py`のtable-cell evidence20件とlist-depth2件だけで、同じdefault `start_pdf`（所有2–3／参照4）の完成状態をinvocation内で1回実生成します。module-localの遅延seedを最初のfunction consumerで生成し、`pdf_input`／converter／monkeypatchはfunction scopeのままです。各caseへworkspace全file（原本・draft・画像・source・preparation・evidence・attempt・translation・履歴・pointer・run・render）と初期request logを独立コピーし、公開draft／source／preparation／translation loadで検証します。永続cache・hardlinkは使いません。

legacy化・damageはコピー後に行い、実CLI revise、全22 parameter、PATH空での受理／拒否、旧byte／hash／pointerの保持、request不変を残します。他の`start_pdf` caller、可変入力／設定、候補publication・中断復旧は実経路のままです。追加の`test_pdf_evidence_workspace_is_relocatable_and_isolated`はseed root全体を退避して2コピーを公開loadし、pointer／log／helper設定の変更非伝播を確認します。seed生成回数とseed全byte（初期log・helper設定を含む）の不変もteardownでassertします。

CIと上の正式PDFコマンドは4ファイルに揃えます。core-onlyではDocling不在でskipされる候補採用moduleの既存48件もPDF側で実行するためです。EPUB、正式チェックの順序・core全件・offline・品質・build／配布、および検証規約全体は本項目で変更しません。

以下は既存PDF extra環境とbuild済みhelperを使う再現入口です。repository rootで**非並行・foreground**実行し、追加install／sync／model取得はしません。SIGINTのdefault handlerを確認し、background shellからignoreを継承する場合はPython親processで`signal.signal(signal.SIGINT, signal.default_int_handler)`へ戻してからsubprocessを起動します。offline変数だけで全通信遮断とはみなさず、既存`ai/tests/run-process.mjs`のnetwork拒否と一時人工credentialを使います。

```sh
set -eu
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
mkdir -p work
EVIDENCE=$(mktemp -d "$PWD/work/issue42-pdf-XXXXXX")
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git diff -- CONTRIBUTING.md .github/workflows/ci.yml tests/test_pdf_candidate_acceptance.py > "$EVIDENCE/verification.diff"
python -c 'import signal; assert signal.getsignal(signal.SIGINT) == signal.default_int_handler'
pytest tests/test_pdf_candidate_acceptance.py --collect-only -q > "$EVIDENCE/collection.log"
# 代表単独でも、function fixtureが有効な間にseedを実生成できることを確認
pytest 'tests/test_pdf_candidate_acceptance.py::test_source_revision_validates_list_parent_depth_across_pages[2]' \
  --basetemp="$EVIDENCE/pytest-single" -q > "$EVIDENCE/single.log" 2>&1
# 同じ保証範囲の22件＋公開load／移設／分離check
pytest tests/test_pdf_candidate_acceptance.py \
  -k 'source_revision_validates_table_cell_evidence_against_original_scope or source_revision_validates_list_parent_depth_across_pages or pdf_evidence_workspace_is_relocatable_and_isolated' \
  --basetemp="$EVIDENCE/pytest-targets" -q --durations=0 --durations-min=0 > "$EVIDENCE/targets.log" 2>&1
pytest tests/test_pdf_candidate_acceptance.py \
  tests/test_review.py::test_review_pending_candidate_reviews_adopted_hash_without_adopting_candidate \
  --basetemp="$EVIDENCE/pytest-module" -q --durations=0 --durations-min=0 > "$EVIDENCE/module.log" 2>&1
# 失敗時は停止してcommand／exit／原因を記録。成功時も所有物だけcleanup
printf '%s\n' 'collection and three pytest commands exited 0' > "$EVIDENCE/exits.txt"
rm -rf -- "$EVIDENCE/pytest-single" "$EVIDENCE/pytest-targets" "$EVIDENCE/pytest-module"
printf '%s\n' 'owned pytest workspaces/artificial credentials removed; evidence retained' > "$EVIDENCE/cleanup.txt"
test -s "$EVIDENCE/revision.txt" && test -s "$EVIDENCE/module.log"
cat "$EVIDENCE/exits.txt" "$EVIDENCE/cleanup.txt"
```

条件・command／exit・raw時間・生成回数・request増分・node ID／parameterの前後比較（既存48件を失わずcharacterizationのみ追加）を秘密なしで証拠へ残します。所有processを回収し、自分のpytest一時workspace／人工credentialだけを削除し、ログはworkspace外へ保持してcleanup後も読み直します。既存venv・model・利用者workspace・`work/`全体は削除しません。計測は同条件のfresh process前後各1回から始め、seed生成・コピー・公開load・追加checkも含めます。22回から1回への生成削減と、正式PDF3→4ファイルの48件追加による時間差は別に報告し、固定秒数目標やCI短縮率へ外挿しません。Rootの正式確認・別途の実CLI／公開load直接照合・独立Reviewをこの例だけで代替せず、実LLM意味品質・実Docling／OCR・viewer・他OS・remote CIは未確認とします。

### PDF取得の検証境界

`acquire_pdf`→`load_pdf_draft`の公開seamで、不足model・不正入力／range、raw item／ref／table cell／provenance、clipしないcharspan不一致、conversion status／errors、全対象ページ／PNG、直接描画scale／unload、artifact hash不一致、writer排他、参照replace失敗時の旧版保持を検査します。Doclingの外部公開`DocumentConverter.convert`と公開page backend境界だけを人工fixtureに置換し、保存・load・workspaceは実物を使います。private helper mockや未依頼CLIは追加しません。core-onlyでのPDF／EPUB API importも検査します。

実変換は人工fixtureテストとは別に、明示承認済みの既存local modelだけを使い、自作PDFで公開APIを呼びます。重みをテストやCIが取得することはありません。準備は[READMEのPDF手順](README.md#pdfを翻訳する)、APIと保存物は[artifact契約](docs/reference/artifacts.md#pdf抽出下書きissue-7)を参照してください。再確認は自作の小さな文字付きPDFと画像のみPDFについて、それぞれ次を実行します（`OWN.pdf`／`MODELS`／workspaceは置換）。

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

#### Issue #46／#47：全所有結果・coverage限定訂正・厳密再利用の再現

公開保存／指示／互換性の契約は上記artifact契約、利用回数・課金非保証・既存workspace復旧案内は[usage](docs/reference/usage.md#ページ欠落が残る既存pdfの復旧確認)に集約します。合成入力だけで欠落→訂正成功、3generate上限、全記録・無送信回収とCLIへの接続を確認します。既存環境とbuild済みhelperを使い、install／sync／model取得はしません。helperが不在／古ければ止めて環境更新の許可を確認します。

以下はrepository rootで実行します。`EVIDENCE`を指定する場合も、今回だけの新規directoryを使ってください。pytestの`--basetemp`は既存directoryを消すため、既存workspaceを指定しません。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
EVIDENCE="${EVIDENCE:-$(mktemp -d "$PWD/work/pdf-correction-issue47-XXXXXX")}"
mkdir -p "$EVIDENCE"
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git diff -- src/doc_translate/pdf_correction.py tests/test_pdf_correction.py tests/test_run.py ai/tests/pdf-correction-process.mjs ai/tests/run-process.mjs docs/reference/artifacts.md docs/reference/usage.md CONTRIBUTING.md > "$EVIDENCE/diff.patch"
# SIGINTをignoreする起動環境でもdefault handlerを戻してforeground実行する
pytest_checked() {
  .venv/bin/python -c 'import signal, pytest; signal.signal(signal.SIGINT, signal.default_int_handler); raise SystemExit(pytest.main())' "$@"
}
pytest_checked 'tests/test_units.py::test_accepted_pdf_figure_membership_is_not_reclassified_by_units[shared-ref]' \
  -q --basetemp="$EVIDENCE/pytest-single" > "$EVIDENCE/single.log" 2>&1
result=$?; printf 'single exit=%s\n' "$result" >> "$EVIDENCE/exits.txt"; test "$result" -eq 0 || exit "$result"
pytest_checked tests/test_pdf_correction.py \
  -q --basetemp="$EVIDENCE/pytest-correction" > "$EVIDENCE/correction.log" 2>&1
result=$?; printf 'correction exit=%s\n' "$result" >> "$EVIDENCE/exits.txt"; test "$result" -eq 0 || exit "$result"
pytest_checked tests/test_pdf_source.py \
  tests/test_units.py::test_accepted_pdf_figure_membership_is_not_reclassified_by_units \
  tests/test_review.py::test_review_exclusion_only_chunk_uses_adopted_images_and_revalidates_offline \
  tests/test_run.py \
  -q --basetemp="$EVIDENCE/pytest-consumers" > "$EVIDENCE/consumers.log" 2>&1
result=$?; printf 'consumers exit=%s\n' "$result" >> "$EVIDENCE/exits.txt"; test "$result" -eq 0 || exit "$result"
cat "$EVIDENCE/exits.txt" "$EVIDENCE/single.log" "$EVIDENCE/correction.log" "$EVIDENCE/consumers.log"
# cleanup前の保存hash（pathは合成試験の元位置。raw/requestが必要なら秘密を除いて別途退避）
find "$EVIDENCE"/pytest-* -type f \( -path '*/attempts/pdf-correction/*.json' -o -path '*/corrections/pdf/*.json' -o -name usage.json \) \
  -exec shasum -a 256 {} + > "$EVIDENCE/observed-hashes.txt"
# 成功確認／必要な観測の保存後、自分が上で作ったpytest一時workspaceと人工credentialだけを削除
rm -rf -- "$EVIDENCE/pytest-single" "$EVIDENCE/pytest-correction" "$EVIDENCE/pytest-consumers"
printf 'owned pytest workspaces/artificial credentials removed; evidence retained\n' > "$EVIDENCE/cleanup.txt"
test -s "$EVIDENCE/revision.txt" && cat "$EVIDENCE/exits.txt" "$EVIDENCE/cleanup.txt"
```

期待する観測と証拠は次のとおりです。

- `test_correct_pdf_requests_full_owned_results_and_image_confirmed_blank_pages`と所有／参照許可テストで、実送信requestの全文・白紙・創作禁止指示、画像と許可範囲を確認します。白紙exclusionは公開correct/loadで成功し、空回答はcoverage拒否、空抽出の判断不能ページはexclusionsを自動生成せずuncertaintiesとして保存されます。
- 旧成功再利用は真正helper成功から今回の`page_results`だけを除き、hashを正しく保存した合成旧形式で確認します。raw回答とusage自体は変更しません。再利用／chunk保存前のOS replace故障からの回収は`before_send`が呼ばれず、旧byteが不変です。policy／config／参照／投影／画像等の差異、不正status／terminal／domain／hashは再利用しません。歴史的な図所属指示欠如のload互換も残します。
- `test_correct_pdf_repairs_missing_pages_with_complete_chunk_and_retains_usage`は所有[1,2,3]の一部欠落から不足[2,3]の固定feedbackを返し、同じpolicy／投影／画像で全chunkの完全回答だけを採用します。`test_correct_pdf_stops_after_three_incomplete_whole_chunk_answers`は異なるページだけを返す回答もmergeせず、3generateで停止しchunkを保存しません。各回のraw回答・全attempt・人工usageを不変保存し、既知のtransientだけのtransport retryも独立に残ることを確認します。schema／不正tableとの複合不正は追加訂正せず、OS replace境界での拒否record保存失敗・取消も次の送信を止めます。
- 訂正成功は`test_correct_pdf_recovers_repaired_success_before_chunk_save_without_resending`でOS replace故障後も無送信回収します。固定指示・code・理由・不足番号の型／並び／範囲／余分fieldやその他入力の差異は誤再利用しません。旧base／新baseの既存再利用試験も維持します。
- CLIのadaptive回収は旧／新base／訂正成功で、保存成功chunkとchunk未確定の成功応答を再送せず、残った所有`[7]`だけを参照`[6]`付きで補正します。`test_resume_pdf_repairs_coverage_only_for_failed_ownership_and_retains_all_usage`は最初のrunで所有[2–6]成功・[7]欠落3回→exit1／answer_invalid／failed[7]／not_processed[]、補正記録4件を期待します。resumeは[7]だけ欠落→訂正成功の2generateで回復し、記録6件に失敗4件と成功2件のraw／観測usageが残りstatusとQA付随usageに対応します。再resumeは空PATHでも無送信、原本・draft・attempt・旧chunk等のhashは不変です。fixtureの従来単一回答／最終request.jsonは保持し、responsesの初回／訂正選択とrequest.json.jsonl追記履歴を補完しています。run-processの既存optionsも維持し、correction_omit_pages／correction_coverage_failureだけで合成欠落を操作します。
- Workerはこの代表consumer単独fresh process・補正module・focused consumer回帰と手順の成立を報告します。Rootは上の正式チェックの選定と別一時workspaceからの公開API直接確認、Reviewerは独立受入れを担当します。今回専用`work/`にcommand／exit、Red→Green、期待／観測・保存hash・未確認・cleanupを残し、cleanup後も証拠を読み直します。既存workspace／attempt／venv／model／lockに触れず、他人のprocessは止めません。

再現用の公開API例はartifact契約を参照してください。offlineの実process検証は補正の構造・範囲・保存に限定され、実LLMの読取り／図分類品質・全文翻訳・実Docling／OCR・viewer・他OS・remote CIの成立は示しません。chunkは別artifactに確定するだけで、`source-snapshot.json`を切り替えません。このchunk API自身はCLI・unit生成・翻訳・resume engine・全chunk所有割当を担当しません。ページ接続は次の独立adapterで行います。

### PDF原文組立の検証境界

Issue #9は受理済みchunk→`assemble_pdf_source(workspace=..., chunk_sha256s=..., snapshot_id=...)`→既存`save_source_snapshot/load_source_snapshot`が公開seamです。API／追加schema／最小入力例は[artifact契約](docs/reference/artifacts.md#pdf原文snapshot組立issue-9)を参照してください。adapterは保存・採用lockを自分で取らず、callerが別途saveします。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_pdf_source.py tests/test_pdf_correction.py tests/test_source_snapshot.py
```

`tests/test_pdf_source.py`は既存の人工draft／evidence／run fixtureと実helper／公開faux providerを再利用し、補正回答から検証済みchunkを作ってから組み立てます。脚注越しの本文接続・分綴・code改行／language・欠損の非補完・callout行対応、別ページlist親子（同名local IDを含む）と未知／複数／循環／kind／深さ／順序のQA、未確定table・図内部非再採用、ページ順・断片／出典／画像のsave/loadをliteral期待値で検査します。同じ原本文書の別draftも、実際の抽出snapshotを残し、draft-localな同名refの内容が違う場合に推測接続しないことを検査します。補正側では接続／親根拠の許可範囲と実raw provenanceも拒否検証します。

所有unionを越える処理完了・意味品質は主張しません。未確定内容は断片とQAを保持しますが、hash不一致・所有重複・異なる原本文書は拒否します。新たな永続管理・private mock・実LLM・model取得・既存書籍処理は追加しません。この組立API自身はCLI／unit／翻訳／renderer／自動割当を担当しません。

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

本節と次節のfocused pytestは境界の再現入口です。Workerの回帰とRoot自身の正式／直接確認を区別し、Rootの範囲は上の変更影響の選定手順で決めます。固定準備のコピーは前提作成だけに使い、実準備→実翻訳→公開load／無送信再利用の対照を維持します。

`tests/test_preparation.py`の自作PDF原文snapshotと実EPUB readerからの人工原文、`ai/tests/preparation-process.mjs`のnetwork拒否下の公開`fauxProvider`＋実`runHelper`を使います。内部`generate`／validator／保存helperをmockしません。保存故障注入はOSの`replace`境界だけです。faux processも通常のBiome／oxlint検査対象です。

- 公開prepare/save/loadの観測で利用者terms優先、複合語・原表記、本文根拠なし候補の非採用、単一policy由来style、固定glossary/contextの保存を確認します。
- 節単位の実送信と整理入力、語がなくても節contextが整理へ届くこと、PDF／EPUB両方のblock／inline code・数式・画像・XHTML全文の非送信、snapshot-boundな用例／出典を検査します。
- 未知unit／捏造引用・根拠なし語の採用、schema不正を拒否し、失敗のraw回答／usage記録と旧版保持、retryの全attempt（失敗分も含む）を確認します。準備の根拠不一致だけは固定理由と問題の用語／引用を未信頼dataとして伝えて最大3回答まで再生成し、収集／整理での回復・上限停止・訂正済みrequestの再利用とCLIの安全な原因表示も検査します。
- 同じ固定版の無送信再利用、準備方式metadataだけの変更で固定翻訳入力hashが変わらないこと、policy／利用者terms改版、全artifactのhash不正、hashを再計算した捏造根拠・採用内容差替え、採用参照replace失敗、writer排他を検査します。

実LLM・実credential・model重み・既存書籍を使いません。この機械検証は候補網羅性・定義の意味や訳語品質の確認ではなく、全文翻訳snapshot／CLI統合の成立も示しません。実送信や取得は別途承認が必要です。

### 翻訳・採用snapshotの検証境界

Issue #12は`doc_translate.translation.translate_document`→`load_translation`の公開seamです。API、回答schema、部分失敗・分割履歴・再利用と保存形は[artifact契約](docs/reference/artifacts.md#翻訳採用snapshotissue-12)を参照してください。保存済みPDF型snapshotと実EPUB readerからの自作原文、固定準備版を使い、`ai/tests/translation-process.mjs`のnetwork拒否下の実`runHelper`／公開faux provider（取消は公開stream provider）を通します。内部generate／validator／saveはmockせず、保存故障注入はOSのreplace境界だけです。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_translation.py tests/test_preparation.py tests/test_units.py tests/test_inference.py
```

- 既定約12,000原文文字・concurrency 2、章節境界、ID全種の不正／空訳／marker inventory・構造の拒否、原位置付き要確認と品質未承認を検査します。
- lengthと公開input上限によるchunk縮小／lossless unit分割・不可分spanの失敗、正常採用を残した部分失敗／再開、有限retryと失敗usage／不明usage生値の保持を検査します。marker違反の全unit・不足／余分なmarker等を未信頼dataとして返す最大3回答の訂正生成、訂正結果のload／無送信再利用・中断回復、入力改変の拒否、実CLI resumeで失敗chunkだけを訂正することも検査します。
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

自作source／EPUB・固定した人工訳文を使い、fence内backtick・callout・list・空cell／span・MathML・PNG／crop・notes／anchors・危険markup／外部参照・元text不変・部分／split訳・stale／hash／atomic保存・writer排他を検査します。訳文は既存の実helper／公開faux providerを通し、private collaboratorをmockしません。CLIのcore-only条件は空PATHで検査します。この機械検査は意味品質の承認でもviewer実表示の検査でもありません。現在の限定した表示確認は上記Issue #18の記録を参照してください。

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
if PATH='' "$PWD/.venv/bin/doc-translate" render "$WORKSPACE" > "$EVIDENCE/render.json"; then code=0; else code=$?; fi
printf '%s\n' "$code" > "$EVIDENCE/render.exit"
test "$code" -eq 1  # 期待する部分出力。set -eでも観測する
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

日本語側に原文＋`※`、英語側に原文、4-backtickのcode fence、数式、code原本欠損のQAが残ります。source mapから元ページ2へ戻れますが、PDF全文とは表示しません。訳文／分割／失敗usageを含む検証は`tests/test_render.py`の既存prepare/translate公開API＋faux fixtureを参照してください。実LLM・実credential・model取得・書籍・viewer・remote CIは使いません。

### 軽量statusと詳細検査（Issue #49）

通常`status DIR`は保存記録のみで成果物未検証です。`--verify`は詳細検査の人向け表示、`--json`／`--verify --json`は従来の詳細完全JSONです。上の`render-status.json`照合は詳細検査として維持します。軽量readerと共有`not_processed`分類の公開seamは`tests/test_render.py`、CLI／formatterは`tests/test_cli_output.py`です。内部workspace_status callerはrun／resume／review／reviseであり、再利用前の検証は軽量化しません。

新しい直接操作・必要破損拒否・保存不変・証拠退避／cleanupの再実行手順は[project検証Skill](.pi/skills/verify-project/SKILL.md)と[Feature Map](.pi/skills/verify-project/references/feature-map.md)へ置きます。repositoryでPiのproject trustを確認し、新セッションまたは`/reload`後に`/skill:verify-project status`で呼ぶか、実ファイルを明示readします。Skillの成立は正式チェックやRootの受入れを代用しません。

共有renderと詳細consumerの変更なので、Workerはtest_render／test_cli_outputと関連consumerのfocused回帰、Rootはcore全pytest・PDF4ファイル・Ruff全体・PDF環境ty・CLI helpを実行します。Node source／依存／lockが不変なら全Node／build／wheelを本Issueのためだけに追加しません。性能は正式PlanのPDF／EPUB人工100／3,000 unit再作成条件でbefore／afterを別途非並行計測し、任意規模・実書籍・同等完全検証の高速化とは主張しません。

## EPUB進捗／共通表示の検証（Issue #50）

既存`test_run.helper/run_args`の機械向けfixtureは`--json --events-json`を指定し、resume JSON consumerも`--json`へ追随します。人向け既定と4組合せは`test_cli_output`で別に検査します。Node source／protocolは変更せず、既存の実Python→実Node helper／network拒否付きfauxを利用します。observer境界はtest_inference／preparation／translation／run／resume、保存後adoption・reuse／並列待機・split／request終端／retryとdomain訂正／取消／JSON書込み拒否を既存testsで限定回帰します。共有helper consumerのtest_revise／test_pdf_candidate_acceptanceも含めます。

TTYの固定更新、36×10狭幅／狭高さ、dark／light背景、空値NO_COLOR、frameと保存結果、部分resume／空PATH reuse・immutable不変は[検証Skill](.pi/skills/verify-project/SKILL.md)と[Feature Map](.pi/skills/verify-project/references/feature-map.md#epub-runresumeと共通進捗issue-50)の再実行手順を使います。標準PTY helperはstderrを接続しstdoutを分離、人工transcript／SGR色付きcell frameを証拠へ保存します。既存Chromeで単一frameを明暗背景・幅込みに表示して現在対象と異常理由を照合し、SGR検出／HTML生成だけを視認性確認と呼ばず、Rootが別invocationで直接観測します。Skillの成立／Worker成功はRoot・Reviewerの受入れを代用しません。status手順は維持し、PDF補正／review表示の完成は含めません。

今回の共通Python接続・依存変更の正式範囲は上記のcore全pytest／PDF4ファイル／Ruff check＋format／PDF環境ty／CLI helpとwheel build＋check_distributionです。既存venv sync／追加installは禁止、Rich直接依存`>=15,<16`とlock／隔離core・wheel依存導入だけを許可範囲で確認します。Node source／protocolへ差分が及ぶ場合は正式Node phasesも追加します。

## PDF進捗・保存後所有ページ集計の検証（Issue #51）

[Feature MapのPDF手順](.pi/skills/verify-project/references/feature-map.md#pdf-runresumeの工程所有ページ進捗issue-51)に、新規公開CLI・Docling公開converter／page backendの既存人工fixture入口、80桁TTY／36桁NO_COLOR、実所有／参照request、部分回復／空PATH再利用のみ、coverage repair／length・input_limit分割／候補待ちの期待・証拠・finally cleanupをまとめます。EPUB／status手順は維持し、Rootは新しい証拠directoryの別invocationで実行・照合します。実Docling／OCR／実providerや書籍品質の保証ではありません。

focused回帰は`tests/test_run.py -k pdf_progress`（通知時のchunk／manifest実体、manifest保存前の成功ゼロ、再利用の二重加算なし、length／input_limit）、`test_pdf_correction.py::test_correct_pdf_preserves_transport_attempts_without_cumulative_usage`（通信retryとcoverage訂正・request終端）、`test_cli_output.py::test_pdf_progress_split_keeps_owned_denominator_and_translation_state_separate`です。既存の所有許可／部分PDF／fills_failed／coverage回復／候補／cancel・record保存失敗も維持します。pytestは上記のSIGINT default復元方式を使います。内部workflow／保存・validatorのmockは加えません。

共通state／表示と工程間reuseに及ぶため、Rootの正式範囲はcore全pytest、PDF4ファイル、Ruff全体check／format、既存PDF環境ty、CLI helpです。Workerは変更moduleとfocused consumer・手順変更部分を再実行します。依存／Node source／protocol／build差分はなく、#50で済んだRich依存化のwheel検査や全Node phaseは今回反復しません。差分がそこへ及べば再選定します。install／sync／model取得・実認証・課金は禁止し、既存環境のみ使います。

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
PYTHONPATH=tests .venv/bin/python - <<'PY_RUN' > "$EVIDENCE/run.json"
import json, os, shutil
from pathlib import Path
from test_epub import make_epub
from doc_translate.cli import main
root = Path(os.environ["RUN_FIXTURE"])
source = make_epub(root)  # 自作mixed-inline EPUB、既存書籍ではない
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
load_preparation(workspace)  # evidence/hash/attempt/実request対応も検証
assert load_translation(workspace).processing.status == 'completed'
assert len((root / 'requests.jsonl').read_text().splitlines()) == 3
assert all((workspace / path).is_file() for path in result['artifacts'].values())
assert '訳:Original' in (workspace / result['artifacts']['japanese']).read_text()
qa = json.loads((workspace / result['artifacts']['qa']).read_bytes())
assert qa['content_review']['meaning_quality_approved'] is False
shutil.copytree((workspace / result['artifacts']['japanese']).parent, evidence / 'run-artifacts')
print('completed; public loads; 3 requests; Japanese output and QA matched')
PY
# 同じworkspaceへの新規runはexit2（setup／実認証／推論を開始しない）
if .venv/bin/doc-translate run "$RUN_FIXTURE/book.epub" --workspace "$RUN_FIXTURE/new" \
    --provider faux --model text --thinking off; then code=0; else code=$?; fi
printf '%s\n' "$code" > "$EVIDENCE/existing-run.exit"
test "$code" -eq 2
```

Rootのコピー前提・拒否の直接確認は、上の人工run直後に行います。元workspaceを一時退避した状態で別pathから公開loadし、不正envelopeを実CLIへ渡します。成功adoption／resumeの代わりにはしません。

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
assert len(requests.splitlines()) == 3
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

最初のrunはstderrへEPUB工程／collect・organize／AI待ち、stdoutへsummaryを表示します。送信前JSONを解析するときはrun／resumeへ`--events-json`を、resumeの最終JSONを解析するときは`--json`を明示してください。`artifacts`から日本語側の`訳:Original`とinline保持、英語原文、source map、QA／usageを確認できます。PDFのsynthetic直接確認は`.venv/bin/pytest -s tests/test_run.py -k 'owns_requested or partial_pdf or without_pages'`で同じ公開CLIを使い、置き換えるのはDocling外部APIとfaux providerだけです。fixtureの取得成功は実PDFの完全抽出や実LLM意味品質・viewer表示の保証ではありません。resume／revise／reviewの検証は各節を参照してください。remote CIの実行は必須ではありません。

## 明示範囲reviewの検証境界

Issue #17は公開`review DIR --units IDS`／`--pages A-B`→workspace・QA/statusと、Python↔実Node helper／公開faux providerの既存seamです。`tests/test_review.py`は`test_run.py`のhelper/run_args・人工PDF converter、`test_pdf_correction.py`の保存draft／PNGと公開補正・組立・準備・翻訳API、人工EPUBを再利用します。内部generate／workflow mock、新Runner／protocol、実provider／認証／書籍／model取得を使いません。Node fixtureの変更は`ai/tests/run-process.mjs`だけで、helper source変更がないためbuildは不要です。PDF extra CIの既存pytest対象にもreviewを追加しています（remote実行は不要）。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_review.py tests/test_cli.py tests/test_render.py tests/test_revise.py tests/test_run.py
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check
pnpm --dir ai format
pnpm --dir ai lint
pnpm --dir ai test
```

- unit／pages排他・必須、未知／空ID、EPUB pages、保存範囲外・跨り・未訳／staleの無送信を確認します。PDF表のblock位置とcell evidenceが異なる人工形状でも選択外cell本文を送りません。
- 文書／祖先contextの非選択sentinel、inline token内の別attribute・他unit本文を送らず、選択unitの保護literalだけを照合できることを実requestで検査します。split/table/attributeのID・親範囲・元位置、選択PNG byte/hash/順/scale/座標、非翻訳block／ページの位置付き指摘を確認します。
- 正常／空findings／未知対象拒否／provider失敗／metadata設定失敗とusage observed/unknown、原文／訳文／準備／run参照と旧byte hash不変、明示overrideとambient非適用、writer lock／atomic artifact保存失敗時の旧版・観測usage保持を検査します。故障注入はOSのreplace境界だけです。
- 保存-only status/renderの現行確認／未確認範囲、機械チェックとの分離、QAの元位置／block／unit／出力anchor、旧reviewの履歴保持と修正後非current、candidate待ちでも採用hashに結ぶことを確認します。空findingsでもmeaning_quality_approved=falseです。

公開CLIの直接人工確認は上の`RUN_FIXTURE/new`作成後に、同じ外部process seamで実行できます（通常の利用者CLIは導入済みhelper／実providerを使うため、これはfaux限定例です）。

```sh
PYTHONPATH=tests .venv/bin/python - <<'PY_REVIEW'
import os
from pathlib import Path
from test_run import helper
from doc_translate.cli import main
root = Path(os.environ['RUN_FIXTURE'])
main(['review', str(root / 'new'), '--units', 'unit-1'], process_command=helper(root))
PY_REVIEW
.venv/bin/doc-translate status "$RUN_FIXTURE/new" --json  # review履歴・未確認範囲、意味品質承認false
.venv/bin/doc-translate render "$RUN_FIXTURE/new"  # offline QA更新
```

これは機械的な範囲／保存／接続の検査であり、実LLMの指摘精度、誤訳不存在、人間の全文査読やviewer実表示を保証しません。実provider・実認証・model取得は別途の個別承認が必要です。

## 基点付き修正・PDF補正候補採用の検証境界

Issue #16は公開`revise`／`resume`／`status`／`render`→workspace参照・保存hash・出力と、補正回答→接続済みsourceの既存seamを使います。新しいコマンド・汎用merge基盤は追加しません。契約は[artifact契約](docs/reference/artifacts.md#基点付き修正pdf補正候補採用issue-16)、利用者操作は[CLI詳細リファレンス](docs/reference/usage.md#原文訳文を修正する推論なし)を参照してください。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/pytest tests/test_cli.py tests/test_revise.py tests/test_run.py tests/test_pdf_candidate_acceptance.py
# Workerは変更境界をfocused確認し、Rootが上記「正式チェック」を実行
```

`tests/test_run.py`／`test_pdf_candidate_acceptance.py`は既存の人工PDF draft／PNG・Docling公開converter／page backend境界と、実Node helper／公開faux providerを再利用します。内部workflow／validatorのmockを増やさず、送信request・採用参照・旧hash・成果物を確認します。外部process故障注入は既存`tests/fixtures/resume_process.py`のatomic replace境界を使います。`ai/tests/run-process.mjs`を変更した場合は既存のNode format／lint／testも実行し、helper source変更時だけ明示buildを行います。

- 完了修正後の入力不変／glossary・文体改版、partialの不足ページだけの補正、補正provider／model／thinking明示変更と不要な取得なしを確認します。
- 候補待ちで下流requestが0件、H/B/Cと新旧scope分離、旧参照／hash保持、IDずれ・段落接続／分割／統合・削除／並替え・table／list／caption／図所属／参照変更で訂正の自動転写なしを確認します。
- Cそのまま／編集Kのオフライン採用、複数revision後の機械基点継承、stale source／candidate・別入力世代・不正構造／ref／hashの拒否を検査します。候補公開前／採用参照切替前後のSIGINT／SIGKILL相当中断、未確定非採用・再送なし・採用済み復帰・排他も検査します。
- 採用後の旧訳／手修正訳は実request全体一致だけで再利用し、code-only無再送と不一致の再翻訳、QA／出力更新、旧byte列hashの互換読込み、EPUB回帰を確認します。

直接の人工入力確認には、既存「新規runの検証境界」の`RUN_FIXTURE/new`を作成後、次の訳文patchを使えます。実provider・実認証・model・既存書籍は不要です。

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
# 上記照合と必要な証拠保存後、自分がこの手順で作った一時directoryだけをcleanup
# WORKSPACEとRUN_FIXTUREが両方とも今回のmktemp値であることを先に確認する
rm -rf -- "$WORKSPACE" "$RUN_FIXTURE"
printf '%s\n' 'owned temporary workspaces/credentials removed; no child left' > "$EVIDENCE/cleanup.txt"
test -s "$EVIDENCE/revision.txt" && test -s "$EVIDENCE/run.json"
cat "$EVIDENCE/copy-reject.txt" "$EVIDENCE/cleanup.txt"
```

cleanup後の出力・対応表・QA／usageは`$EVIDENCE/{render,run,patch}-artifacts/`で再読込みできます。summary内のworkspace相対pathは元の保存位置であり、cleanup後の証拠参照にはこの退避先の同名fileを使います。

PDF候補も同じ公開CLIを通す直接入口は`.venv/bin/pytest -s tests/test_run.py -k 'candidate_offline_adoption or revised_partial_pdf'`です。旧訂正版Hの保持、新候補Cの雛形コピーと任意のK編集、Nodeなしの明示採用→resume、元hash不変を検査します。検査対象は機械的な構造・scope・保存・再利用契約であり、意味的な訂正の完全保持・実LLM／Docling品質・viewer表示・remote CI成功ではありません。関連検査の成功を全体受入れや実provider検証の完了としません。

## wheelの明示buildと導入検査

```sh
pnpm --dir ai build
uv build
uv run python scripts/check_distribution.py dist/doc_translate-0.1.0-py3-none-any.whl
```

この検査は一時directoryで再compileして`ai/dist`と比較し、wheel内のdist／manifest／両lockをbyte比較します。core-onlyの別venvへwheelをinstallし、Nodeもmanagerもない空PATHで自作EPUB ZIPのreader→source保存／load→unit生成→実CLI render/statusを検査します。訳文なしのrenderはexit1・部分出力・`※`、statusはexit0・出力鮮度をassertし、実LLMや内容品質の成功とは扱いません。その後、workspace外の別々のclean data directoryで実CLI `setup-ai --package-manager MANAGER`を実行します（依存packageのregistry取得あり）。内部で使用する固定コマンドは次のとおりです。

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

login／refresh／TTL判定・認証変換はpi-ai 1.5.0の公開APIに任せます。OpenAI OAuthは安定UUIDと`agentName: "doc-translate"`を渡します。`providers[].login_methods`は公開interactive loginの有無だけを示し、ambient-onlyにlogin methodを作りません。OAuth refresh失敗後のAPI key fallback／request overrideは追加しません。

Pythonの`authenticate`はCLIから実helperへJSONLを送り、controlling TTYだけで非echo入力し、認証URL・device codeをterminalへ表示します。認証record／入力／URL・code／raw stderrやprovider例外をログへ残しません。SIGINT／SIGTERM／EOFで取消し、prompt取消・終了で入力queueをflushしてechoを復元します。進行中のstore callback／保存は取消から保護し`drain`、Pythonは強制kill前に30秒待ってchildを回収します。取消後の`storage_failed`も非秘密の保存エラーと再login案内を表示します。SIGKILL／disk障害時の保存・TTY復元は保証できません。

認証の当時のApproved Planでは、承認済み公開seamでTDDを行いました。今後は上の「保証・境界を決めてからTDDを選ぶ」に従い、既存PlanのTDD指定は勝手に解除しません。既存の保証とテストは維持します。`ai/tests/credentials.test.ts`は実storeと公開`createModels/createProvider/getAuth`で同時refresh・失敗時のno fallback・保存維持を検査し、Nodeの実processでも排他・heartbeat・UUID・drainを確認します。`ai/tests/auth.test.ts`は公開provider／実helper境界、`tests/test_auth_cli.py`は公開CLI・実helper／外部fake・PTYで非echo、callbackによるprompt取消、SIGINT／SIGTERM／EOF、未送信secretの破棄、取消中の保存失敗とchild回収を検査します。実credentialを読み込まず、人工credentialとnetwork拒否で検証します。このoffline検査に実認証・実token refresh・推論・文書送信は含めません。個別承認後の実認証・小範囲送信は上記Issue #18の記録を参照し、新たな送信等には別途個別承認が必要です。

## 推論adapterの実装・検証境界

`doc_translate.inference.generate`は文書workflowとは独立したPython APIです。段階指定→共通指定→`PI_PROVIDER/PI_MODEL/PI_REASONING_LEVEL`で設定を解決し、文書を含まないpreflightで正確なmodel／thinking・画像能力・認証sourceを確認してから、明示policy／text／context／glossaryと必要なPNGだけを送ります。各試行は新しいNode processで、Pi CLI設定・履歴・tools・探索・agent loopを使いません。文書CLIとの接続は`run.py`が担当します。APIの型・固定code・JSONLは[artifact契約](docs/reference/artifacts.md)を参照してください。

Pythonは回答JSONを別に解析してcallerのPydantic model（domain validatorを含む）で検証します。ID／根拠・送信許可範囲・座標説明の意味検証、artifact保存・採用は文書callerが所有します。PDF補正・準備・翻訳のcallerと、文書全体のsource採用は各文書stage／`run.py`に実装しています。画像は完全decode・実寸・hash等を検証して元byte列と順序を保持し、縮小・再encode・保存しません。公表入力上限超過は`input_limit`で停止します。`inputLimits.images.resize.maxBytes`はraw PNGではなくcanonical base64 payloadのASCII byte量に適用します。

成功にはNode terminal／stop reason・送信先・toolCallなし・Python schema検証・正常process終了がすべて必要です。workflow retryは最終`generation_failed`のlibrary heuristicによるretryableなerrorだけに初回込み最大3試行・待機3／6秒です。schema／認証／保存／timeout／取消はretryしません。libraryへ`maxRetries:0`を渡しますが、内部retryは観測できないため不明のままです。未観測usageも0ではなく不明とします。timeout／取消はchildへ伝え、stream終了を最大30秒待ち、協調的providerのterminal／最終message／usageを失敗のまま保持します。終了すれば直ちに待機を終え、credential refresh／保存のdrainも維持します。親は原則30秒のgraceでchildを回収し、非協調providerのterminal／usageは保証しません。drainで上書きされた失敗は、保持されたretry metadataだけを理由に再試行しません。

`ai/tests/generation.test.ts`と`generation-process.mjs`は公開fauxProvider／createProviderと実HTTP adapter＋injected fetchで実効入力・terminal・usage・retry設定・取消／refresh drainを検査します。`tests/test_inference.py`は実helperを使ってPython側の独立validation・domain validator・有限retry・画像保持・timeout／signal取消を検査し、`ai/tests/inference-provider.mjs`は人工認証／取消process、`tests/fixtures/inference_process.py`はprotocol故障注入だけを担当します。全Node fixtureは上記Biome／oxlintの対象です。このoffline検査は実provider通信・実credential・実token refresh・account利用権・課金・翻訳品質・全adapterの内部retry回数を検証しません。個別承認後の小範囲実動は上記Issue #18の記録を参照してください。offlineテストやwheelのimport成功をこれらの成功と扱いません。

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
resumed = subprocess.run([*entry, '--', 'resume', str(workspace), '--json'], capture_output=True, text=True, check=True)
summary = json.loads(resumed.stdout)
assert summary['processing']['status'] == 'completed'
assert len((work / 'requests.jsonl').read_text().splitlines()) == 5
assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in old.items())
status = subprocess.run([str(Path(sys.executable).with_name('doc-translate')), 'status', str(workspace), '--json'],
                        capture_output=True, text=True, check=True)
assert json.loads(status.stdout)['render_current'] is True
print(work, 'completed; 5 requests; old hashes preserved; saved status current')
PY
```

残る一時fileは採用対象でなく、試験workspaceも自動削除しません。実provider／実認証・OCRの新たな実行やmodel取得はこの検証に含めません。
