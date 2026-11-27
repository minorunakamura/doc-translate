# artifact／JSONL契約（schema version 1）

現在のコードが実装する公開API・保存形式・拒否条件・exit codeを記述します。責務と方針は[基本設計](../design/doc-translate.md)、CLIの使い方は[CLI詳細リファレンス](usage.md)、検証手順は[開発ガイド](../../CONTRIBUTING.md)を参照してください。

共通の前提：保存modelは余分なfieldと非有限数値を拒否します。保存は既存`workspace_lock`（非blockingのPOSIX flock、`WorkspaceLockedError`）下で、同filesystemの一時file→write／flush／fsync→atomic replace→directory fsyncの順に不変artifactを確定し、採用pointer（`SnapshotReference`：`{"schema_version":1,"sha256":"…"}`）を最後に切り替えます。旧版・途中artifactは削除せず（翻訳snapshotの途中版は後述「snapshotの保持」の例外）、pointer切替前の残存物を自動採用しません。loadはbyte hashとschemaを再検証します。workspace lockは非再入で、公開stage APIは自分でlockを取るため、callerは外側から同じlockを取りません。run／resumeだけが全工程のlockを所有し、同process／同threadのstageへ内部で貸します。

## 明示範囲review

公開CLIは`review DIR --units unit-1,unit-2`またはPDFの`review DIR --pages A-B`、APIは`doc_translate.review.review_document(workspace=..., unit_ids=[...], selection=Selection(...), process_command=..., before_send=..., observer=...)`（ページ指定時は`unit_ids`に代えて`pages=(A,B)`）です。`load_reviews(workspace)`はhash・歴史的source／translation・実request・helper結果・対象ID／元位置を保存-onlyで再検証します。推論は既存`generate(stage="review")`を使い、会話履歴や自動全体reviewはありません。

### 入力と範囲

- unit IDsかページ範囲のどちらか一方が必須です。IDは現在の有効translation chunkの分割済みunit（`.part-N`含む）で、未知ID／親IDの推測展開はしません。現在のsource・準備・runのpolicy／terms／翻訳設定に対応する受理済み訳文が必要で、未訳・staleなら無送信で不足条件を示します。candidateがpendingでも採用版は照合できます。
- unitモードは選択unitのID・block/cell/node/attribute・親unit／source_range、種別、marker原文・対応訳、選択inline tokenのmarker／kind／text、表の行列・spanと固定policy／採用glossaryだけを送ります。tokenの任意metadata、他unit本文を含む`unit.context`、全文`preparation.context`、block全文、画像は送りません。
- PDFページモードは保存`page_images`の指定ページだけをページ順に使い、PNG bytes／hash／実寸／scale／座標・描画条件を検査します。送るのは指定ページのblock本文・最小構造（table cells等）と対応unit訳だけです。block位置・table cell evidence・unit位置・図regionのページを検査し、選択外へ跨る本文／訳文は送らず範囲エラーにします。
- 設定は明示指定 > 保存該当段階設定（unitsはtranslation snapshot、pagesはrunまたは採用sourceのcorrection設定）。不足は明示エラーで、`DOC_TRANSLATE_*`／別providerへのfallbackや保存設定の変更はありません。stderrの既定進捗は原本名・文書hash、実選択unit／block／画像ページ、preflightで判明したprovider/model/thinking・非秘密auth source/typeを送信前に表示します。完全な送信前JSON通知は`--events-json`指定時だけです。

### 回答・保存・QA

LLM回答は`ReviewAnswer`のみです。

```json
{"findings":[{"target_kind":"unit","target_id":"unit-1","reason":"照合が必要な具体的な理由"}]}
```

`target_kind`は`unit/block/page`、pageの`target_id`は1始まりページの10進文字列です。選択範囲外・未知ID、余分field、不正schema、空白だけの理由は拒否します。非翻訳block／blockのないページにも指摘できます。`block_ids/unit_ids/locations`はPythonが保存対象から補い、PDF table cellは保存cell evidenceのページを使います。

| path | 内容 |
| --- | --- |
| `attempts/review/<sha256>.json` | `stage/status`、対象source／translation hash、実request/config、error、経過秒、全attempt（preflight／raw最終回答／終了／画像metadata／usage／retry）。成功・空findings・拒否・推論失敗を保持。未観測usageは0としない |
| `reviews/<sha256>.json` | 受理成功のみ。`ReviewRecord`: schema_version=1、source_snapshot_sha256／translation_snapshot_sha256、文書順unit_ids／block_ids、ページ順pages、config、reviewer=`provider/model`、method=`independent_llm`、attemptへの`run` hash、位置付きfindings。このartifactの確定がcommit |

attemptを先に、reviewを後に保存します。保存失敗でも旧review・旧snapshot・採用参照・旧renderを保持します。応答損失や強制終了時のexactly-once課金は保証しません。

戻り値とCLI `--json`は詳細statusに`review: {status: validated|rejected, error, run, sha256?, artifact?}`を加えます。exitは0＝記録成功（findingsありを含む）、1＝推論／保存失敗、2＝入力・範囲・設定エラー。reviewは出力を再生成しません。後のrenderが現行findingsを`stage:review`のQAと`※`へ反映します（鮮度判定は`content_review`も比較、旧版reviewは履歴だけ）。findingsの有無にかかわらず`meaning_quality_approved=false`です。

## 新規run

公開CLIは`doc-translate run INPUT --workspace DIR`、APIは`doc_translate.run.run_document`です。PDF／EPUBから`SourceSnapshot`を保存し、`prepare_document`→`translate_document`→`render_document`へ進みます。workspaceは新規`mkdir`で作り、既存path（空directory・file・symlink／dangling symlinkを含む）を拒否します。続行はresumeを使います。

- EPUBは原archiveを`originals/<sha256>.epub`へatomicにコピーし、同じbyte列をbounded readerで読みます。PDF依存は読み込まず、ページ指定は拒否します。
- PDFは`--artifacts-path`で準備済みlocal modelを要求し、`acquire_pdf`→所有5ページ＋許可済み前後各1ページで補正→`assemble_pdf_source`→source保存です。`--pages A-B`省略時は全対象ページが所有範囲です。`--reference-pages A-B`は参照専用許可で所有を増やしません。離れた参照指定はmin–maxの連続ローカル取得を許しますが、補正requestへは所有＋許可済み隣接ページだけを投影します。
- `length/input_limit`なら所有chunkを2分し、許可範囲内の隣接contextを再計算します。1ページでも失敗すればfailedとし、成功chunkは保持します。成功所有unionを指定範囲全体とは表示しません（`pdf_run_scope`検査・処理状態・uncertainty）。
- 準備・翻訳の設定は段階設定→共通設定→`DOC_TRANSLATE_*`のfield単位優先順位です。`--glossary FILE`は`Glossary(version/book/style/terms)`を検証し`terms`だけを利用者優先指定に使います。bookは入力stem、versionは`1`、styleは固定`StylePolicy`から生成します。

```text
WORKSPACE/
  run.json                         # RunManifest、非秘密設定と意図した範囲
  failures/<UTC時刻>-<工程>.txt     # 想定外例外の種類・メッセージ・traceback（本文・credentialなし）
  originals/<sha256>.pdf|epub      # 受け付けた不変byte列
  ...                              # 以下の各段階artifact
```

`run.json`は`schema_version: 1/document/source_name/owned_pages/reference_pages/correction/translation/preparation_concurrency/correction_concurrency/policy/user_terms/corrected_pages/failed_pages/not_processed_pages/chunk_sha256s/artifacts_path/error/error_code`です。EPUBの`owned_pages`／`correction`はnull、PDF全体指定の`owned_pages`は取得後に実ページ番号を固定します。進行に応じatomicに更新し、credential・秘密値・raw例外は保存しません。`error_code`は観測した推論分類（未観測はnull）、`error`は非秘密の案内です。`InferenceError`／`PreparationValidationError`以外の例外で準備・翻訳が、または`RuntimeError`でPDF取得・補正が失敗したときは、`error`に`details: failures/<UTC時刻>-<工程>.txt`を含め、例外の種類・メッセージ・tracebackを新規fileへ保存します。pydantic検証例外は入力値・URL・`loc`を除いた`errors()`だけです。記録保存に失敗したら固定案内だけを残し、exit codeは変えません。

並列上限：

- 準備collect：`run_document(preparation_concurrency=1)`／`resume_document(preparation_concurrency=None)`、CLI `--preparation-concurrency {1,2,4}`。
- PDF補正：`run_document(correction_concurrency=None)`／`resume_document(correction_concurrency=None)`、CLI `--correction-concurrency {1,2,4}`。runの未指定は1を保存します。EPUBは明示1も入力エラーです（未指定は可）。

どちらもrun manifestへ保存し、resume未指定は保存値、manifestのfield欠落は1、明示overrideは保存します。bool／floatを含む不正値はworkspace作成／更新・送信前に拒否します。並列数は送信request／config・候補世代hashに入らず、変更だけでは再送しません。

送信前通知は既存推論の**preflight→generate境界**で行います。`generate`と各stage公開APIは任意の`before_send: BeforeSend | None = None`を持ち、`BeforeSend = Callable[[GenerationConfig, GenerationInput, Preflight], None]`です。検証済みpreflight・画像確認の後、generate recordを送る直前に毎回（retryを含む）呼びます。CLIのrun／resumeは`--events-json`時にstderrへ入力名／hash、意図範囲、実requestの送信範囲・内容種別、provider／model／thinking、auth source/typeをflushします。`cli.main(argv=None, *, process_command=None)`の`process_command`は外部processテストseamだけです。

### PDF／EPUB共通観測境界

`run_document`／`resume_document`／`prepare_document`／`translate_document`／`correct_pdf`／`review_document`／`generate`は任意keyword `observer: Observer | None = None`を持ちます。`doc_translate.progress.ProgressEvent`の工程／kind、phase、実unit ID、job／attemptのrequest ID、固定理由code、非秘密の設定と保存後件数だけを同期callbackへ渡し、本文／回答／payload／token／例外文字列は含めません。callbackは呼出元threadで動き、例外は処理へ伝播し得ます（起動済みchildは回収）。

- metadataは既存の実unit／phaseから投影します（collectは送信unit／section、translationは実chunkのunit、organizeは`unit_ids=[]`、PDF補正は実requestの所有／参照）。
- requestはPopen前から一意なattempt IDで観測し、起動失敗・preflight拒否・callback失敗・timeout・取消・例外も終端します。generate pipe書込み完了後だけAI応答待ちです。retryは3／6秒、取消drainは30秒です。BeforeSendの書込み失敗は送信せず失敗にします。
- 翻訳件数は採用snapshot／pointer保存後に、その採用chunkのleaf unitで成功／失敗／未処理を投影します（再利用は成功の内訳）。準備は収集記録の保存／再利用とorganizeを分けます。render完了も保存後に通知します。進捗file／heartbeat／run.jsonのライブ更新はありません。
- PDFはvalidate、extract、correction、assemble、採用前validate、後段の順に通知します。`ProgressEvent.owned_pages: tuple[int, ...] | None`／`reference_pages: tuple[int, ...]`はPDF用metadataです（EPUBには投影しない）。chunkの再利用・成功は検証とrun manifest保存後にだけ`adopted`で通知します。`succeeded`は所有ページ数、`reused`はその内訳、`total`は所有総数です。候補変更時はvalidateの`candidate_adoption_required`で停止し後段を呼びません。`correct_pdf`単独はrunの成功集計を発行しません。
- reviewは`review_validate`→`review`→`review_save`→`review_verify`の順に通知します。指摘ありも正常終了で、推論／対象拒否は固定理由`review_rejected`の失敗です。`ProgressEvent.block_ids`／`pages`はreview専用metadataで、pagesは画像ページです。通常stdoutの件数は返却`content_review.review_history`の今回記録から取得します。

