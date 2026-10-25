# doc-translate 初期実装計画 — PDF・テキストEPUBから日本語Markdownへ

## 目的

独立したPython／uv CLI `doc-translate` を新規作成する。英語の技術PDFとテキスト主体のEPUBを、内容を省略せず、原本への追跡・構造保持・要確認事項の可視化・再開を備えた日本語Markdownへ変換する。

本Planは実装の指示書であり、既存書籍の全文再実行や有料API実行の許可ではない。Planning後のImplementationは、新しいセッションでWork Itemごとに行う。

## 要件・決定事項

### 入力設計と確認済みの前提

- 基本契約は [基本設計](../design/doc-translate.md) と [用語集](../../GLOSSARY.md)。実装・検証担当は基本設計全体を確認する。特に§5–8（取得・原文・位置・unit）、§9–10（context・glossary・翻訳）、§12–17（安全・QA・保存・出力・利用量）、§18–19（実績と限界）を参照する。
- リポジトリには設計・用語集・PDFサンプルだけがあり、production code・package設定・既存の正式チェックはない。既存ファイルは未コミットであり、本作業で削除・上書き・勝手なcommitをしない。
- PDFはDocling固定、原本画像との補正LLMを経由する。EPUBは構造を直接取得し、OCR・補正LLM・PDF変換を要求しない。翻訳とMarkdown生成は共通化する。
- 両入力とも初期版の要件とし、EPUBを後回しの対象外にしない。受理範囲は基本設計§1に従う。
- 本文はですます調。見出し等は自然な体言止めを認める。要約・著者コードの改善・欠損の創作はしない。
- Markdownを中間正本にしない。翻訳前原文・訳文対応表・構造・assetsが出力生成の正本となる。
- 既存29／346ページ実験は参考資料であり、汎用実装・テストとして丸ごと移植しない。346ページ版の品質は未承認のままとし、その残件修正・全文再生成は今回の対象外。

### ユーザー確認済みの運用方針

1. 動作保証はmacOSを必須とする。検証対象は現在のmacOS 26.7.1 arm64環境、Python 3.14、Node 24とpi-ai 1.4.0を基準とし、macOS一般の最低対応versionやIntelでの成立を未検証のまま保証しない。Linux／Windowsの実機保証は対象外。
2. オフライン自動テストに加え、小範囲の実LLM検証を受け入れに含める。実行時に入力・hash・対象／参照範囲・送信先provider／modelを提示して個別に承認を得る。今回のPlan承認をその送信・課金許可として流用しない。
3. PDFの `--pages A-B` は翻訳所有範囲であり、既定の送信許可範囲もA–B内とする。外側のページは `--reference-pages` で明示指定した場合だけ参照専用として送る。参照ページは出力所有範囲へ追加しない。境界contextがなければ推測接続せず、断片と要確認事項を保持する。部分範囲を全文処理完了と表示しない。
4. Python 3.14を開発・検証基準とし、linter／formatterはRuff、静的型checkerはty、test frameworkはpytestを使用する。以前の3.12案は既存実験に合わせたものであり、3.14非対応を確認した判断ではない。依存解決・import・OCRを最初に確認し、不成立でも無断で3.12へ戻さない。
5. 推論は `@earendil-works/pi-ai@1.4.0` の直接利用へ変更し、利用者にPi CLI／coding agentを要求しない。provider選択と補正／翻訳の段階別model・thinkingは維持する。OpenAI固定にはせず、ChatGPTサブスクリプションOAuthを必須の実動確認対象とする。
6. 開発はNode＋pnpmを標準とし、初回helper依存導入はnpm／pnpmの両方をサポートする。通常実行は導入済みhelperをNodeで起動するだけとし、実行のたびにpackage managerを起動／installしない。
7. Node側のformatterはBiome、linterはoxlint＋正式package名 `oxlint-tsgolint`、test frameworkはVitestを使用する。type-aware／type-checkを有効にし、lint警告を解消してローカル正式チェックを通す。remote CIの実行・成功確認は受け入れ条件にしない。
8. GitHub ActionsによるCIを追加する。READMEは利用者向けドキュメントに限定し、設計・実装構成・開発手順は別文書へ分ける。

### remote CI受け入れ要件の改訂（Issue #2の再計画）

- 再計画対象は https://github.com/minorunakamura/doc-translate/issues/2 。ユーザー指定「CIの実行は要件にしなくて良い。今後も」に基づき、remote CIの実行・成功確認を今回および後続Work Itemの受け入れ条件から外す。実行ref／run URLの取得・記録、remote CIの起動・完了待ち、そのためのcommit／pushも必須にしない。
- GitHub Actions workflowの設定、既存および本Planで要求するsmoke検査、ローカル正式チェック・build／別環境導入検証は維持する。remote CIの未実行だけを理由にWork Itemを未完了・blockedにしない。設定済み／ローカル検証済みとremote実行済みを区別し、未実行をremote成功とは表示しない。remote実行結果によらず、必要なローカル検証で確認した不具合は修正する。
- 小範囲実LLM検証と個別の送信・取得承認、安全境界、lint警告0件等の他の要件は変更しない。
- 関連文書の整合修正は、Issue #2のImplementation再開時に `CONTRIBUTING.md` の「fixture・CIの境界」と基本設計§19の「初期CI・配布」のremote必須記述だけを対象に行う。CI設定・smoke検査・過去実験の記録は削除しない。更新完了までは、この節の方針を両文書の変更前のremote必須記述より優先する。
- 既存Work Itemの分割・依存関係・種別を維持し、Issue #2および後続のopen Work Itemは本改訂Planを参照する。Issue #18のremote CI実行・成功確認要件も除外する。完了済みIssue #1の過去の受け入れ記録は書き換えず、再openしない。
- この再計画では正式Planと確認済みWork Itemの更新のみを行い、既存の未コミット変更・部分実装はそのまま保持する。Implementationは別の新しいRootセッションで再開し、過去のチェック・Review・Code Review Approveを変更後の検証や承認として流用しない。

### 入力設計との差分と更新の合意

Pi CLIを経由しないことは基本設計からの合意済み変更である。目的・翻訳／補正の内容契約・原資料／snapshot・PDF／EPUB受理範囲・Python Workflowの文書処理責務は維持し、推論の接続方法だけを次のように置き換える。

- 基本設計§1–3・§5・§11–12・§15・§17・§19と関連する図／表記のうち、Pi CLI依存、Pi process、認証、終了event、依存と記録の説明を、Python＋Node helper＋pi-aiの契約へ整合させる。
- 新実装では「補正LLM」「翻訳LLM」を役割名とする。coding agentのtool／探索／context注入／自動回復は採用しない。Pythonが文書state・検証・有限retry・保存を所有し、Node helperは推論接続と認証の公開API実行に限定する。
- 終了判定はPi sessionの `agent_settled` ではなく、pi-aiのterminal event／最終AssistantMessage／helper正常終了とPython validationで行う。Pi CLIの認証／model設定を自動読込み・コピーせず、アプリ用の最小credential保存をworkspace外に設ける。
- 変更理由は、同じpi-ai providerの推論能力を利用しながら、利用者のPi CLI導入・coding agent設定依存をなくすため。CLI独自の画像前処理を失ったままにせず、画像・policy・model・thinking等の実効入力を明示検査する。
- **ユーザー確認済みの更新時期は、承認後の最初のImplementation。** Planning中は基本設計を編集しない。更新完了まで、この節と本Planの推論接続契約を変更前のPi CLI前提より優先する。
- §18の過去実験のPi／Python version・結果・限界は歴史的事実として変更せず、pi-ai直接経路で再実証済みとも扱わない。既存の用語集に無関係な追記や、新しいADR／差分管理文書は作らない。

## 制約・対象外

- 現在の出力は日本語Markdown、閲覧用英語Markdown、相対参照assets、対応表・QA・実行記録。PDF／EPUBの出力生成、DOCX、同一組版、図内文字の翻訳は作らない。
- EPUBはDRM解除不要のEPUB 2／3・整形式XHTML・リフロー型。画像主体、fixed-layout、DTBook専用、複数renditionの自動選択、非整形式XMLの自動修復、画像OCRは対象外。受理不能な入力は理由を示して停止する。
- 任意PDFの完全復元、実スキャンへの一般化、任意viewerの表示、全文の意味品質は保証しない。取得不能部分は黙って落とさない。
- 汎用plugin基盤、抽出ツール切替、汎用workflow engine、DB／常駐サービス、GUI／対話型編集画面、先回りしたmodel最適化は追加しない。
- 実験資料のコピー、書籍・その切出し・認証情報・model重みのrepositoryへの同梱／公開はしない。fixtureは原則として自作の小さな文書と人工応答を使う。
- packageやmodelの取得、大きなダウンロード、範囲外データや別providerへの送信を無断で実行しない。事前費用見積りを新たな必須停止点にはしない。

## 実装方針

### 1. package・依存・実行環境

- `src/doc_translate/`、`tests/`、`ai/`、`pyproject.toml`、`uv.lock`、`.python-version`、README・CONTRIBUTINGを新設し、console entry pointを `doc-translate` とする。コードは通常のPython／TypeScript・英語identifierで記述する。
- READMEは日本語の利用者向け案内に限定する。対応入力と限界、前提環境、導入／setup、provider選択／認証、翻訳／再開／局所修正のCLI例、成果物・QAの読み方、エラー時の対処を記載し、設計・内部実装・開発toolの手順は載せない。
- 開発環境、source構成、正式チェック、helper build、lock更新、fixture／CIの扱いは `CONTRIBUTING.md` に記載する。基本設計は既存の `docs/design/doc-translate.md`、artifact／修正JSONの契約は `docs/reference/artifacts.md` に置く。
- Python 3.14を開発・検証基準とする。`argparse` と標準ライブラリを優先し、永続／LLM JSON契約にPydantic `>=2.12,<3`、EPUB XMLに `defusedxml==0.7.1`、共通の画像検証・切出しにPillowを使う。CLI framework、EPUB framework、Markdown再解析用依存は追加しない。
- PDF依存はoptional extra `pdf` に分離し、`docling[ocrmac]==2.142.1`、`docling-core==2.104.0`、`ocrmac==1.0.1` を宣言する。Pydantic・Pillow等の解決済みversionと推移依存もuv.lockへ固定する。未検証のslim-extra最適化はしない。
- OCRは `OcrMacOptions(lang=["en-US"], framework="vision", recognition="accurate")` を明示し、macOSのローカル認識を使用する。EasyOCR／RapidOCRへの暗黙fallbackをしない。
- PDFはCPU実行を基準とし、Heron layout（`docling-project/docling-layout-heron`、revision `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`）とTableFormer V1 accurate（`docling-project/docling-models`、revision `fc0f2d45e2218ea24bce5045f58a389aed16dc23`、v2.3.0）を初期方式とする。不要なVLM・code/formula enrichment・画像説明modelは有効化しない。
- layout model cardはApache-2.0、table model cardはCDLA-Permissive-2.0、Docling／OCRMacのcodeはMIT。Apple VisionはOS側の条件でありMITと扱わない。利用する推移依存のlicense・native動作条件も導入時に記録し、packageのlicenseだけで重みやOS frameworkまで一括認定しない。
- model重みは同梱せず、利用者が明示準備したlocal artifact directoryを使う。READMEに対象repo・固定revision・必要ファイル・licenseと限定取得手順を記載する。汎用の全model downloaderや新しい取得管理機構は作らない。変換中の自動取得を抑止し、不足時は必要artifactを示して停止する。
- `enable_remote_services=False`、`allow_external_plugins=False` に加えてHub／Transformersのoffline設定とlocal pathを使用する。remote無効化だけをダウンロード禁止と誤認しない。
- EPUBのinstall／実行にDocling・OCR modelを要求しない。保存結果からのstatus／render／reviseにはNode helper・provider認証を要求しない。PDF依存は遅延importする。
- 開発依存はpytest・Ruff・tyとし、Ruffのtargetを `py314`、tyの `[tool.ty.environment] python-version` を `"3.14"` に揃える。正式チェックをCONTRIBUTINGへ記載する。release・project全体のlicense宣言は本Planの対象にしない。
- Node helperのsourceは `ai/src/` のTypeScriptとし、明示buildでESM `.js` へcompileする。TypeScript 7系以降の対応compiler／tsconfigを使い、pi-ai 1.4.0とファイル排他用のproper-lockfileをproduction dependencyにする。独自OAuth protocol、SDK、token refreshアルゴリズム、汎用agent frameworkは実装しない。依存とlicenseをlock／記録する。
- Node開発依存は `@biomejs/biome`、`oxlint`、`oxlint-tsgolint`、`vitest`、TypeScriptと必要な型宣言に絞る。互換性のある解決済みversionを両lockへ固定する。formatterはBiomeのformat機能だけを使用し、Biomeのlinter／assistは無効にする。oxlintは `--type-aware --type-check --deny-warnings` を使用する。警告を隠すquietや包括的disableで通さず、指摘を解消する。
- 開発の実動基準はNode 24.21.0／pnpm 12.10.1、npm導入の検証基準はnpm 11.19.0。pi-ai自体のNode要件は22.19以上だが、未検証の全Node versionでの動作を保証しない。
- compile済みhelper・package.json・pnpm-lock.yaml・package-lock.jsonをPython package dataとして同梱し、build済みwheelから初回setupできるようにする。Python wheel作成前にhelperを明示buildし、生成物とsource／manifest／lockの鮮度・包含を検証する。runtime install hookによるcompileは要求しない。Node自体の同梱や単一binary化はしない。
- `setup-ai --package-manager pnpm|npm` は明示指定されたinstalled managerのみを使い、アプリ所有のworkspace外runtime directoryへ依存を導入する。pnpmは `install --prod --frozen-lockfile --ignore-scripts`、npmは `ci --omit=dev --ignore-scripts --no-audit --no-fund`。通常起動時の自動install／npx／managerの自動取得はしない。
- 共通package.jsonと各manager用lockを保持し、依存更新時は両方を更新・clean install検証する。推移依存が両managerで完全同一だとは仮定せず、使用manager・lock hash・helper／library versionを記録する。既存modules treeへ別managerを上書きせず、選択lock／helper versionに対応する別runtime directoryを準備する。文書workspaceとcredentialは導入操作で削除しない。
- Node依存不足／不一致は必要なsetup操作を示して停止する。core-onlyの状態でstatus／render／reviseを使うためにNode依存や認証を要求しない。