人向け表示と`--json`／`--events-json`は[usage](usage.md#cliの表示とjson出力)、直接操作は[検証Skill](../../.pi/skills/verify-project/SKILL.md)を参照してください。

### 結果とexit

- 指定範囲が出力まで到達したらexit0。要確認だけなら`processing.status=completed`とQA件数を併記し、品質承認にしません。
- 補正・準備・翻訳の失敗／部分出力はexit1。sourceがあれば原文と受理済み訳文を保持してrenderを試み、summaryは`processing.status=partial`、未処理／失敗unitと`scope.run`の意図したPDF範囲を表示します。
- 入力／設定エラー・既存workspaceはexit2。推論の`invalid_request/unknown_provider/unknown_model/unsupported_thinking/unsupported_image/invalid_image`はこの分類です。chunk縮小後の`length/input_limit`はexit1です。
- sourceを1件も採用できなければMarkdownは作らず、partial summaryの`artifacts.original/run`と取得できた`pdf_draft`を返します。
- render/statusは`scope.run`にmanifestを表示します。source未取得のrunも詳細statusで原本hashと保存pathを検査できます。status検査成功は内容失敗でもexit0です。

利用手順は[CLI詳細リファレンス](usage.md#文書を翻訳する通常利用)、offlineの実CLI直接確認は[開発ガイド](../../CONTRIBUTING.md#新規runの検証境界)を参照してください。

## 再開

公開CLIは`doc-translate resume DIR`、APIは`doc_translate.run.resume_document(*, workspace, translation=None, preparation_concurrency=None, correction_concurrency=None, correction=None, shared=None, policy=None, artifacts_path=None, user_terms=None, process_command=None, before_send=None, observer=None)`です。保存済み`RunManifest`を既定にし、明示stage設定 > 明示共通設定 > 保存値で解決します。環境変数の設定fallback・新たなページ許可optionはありません。明示変更は`run.json`へatomic保存します。

- `artifacts_path`はPDF再取得時だけ必要です（manifestでnull可）。再開はworkspace内の原本hash／pathを検査します。
- EPUBは保存原本から原文を再構築し、採用済みsourceとの対応を検査します。
- PDFは`load_pdf_draft`で原本・raw・全PNGを検査し、取得version／設定・必須check・画像被覆を比較します。必要なら再取得します。検証済みchunkの所有を照合し、manifest更新前に確定したchunkも回収して不足所有だけを補正します。
- 採用source／preparation／translation／render参照とattempt hashを送信前に検査し、不一致なら停止します。破損artifactを上書き・破棄しません。
- 補正・準備・翻訳は実request／config／画像metadata・成功code／helper終了・回答domainを再照合し、受理可能な結果だけを再利用します。回答保存後・pointer前の中断でも再送しません。
- glossary／policy変更は準備と影響する翻訳だけを更新し、PDF取得／補正を無効化しません（`resume --policy FILE`は`StylePolicy(version, text)`）。補正設定変更だけでDoclingを再実行しません。新しい機械生成原文が取り込み済み基点と異なる場合は次節の候補採用によります。
- `usage.note`とusage artifactは常にusage不明・再送による重複課金の可能性を明示します。sourceがない失敗summaryにも段階attempt件数と同じ注意を返します。

SIGINT／SIGTERMは共有cancel eventを設定し、新規paid requestを止めます。helperの30秒drainは維持します。SIGKILLでOAuth refresh保存やexactly-once課金は保証しません。成功時はstaleなrun errorと失敗ページ状態を更新します。

## 基点付き修正・PDF補正候補採用

公開CLIは`revise DIR --source FILE --reason TEXT`または`revise DIR --translations FILE --reason TEXT`、APIは`doc_translate.revise.revise_document(*, workspace: Path, translations: Path | None = None, source: Path | None = None, reason: str) -> dict`です。片方だけを指定し、理由は空白だけでない文字列です。lock下で検証・保存・参照更新を行い、Node・認証・推論・PDF model・Doclingを使いません。

| envelope | 必須field（`schema_version: 1`） | 検証と採用 |
| --- | --- | --- |
| `SourceReplacement` | `base_source_sha256`、`source: SourceSnapshot` | 採用sourceとのCAS、原本文書の一致、全snapshotのschema・unit・構造・根拠・assets・参照を検証して全置換 |
| `TranslationPatch` | `base_source_sha256`、`base_translation_sha256`、空でない`translations: list[UnitTranslation]` | 両参照とのCAS、現在原文・準備・設定との対応、unit IDの既知／一意性、非空訳・保護markerを検証して指定unitのみ変更 |

`SourceReplacement.candidate_sha256`は候補採用時だけの任意fieldで、省略時は現在版の機械基点・provenance・scopeを継承します。`change_reason`／`previous_snapshot_sha256`はアプリが設定し、機械基点・provenance・PDF工程scope（`processing`、`pdf_run_scope`、correctionのuncertainty）は検証済み機械版から設定します。利用者の自己申告で未処理／失敗を消しません。

訳文patchは`UnitTranslation(unit_id, text, uncertainties)`です。変更chunkは`translations/chunks/<sha256>.json`に`request/config/answer/manual_revision`（`base_translation_sha256/base_chunk_index/reason`）を保存し、最後に`translation-snapshot.json`を更新します。返却JSONはsource修正時`source_snapshot_sha256/reason/base_source_sha256`、訳文修正時`translation_snapshot_sha256/reason/base_translation_sha256`です。exitは成功0、stale基点・不正入力2、保存等の失敗1。envelopeの作り方は[CLI詳細リファレンス](usage.md#原文訳文を修正する推論なし)を参照してください。

### 候補・実入力・採用の確定点

`B`＝取り込み済み機械生成原文、`H`＝現在採用中の利用者修正版、`C`＝現在の補正成果から再組立した候補、`K`＝利用者が採用する全snapshotです。`C`と`B`の内容が同じならHを保持して続行します。異なるときは**自動併合せず候補を保存し、準備・翻訳の前で停止**します。同じ`pdf-N`／`unit-N`を内容対応とはみなしません。成功chunkなしは工程失敗です。

`PDFSourceInputs`は`draft_sha256/chunk_sha256s/correction/correction_policy/owned_pages/reference_pages/corrected_pages/failed_pages/not_processed_pages`（`correction`は`GenerationConfig`）です。`generation_sha256(document)`はdocumentと、chunk digest・成功／失敗／未処理ページを除いた入力のsorted JSON SHA-256で、完全な`inputs`の一致検証は別に行います。

`SourceCandidate`は`schema_version: 1/document/head_sha256/base_sha256/source_sha256/generation_sha256/inputs`です。各hashはH／B／Cの保存byte列hashで、候補記録自身のhash（`candidate_sha256`）とは別です。`run.load_source_candidate(workspace, manifest=None)`は参照・不変記録・H/B/C・原本・実入力／世代・chunk／scopeを再検証します。

```text
WORKSPACE/
  snapshots/source/<sha256>.json                      # B/H/C/K
  candidates/source/<candidate_sha256>.json           # SourceCandidate
  candidates/source/<candidate_sha256>.envelope.json  # Cをsourceに含む採用雛形
  source-candidate.json                               # 候補公開参照
  source-snapshot.json                                # 採用版のみ
```

雛形は`schema_version/base_source_sha256/candidate_sha256/source`です。利用者はコピーをCのまま、または訂正統合版Kへ編集します。採用ではCASに加え候補のH/B/C・原本・世代・scope／進捗を照合し、不一致・不正構造を拒否します。Kの親はH、`machine_base_sha256`はC、`adopted_candidate_sha256`は候補記録hash、`machine_inputs`は候補入力です。`source-snapshot.json`の切替だけが採用commitです。候補公開前の中断は受理済みchunkから再構成し、補正を再送しません。

### 状態表示・後続更新

`source_adoption`は`status: pending`と`candidate_sha256/H/B/C/old_inputs/new_inputs/page_changes/envelope/next_operation/stale`を表示します。H/B/Cは`sha256/path`、`page_changes`は`added/removed/reassembled/note`（成功所有ページ集合の差の概要）です。`next_operation`は絶対path付き`revise … --reason TEXT && … resume …`です。

採用待ちは`processing.status=partial`、resume／render exit1、status検査成功exit0、入力不正exit2です。status／renderはHだけを使います。`scope.adopted_source`は採用sourceの`machine_inputs`（なければnull）、`scope.run`は現在のRunManifestです。詳細statusの`adopted_output_current`は保存出力のsource／translation hash一致と訳文非stale、`render_current`は候補状態・scope・usage・処理状態を含むsummaryの鮮度です。

Hを再修正すると旧雛形はstaleになり、resumeがCを現在Hへ結び直して再公開します。採用待ち中は準備・翻訳を送りません。採用後のresumeで準備・unit・訳文・QA／出力を更新し、旧訳／手修正訳は**実request全体一致**の場合だけ再利用します。

## 原文snapshot

公開APIは`doc_translate.snapshot.SourceSnapshot`、`store_source_snapshot(workspace, snapshot) -> str`（不変保存のみ）、`save_source_snapshot(workspace, snapshot) -> str`（保存後に採用参照を切替）、`source_snapshot_sha256(workspace) -> str`、`load_source_snapshot(workspace, sha256=None) -> SourceSnapshot`です。返り値は保存byte列のSHA-256で、loadのdigest省略時は採用参照を読みます。位置unionは`format`をdiscriminatorとし、文書形式と一致させます。

| field | 契約 |
| --- | --- |
| `schema_version` | `1` |
| `snapshot_id` | 空でない識別子。artifact hashとは別 |
| `previous_snapshot_sha256` | 旧snapshotの64桁小文字hex、初版はnull。指定時は空でない`change_reason`必須 |
| `change_reason` | 変更理由またはnull |
| `machine_base_sha256` | 取り込み済み機械原文のdigest。省略可 |
| `adopted_candidate_sha256` | 明示採用した候補記録のdigest。通常revisionへ継承。省略可 |
| `machine_inputs` | 検証済み`PDFSourceInputs`。省略可 |
| `document` | `format: pdf | epub`、`sha256: 64桁小文字hex`、`artifact: 空でない参照文字列` |
| `blocks` | 原文block配列。配列順が読み順。block IDは一意 |
| `uncertainties` | 理由・対象・位置・工程・原本欠損か処理問題か（既定空） |
| `processing` | `stage`（非空）、`status: not_processed | completed | failed` |
| `checks` | 機械検査結果配列（既定空） |
| `content_reviews` | 内容確認者・方法・範囲の配列（既定空） |

blockは`block_id`／`kind`（非空）、`text`、1件以上の`locations`、`structure`（JSON object、既定空）を持ちます。共通model自体はkindの意味や構造の整合性を検査せず、各adapter（下記）が生成・検査します。`uncertainties`は`reason`（非空）、`target_ids`、`locations`、`stage`（非空）、`origin: original | processing | undetermined`、`checks`は`name`／`target_ids`／`passed`／`details`（既定空文字列）、`content_reviews`は`reviewer`／`method`／`details`（非空）と`target_ids`です。

### PDF位置

`format: pdf`と1始まり`page`が必須です。`extraction_ref`と`extraction_snapshot`は両方指定か両方省略です。抽出refなしで画像から復元した内容は空でない`evidence`に見える位置・理由を残します。任意の`bbox`は`left/top/right/bottom`、`origin: top-left | bottom-left`、非空の`unit`と`reference_image`、`rendering`（JSON object）を持ちます。

### EPUB位置

`format: epub`、非空`resource_path`が必須で、`element_id`または`node_path`＋`node_snapshot`（2fieldは一緒に指定）を持ちます。archive pathの安全性はEPUB読込み境界で検査します。

両形式の実例は`tests/fixtures/`にあります。

### 保存・採用・lock

```text
WORKSPACE/
  .writer.lock
  source-snapshot.json            # SnapshotReference
  snapshots/source/<sha256>.json  # 不変の原文snapshot
```

store／saveは冒頭の共通手順で保存し、saveだけが最後に採用参照を更新します。既存digestはhashとschemaを検査します。lock fileの残存は所有を意味しません。

provenance fieldがない保存済みsnapshotも保存byte列のhashのまま読み込み、再serializeで参照hashを置き換えません。PDF revisionの機械基点は、不変履歴・旧source／chunk・保存scopeから一意に検証できる場合だけ復元し、不明・循環・欠落・hash不一致は送信前に拒否します。

## 共通翻訳unit生成

公開APIは`doc_translate.units.generate_translation_units(snapshot, *, source_sha256=None) -> list[TranslationUnit]`、`split_translation_units(units, *, max_source_chars) -> list[TranslationUnit]`、`validate_unit_text(unit, text) -> None`です。受理済み原文からPythonだけで生成し、workspace書込み・LLMは行いません。

### identity・出典・context

| field | 契約 |
| --- | --- |
| `unit_id` | 読み順の`unit-1`等。block／cell／Docling ref／元XHTML IDとは別で、snapshot間の恒久identityではない |
| `block_id`／`cell_id`／`node_path`／`attribute` | 所属blockと、該当時のみ採用cell、EPUB tree node、`alt/title`属性 |
| `kind`／`format` | 段落・見出し・list・table_cell・caption・footnote・callout・alt等、`pdf/epub` |
| `source_text`／`tokens` | 保護marker付き原文と、inline構造／保護対象のローカル対応 |
| `source_hash` | marker原文とtokenのkind／保護値のcanonical JSON SHA-256。位置は含めず、literal／link target等の変更は含める |
| `context`／`context_hash` | 見出しpath、table行列・span・関連見出しcell、list祖先・depth／marker／親、caption対象／callout行、EPUB role等と、そのSHA-256 |
| `locations` | 所有blockの元位置、またはEPUBのunit node位置。PDF cellはtable位置とcell ID／行列／根拠 |
| `snapshot_id`／`source_snapshot_sha256`／`document_sha256` | 参照snapshot、原文artifactのhash、原本文書hash。保存版から生成するときは`source_sha256`で保存hashを渡す（省略時はmodel JSONのhash） |
| `parent_unit_id`／`parent_source_hash`／`source_range` | 未分割時null。分割時は元unitのidentity／hashと、元marker付き`source_text`に対する半開区間`{start,end}`（Python文字offset） |

PDFでは採用`blocks`と確定tableの`cells`だけを扱い、`fragments/retained_blocks/retained_cells`から本文を再採用しません。code／formula／picture本体と、図のconfirmedなblock／cell所属はunitにしません。uncertain／partialの対象、不確実cellを持つ採用table、caption・脚注・図外の内容は残します。未確定tableの候補cellは翻訳しません。

EPUBでは`structure.tree`を正本とし、段落／item／cell全体のmixed contentを1unitにします。nested listは別itemです。`li`が直下に子リスト（`ul`／`ol`／`dl`）を持つ場合は、子リストの前・間・後の文を同じ`node_path`の別unitに分けて原本順に並べます（空白だけの部分はunitにせず、子リストより後ろが空白だけなら`li`全体を1unit、番号と親anchorは最初の部分だけ）。表の0始まり行列はrowspan／colspanとrow group境界を考慮し、`rowspan=0`はgroupの残行です。図内部のprose、pre／code本体、inertな数式／SVG／active subtreeは翻訳しません。caption・notes・画像alt／titleは別unitです。未知のblock kind、tree参照不整合、解決できないtable span、不正inline表現は例外停止します。

### inline保護と分割

`source_text`は例えば`Use ⟦1⟧the ⟦2⟧guide⟦/2⟧⟦/1⟧ with ⟦=3⟧.`です。`InlineToken`は`token_id/kind/start_marker/end_marker/value`を持ち、paired markerが強調・link等、単独markerがinline code／literal／identifier／数式・図の参照です。原文中のmarker区切り文字もliteral tokenへ退避します。**`tokens.value`はローカル復元用で、LLMへ送りません。** context内の見出し／祖先textも保護済み表現です。

PDFの任意`structure.inline`（cellも同じ）はordered配列で、leafは`text/code/literal/identifier/formula`＋`text`、paired構造は`emphasis/strong/subscript/superscript`＋`children`、`link`は追加の`target`です。全leafの連結が`block.text/cell.text`に一致しなければ拒否します。平文からはHTTP(S) URL、backtick literal、snake_case／CamelCase／dotted identifier、数値literalを保護します。普通の単語に見えるidentifierは明示inlineで指定します。

表の見出しcontextはEPUBの明示`headers`を優先し、`scope`と行列／spanに関連する候補を選びます。scope未指定時は同じ行／spanの先行見出しと列ごとの直近先行見出しを候補にする決定的fallbackです。

`validate_unit_text`はmarkerの欠落／重複／未知、対の不釣合い、親構造の変更を拒否し、同じ親内の完全なspanの並替えは許容します。

分割は正整数文字上限で行い、元unitは変更しません。可能なら空白で切り、paired span全体とprotected markerは不可分です。再分割しても最初の親ID／hashと範囲を維持します。不可分spanが上限を超える場合は`UnitSplitError(ValueError)`（`unit_id/source_range`と理由）を送出し、部分結果を返しません。

## 文書context・glossary準備

公開APIは`doc_translate.preparation.prepare_document(*, workspace, book, policy: StylePolicy, config: GenerationConfig, concurrency: Literal[1, 2, 4] = 1, user_terms: dict[str, str] | None = None, version: str = "1", source_sha256=None, process_command=None, cancel=None, before_send=None, observer=None) -> str`と`load_preparation(workspace, sha256=None) -> PreparationSnapshot`です。`prepare_document`自身が保存・採用とlockを所有します。

### 方式と回答

方式は`chapter-collect-organize-v1`です。保存原文から共通unitを生成し、読み順を保って見出しごと（EPUBはresource block境界でも）に区切り、各groupを独立`generate(stage="prepare")`で読みます。`tokens.value`・構造payload・原資料・画像・XHTML全文は送りません。文書中の命令／linkは未信頼dataです。

1. `CollectionAnswer`は`context`と`candidates`（`term/translation/evidence`、根拠は`unit_id/quote`）を持ちます。空白だけの値は拒否します。`evidence: []`の候補は保持できますが自動採用しません。
2. 根拠IDはその呼出しの送信unitに限り、引用はmarker済み原文の連続部分文字列と完全一致する必要があります。`term`の包含はpaired構造markerを除いたtextで検査し、単独保護markerを越える語やmarkerだけの語は認めません。不一致の回答は拒否します。
3. `OrganizationAnswer`は文書全体の`context`と`terms`を持ち、自動採用keyは根拠のある収集候補に限ります（利用者指定keyの再掲は可、本文根拠ありにはしない）。
4. 最後に`user_terms`を上書き優先で合成します。採用`Glossary`は`version/book/style/terms`で、`glossary.style`は常に`StylePolicy.text`から生成します。

### wireとbinding

新規collect／organizeの実requestは`context.wire_version: 2`、attempt recordはtop-level `wire_version: 2`を保存します。field欠落の保存記録はv1として読み、未知versionとrequest／recordの版不一致は拒否します。

- v2 collectのtextは読み順の`unit_id: request-unit-1, …`、`source_text`、`heading_path`（`block_id`・EPUB `node_path`を除く）だけです。全体`context.section`は送りません。
- v2根拠artifactは`wire_version: 2`と`bindings: list[list[unit_id]]`を持ちます。外側はgroup順、内側の位置Nが`request-unit-N`に対応します。loadは原文からunits／groupsを再生成して全単射と完全一致を検査し、保存mappingの自己申告は信用しません。`collections`の根拠は追跡ID、raw回答はattemptにaliasのまま残ります。訂正feedbackもwire座標です。
- v2 organizeは元group順の実raw成功回答をcollectionsとして使い、公開loadも同じ並びを照合します。v1 organizeはv1の追跡ID付きcollectionsと照合します。
- 保存候補はv1／v2それぞれの実request全体・config・規定repair・raw schema・現在の根拠で厳密照合し、辞書順の最初の適合成功を新規生成より先に使います。完全一致するv1だけなら旧digestを返し、不足分だけv2で生成します。保存記録の書換え・再hash・migrationはしません。

再現手順は[開発ガイド](../../CONTRIBUTING.md#文書contextglossary準備の検証境界)を参照してください。

### 固定版・根拠・実行記録

```text
WORKSPACE/
  preparation-snapshot.json              # SnapshotReference
  snapshots/preparation/<sha256>.json    # PreparationSnapshot
  preparation/evidence/<sha256>.json     # 候補・用例・出典・整理回答・利用者terms
  attempts/preparation/<sha256>.json     # 1呼出しごとの全attempt、失敗も保存
```

`PreparationSnapshot`は`schema_version: 1`、`source_snapshot_sha256`、`glossary`、`context`、`policy`、`translation_inputs_sha256`、`evidence_sha256`、`runs`（元group順のcollect＋最後にorganizeの実行記録digest）です。根拠artifactは`source_snapshot_sha256/sources/collections/organization/user_terms`を持ち、`sources`はunit IDごとに`block_id/cell_id/node_path/attribute/locations/snapshot_id`です。

実行記録は`stage: prepare`、`method`、`status: validated | rejected`、実`request/config`、`elapsed_seconds`、全`attempts`です。根拠検証の失敗（未知unit・引用不一致・用語表記不一致・整理後の根拠なし用語）は固定codeの`PreparationValidationError`で、未信頼dataの`context.validation_feedback`（code、不正なindex・用語・unit_id／quote）と固定の訂正指示を加えて初回込み最大3回答まで再生成し、失敗recordに`validation_error`を残します。schema不正・認証・保存・timeout・取消は再生成しません。通信retry（最大3試行）と合わせ1呼出しの新規helper試行は最大9回です。準備usageは翻訳usageと合算しません。

`translation_inputs_sha256`はsort済みUTF-8 JSONの`{glossary, context, policy}`だけから生成します。原文・policy／book／version／利用者terms・各呼出しの方式・config・実requestが現採用版と一致すれば、loadで再検証してdigestを返し再送しません。`version`はcaller指定の非空文字列です。

attempt探索はfile名の辞書順で、受理可能な一致で止まります。1回の`prepare_document`内では到達したraw recordだけをmemo化し（照合は省かない）、未到達recordは先読みしません。

`concurrency`は1・2・4（既定1）で、collectだけを上限まで投入します。collections／runsは元group順に戻し、全group成功・保存後にorganizeを1回行います。同じwire request＋configの同時missはFutureでraw結果を共有し（usageは一度だけ集計）、各利用箇所で再検証します。失敗／取消／保存例外の後は新規投入を止め、開始済みhelperへ取消を伝えて最大30秒drainを待ちます。親main threadのSIGINT／SIGTERMは共有Eventへ伝え、終了後に元handlerへ戻します。observer／before_sendはworkerからも呼ばれます。

schema・根拠拒否でも全attemptを記録してから例外を返し、旧採用参照を維持します。`load_preparation`はNode・認証・通信なしに、byte hash／schema、原文・根拠・全runのhash、元unit／位置・引用の対応、収集範囲と整理語、raw回答と実request、glossary／style／context／固定入力hashを再検証します。

## 翻訳・採用snapshot

公開APIは`doc_translate.translation.translate_document(*, workspace, config: GenerationConfig, source_sha256=None, preparation_sha256=None, max_source_chars: int = 12_000, concurrency: int = 2, process_command=None, cancel=None, before_send=None, observer=None) -> str`と`load_translation(workspace, sha256=None) -> TranslationSnapshot`です。lockを自分で取り、digest省略時は採用参照を使います。

### 翻訳入力・回答と検証境界

見出しとEPUB resource境界を越えず、unitを読み順に約`max_source_chars`原文文字で束ね、大きすぎるunitは`split_translation_units`で分割します。送るのはunitの`unit_id/kind/source_text/context`、固定文書context、採用glossary、policy、回答schemaと翻訳指示だけです。

回答schemaは`TranslationAnswer.model_json_schema()`です。v2 raw回答の例：

```json
{"translations":[{"unit_id":"request-unit-1","text":"訳文です。","uncertainties":[{"reason":"原文の意味が曖昧です。","origin":"undetermined"}]}]}
```

- 全IDが入力順に1回ずつ必要です。欠落・重複・未知・順序違い、空白だけの訳文、不正schema、marker inventory／釣合い／包含構造の変更を拒否します。
- `uncertainties`は必須配列で、`origin`は`original/processing/undetermined`です。元位置・対象ID・`stage: translation`はPythonが付けます。
- `processing.status: completed`は全unitの機械的受理で、品質承認ではありません。`translations`は受理unitを返すproperty、`content_reviews`は常に空です。
- marker違反だけは、chunk内の全違反unitのID・理由・原文側／回答側marker列・不足／余分marker・不正訳文を`context.translation_feedback`（未信頼data）へ入れ、固定の訂正指示を加えて初回込み最大3回答まで訂正生成します。ID不一致・schema不正・認証・保存・timeout・取消は訂正しません。通信retry（3試行、3／6秒）と合わせ最大9回です。
- `length/input_limit`は複数unitならchunkを分け、単一unitなら文字上限を半減して再分割します。不可分spanや1文字でも不成立なら失敗とします。
- 並列は既定2です。main-threadのSIGINT／SIGTERMは共有cancelへ伝え、childの取消・最大30秒drainを待って戻ります。
- helperの`auth_failed`／`storage_failed`は全体停止です。以後、未開始job・分割の次の子・通信retry・marker訂正を送信しません。未送信分はattemptを残さずchunk `not_processed`とします。送信済みrequestは結果を回収します。利用者取消時だけ未処理を`cancelled`にします。局所の拒否やlength縮小は他jobを続けます。

### wireとbinding

新規自動requestは`context.wire_version: 2`、attemptとchunk artifactは`wire_version: 2`を明示します。wireの`unit_id`はrequest内の`request-unit-N`です。`heading_path`の`block_id/node_path`だけを送らず、他のcontextは変えません。

v2 chunk artifactは`request/config/answer/run`に`wire_version/bindings`を加えます。`bindings`はchunkの全追跡unit IDを順に列挙し、alias N→bindings[N-1]です。raw回答はalias、artifactとsnapshotのanswerは追跡IDです。loadは原文から全unitとsplitを再生成してbindingsを照合し、raw schema・alias順序・markerを検証します。

splitを含むv2 requestだけは`context.split_inputs`（非splitはnull、splitは`parent_source_hash/source_range`）をunit順に加えます。親全文は送りません。同じ断片本文でも親hash／範囲が異なればmissです。

版field欠落の保存記録はv1として追跡ID requestのまま厳密load／完全一致reuseし、変換・再hashしません。`manual_revision`は追跡IDのv1 requestを保存します。manualをaliasや似た本文で別unitへ移さず、採用済みmanual／acceptedを優先します。

### 保存・部分失敗と再利用

```text
WORKSPACE/
  translation-snapshot.json             # SnapshotReference
  snapshots/translation/<sha256>.json   # TranslationSnapshot
  translations/chunks/<sha256>.json     # 検証済みrequest/config/answer/run
  attempts/translation/<sha256>.json    # 成功・失敗・縮小の全記録
```

`TranslationSnapshot`は`schema_version: 1/source_snapshot_sha256/preparation_sha256/config/chunks/splits/runs/processing`です。各chunkは現在原文の完全な`units`と`artifact_sha256/answer/error`を持ち、受理済みならerrorはnull、未処理／失敗ならanswerとartifactはnullです。`splits`は`unit_id/max_source_chars`の操作順で、loadで再実行・再検証します。

`runs`は実行記録digestです。記録は`stage/status/request/config/error/elapsed_seconds/attempts`、`status`は`validated/rejected/adjusted`、ローカル分割調整は`adjustment`と空の`attempts`を持ちます。marker拒否のrecordは`validation_feedback`を持ちます。未観測usageは0として加算しません。

attempt→chunk artifact→snapshot→採用参照の順に保存します。並列jobの完了ごとに部分snapshotを採用し、別chunkの失敗で正常採用を破棄しません。部分失敗でもdigestを返すので、callerは`processing.status`と各chunk.errorを確認します。保存失敗は新規送信を止め、進行中jobを待ってから例外を送出し、採用参照は最後の整合版のままです。

snapshotの保持：1回の`translate_document`が新しく作った途中版は、次の版を保存してpointerを更新した**後**に削除します。呼び出し前から存在した版、最後にpointerが指す版、`revise`が保存する版、`review`／`revise`／`render`の記録が参照する版は残します。

再利用は検証済みの採用chunk（manual含む）を優先し、次に辞書順の保存attemptから回収します。条件は連続unit・分割対応と、版付き実wire request全体およびconfigの完全一致です（訂正requestは固定指示と`context.translation_feedback`だけを加えた形）。source hashの差だけでは無効化せず、codeだけの変更は現在のunitへ結び直します。曖昧な旧訳を分割・接合しません。

attempt探索のcatalogは単一操作内だけで、開始時に全recordをread／hash／JSON検証します（無関係な破損も拒否）。同じv2 request＋configの同時missはFutureでraw結果を共有し、usageは一度だけ計上します。

`load_translation`はNode・認証なしで各byte hashとschema、原文／準備、分割履歴と全unit対応、受理artifactの実入力・raw回答・ID／marker、runの最終payload schema・`validated`状態・設定・成功code／exit code・preflightを再検証します（terminal・送信先・submitted設定はhelperの検査を信頼します）。

## 安全Markdown・対応表・QA・render/status

公開APIは`doc_translate.render.render_document(workspace) -> dict`、詳細検査`workspace_status(workspace) -> dict`、軽量`read_saved_status(workspace) -> dict`です。CLIは`render DIR`（summary JSON）、`status DIR`（保存記録readerの人向け表示）、`status DIR --verify`（詳細検査の人向け要約）、`status DIR --json`／`--verify --json`（詳細JSON）です。renderと詳細検査は原文と訳文を既存loadで検証し、現在のsource hashと訳文の出典が違えば`stale_translation: true`として旧訳を使わず原文を残した部分出力にします。LLM・Node・PDF依存は呼びません。

```text
WORKSPACE/
  render.json                   # schema_version / summary / files(path→SHA-256)
  renders/<render-digest>/
    japanese.md
    english.md
    assets/<sha256>.<png|jpg|gif|webp|txt>
    source-map.json
    qa.json
    usage.json
    summary.json
```

renderは全出力を確定してから`render.json`を切り替えます。詳細statusは書込みせず、出力参照のschema・path・全file hashと現在入力／保存attempt集合を比較して`render_current`を返します。旧出力は`previous_artifacts`、入力対応は`adopted_output_current`で区別します。

### 保存記録reader

`read_saved_status`はrun／採用pointer集合と必要なJSONの小さいprojectionを読み、`format/run/pointers/translation/render/candidate/record_mtimes`を返します。`translation`がnullなら件数は不明（0ではない）です。訳文があれば保存chunkのleaf unit／answer／errorから`total/succeeded/failed/not_processed/cancelled`を集計します。

読取り対象はrun.json、source／preparation／translation／PDF draft／render／candidateのpointer、訳文snapshot、保存render summary、candidate envelope、runがない場合の形式確認sourceです。runと訳文があれば訳文参照preparationのpolicy／evidence hashとuser_termsも読み、runとの差を示します。schema・型・digest・workspace内path（symlinkを含む）・参照先byte hashを検査しますが、完全loadやMarkdown／QAの再構成は行いません。`record_mtimes`は記録fileのmtimeです。

runとpointerを前後比較し、変化時は一度だけ再読、なお変化すれば`RuntimeError`（CLI exit1）です。読取り成功はpartialでもexit0、壊れた必要記録は`ValueError`／`FileNotFoundError`（CLI exit2）です。書込み・lock・再送はありません。詳細`workspace_status`中のpointer変化も`RuntimeError`です。共有render分類では`chunk.error == "not_processed"`を`not_processed`に数えます。

### 出力の内容

- **source-map**：`blocks`はblock ID／kind／元locations／出力anchor（独立出力しないblockはnull）、`units`は翻訳入力unit全fieldと出力anchorです。split時も親unit・元marker範囲を保持します。
- **QA**：sourceの機械検査、保存入力検証、両言語の実unit到達・anchor一意性／内部参照／local assetsを分けます。`render_references`は生成境界で記録したanchor・内部参照・画像assetを検査します（code中の`id/href/src`は参照と扱わない）。`uncertainties`は元recordに`block_ids/unit_ids/output_anchors`を加え、render由来の安全fallback／未訳も記録します。目印`※`はcode／formula内部に入れません。EPUBのblock対象uncertaintyに具体的なnode位置があれば、同じresource_path・node_snapshotの祖先／子孫unitだけに対応付けます（node pathは区切り単位で比較）。
- **内容確認**：`content_review.records`はsourceの確認記録、`translation_records`は現行source／translation hashのreview記録、`review_history`は全review記録です。`unreviewed_source_unit_ids/unreviewed_source_block_ids`と`unreviewed_translation_unit_ids`を別記します。`meaning_quality_approved`／`viewer_display_checked`はfalseです。
- **summary**：正本hash、`processing.status: completed | partial`、unitの`output_reached/not_processed/failed/render_failed`、`output_reached_blocks`、source処理状態、機械検査、要確認／原本欠損件数、内容確認、usage、成果物pathと`render_elapsed_seconds`。`scope.ownership_records`は保存済みPDF所有記録、`pages_with_source_blocks`は本文位置です。
- **usage**：`stages.preparation/correction/translation/review`にworkspace内の全attempt artifact／hash／raw recordを列挙します（失敗・retry・未採用を含む）。意味の異なるtoken値を合算せず、reasoningを二重加算しません。保存情報がない時間・実請求はnull／unknownです。

表示は保存構造から生成し、raw XHTMLをコピーしません。単純な表はMarkdown、span付き／非単純表と検証できたEPUB `colgroup/col`は固定tagのHTMLです。未確定PDF表は原ページ画像と正本参照を残します。code fenceは原text内のbacktickより長くします。PDF図は検査済みPNGを記録領域でcropします。EPUBは許可tag／属性のPresentation MathMLとdecode済みraster画像だけを使い、active SVG・外部画像・不許可数式は`.txt`原データ参照とQAに残します。任意Markdown・HTMLはescapeし、link先は生成anchorまたは検査したHTTP(S)／mailtoだけです。

renderのexitは0＝出力到達、1＝部分出力／実行失敗、2＝入力／hash等のエラー。statusは検査成功ならpartialでも0です。

## PDF抽出下書き

公開APIは`doc_translate.pdf.acquire_pdf(source: Path, *, workspace: Path, artifacts_path: Path, pages: tuple[int, int] | None = None, image_long_edge: int = 1800) -> str`と`load_pdf_draft(workspace, sha256=None) -> PDFDraft`です。local PDFだけを読み、原資料をbyte単位で保存し、同じbyte列をDoclingへ渡します。`pages`は1始まり・両端含む連続範囲（`None`は全ページ）で、正整数・昇順・ページ数以内を検査します。取得範囲は送信許可ではありません。

Docling／OCRMacは取得時のみimportします。固定version、CPU、`OcrMacOptions(lang=["en-US"], framework="vision", recognition="accurate")`、HeronとTableFormer V1 accurateを使います。modelの固定repo／revision／必要ファイルは[READMEのPDF手順](../../README.md#pdfを翻訳する)と[modelファイルの詳細](usage.md#pdf読取りの依存とmodelファイル)を参照してください。local directoryは`docling-project--docling-layout-heron/`と`docling-project--docling-models/model_artifacts/tableformer/accurate/`を持ち、symlinkも使えます。重みはworkspaceへコピーしません。

`enable_remote_services=False`／`allow_external_plugins=False`、明示local path、`HF_HUB_OFFLINE=1`／`TRANSFORMERS_OFFLINE=1`を強制します（firewallではありません）。enrichment・picture classification／description・VLMは無効で、変換中のdownloadやfallbackはありません。不足localファイルは`FileNotFoundError`、PDF extra不足／version不一致は`RuntimeError`、不正range／描画設定は`ValueError`です。

### 保存物と検査

```text
WORKSPACE/
  originals/<sha256>.pdf
  pdf-draft.json                      # 最後に保存したdraftへの参照（原文採用ではない）
  drafts/pdf/<sha256>.json            # PDFDraft
  drafts/pdf/raw/<sha256>.json        # DoclingDocument.export_to_dict()の完全なJSON
  drafts/pdf/images/<sha256>.png      # 原本ページの直接描画
```

| PDFDraft field | 契約 |
| --- | --- |
| `schema_version`／`kind` | `1`／`pdf-extraction-draft`。正しい原文とは認定しない |
| `document`／`source_name` | `format=pdf`、原PDFのSHA-256とartifact、入力file名 |
| `target_pages` | 対象ページ番号。ページ数が得られない失敗下書きでは空 |
| `versions` | Docling／docling-core／OCRMac、docling-parse／docling-ibm-models／Torch／Transformers／Hub／Pillow、PythonとOS |
| `settings` | 実効pipeline options、offline flag、要求page range（nullは全ページ）と画像長辺 |
| `models` | 固定repo／revision、必要ファイルのlocal path／SHA-256 |
| `raw` | raw JSONのartifact／SHA-256 |
| `pages` | `PDFPageImage`配列。ページ番号、PNG artifact／hash、`width_points/height_points`、画像実寸pixel、`image_origin=top-left`、`page_unit=pt`、`scale_x/scale_y`、描画条件 |
| `conversion_status`／`conversion_errors` | Docling status生値とErrorItemの全field |
| `checks` | `conversion_status`／`conversion_errors`／`page_coverage`／`page_images`／`charspans` |

画像は公開`ThreadedDoclingParseDocumentBackend.iter_pages()`→`PdfPageBackend.get_size()`／`get_page_image(scale=...)`で原PDFから直接描画します。scaleは要求長辺／point長辺です。対象外ページの画像は保存しません。raw抽出ページとの寸法不一致は画像checkの失敗です。座標はraw provenanceの`coord_origin`を保持し、TOPLEFTからpixelは`(x*scale_x, y*scale_y)`、BOTTOMLEFTからは`(x*scale_x, (height_points-y)*scale_y)`です。

charspanは非負・順序と、textがあるitemでは保存text長以内かを検査します。不一致はclipせず、`charspans.passed=False`、`target_ids`にref、`details`にprovenance index／span／text長を記録します。Docling diagnosticsは`conversion_errors` checkを失敗にしますが、successなら例外停止しません。callerはcheckを確認してください。

conversionがsuccess以外、全対象ページのraw被覆不足、PNG欠落／描画失敗は、rawと得られたPNGを保存した後に`PDFExtractionError`を送出し、`error.draft_sha256`で失敗下書きをloadできます。Doclingが結果を返す前の例外では原PDFのみ保存され`draft_sha256=None`です。既存の同digest byte列が壊れていれば上書きしません。`load_pdf_draft`はhash／schemaと原PDF・raw・全PNGのhash、PNG decode・実寸を検証し、workspace外参照を拒否します。重みはload時に要求しません。

## PDF補正chunk

公開APIは`doc_translate.pdf_correction.correct_pdf(*, workspace, draft_sha256, owned_pages: list[int], reference_pages: list[int], policy: str, config: GenerationConfig, process_command=None, cancel=None, before_send=None, observer=None) -> str`と`load_pdf_correction(workspace, sha256) -> PDFCorrectionChunk`です。返り値は保存chunkのSHA-256です。CLIはありません。

### 許可範囲・送信内容

- 成功したdraftをloadして再検査します。所有ページは1件以上、所有／参照は正整数で重複なし・draft内に存在し、各ページにPNGが1枚必要です。所有→参照の順に全画像を送ります。
- rawは送らず、許可ページの`ref/page/charspan/text/bbox`とtable cell／spanの投影を送ります。`orig`・文書名・木全体・許可外のprovenanceや画像は送りません。bboxは元ページpoint／Docling原点、図領域は原PNGのtop-left pixelです。
- textは保存textに収まる有効なcharspanからだけ取ります。clipやitem全文へのfallbackはしません。複数ページtableのcell textは送りません。送らなかったfragmentの理由はrequest記録に、所有ページの問題は採用chunkのuncertaintyにも残します。
- request contextはdraft digest・所有／参照ページ・座標説明・回答JSON schema・`figure_membership`指針・`page_results`指示・`adoption_rule`です。glossaryはnullです。
- `page_results`は全所有ページの見える原語本文・見出し・構造の全文返却を指示し、見えない内容の創作を禁じます。**画像で白紙と確認したページだけ**を`exclusions`の`text: ""`と理由・ページ根拠（`ref: null`）で表します。判断不能なら`uncertainties`に残します。

### 補正回答の型と意味検証

`CorrectionAnswer`は`schema_version=1`、`draft_sha256`、`owned_pages`、`blocks`、`exclusions`、`uncertainties`を必須とします。draftと所有ページの完全一致、全所有ページの結果根拠、block／cell IDの一意性、関係先の存在・種別・局所構造を検査します。参照ページは本文・除外・uncertainty・図内部根拠に使えません（接続候補と親候補の根拠だけは所有＋参照ページで検証）。

全blockは`block_id/text/evidence`を持ち、配列順が読み順です。`kind`によるdiscriminated unionです。

| kind | 型付き構造 |
| --- | --- |
| `body`／`formula`／`caption`／`footnote` | 原語のtext。`target_id`で対象を保持可能 |
| `heading` | `level`（正整数） |
| `list` | `depth`（1始まり）、`marker`、任意の`parent_id`と`parent_evidence`。局所親は深さが1小さいlist |
| `code` | 原語のtext（空白・改行を保存）、任意の`language` |
| `callout` | `code_id`、`marker`、1始まりの`lines` |
| `table` | `rows/columns`と`cells`（`cell_id`、0始まり`row/column`、正の`row_span/column_span`、`text/heading/evidence`）。`structure_confirmed`（既定true）なら矩形の被覆・非重複・境界を検査。falseは未確定候補で`cells=[]`も可 |
| `picture` | `region: {page,left,top,right,bottom}`、`caption_ids`、`members` |

`CorrectionEvidence`は`page`、実在する`ref`またはnull、空白のみでない`position/reason`、任意の`charspan: [start,end]`または`cell_index`です。refはrawの実在item・`self_ref`・当該ページのprovenanceに照合し、spanは有効な当該ページprovenance内、cell indexは単一ページtableの実在cellであることを検査します。画像からの復元は`ref=null`で表します。除外は`text/reason/evidence`、uncertaintyは`reason/target_ids/evidence/origin`です。

### 所有本文の採否とコードの接続

`correct_pdf`は生回答をattemptへ保存し、別に採用chunkを作ります。採用方式は`request.context.adoption_rule`に記録し、現行は`adopted-edge-target-v17`（chunk `schema_version: 18`）です。判定にAIの回答・ページ番号・ref・固有文字列・固定座標を使わない規則として、次を実装しています。

- **出典の照合**：draftの読順・所有page・有効な文字範囲をblock／cellの出典と照合し、案が欠いた読める範囲を見出し・本文・脚注・コード等の位置と役割に戻します。図内文字や参照ページは本文に足しません。表は再構築しません。
- **本文の語間SPACE**：案との差が局所的な語間SPACEだけなら、hash検証した原本PDFの所有ページprovenance bbox内で、同一行の実文字SPACEまたは通常の字送りを越える余白がある場合だけ採用します。裏付けのない差は抽出文字を採り、差と位置をQAに残します。
- **1記号の置換・挿入**：通常本文で、他の所有と競合せず、前後が同じ局所描画に対応し、対象文字を描画した方が対応PNGの画素を説明できる場合だけ採用します。`evidence`に抽出側の位置と描画bbox（pt、左下原点）を残します。
- **コードの表示**：コードは紙面表示として、局所文字層の各行を案の全文・順序と照合し、行頭と等幅の実測字送りで相対字下げを確認します。全行共通の余白は表示基準とし、絶対字下げは未確認としてQAに残します。抽出の改行・空行を保持し、案だけの余分な空行は足しません。裏付けがなければ抽出文字をcodeとして採用しQAに残します。captionとコードが同じ抽出項目にあっても、局所描画で一意に分けられれば非重複charspanの別blockにします。
- **無効charspanのコード案**：範囲を丸めたり`orig`を使ったりせず、競合しない連続出典と原本の描画行・可視性を照合し、案の全非空行と一致した描画だけを採用します（行頭座標を保存）。listグループ（`#/groups/N`、子がすべて案のref）や描画行が案と一致するsection_headerも連続とみなします。生成空白の数だけを描画文字の送り幅の格子から決め、非空白文字・行の数と順・字下げは描画と一致が必要です。格子外記号の後の空白数を決めた場合は`Space count after an off-grid symbol was set from the drawn grid`を行位置付きで残します。
- **表を出典とするコード**：所有ページの単一ページ・単一provenanceの表（`#/tables/N`）だけを出典とし他blockが挙げないコード案は、表の直前・直後に連続しbboxが交わる未所有テキスト項目を罫線候補として、外接矩形の描画行が案の全行と一致する場合だけ描画表示を採用します。成立しなければ`No unique owned extraction range for the proposed display lines; ...`のQAを付けます。
- **共有された抽出項目**：複数blockが同じ抽出項目を主張する場合は、題名＋コード、コード／本文／コードの混在、本文とコード1行の共有、本文同士の共有、出典が抜かした中間項目、分かれた断片（list markerと本文、索引断片）、索引断片と表セル文字の重複のいずれかで、項目の全文字を紙面順に一通りに説明できるときだけ重ならない範囲に分けます。差が引用符の字形や上付き注番号だけなら抽出文字を採用してQAに残します。成立しなければchunk全体を拒否します（`Overlapping ownership of extraction text`等）。
- **囲み（sidebar）**：所有ページの原本PDFのページ直下の塗りpath（接する塗りと左右端が揃った高さ2pt以下の横罫線を連結、罫線だけは除外）に、見出し・本文・リストだけが完全に収まり、それらをこのchunkのblockだけが出典にし枠外を出典にしない場合、blockに採用chunkだけの任意`enclosure`（chunk内番号）を付けます。回答（`CorrectionAnswer`）の`enclosure`は拒否します。囲みは内部順を保ち、枠より上の枠外blockの後・下のものの前へ移します。成立しなければ`enclosure`なしで採用します。

組立のコード接続は、両端の元提案と採用断片が相互に相手のtargetへ合い、出典・selector・`newline`・language・ページ順と読順が整い、両端で相手が一意な場合だけです。間に入れるのは空・独立脚注・完結した囲みだけです。一方向のtargetが相手の案のevidenceに合わず採用後のevidenceにだけ合う場合は、その範囲が組立対象の1断片だけにあり、相手の案本文の接続端の行に含まれ、相手の採用範囲のうち読順の最初（相手が右）／最後（相手が左）で、逆方向が案と採用の両方で一致するときだけ適合とみなします。拒否は両断片と位置付きQA・失敗した`pdf_connection` checkに残します。

**現行と異なる方式の保存記録**：`load_pdf_correction`・成功再利用・未採用attempt回収は、現行chunk schemaで、参照attemptの`request.context.adoption_rule`が現行と一致する記録だけを読み、方式値・実入力・再構成した保存内容をbyte単位で検証します。それ以外（欠落・旧方式名・未知名）は「このworkspaceのPDF補正は現在と異なるルールで保存されています。新しいworkspaceで `run` し直してください。」で止まります。`resume`は全保存chunkを送信前に検査し、保存物を変えずexit2で止まります（移行なし）。補正chunkを再検証しない`render`と`status`（`--verify`を含む）は、候補の採用待ちがない限り動きます。

### 図の所属と採用結果

`members`の要素は候補block／cellの`target_id`または`ref: CorrectionEvidence`の**どちらか一方**と、`status: confirmed | uncertain | partial`、`reason`です。図と同じ所有ページの実在対象だけを扱い、bboxの重なりから所属を推測しません。

- 全体の所属を確定できた内部blockは`chunk.blocks`へ採用せず`retained_blocks`へ残します。caption／脚注を内部指定で除去する応答は拒否し、粗いrefがcaptionにもかかる場合は保持して要確認にします。
- tableの内部cellは`retained_cells`に残し、行列座標は変えません。table全体が内部なら`retained_blocks`です。**unit生成は`blocks`と採用tableの`cells`だけを対象にします。**
- 不確実／部分所属・粗い共有ref・部分spanは対象全体の削除根拠にせず、原画像・元text・要確認を保持します。細分化した明示spanが完全に内部に収まる候補だけを内部扱いにします。
- 全図は`region.page`の`page_images`（PNG artifact／hash・実寸・scale・描画条件）を保持します。切出し画像は生成しません。

### 保存・load・失敗

```text
WORKSPACE/
  attempts/pdf-correction/<sha256>.json  # config／実request／全attempt・raw回答／status／validation_feedback
  corrections/pdf/<sha256>.json          # PDFCorrectionChunk（所有範囲のみの採用結果）
```

chunkは`schema_version/draft_sha256/owned_pages/reference_pages/blocks/retained_blocks/exclusions/uncertainties/page_images/run`です。`page_images`は所有・参照ページだけ、`run`はattempt記録のartifact／hashです。

各generateの全attempt・raw回答・観測usage・実requestを、次のgenerate／chunk保存より前に保存します。`status=validated`は回答のschema／domain検証までです。不正時は`status=rejected`を残しchunkを作りません。保存OSエラーは伝播し、追加送信しません。

run／resumeでは親がlockを保持し、同じ処理の内部coreを独立所有jobへ渡します。workerはattempt／chunkだけ、親はmanifestと集計・source採用を更新します。成功所有は再投入しません。chunk digest列は最小所有ページ順、corrected／failedページは昇順です。局所的な補正拒否はfailed ownershipとして他jobを続け、取消・設定／認証／storageエラー・保存例外では新規送信を止めて開始済みchildをdrainし、旧pointerを維持します。

所有ページの網羅性は全domain検証の**末尾**で検査します。不足だけなら専用の`ValueError` subclassで識別し、`validation_feedback`に`code: "owned_page_coverage_incomplete"`、昇順・重複なしの`missing_pages`、固定`reason: "Owned page result coverage is incomplete; blank pages need explicit evidence/reason"`を記録します。contextに固定指示`coverage_correction`と未信頼dataの`correction_feedback`（累積しない）を加え、初回込み最大3回generateし、全検証を通った最終回答だけを採用します（mergeなし）。上限でも不足ならchunk全体を失敗（CLIは`answer_invalid`）にします。他の失敗種別には適用しません。transport retryと合わせ最大9 attemptです。

loadはchunk／draft／原資料／raw／PNG／runのhashを照合し、元runのraw回答を再検証して採用結果との一致を確認します。保存応答の回収は、現在のbase request、またはbaseに規定の訂正contextだけを加えたrequestと全体一致し、status・preflight・成功code・raw回答を再検証できた成功記録に限ります。chunk保存前の中断でも無送信で回収できます。空回答・ページの黙示省略は拒否し、空抽出を自動白紙化しません。このAPIは`source-snapshot.json`を変更しません。

## PDF原文snapshot組立

公開APIは`doc_translate.pdf_source.assemble_pdf_source(*, workspace, chunk_sha256s: Sequence[str], snapshot_id: str) -> SourceSnapshot`です。各chunkを`load_pdf_correction`と同じ経路で再検証します（同じdraftのdraft／rawは1回の組立内で再利用）。workspaceを書き換えず、LLM／Node／Doclingを呼びません。保存はcallerが`save_source_snapshot`で行います。

### 補正回答の接続・親候補

全blockの任意`incoming/outgoing`は本文の前後接続の**提案**（例：前半`inter-`の`outgoing`と後半`face`の`incoming`が互いを指す`dehyphenate`）で、`{"target": CorrectionEvidence | null, "mode": "space" | "dehyphenate" | "newline"}`です。nullのtargetは相手が見えない境界での継続を表しQAへ残します。targetはdraftの実在ref／ページ／selectorを所有＋参照範囲内で検証します。

別ページのlist親は子の`parent_evidence`で指定します。`parent_id`はchunk内IDです。親候補が複数／未知、局所指定と矛盾、kind／深さ／読み順が不整合なら`parent_id=null`と元提案・断片・QAを残します。採用親は先行する深さ1小さいlistです。

`table.structure_confirmed=false`のtableは、sourceの`cells`を空にし、候補は`retained_cells`と原断片に置き、原画像とQAを残します。複数ページtableの接合は行いません。

### 接続・出典・未確定内容

- chunkの所有ページのunionが選択範囲です。空入力、所有重複、異なる原本文書（`SourceDocument`不一致）、不正artifactは拒否します。同じ原本文書の別draftは受理し、位置は各chunkのdraft hashを保持します。
- ページ順、同一ページ内はchunkの配列順です。block／cell／図内部のIDは文書内`pdf-N`へ割り当て、`structure`の関係IDも対応付けます。
- 接続候補はページ・ref・selectorで所有blockへ一意に解決し、画像のみの根拠はページ＋`position`で照合します。別draftでは同名refだけで一致とせず、`self_ref`を除いた実raw itemの一致も必要です。
- 相互一致・mode・kind・ページ／読み順・構造を検査します。prose（body／caption／footnote／同階層・同親のlist）は`space`で境界空白を1空白に、`dehyphenate`で末尾のASCII `-`を除いて結合します。codeはlanguageが一致する`newline`だけで、空白・改行を削らず、境界に改行がなければ1改行を挿入し、callout行も対応付けます。間の脚注・完結した囲みは保持して接続できます（囲みは接続した段落と脚注の後に内部順で置きます）。
- 相手不明／複数、前後不一致、矛盾、範囲末尾継続は、元断片・位置・画像を残し、`uncertainties`と失敗した`pdf_connection`検査へ記録します。codeの欠けた末尾を補いません。
- 各blockの`structure.fragments`は`chunk_sha256/draft_sha256/block/id_map`を持ちます。`structure.page_images`は許可済みPNGの記録です。`locations`は所有ページだけで、`extraction_snapshot`は実draft hash、`evidence`にselector・位置・理由を保持します。空白・除外のみのchunkも`pdf_chunk_ownership`検査から辿れます。
- `retained_blocks`／`retained_cells`を本文として再採用しません。

`processing=pdf_connection/completed`は選択chunkの組立到達であり、全PDF処理完了や品質承認ではありません。`content_reviews`は空です。

## EPUB安全読込み

公開APIは`doc_translate.epub.read_epub(path: Path) -> EPUB`です。`defusedxml==0.7.1`と標準`zipfile`／`zlib`／`struct`を使い、入力を書き換えず展開fileも作りません。返り値はfrozen dataclassと読取り専用mapping／tupleです。

| field | 契約 |
| --- | --- |
| `package_path` | containerから選んだ唯一のpackageのarchive内path |
| `resources` | archive内の通常fileのpath→展開後`bytes`。空directory entryは返さず、非空directoryは拒否 |
| `manifest` | 元ID→`ManifestItem(id, href, resource_path, fragment, media_type, fallback, properties)`。manifest順。外部は`resource_path=None` |
| `spine` | 元spine順の`SpineItem(idref, resolved_id, fallback_chain, linear, properties)`。`linear=no`も`False`で残す |
| `encryption` | font難読化の対象path→algorithm。解除しない |

`book.resolve(base_path, uri) -> ResourceReference`は`resources`中の正規pathを基点にURIを解決し、`resource_path`、`fragment`、`query`、`external_uri`を返します。percent encodingはUTF-8として1回だけdecodeし、dot segmentをarchive内で解決します。localの欠落／root逸脱／不正encoding／NUL等／backslashは`EPUBError`です。外部scheme／authorityは`resource_path=None`と`external_uri`で返すだけで、取得・実行しません。

ZIP member名はarchive内の実名として扱い、percent文字をdecodeしません。絶対／drive／traversal／dot・空segment／backslash／制御文字path、同一名・NFC同値名の衝突、file/directory衝突、symlinkを拒否します。大文字小文字は区別します。

### 読込み上限・XML

上限と等しい値は許容し、超過は`EPUBError`です。設定optionはありません。

| 公開定数 | 上限 |
| --- | --- |
| `MAX_ARCHIVE_BYTES` | archive 128 MiB（`zipfile`に渡す前にsizeを検査） |
| `MAX_MEMBERS` | 4,096 entry（空directoryを含む） |
| `MAX_MEMBER_BYTES` | 1 entryの展開後32 MiB |
| `MAX_TOTAL_BYTES` | 展開後合計256 MiB |
| `MAX_XML_DEPTH` | XML resourceごとに128（rootを1とする） |
| `MAX_XML_NODES` | XML resourceごとに100,000 element |
| `MAX_XML_NAME_CHARS` | 1つの未展開／展開後の要素・属性名、namespace prefix／URIごとに4,096文字 |
| `MAX_XML_TOTAL_NAME_CHARS` | XML resourceごとに開始要素名・属性名（展開後）とnamespace宣言の累積8 Mi文字 |
| `MAX_XML_ATTRIBUTES` | 1要素ごとに1,024属性（namespace宣言を含む） |
| `MAX_SPINE_FALLBACK_IDS` | 全spineの`fallback_chain`のID総数100,000 |

申告展開量を先に検査し、`ZipFile.open`から最大64 KiBずつ読んで実量も検査します。storedはsize一致、deflateは`zlib`で圧縮領域を追加検査し、超過・実量不足・終端欠落・余分なbyteを拒否します。CRCは`zipfile`で検査します。ZIP暗号化とstored／deflate以外の圧縮方式は拒否します。

XMLはSAX preflightと`DefusedXMLParser`の`TreeBuilder` callbackで上限をDOM追加前に検査します。entity宣言を禁止し、外部entity／外部XML参照を読みません。DOCTYPEは不活性に扱います。非整形式XMLは修復しません。検査対象はcontainer／package／encryption、`.xml/.opf/.xhtml/.html/.ncx/.svg`、manifestで`+xml`／`application/xml`／`text/xml`と宣言されたlocal resourceです。XML安全読込みは表示用markupの無害化ではありません。

### Package・受理範囲・暗号化

EPUB 2.0／3.0 package、単一manifest／spine、IDの一意性、fallback連鎖の存在／非循環、spine idref・linearを検査します。非XHTMLやremote本文はfallbackを辿り、採用可能なXHTMLへ到達しなければ理由付きで停止します。

複数rootfile、`rendition:layout=pre-paginated`（全体・itemref）、EPUB 2系の明示`fixed-layout=true`／Apple display optionsを拒否します。**spineの取得対象bodyに本文textがなく画像だけの出版物は停止**します。head／title／script／style／SVG内text、nav／tocは本文textの根拠にしません。

`META-INF/encryption.xml`は、IDPF `http://www.idpf.org/2008/embedding` またはAdobe `http://ns.adobe.com/pdf/enc#RC`とmanifestのfont media-typeの組合せだけを難読化fontとして保持します。それ以外は「未対応の暗号化algorithm/resource」と対象／algorithmを示して停止します。復号はしません。

受理範囲／入力不整合／安全違反は`EPUBError(ValueError)`、filesystemの失敗は元の`OSError`です。

## EPUB原文snapshot抽出

公開APIは`doc_translate.epub_source.extract_epub_source(book: EPUB, *, document: SourceDocument, snapshot_id: str) -> SourceSnapshot`です。`document.sha256`は**元archiveのhash**で、callerが供給します（adapterは原資料のコピー・hash照合をしません）。保存は`save_source_snapshot`を別に呼びます。

### 読み順・原文block

- 採用XHTMLをspine順に訪問し、同じresource pathは一度だけ抽出します。その後、保持nodeの参照から見つかったlocal XHTML（manifestのmedia typeまたは`.xhtml/.html`）を発見順に訪問し、循環を再訪しません。
- 1 resourceを1つの`kind="section"` blockにします。`block_id`は訪問順の`epub-1`等です。
- `text`は構造化したbody text／child／tailの連結projectionで、空白を補いません。構造の正本は`structure.tree`です。XML parserが返す空白・改行を保持します。
- `structure.spine`はresourceに対応する全itemrefの`index`（1始まり）、`idref`、`resolved_id`、`fallback_chain`、`linear`、`properties`です（spine外は空配列）。

### tree・位置・coverage

`structure.tree`は`{"root":"/1","nodes":{"/1":{...},"/1/1":{...}}}`のflat node tableです。**表示用HTMLではありません。**

| field | 意味 |
| --- | --- |
| `tag` | namespace展開後の元XML要素名 |
| `location` | `EPUBLocation`。resource path＋`node_path`＋`node_snapshot`。非空の`id`（なければ`xml:id`）を`element_id`へ |
| `original_ids` | `id/xml:id`がある場合だけ属性名→値 |
| `role` | `body / toc / supplement`。nav／`epub:type=toc`はtoc、non-linear／spine外resourceと`epub:type`のfootnote(s)／endnote(s)／rearnote(s) subtreeはsupplement。子孫へ継承し、同じnodeではtoc優先 |
| `disposition` | `structured / excluded / inert`。body外はexcluded（rootはstructured）、script／style／埋込・form等と非XHTML namespaceの子孫はinert |
| `attributes` | `alt/title`、`rowspan/colspan/scope/headers`、listの`start/value/type/reversed`、`role/lang/xml:lang/epub:type`だけ（原表記） |
| `content` | text、child参照`{"node_path":"/1/2/1"}`、tailの順の配列。excluded／inertのtextは入れない |
| `references` | URI属性の参照配列（次節） |

node位置はrootを`/1`、子をtag名によらない1始まりordinalで表します。`node_snapshot`は入力`snapshot_id`で、そのsnapshot専用の位置です。duplicate IDは各node位置で区別し、候補位置と原本由来のuncertaintyを記録します。

各resourceの`structure.coverage`にdisposition別件数、`checks[name="epub_node_coverage"]`を残します。参照先がXHTMLのhtml/bodyでない場合はtree全体をinertにして要確認を残します。`processing`は`epub_extraction/completed`、`content_reviews`は空です。

### links・notes・assetsと原資料参照

`references`は`href/src/data/poster/longdesc/cite/xlink:href`の元`attribute/uri`、`EPUB.resolve`の結果、`status`、`targets`を持ちます。外部URIは`external`として保持するだけです。inert／excluded nodeの参照からXHTMLを追加しません。

- local XHTMLのfragmentが1候補なら`resolved`と`targets: [{location, disposition}]`です。fragmentなしはresource rootを指します。
- IDの欠落は`missing_fragment`、重複は`ambiguous_fragment`で、要確認を残します。重複候補は参照先blockの`structure.target_index[元ID]`へ一度だけ保存し、各参照の`target_index: {resource_path, element_id}`から辿ります（`targets`は空）。
- 解決不能／unsafe URIは`unresolved`、非XHTML assetは`inert_resource`と原資料参照・要確認で保持します。

最初のblockに`structure.package_path`と`structure.resources`（全archive fileをpath順に`resource_path/byte_length/manifest/encryption/disposition`）を置きます。resourceの処置は`structured/inert/unvisited`です。snapshotにraw byte列はコピーしません。

## Python↔Node JSONLの公開codec

Python：`doc_translate.jsonl.JSONLRecord`／`encode_record`／`read_records`。Node：`@doc-translate/ai/jsonl`（`ai/dist/jsonl.js`）の`JSONLRecord`型、`encodeRecord`／`readRecords`。envelopeは次の**4fieldのみ**です。

```json
{"schema_version":1,"request_id":"r1","type":"echo","payload":{"text":"日本語"}}
```

- `schema_version`は数値1（booleanは拒否）。`request_id`と`type`は空でない文字列、`payload`はJSON object。余分なfield、欠落、非有限数値を拒否します。
- `payload`内の整数値（整数値の有限floatを含む）はJavaScriptのsafe integer範囲に限り、encode／decode両方で検査します。
- UTF-8、1recordにつき末尾LF必須。空行、壊れたJSON、不正UTF-8、未完了末尾を拒否します。U+2028／U+2029はdataです。
- Python `read_records`はLFで終わる行のiterable、Node `readRecords`は同期／非同期の任意binary chunk iterable（分割を再構成）を受け取ります。
- codecはdispatch・認証・成功判定を行いません。echoはproduction operationではありません。

## metadata helper

compile済みESM `dist/main.js`をNodeで起動し、stdinへ1 requestを渡します。CLIは1 process／1 requestで、正常終了・response1件・request ID一致・result schemaを確認します。

| request type | payload | 正常response payload |
| --- | --- | --- |
| `providers` | `{}` | `helper_version`、`library_version`、`providers: [{id,name,login_methods}]` |
| `models` | `{"provider":"anthropic"}` | `helper_version`、`library_version`、`provider`、`models: [{id,name,image_input,reasoning,thinking_levels}]` |

`login_methods`はinteractive login可能なmethodを`api_key`、`oauth`の順で列挙します（ambient-only providerは空配列）。`image_input`は画像入力対応です。`thinking_levels`は`off/minimal/low/medium/high/xhigh/max`の順で、reasoning非対応なら`off`のみ、`xhigh/max`は明示mapがある場合だけです。

未知providerは`error`／`{"code":"unknown_provider"}`、未対応type・余分／不足payloadは`{"code":"invalid_request"}`でexit 2、不正envelopeもexit 2、成功はexit 0です。credentialを読みません。

## 認証helper

同じ`dist/main.js`を1 process／1認証操作で起動します。JSONLはログやartifactへ保存しません。CLIのstdoutは成功時の非秘密result payloadのみです。

| 方向／type | payload |
| --- | --- |
| Python→Node `login` | `{"provider":"openai","method":"oauth"}`。methodは当該providerの`login_methods`にある`oauth`／`api_key` |
| Python→Node `logout` | `{"provider":"anthropic"}` |
| Node→Python `prompt` | `{"prompt_id":"1","prompt":{...}}`。`type`（`text`／`secret`／`select`／`manual_code`）、`message`、任意`placeholder`、selectは`options: [{id,label,description?}]` |
| Python→Node `response` | `{"prompt_id":"1","value":"..."}`。selectはoption ID |
| Node→Python `prompt_cancel` | `{"prompt_id":"1"}` |
| Node→Python `notify` | `{"event":{...}}` |
| Node→Python `result` | `{"operation":"login","provider":"openai","method":"oauth"}`または`{"operation":"logout","provider":"anthropic"}`のみ |
| Node→Python `error` | `{"code":...}`。`invalid_request`／`unknown_provider`／`unsupported_method`／`cancelled`／`auth_failed`／`storage_failed` |

notify eventは`info`（`message`、任意`links`）、`progress`、`auth_url`（`url`、任意`instructions`）、`device_code`（`userCode`、`verificationUri`、任意`intervalSeconds/expiresInSeconds`）です。Pythonは通知と入力をcontrolling TTYだけで扱い（非echo、pipe fallbackなし）、古いprompt IDへの応答はNodeが無視します。

exitは成功0（要求operation／provider／methodとの完全一致も確認）、未知provider・未対応method・不正request 2、その他の失敗／取消1です。SIGINT／SIGTERM／EOFは取消で、Pythonはechoを復元します。Nodeはstore操作を`drain`してから応答し、保存失敗は取消より優先して`storage_failed`です。Pythonは30秒graceでchildを回収します。

### アプリ所有credential（workspace外）

`FileCredentialStore(directory?)`の公開操作は`read/list/modify/delete/getDeviceId/drain`です。既定directoryは`$XDG_CONFIG_HOME/doc-translate/`（未設定・空なら`~/.config/doc-translate/`、0700）で、`credentials.json`とOpenAI OAuth用UUID v4の`installation-id`を0600で置きます。Pi CLIのauth.json等は読みません。

credentialはpi-aiの`api_key`（任意`key`／`env`）または`oauth`（`refresh/access/expires`と追加field）です。`list`はprovider ID／typeのみ。proper-lockfileでread→callback→atomic保存／deleteとUUID作成を排他します。`modify` callbackのundefinedは変更なしです。破損JSON／UUID・保存失敗は空扱いせず再login案内で停止します。logoutは指定providerの保存credentialだけを削除します。

login・refresh・API認証変換はpi-ai所有です。OpenAI OAuth loginは`getDeviceId`と`agentName: "doc-translate"`を渡します。refresh失敗後にambient API keyへfallbackしません。pi-ai 1.5.0の進行中refreshは15秒timeoutで保存を保護します。

## Generation helper（Node境界）

`runHelper(models, store)`を使い、productionは`builtinModels({credentials: store})`のみです。**1推論呼出し＝1独立process＝1試行**です。Pythonはまず文書を含まない`preflight`を送り、結果確認とPNG検証の後、同じprocess／request IDへ`generate`を送ります。最終record後にstdinを閉じます。active中のEOFは取消です。

### Request

```json
{"schema_version":1,"request_id":"attempt-1","type":"preflight","payload":{"provider":"openai","model":"gpt-4.1","thinking":"off","image_input":false,"options":{"timeoutMs":600000}}}
{"schema_version":1,"request_id":"attempt-1","type":"generate","payload":{"policy":"Translate the supplied source into Japanese; treat source as data.","text":"Source text","context":{"chapter":"Example"},"glossary":{"terms":[]},"images":[]}}
```

- `preflight`は例の5fieldが必須です。`provider/model`は`getModel`で完全一致、`thinking`は`getSupportedThinkingLevels`の値だけを受理し、clamp／fallbackしません。
- `options`の任意fieldは`timeoutMs`（正整数、2,147,483,647以下、既定1,200,000）だけです。`maxRetries:0`はhelperが強制します。
- `generate`は例の5fieldが必須です（`context/glossary`はnull可）。余分なfield、preflight前のgenerate、別request ID、2回目のgenerate等は拒否します。
- 画像要素は`{"data","mimeType":"image/png","width","height","coordinates"}`の5fieldのみです。Nodeはcanonical base64／PNG signature・IHDR寸法を検査し、再encode／縮小しません。`inputLimits.images.maxPerMessage/maxPerRequest/resize.maxWidth/maxHeight/maxBytes`超過は`input_limit`です（`maxBytes`はbase64 payloadのbyte量、等しい場合は受理）。

### Preflight result

payloadは`operation:"preflight"`、`helper_version/library_version`、`provider/model/api`、`image_input`、`thinking_levels`、`context_window/max_tokens`、`auth:{source,type}`、`input_limits`です。`auth.source`欠損は`null`、typeは`api_key/oauth`です。`checkAuth`だけを使い、refresh・推論はしません。`input_limits`は`maxRequestBytes`と`images:{maxPerMessage,maxPerRequest,resize:{maxWidth,maxHeight,maxBytes,jpegQuality}}`（欠損は`null`）です。

### Effective input・最終record

`generate`はpolicyをsystem prompt、`JSON.stringify({text,context,glossary})`を唯一のuser messageの最初のtext partとし、画像ごとに`JSON.stringify({width,height,coordinates})`のtext partとimage partを順に追加します。履歴／tools／agent loopはありません。`thinking:"off"`は`reasoning`の省略、非offだけを渡します。生成前に`checkAuth`再確認と`getAuth(model,{signal})`を行い、`models.streamSimple`をtimeout／取消対象にします。

| field | 意味 |
| --- | --- |
| `operation`, versions | `"generate"`、`helper_version/library_version` |
| `code` | 成功は`null`、失敗は固定code |
| `terminal` | `{type:"done"または"error",reason}`、未観測は`null` |
| `message` | `role/provider/model/api/stopReason/content/provider_thinking_level/response_model`。text partは`{type,text}`、thinking／toolCallは`{type}`のみ |
| `usage`, `usage_status` | 正規化usage（input/output/cacheRead/cacheWrite/totalTokens/cost、任意reasoning/cacheWrite1h）と`"observed"`。観測できない場合は`null/"unknown"`。reasoningはoutputの内数。costの任意`totalFromProvider`はprovider報告額かを示す |
| `elapsed_ms` | generation処理の経過ms |
| `requested` | `{provider,model,thinking}` |
| `submitted` | `reasoning`（offは`null`）、`maxRetries:0`、`timeoutMs`の3fieldだけ |
| `provider_confirmed` | `{thinking:<providerThinkingLevelまたはnull>}` |
| `retry` | `{helper_attempts:1,maxRetries:0,observed_responses,internal_retries:"unknown",retryable,classification:"library_heuristic"またはnull}` |
| `request_bytes_checked` | `maxRequestBytes`があり`onPayload`でbyte量を検査したか。超過は送信前に拒否 |

`retryable`は`generation_failed`かつ`error` terminalの場合だけ`isRetryableAssistantError`で判定します。成功には`done/stop` terminal、`stopReason:"stop"`、正確なprovider/model/api、toolCallなしが必要です。exitは成功0、入力設定エラー2、その他の失敗1です。preflight／入力検証の失敗は`error/{code}`だけ、preflight計算後に取消・drainが失敗した場合は完全なpreflight payloadに`code`を加えた`error`です。codeは`invalid_request/unknown_provider/unknown_model/unsupported_thinking/unsupported_image/invalid_image/input_limit/auth_unavailable/auth_failed/generation_failed/missing_terminal/model_mismatch/tool_call/timeout/cancelled/storage_failed`です。

SIGINT／SIGTERM／stdin EOF／stdout write failureは推論を取り消します。timeout／取消後もstream終了を最大30秒待ち、観測したterminal／usageを保持します。store.drainは最終record前とhelper終了前に待ちます。typed OAuth refresh失敗は`auth_failed`、保存失敗は`storage_failed`を優先します。

### Python inference adapter

公開APIは`doc_translate.inference.generate(config, inputs, *, response_model, images=(), cancel=None, process_command=None, before_send=None, observer=None)`です。`GenerationConfig`は`provider/model/thinking`と`GenerationOptions`（`timeoutMs`のみ）、`resolve_config(stage=Selection(...), shared=Selection(...), options=...)`はfieldごとに段階→共通→`DOC_TRANSLATE_PROVIDER/DOC_TRANSLATE_MODEL/DOC_TRANSLATE_THINKING`で解決します。不足・未知thinking・不正optionsは送信前に拒否します。

保存済み`options`／`submitted`に削除済みのkey（`temperature/maxTokens/transport/cacheRetention/thinkingBudgets`）を持つworkspaceは読み替えず、`resume`／`render`／`status`（`--verify`の有無を問わない）／`review`／`revise`がAIへ送らず保存物を変えずにexit 2で止まり、「このworkspaceは古い形式で保存されています。新しいworkspaceで `run` し直してください。」を表示します。

`GenerationInput`は`stage: correction | prepare | translation | review`と必須の`policy/text/context/glossary`です（不要時は`None`）。`GenerationImage(data=<PNG bytes>, mimeType="image/png", width, height, coordinates)`はcorrection／reviewでのみ渡せます。画像のSHA-256・byte量・base64 byte量・MIME・実寸・座標・`scale:1`をattemptの`ImageMetadata`へ保持します。

成功は`GenerationResult[T](answer, attempts)`、失敗は`InferenceError.code`と`attempts`です。回答textをJSONとして解析し、NaN／Infinity／overflow・余分なtextを拒否してからcallerのPydantic modelで検証します。`Attempt`はrequest ID・preflight・最終projection・usage（未観測は`None/unknown`）・終了code・経過秒・retry待機秒・画像metadataです。

Pythonは正常JSONL・`request_id`・preflight（version・送信先・thinking・能力）・最終payloadのschema、成功時のexit 0を検証し、helperの検査結果（done/stop・送信先・toolCall・submitted・usage）は再検査しません。workflow retryは最終`generation_failed`かつ`retryable:true`だけを最大3試行、新processで待機3／6秒です。`cancel`は`threading.Event`で、main threadではSIGINT／SIGTERMも取消にします。親側timeoutはpreflightとgenerateそれぞれに適用します。取消後もstdoutをdrainし、30秒grace後に回収します。

productionは検証済みinstalled runtimeだけを起動し、safe cwd・NODE_PATH／NODE_OPTIONS除去・stderr破棄を行います。

## アプリ所有runtimeの導入記録

`setup-ai`はinstalled npmだけを起動し、`package-lock.json`とcompile済みresourceを新しいruntimeへコピーします。`~/.local/share/doc-translate/ai/`（`XDG_DATA_HOME`指定時はその下）の`npm-LOCK_HASH_PREFIX-HELPER_VERSION-.../`ごとにnode_modulesを持ちます。`runtime.json`は`package_manager`（`npm`）、`package_manager_version`、`lock_sha256`、`helper_version`、`helper_sha256`、`library_version`を記録します（認証情報なし）。

依存導入・version・helper catalog検査の成功後に`current` symlinkをatomic replaceします。通常参照は`current`の記録と同梱resource／lock／直接依存versionを照合し、不一致ならsetup案内で停止します。失敗したsetupは何も変更・削除しません。成功したsetupは`current`差替え後に、runtime home直下の`npm-*`／`pnpm-*`のdirectoryと`.current-*`のsymlinkのうち新しいruntime以外を削除します（`npm-*`／`pnpm-*`のsymlinkはlink自体だけ、`.current-*`の実directoryは削除しない）。削除失敗はsetupの失敗にしません。実行中の要求と同時実行の制約は[usage](usage.md#導入動作環境の詳細)を参照してください。