### 2. 共通データ契約とworkspace

実装の初期段階で、契約を `docs/reference/artifacts.md` とPydantic modelsに記述し、小さなJSON例で両入力経路を確認する。将来用の抽象化ではなく、以下の保持情報と不変条件を共通の土台にする。

- schema version、入力形式、原本文書hash、不変の原資料、原文snapshot、block ID、unit ID、翻訳snapshot、policy／glossary／context snapshot、変更理由と基点。
- 原文blockの種別・本文・読み順・見出し／リスト階層・表cell／span・caption／脚注／コード注釈・図assets・数式・inline構造。
- PDF位置は文書内1始まりページ、抽出ref＋抽出snapshot、根拠位置、bboxの原点・単位・参照画像・描画条件。EPUB位置はresource path＋原本IDまたは当該snapshotのnode位置。EPUBへページやDocling refを要求しない。
- block／unit identityは位置情報と別にする。unitはblock／cell等への対応、原文、種別、heading／table／list祖先context、入力hash、参照snapshot、保護対象を保持する。
- 1itemの分割・複数itemの統合、元block／unitからの分割範囲・履歴を記録する。文字精度のない参照を文字単位の精度と表示しない。
- 不確実性は理由・対象ID・元位置・工程・原本自体の欠損か処理上の問題かを保持する。処理状態、機械検査結果、内容確認者／手段／範囲は別の情報とする。

workspaceは原資料、取得下書き、ページ画像／assets、snapshots、attempt、検証済み結果への参照、manifest、対応表、QA、usage、出力を通常のファイルとして保持する。既存原資料・旧版・元回答を破壊しない。

- 確定は同一filesystem上の一時ファイル書込み・flush／必要なfsync・atomic replaceで行い、参照先artifactを確定してから採用snapshot参照を切り替える。保存途中・未受理・確定済みを区別し、複数ファイルの途中状態を一括成功扱いしない。
- macOSのOS file lockで同じworkspaceへのwriter重複を防止する。lock fileの残存だけで稼働／停止を判断せず、crash後はOS lockの解放と確定artifactの再検証で再開する。lock所有権の独自DBは作らない。
- 再利用は基本設計§14の段階別実入力hashとartifact hashを検査する。原文取得、PDF補正、準備、翻訳、renderingで必要な入力だけを比較し、glossary変更でPDF補正を無効化しない。
- 検証済みの確定結果は再送しない。未完了回答と保存途中を隔離・記録し、受理していない結果を検証なしで復帰させない。送信後に応答が失われた場合のexactly-once課金は保証できず、usage不明・再送可能性を明示する。

### 3. CLIと公開操作

以下はすべて利用者が実行する **`doc-translate` CLIの公開サブコマンド** であり、内部API名ではない。 **これはコマンド一覧であり、全項目を毎回順番に実行する手順ではない。** `INPUT`／`DIR`／`ID`等は利用者が置き換える引数である。`MANAGER`は `pnpm` または `npm`、`METHOD`は `oauth` または `api_key` を指定する。各操作の入力・exit code・生成artifactを利用者向けREADMEとCLI helpに記載する。

| 操作 | 契約 |
| --- | --- |
| `doc-translate setup-ai --package-manager MANAGER` | 明示的な初回helper依存導入。Node／選択managerの可用性と固定lockを確認。推論・model取得・loginはしない |
| `doc-translate providers`／`doc-translate models --provider ID` | pi-aiのbuilt-in provider／modelと画像・thinking capabilityを非秘密metadataで表示。指定なしのcatalog refreshやLLM送信はしない |
| `doc-translate login --provider ID --method METHOD`／`doc-translate logout --provider ID` | 当該providerの公開認証flow／削除を実行。秘密入力は非echo、credentialはユーザー専用directoryへ保存 |
| `doc-translate run INPUT --workspace DIR` | 新規workspaceを作り、形式別取得→準備→翻訳→QA→Markdown生成。既存workspaceを黙って上書きしない |
| `doc-translate resume DIR` | 保存済み入力・設定を基準に検証し、未処理／失敗／明示変更で無効になった工程だけを実行 |
| `doc-translate status DIR` | 指定範囲、処理／失敗／QA／内容確認範囲、利用量、成果物の場所を表示。LLMを呼ばない |
| `doc-translate render DIR` | 保存した正本から出力・出力QAのみ再生成。取得・補正・翻訳を呼ばない |
| `doc-translate revise DIR --source FILE --reason TEXT` または `doc-translate revise DIR --translations FILE --reason TEXT` | 利用者が準備したJSON修正を検証し、新snapshotとして記録。LLMを呼ばない |
| `doc-translate review DIR --units IDS` またはPDFの `doc-translate review DIR --pages A-B` | 明示範囲だけを独立LLM呼出しで内容照合し、指摘・確認範囲・usageを記録。訳文／原文は自動上書きせず、品質全体を承認しない |

#### 初回準備と通常利用

必要なPython／Node等の環境を導入済みであることを前提に、未導入helperでOAuth認証を初めて使う基本手順は次のとおり。

1. **初回だけ** `doc-translate setup-ai --package-manager pnpm`（または `npm`）を1回実行する。
2. **初回認証時だけ** `doc-translate login --provider ID --method oauth` を1回実行する。
3. **文書を翻訳するとき** `doc-translate run INPUT --workspace DIR` にprovider／model／thinking等を指定して実行する。認証済みの2回目以降は原則この操作だけでよい。既存workspaceの続行は `resume` を使う。

- `providers`／`models` はproviderやmodel IDを調べたいときだけ使う**任意の参照操作**であり、setupとloginの間に必ず実行するものではない。
- provider／model IDを知っていれば参照操作を省略できる。provider選択をOpenAIへ固定しない。
- API key等のambient認証を選ぶproviderではlogin操作が不要な場合もある。OAuthは保存credentialを再利用し、通常のtoken refreshは内部で行う。毎文書の再loginを要求しない。失効等で再認証が必要なら理由を表示する。
- helper依存更新や初回setup未実施時にはsetupを案内するが、通常runで自動installはしない。
- PDF extra／local OCR・layout model等の形式固有の事前準備は、これらの共通helper／認証操作とは別に利用者READMEで明示する。EPUBにその準備を要求しない。

#### 各操作の入力・変更時の規則

- 初回runにも `--glossary FILE` を設け、自動準備結果より利用者指定のtermsを優先する。resumeでも同じ契約で新版を指定できる。
- model／thinkingは段階個別指定→共通指定→起動環境の `PI_PROVIDER`・`PI_MODEL`・`PI_REASONING_LEVEL`。PDF補正と共通翻訳にそれぞれ `--correction-*`／`--translation-*` を用意する。context／glossary準備は翻訳設定を使用する。reviewは当該操作の明示設定／保存された該当段階設定を使用する。値がない場合は送信前に不足を示し、未知modelへfallbackしない。
- PDFには `--pages`・`--reference-pages`・local model directory指定を用意する。所有範囲と送信範囲を別々に保存・表示し、参照指定で翻訳対象を増やさない。EPUBでページ指定は受理しない。初期EPUBは文書全体を対象とし、章範囲選択は追加しない。
- run／resume／reviewは送信前に原資料・hash・有効範囲・段階ごとの送信先を明示する。範囲／providerを拡大する変更は明示的な利用者入力を必要とし、自動fallbackしない。継続実行時も前回の許可範囲を越えない。
- reviseのJSON envelopeに修正元source／translation snapshotまたは同等の実入力hashを必須とする。古い基点・未知ID・不正参照・不正schemaは採用前に拒否する。source置換は全snapshotを検証し、translation patchは変更unitと保護対象・入力対応を検証する。ID一致だけで旧修正を適用しない。
- revise自体は再課金処理を開始しない。必要な影響範囲を記録し、後のresume／renderで更新する。policy変更は検証可能な新版ファイルを明示入力した場合に扱い、自由な実行コードとして解釈しない。
- 実処理のexit codeは0＝指定範囲が検証・出力まで到達、1＝処理失敗／部分出力、2＝入力・設定・受理範囲エラーを基本とする。要確認だけで処理失敗と同一にしない。statusは検査操作の成否を返し、内容状態は表示する。

### 4. pi-ai直接推論・認証・安全境界

#### 推論接続と有効入力

- 補正・準備・翻訳・局所reviewごとにNode helperを独立processとして起動し、公開 `builtinModels()`／`models.streamSimple()` を使う。coding-agent／agent-coreは依存に加えず、Pi CLIへのfallbackも作らない。tool宣言、会話履歴、project context、extensions／skills、agent loop、compaction、自動品質reviewを持ち込まない。
- built-inのOpenAI、Anthropic、Google、OpenRouter、Bedrock等を選べる。補正と翻訳は別provider／modelでもよい。認証・model条件を満たさないものは理由を示し、別provider／model／認証modeへ勝手に切り替えない。外部provider pluginやPi固有virtual model設定の再実装は対象外。
- Python↔Nodeのprotocolはschema versionとrequest IDを持つ最小のLF区切りJSONLとし、metadata／generation／認証対話に必要なrecordだけを定義する。LLM回答JSONとは別に解析する。文書dataからhelper操作やファイル探索を実行できるprotocolにしない。
- 正確なprovider／model ID・対応thinkingを公開APIで解決し、helper内では完全なmodel定義を使う。metadata段階で画像対応／認証sourceを確認してから文書requestを送る。full headers・秘密値・全credentialを返さず、Pythonは非秘密の設定だけを保存する。accountのmodel利用権をcatalog掲載だけで保証しない。
- 保存済み原文・明示policy・固定context／glossaryと、必要なPNG画像だけで新しいrequest contextを作る。provider／model・reasoning・thinking budgets・output budget・transport／timeout／retry等の実効request設定とversionを記録する。`off` を含む対応levelを確認し、要求値とproviderが確認できた実効値を区別する。
- 画像はPythonでPNG／寸法／base64 byte量・順序・hash・座標系を検査する。modelの `inputLimits` 等に従い必要な縮小を行う場合は最終画像・変換率・実寸と座標説明を保存して渡す。小文字が読めない状態や上限超過を画像の黙った省略で成功させない。provider側の未確認上限や追加変換まで保証したことにしない。
- ソースではCLIのModelRuntimeとpi-ai Modelsが同じ `provider.streamSimple()` へ合流する。直接経路に翻訳／visionのmodel能力欠落はないが、CLIの画像変換・残存prompt・設定overlayは自動では再現されない。このため単なるmodel名一致でなく、policy／画像／thinking等の実効入力をテストする。CLIとの出力完全一致・実データの同等品質を未検証のまま保証しない。

#### 認証を成立させる最小の保存

- pi-aiが提供するのはin-memory storeと `CredentialStore` 公開interfaceであり、永続storeはアプリが用意する。これは独立process間のOAuth refreshを安全に継続するために必要な最小追加であり、文書処理stateや独自アカウント管理基盤は作らない。
- ユーザー専用config directory（0700）にprovider IDをkeyとするcredential JSON（0600）とOpenAI login用の安定したinstallation UUIDを置く。文書workspace／export／repositoryへ秘密をコピーせず、Piのauth.json等も自動読込・移行しない。
- 公開 `read/list/modify/delete` を実装し、proper-lockfileの共通lock identityで**ファイル全体**のread→callback→atomic保存を排他する。異なるproviderの同時更新も失わせない。初期ファイル作成、atomic replace、heartbeat、lock待ちの取消をテストする。壊れたJSONや保存失敗を空credentialとして黙殺しない。
- `modify` callbackのundefinedは変更なしとして扱う。OAuthの `clientId/scopes` 等を含む追加fieldを削らず保持し、deleteも同じ排他で実行する。`list` はprovider／typeだけを返し秘密を解決・出力しない。
- login／refresh／API変換はprovider所有の公開APIへ委ねる。OpenAIは `models.login("openai","oauth",interaction,{getDeviceId,agentName:"doc-translate"})` を使用し、UUIDは再利用する。callback／manual code、secret入力・prompt取消をlibraryのinteraction契約で扱う。ambient-only providerは不可能なlogin methodを表示せず、公式の環境認証手順を案内する。
- 送信前には認証source・typeと送信先を明示する。OAuthを使用する実行でAPI keyをrequest overrideへ勝手に注入せず、refresh失敗後にenv key／別modeへfallbackしない。OpenAIのChatGPT subscriptionは共有利用枠であり、API課金・全model権限・無制限利用を保証しない。
- raw環境変数、secret入力、token／headers、秘密入りURL、認証をechoし得るstderrを無加工でlogへ保存しない。認証recordと文書generation recordを分け、logは非秘密metadataと検証・終了情報に限定する。

#### 終了・取消・有限retry

- `stream.result()` はerror／abortedでもmessageへresolveする。正常process終了だけ、または例外がないことだけで成功扱いしない。terminal done／error、最終AssistantMessageのstop reason、provider／model、helper終了、回答JSON・schema・根拠／ID validationを確認し、stop以外やtoolCallを採用しない。Pi sessionのsettled eventは要求しない。
- LF framingを連続消費し、Unicodeの行区切りをrecord境界と誤認しない。進捗recordから未完了内容を採用しない。失敗途中でも得られたusageを保持し、欠損は0でなく不明にする。
- 呼出しは初期値1200秒、workflowは初回を含め最大3試行・待機3／6秒。provider optionsには `maxRetries:0` を明示し、利用adapterでの反映を検証する。確認できない内部retryを0と断定せず、workflow試行数と内部の観測／不明を分ける。認証／範囲／隔離違反は闇雲に再試行しない。
- SIGINT／SIGTERM／親側pipe切断時はAbortSignalで推論を停止し、新規requestを受け付けずchildを回収する。強制kill前に原則30秒のgraceを確保する。
- **OAuth refresh中の即時exitは禁止する。** pi-ai 1.4.0はrefresh開始後、rotated tokenの保存を守るためcaller取消から切り離し、15秒の専用timeoutを使う。helperは進行中のstore.modify／保存完了を待って終了し、Pythonもそのdrainを待つ。SIGKILL／disk障害等で保証できない場合は、破損の空扱いや旧tokenへの無条件rollbackをせず、明示エラーと必要な再login案内を出す。
- 文書中の指示・shell例・linkは未信頼dataで、実行／自動fetchしない。翻訳・準備には原本画像・原本file・code本体・数式・XHTML全文を渡さない。Node helperに文書artifactの変更やsnapshot採用を任せず、Pythonがvalidation・保存を所有する。

### 5. PDFの取得・補正・接続

- Docling JSONのraw item・ref・provenance・table cell等を保存し、conversion status／errors／対象ページ被覆を検査する。Doclingの木を正本と断定しない。charspanを検証し、不一致を黙ってclipしない。
- Doclingのpage image公開APIから原本ページ画像を保存し、補正には所有ページすべての画像を渡す。長辺1800pxを初期値とし、描画条件・hash・座標変換を記録する。Popplerを新たな必須依存にしない。
- 補正chunkは所有5ページ＋前後各1ページを初期値とし、参照は許可済み範囲内だけにする。所有ページは重複なく被覆し、contextページを二重出力しない。
- 補正JSONに本文・見出し・list・code／callout・formula・table・picture・caption・footnote・除外理由・根拠・不確実性を表現する。画像からのみ復元した内容は実在しないrefを作らず、ページ・見える位置・理由を持たせる。
- 図は保持する画像領域と、その内部に属するref／block／cell等を明示して対応付ける。Pythonは内部内容を翻訳unit／本文として二重出力しない。caption・外側本文は残し、bbox重なりだけで削除しない。部分itemの所属を確定できない場合は原画像と要確認を残す。
- proseの空白／分綴／code改行の接続と、listの親子継続を分離する。ページを越す親は元位置・根拠に基づく参照をPythonが所有blockへ解決し、文書内IDにする。参照ページの親blockを重複出力しない。kind・順序・階層・循環・複数候補を検査し、曖昧な親を推測しない。
- 接続矛盾は断片を保持して別々に翻訳し、QAへ示す。長いcode／複数ページtableを要約・勝手に接合しない。構造を確定できない表等は画像／原データで保持する。
- 上限時はchunkを縮小する。単一ページ／構造がなお上限を超える場合は必要位置と原画像を残し、確定できない復元を成功扱いしない。

### 6. EPUBの直接取得

- `zipfile` のbounded readと `defusedxml` を使う。archive member数・個別／総展開量・XML深さ／node数に有限の上限を設けて文書化し、実際の読込量でも検査する。`extractall` に依存しない。
- traversal、絶対path、重複／曖昧な正規化path、symlink、不正圧縮・暗号化ZIP等を検査する。resource解決はpackage／XHTMLの基点に従い、percent-encoding・fragmentを含めarchive内位置へ正規化する。外部resourceを取得しない。
- entities・外部XML参照を禁止する。外部取得／実体展開を伴わないDOCTYPEと整形式の本文を扱い、旧EPUBのDOCTYPEがあるという理由だけで無条件拒否しない。独自entity解決や壊れたXML修復はしない。
- container→単一package→manifest／spineの順で読む。順序、idref、fallback連鎖の欠落／循環を検査し、textで取得できない本文resourceを黙ってskipしない。
- fixed-layoutの全体／item設定、画像主体、DTBook、複数rendition等を検出して理由を示す。encryption.xmlの存在だけでDRMと断定せず、対象resource・algorithmを検査する。本文／必要画像のDRM解除は行わない。未使用の難読化fontは原資料に保持し、そのためだけに本文抽出を禁止しない。
- XHTMLのmixed contentをtext／child／tail順に取得し、見出し・list・table span・caption・notes・links・alt・inline強調を対応付ける。spineのlinear=noやspine外の参照notesを取りこぼさず、目次／補足と本文を区別する。対象nodeの処置をcoverageとして記録する。
- 段落やitemの文脈をunitにし、各text nodeを独立翻訳にしない。構造markerと保護tokenを持つinline表現を使い、和訳の語順を妨げず、link target・ID・literal・code・数式をLLMの自由編集から外す。構造の釣合い・必要token対応を検証する。
- pre／codeの空白・改行を保持する。数式はMathML等の原データ、図は元assetと出典を保持する。script・event属性・任意CSS・外部自動参照を出力へ無加工で継承しない。

### 7. unit・context／glossary準備・翻訳・局所修正

- Pythonが検証した翻訳前原文からunitを生成する。code本体・formula・figure内部を翻訳unitにしない。表cell・caption・notes・alt等は原構造へ戻せるIDを持つ。
- 大きすぎるunitはworkflowで範囲と親対応を保持して分割する。inline／保護範囲を壊さず切れる箇所がなければ黙って省略せず、部分出力／失敗と理由を残す。LLMにunit変更を任せない。
- 保存原文を章等のまとまりで読み、用語候補・用例・出典位置を収集して文書全体で整理する方式を1つ実装する。出典ID／引用の原文対応を検査し、一般知識だけの候補を本文確認済みと扱わない。
- context、採用glossary、文体policyを翻訳前に固定する。採用glossaryは `version/book/style/terms` を基本とし、候補・根拠は別ファイル。準備の実行設定・model・usageも別工程として記録する。利用者termsを優先し、styleは単一の文体policyから導出する。
- 翻訳chunkは章節／unitを尊重し、原文約12,000文字・concurrency 2を初期値とする。翻訳LLMには固定unit・必要なcontext・採用glossary／policyだけを渡し、全IDを順番通り1回ずつ返させる。空訳・欠落・重複・未知ID・保護対象の変更を検査する。
- 原文／context／schema／model等の実入力一致がある訳だけ再利用する。原文の分割／統合で対応が曖昧なら旧訳を機械接合せず、履歴を残して必要範囲を再翻訳する。codeだけの変更で翻訳入力が同じなら本文を再送しない。
- glossary／文体変更後は影響する訳を再検証・必要に応じ再翻訳する。限定範囲を証明できなければ旧訳全体を未確認として扱い、初期実装では保守的に翻訳全体を更新対象とできる。単語検索／一括置換だけで適合済みにしない。原文取得・PDF補正はやり直さない。
- 準備方式だけの変更で採用glossary・context等が同じなら再翻訳しない。
- reviewは明示範囲のfindingsと確認手段を記録するだけで、空の指摘やmodel自己申告を品質承認としない。修正はreviseの検証・履歴保存を経る。全文の追加LLM reviewは既定にしない。

### 8. Markdown・QA・利用量

- 保存した原文・訳文・構造・assetsからPythonで日本語／英語Markdownを生成する。code／formulaは原文snapshotを使い、code calloutはfence外、図は原画像／保持assetを使用する。
- 単純表はMarkdown、結合等を持つ表は保存構造から安全なHTML tableを生成する。raw EPUB HTMLは貼り付けない。出力文字列・属性・URLを適切にescape／許可制限する。
- LaTeX・安全なPresentation MathML・画像数式を保持する。MathML→LaTeX変換は要求しない。任意annotation-xml等を通さない。安全に表示できない数式／markup／active assetは元データを保持し、実行可能な形で埋め込まず、escapeした参照資料等とQAで明示する。危険なSVG等を無加工で有効化しない。
- EPUB内link／note／anchor／assets参照を出力位置へ対応付ける。未解決の参照は原情報と要確認を残す。外部resourceの自動読込みを防ぎ、literal URLの内容は保持する。
- 要確認は表示時に `※` だけを該当箇所の末尾等へ付ける。原文／訳文そのもの、code／formula内部に目印を混入しない。QAには理由・元位置・block／unit ID・出力anchor／位置を対応付ける。
- QAは全範囲の構造／coverage／ID／保護内容／参照関係／出力整合を検査する。PDF独立抽出や単語一致を意味品質の証明にしない。内容reviewの実施者・方法・範囲と未確認範囲は別記録にする。
- summaryには指定範囲の出力到達／未処理／失敗、機械検査、原本欠損・要確認件数、内容確認範囲、QA・成果物pathを表示する。未処理／失敗が残れば部分出力とし、「完全翻訳」「内容品質承認済み」と表示しない。
- 準備／補正／翻訳／reviewの全attemptについてprovider・model・要求／確認済み設定・usage生値・経過時間・終了・retryを記録し、ローカル処理時間と全体wall timeを分ける。reasoningはoutputへ二重加算しない。失敗時usage不明は不明とし、参考価格換算と実請求を区別する。

## 実装順序と影響範囲

以下は依存する責務の順序であり、Work Item分割の承認ではない。承認後にticket Skillで実装セッション単位へ分割する。

1. 合意済みの基本設計更新、Python／Node package・二種類のhelper lock／正式チェック／共通schema・fixture・artifact契約、最小のsnapshot／lock保存、GitHub Actionsの品質／offlineテスト基盤。
2. setup-ai／認証store・login／公開pi-ai helper契約・preflight・失敗／利用量の扱い。
3. PDF取得／補正／図所属／ページ接続、およびEPUB安全読込み／構造・inline取得。両経路は同じ原文snapshotへ接続する。
4. unit生成・文書context／glossary準備・翻訳validation。
5. 安全なMarkdown生成・対応表・QA・status。
6. resume／revise／局所review・全CLIの接続、障害復旧検証、CIのbuild／両manager導入検査、小範囲実動確認、利用者READMEとCONTRIBUTINGの仕上げ。

変更が到達する場所は新規package、console entry point、両入力adapter、共通schema／workflow、Node helper／pi-ai境界・認証、保存・修正、render／QA／usage、tests／fixtures、Python／Node依存lock、GitHub Actions workflow・tool設定、基本設計の合意済み箇所・利用者README／CONTRIBUTING／契約文書。既存実験やsampleを実装の必須読込元にしない。

## GitHub Actions CI

- `.github/workflows/ci.yml` を新設し、push／pull_requestで正式チェックを実行する。最初は標準GitHub-hosted `macos-15`（ARM64）を使い、Python 3.14・Node 24・pnpm 12を明示選択する。Linux CIだけでmacOS OCR対応を検証したことにしない。
- 少数のjob／stepで、PythonのRuff／ty／pytest、NodeのBiome format／oxlint type-aware＋type-check（warning 0）／Vitest、helper build、wheel build、別環境installを実施する。core-onlyとPDF extra付きの依存解決・importを分けて確認する。
- buildしたwheelのresourceからnpm／pnpmそれぞれを別々のclean runtime directoryへ固定lockで導入し、CLI help／static metadata／offline helper protocolを確認する。開発source treeに偶然残っていたnode_modulesやPi CLIに依存しないことを検査する。
- CIでは実credential・実providerへの推論・OCR／layout重みの自動取得・既存書籍を使用しない。人工fixtureとfaux providerを使い、Hub offline設定等を明示する。依存packageを取得するinstallと、外部接続なしのテスト本体を区別する。PDF依存のimport成功を、重みを使う実Docling変換／翻訳の成功と表示しない。
- actionsは公式setup／checkoutを使用し、導入時に対応versionと固定refを確認する。pnpm setupは指定version・install自動実行なしとし、lock付きinstallを別stepで明示する。
- `permissions: contents: read`、checkoutの `persist-credentials: false` を基本とし、provider secretsは登録・参照しない。privilegedなpull_request_targetでPR codeを実行しない。実認証や有料検証をPR CIの必須stepにしない。
- workflowとローカル正式チェックを揃え、lint警告は抑制して合格にせず解消する。チェックをskip／continue-on-errorで見かけ上greenにしない。
- private repositoryでのmacOS runner利用は利用枠・課金条件に従う。remote CIの実行・成功確認・実行ref／run URLの記録は、今回および後続Work Itemの受け入れ条件にしない。CI実行のためのcommit／push／releaseを要求せず、このPlanningでも行わない。設定済み・ローカル検証済み・remote実行済みを区別し、設定だけを作ってremote CI成功とは扱わない。

## 完了条件

1. macOS／Python 3.14でcore-onlyとPDF extra付きのinstallが成立し、EPUBだけならDocling／OCR modelを要求しない。build済みwheelからnpm／pnpmの両経路でsetupでき、Pi CLI非依存・通常実行時Nodeのみを確認する。
2. テキストPDF・画像のみPDF・受理対象EPUBの小入力が各経路から共通の原文／unit／訳文へ進み、日本語Markdown・英語Markdown・assets・追跡対応・glossary根拠・QA・usageを保存できる。
3. 図内部textの二重翻訳防止、ページを越すlist親子、脚注を挟む本文継続、code改行、表cell／span、inline／literal保護がfixtureで確認できる。
4. 範囲外参照の無断送信、秘密の永続log混入、tool／extension実行、EPUBのpath逸脱／過大展開／外部取得／active markupを防ぐ検査がある。対象外入力・不正応答を成功扱いしない。
5. SIGINT／強制終了・保存途中・provider失敗後も旧成果物を失わず、検証済み結果を再送せず再開できる。重複writerを拒否し、未完了結果やhash不一致を無条件採用しない。
6. glossary／policy／source／訳文の局所変更を履歴付きで扱い、stale patchを拒否する。取得・補正を不要に再実行しない。renderはLLMを呼ばない。
7. 指定範囲の処理到達・機械検査と内容品質の確認範囲を分けて報告し、部分出力や要確認を隠さない。
8. GitHub Actionsに下記のoffline正式チェック・build／導入検証（Python: Ruff／ty／pytest、Node: Biome／oxlint／oxlint-tsgolint／Vitest）を設定し、対応するローカル正式チェック・build／導入検証を完了する。lint警告は0件とする。remote CIの実行・成功確認・実行ref／run URLの記録は今回および後続Work Itemの受け入れ条件ではなく、未公開／未実行だけでは未完了としない。CI workflow・smoke検査は維持する。
9. 下記の正式チェックと、実行時に別途承認された小範囲実LLM検証（OpenAIのChatGPT subscription OAuthを含む）を完了し、条件・観測・限界を記録する。有料検証が未承認／未実行なら、その受け入れ条件を未完了と報告する。

## 動作確認方法

### TDDと公開テスト境界

Implementationでは `tdd` Skillを読み、1つの振る舞いのred→greenを繰り返す。本Planの承認対象として、次のseamをテスト境界にする。private helperのmockや、実装式を再計算するだけのassertは避ける。

| seam | 確認すること／限界 |
| --- | --- |
| CLI→workspace／出力／status | 利用者操作、exit code、保存／再開・修正の観測結果。意味品質までは保証しない |
| PDF／EPUB取得→原文snapshot | 受理範囲・coverage・位置／構造・安全境界。PDF読取りの完全性は原本照合も必要 |
| 補正回答→接続済み原文／units | 根拠・所有・図所属・list親子・断片保持。modelが画像を正しく読めた証明ではない |
| Python↔Node／pi-ai境界 | 実helper processと公開faux providerでJSONL・最終result／abort・認証保存・timeoutを検証。実providerの品質／利用権は別途smoke検証 |
| 準備／翻訳→採用glossary／訳文snapshot | 根拠対応・利用者優先・固定version・ID／保護token・再利用。訳文全体の意味は別確認 |
| 正本→Markdown／対応表／QA | code／table／formula／links／目印と原データの不変性。viewer表示は実表示も確認 |

人工PDF／EPUBと固定した人工LLM応答を使い、内部部品を置き換えず外部process／model境界だけをfake化する。helper側はpi-ai公開faux provider／createProviderをテストseamで注入し、private methodをmockしない。Python CLI／workspaceとprocess境界はpytest、Node helper／認証store／pi-ai接続の公開契約はVitestで検査する。Vitestからも公開faux provider／createProviderを使い、private実装をmockしない。大量の想像上のテストを先に書かず、各公開動作を縦に実装・検証する。

必須回帰・境界ケース：

- 図全体と内部paragraph／table cellを対応させ、内部だけunitから除外する。近接本文とcaptionは残す。誤所属・不明所属も確認する。
- 前ページの親listと次ページの子を別blockのまま保持し、親を1回だけ出力。未知／複数／循環親を確認する。
- 脚注越しの本文接続、分綴、code language不一致、code末尾欠損の非補完、結合cell／空cell、巨大unit・length stopを確認する。
- EPUB2／3、manifest順と異なるspine、linear=no／spine外notes、IDなしnode、mixed inline、pre空白、画像alt、MathML、fallback循環、固定layout／DRM／複数renditionの停止を確認する。
- ZIP path／重複／symlink／展開上限、XML entity／外部参照、script／active URL／SVG／markup注入を確認する。DOCTYPEがある安全なXHTMLも確認する。
- model違い／画像非対応、stream.resultがerrorへresolve、terminal欠落／aborted／length、壊れたJSONL末尾、未知／欠落／重複ID、toolCall／retry条件、usage不明、U+2028／2029を含むJSONLを確認する。
- request境界でpolicy・image bytes／MIME／順序／実寸／座標説明・context・glossary・model metadata・reasoning設定を確認する。画像の黙った縮小／省略、CLI依存のcwd等の混入を防ぐ。これは意図したrequest保持のテストであり、実訳の完全一致テストではない。
- OpenAIに固定せず、複数provider／補正と翻訳で異なるmodelを選択できることをmetadataと人工providerで確認する。static provider列挙で不要なrefresh／送信をしないことも確認する。
- credentialの別provider同時更新、OAuth refresh二重実行防止、取消中のrefresh drain／保存、壊れたJSON、保存失敗、logoutとの排他、追加OAuth field保持、logへのsecret混入防止を人工credentialで確認する。実credentialをテストfixtureにしない。
- 各確定前後でprocessを止めて再開し、正常artifactの保持、未確定の非採用、hash不一致、stale修正、同時writer拒否を確認する。採用済み工程の再送がないことは外部fake processの入力記録で確認する。

### 正式チェック

以下の正式チェック・build／導入検証はローカルで実行する。remote CIでの再実行・成功確認・実行ref／run URLの記録は受け入れに必須ではない。各Work Itemはその責務に対応する検証を行い、統合検証で本Plan全体を確認する。

実装後の基本コマンド：

- `uv sync --locked --dev`、core-onlyテスト（PDF integrationを明示的に区別）。
- `uv sync --locked --dev --extra pdf`、`uv run pytest`。PDF／OCR integrationはlocal model準備済みのmacOSで実行する。
- `uv run ruff check .`、`uv run ruff format --check .`、`uv run ty check`。
- `pnpm --dir ai exec biome format . --error-on-warnings`、`pnpm --dir ai exec oxlint --type-aware --type-check --deny-warnings .`、`pnpm --dir ai exec vitest run`。Node source・test・設定を対象とし、生成物／node_modulesを検査対象へ無差別に含めない。
- `pnpm --dir ai build` でcompile済みhelperとruntime resourceを準備後、`uv build`。pytestからcompile済みhelperの実process契約も検査する。
- buildしたpackageの別環境installと `doc-translate --help`、core-only EPUB経路、PDF extra付き経路を確認する。npm／pnpmは別々のclean runtime directoryで固定lock導入を確認し、Pi CLIがPATHになくてもhelperが動くことを確認する。開発はpnpm経路を基準にし、両lockの変更時は両導入経路を再検証する。
- 外部model／認証を必要とする検証のskipは理由付きで区別し、未実行を成功と表示しない。

### 小範囲の実動・表示確認

- 自作の数ページの技術PDFと同内容の画像のみPDF、構造を含む小さなEPUBで実際の取得・準備・補正／翻訳・出力を確認する。電子版と画像のみ版の区別、使用OCR／model・条件を記録する。
- 既存実験の失敗形状を自作fixtureで回帰させる。既存346ページ書籍の再実行を前提にせず、既存ページそのものの送信が必要なら別途明示承認を得る。
- OpenAIのChatGPT subscription OAuthによる画像補正と翻訳を小範囲で確認する。利用権・利用量上限による失敗はAPI keyへのfallbackで回避せず、未完了を報告する。別providerも選択可能な構成を保ち、実認証がある場合は別途承認された範囲で追加smoke検証できる。
- 送信前に対象・hash・所有／参照範囲・各provider／model・認証sourceを提示して承認を得る。model取得も対象・revisionを示し、無断追加しない。
- code／formula／figure、結合表、list階層、EPUBリンク／脚注／inline、glossaryの本文根拠と適用、ですます調、要確認の目印を原資料と標本照合する。主張できる確認範囲だけを記録する。
- HTML tableと数式／MathMLは、確認に使用するMarkdown viewer名・versionと表示条件を記録して実表示を確認する。対応しないviewerでは原データ参照とQAで追えることも確認する。
- 人間による全文査読・統計benchmark・任意スキャンの一般性をこの小範囲検証から主張しない。

## 根拠・参照先

### 実装判断に必要な公開一次資料

- [Docling 2.142.1 package metadata](https://github.com/docling-project/docling/blob/v2.142.1/packages/docling/pyproject.toml)、[依存宣言](https://github.com/docling-project/docling/blob/v2.142.1/pyproject.toml)、[page／figure image API例](https://github.com/docling-project/docling/blob/v2.142.1/docs/examples/export_figures.py)。
- [OCRMac adapter](https://github.com/docling-project/docling/blob/v2.142.1/docling/models/stages/ocr/ocr_mac_model.py)、[OCR options](https://github.com/docling-project/docling/blob/v2.142.1/docling/datamodel/pipeline_options.py)、[OCRMac 1.0.1 metadata](https://pypi.org/project/ocrmac/1.0.1/)、[OCRMac license](https://github.com/straussmaximilian/ocrmac/blob/main/LICENSE)。
- [Heron固定revision model card](https://huggingface.co/docling-project/docling-layout-heron/blob/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8/README.md)、[Table model固定revision card](https://huggingface.co/docling-project/docling-models/blob/fc0f2d45e2218ea24bce5045f58a389aed16dc23/README.md)。取得対象はruntimeに必要なファイルへ限定する。
- [EPUB 3.3](https://www.w3.org/TR/epub-33/) のOCF／package／spine／fallback／rendition、[OPF 2.0.1](https://idpf.org/epub/20/spec/OPF_2.0.1_draft.htm)、[EPUB構造意味語彙](https://www.w3.org/TR/epub-ssv-11/) のnotes／noteref。
- [defusedxml 0.7.1 implementation](https://github.com/tiran/defusedxml/blob/v0.7.1/defusedxml/ElementTree.py)、[Python 3.14 zipfile](https://docs.python.org/3.14/library/zipfile.html)。parserだけでは出力markupやarchive全体の安全性は保証されない。
- [pi-ai 1.4.0 README](https://github.com/earendil-works/pi/blob/v1.4.0/packages/ai/README.md) のModels／Auth／Credential Store／OAuth／Image Input／Simple options／Error handling、[公開types](https://cdn.jsdelivr.net/npm/@earendil-works/pi-ai@1.4.0/dist/types.d.ts)、[認証interface](https://cdn.jsdelivr.net/npm/@earendil-works/pi-ai@1.4.0/dist/auth/types.d.ts)、[refresh実装](https://cdn.jsdelivr.net/npm/@earendil-works/pi-ai@1.4.0/dist/auth/resolve.js)。
- [CLI側ModelRuntime](https://github.com/earendil-works/pi/blob/v1.4.0/packages/coding-agent/src/core/model-runtime.ts) と [pi-ai Models](https://github.com/earendil-works/pi/blob/v1.4.0/packages/ai/src/models.ts) のprovider.streamSimpleへの合流、[CLI画像処理](https://github.com/earendil-works/pi/blob/v1.4.0/packages/coding-agent/src/utils/image-process.ts)。同じmodel名だけでなく実効入力を検査する根拠。
- [Biome CLI](https://biomejs.dev/reference/cli/)、[Oxlint type-aware](https://oxc.rs/docs/guide/usage/linter/type-aware)／[warningの扱い](https://oxc.rs/docs/guide/usage/linter/quickstart.html)、[Vitest](https://vitest.dev/)、[GitHub-hosted runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、[GITHUB_TOKEN permissions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication)。
- [npm ci](https://docs.npmjs.com/cli/v11/commands/npm-ci)、[pnpm install](https://pnpm.io/cli/install)、[proper-lockfile](https://github.com/moxystudio/node-proper-lockfile)、[ty Python target](https://docs.astral.sh/ty/python-version/)、[Pydantic 2.12のPython 3.14対応](https://pydantic.dev/articles/pydantic-v2-12-release)。

### ローカル根拠と限界

- 基本設計§18が保存実験の主要結果と限界を自己完結で記録している。実験directoryが失われても仕様理解は成立する。
- 保存済み346ページrunnerの `run.py:137–186,315–369` は図内部所属情報の欠如と同一ページ限定のlist親参照を示す。新実装ではその制限を引き継がない。保存先は基本設計§18を参照し、実験へのruntime依存は作らない。
- Python 3.14向けmacOS arm64 wheelの存在とpi-aiのsubscription／multi-provider APIは公開一次資料で確認した。依存全体のresolve／install、Node helper／credential store、provider認証・画像補正・翻訳品質の実動はまだ確認していない。実装開始時のcompatibility検証と小範囲受け入れを省略しない。
- Docling／OCRMac／pi-aiの今回の選定は公開API・metadata・licenseを確認した設計判断であり、今回のPlanningではinstall・model取得・OCR／LLM実行をしていない。導入互換性・表示・内容品質・任意時点復旧はImplementationで上記の方法により検証する。
