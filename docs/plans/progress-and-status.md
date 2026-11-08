# PDF／EPUB共通の進捗表示・送信通知・軽量status

## 目的

PDF／EPUBのrun／resumeと、明示範囲reviewについて、止まって見える無表示時間と通常画面を埋めるJSON通知を解消する。現在工程・ローカル処理／AI応答待ち・保存済み進捗・再利用・再試行／訂正／失敗／取消を、人が実行画面だけで把握できるようにする。通常statusは保存記録の軽量な確認に限定し、詳細検査は維持する。

## 入力・調査根拠

- 基本設計：[docs/design/progress-and-status.md](../design/progress-and-status.md)。本Planと以下の確認済み変更をImplementationの正式入力とする。調査時HEADは`94306061ed14238cbc238a9bc9360d697b0e946d`。入力設計書は未追跡で存在しており、削除・置換しない。
- 現行公開契約：[CLIリファレンス](../reference/usage.md)の「CLIの表示とJSON出力」「保存済み正本の再出力・状態確認」「文書を翻訳する」「翻訳結果をAIに確認させる」、[artifact契約](../reference/artifacts.md)のrun／resume／review／翻訳／render-status節。
- 一次情報：`src/doc_translate/run.py`のrun_document／resume_document／_continue_run／_pdf_source、`preparation.py`の_call／prepare_document、`translation.py`の_process／_adopt／translate_document、`inference.py`の_attempt／generate、`review.py`のreview_document、`render.py`の_inputs／_build／workspace_status、`snapshot.py`の_atomic_write、`workspace.py`のlock。
- `workspace_status()`はunit再生成・準備／翻訳の再検証・日英Markdown／QA再構成・attempt走査・成果物hash検査を行う。resumeの再利用前検証、review、revise、runの早期終了もこのAPIを利用するため、APIを一律に軽量化してはならない。
- 各pointerは個別atomic更新であり、複数pointerの一括transactionではない。`not_processed`は翻訳chunkの未処理sentinelだが、現在の_buildはerrorがあるだけで失敗へ含める。

## 要件・決定事項

### 1. ユーザー確認済みの表示契約

| コマンド | 既定 | 明示JSON／詳細検査 |
| --- | --- | --- |
| run | stderrに進捗、stdoutに既存の人向け結果 | `--json`は従来の最終summary JSON |
| resume | stderrに進捗、stdoutに人向けの再開結果・未処理／失敗・出力先 | `--json`を追加し、従来の最終JSONを取得可能にする |
| review | stderrに進捗、stdoutに確認記録の保存結果・対象・指摘件数・記録先と品質未承認の説明 | `--json`を追加し、従来の最終JSONを取得可能にする |
| status | 保存記録のみから軽量な人向け要約 | `--json`は従来どおり詳細検証＋完全JSON。`--verify`は詳細検証＋人向け要約。併用時も同じ詳細JSON |

run／resume／reviewに`--events-json`を追加する。この指定時のstderrは従来用途の送信前JSON通知のみとし、人向け進捗や周期更新を混ぜない。これは全工程event streamではない。stdoutの`--json`とは独立し、`--json`だけではstderr進捗を無効にしない。通常のparser診断・致命的エラーは従来同様発生し得る。

render／revise／認証／catalog等の出力方式は変更しない。保存artifactのschema、Python既存APIの戻り値、終了コード0／1／2の基本契約は維持する。JSON内の`not_processed`誤分類は修正対象であり、誤った値まで互換性として固定しない。

### 2. 入力設計からの確認済み変更と更新時期

- 対象をrun／resumeからreviewへ拡張する。reviewにも重い検証・AI待機・保存／終了検査があり、無表示時間と読みづらいJSONを同じ共通表示で解決するため。
- resume／reviewの最終stdoutは、JSONを既定で維持する方向から、人向け既定＋明示`--json`へ変更する。JSON構造を知らない利用者にも結果を理解可能にするため。
- 通常statusだけ軽量化し、既存`status --json`の詳細検証を維持する。軽量JSONは今回は追加しない。
- 入力設計書の対象・出力責務・互換性に関する関連箇所は、ユーザー合意に従いImplementationで更新する。それまでは本節を変更前の該当記述より優先する。無関係な基本方針は変更しない。

### 3. 実行画面

- Richを表示層のみに使い、TTYでは工程一覧＋現在対象／送信先／待機状態を同じ場所で更新する。重い処理へ入る前に工程を表示する。
- PDF：保存データ検証、本文・レイアウト抽出、PDF補正、翻訳準備、翻訳、Markdown出力。EPUB：本文・構造抽出、保存データ検証、翻訳準備、翻訳、Markdown出力。resumeでは最初に再利用前検証を示し、実処理順を偽装しない。存在しないPDF工程／ページ数をEPUBに出さない。
- review：保存データ・選択範囲の検証、AIによる内容確認、確認結果の保存、終了時の保存データ検査。指摘があることと処理失敗を混同しない。
- 未着手○、実行中spinner、正常完了✓、再試行／部分完了!、失敗!を文言と併記し、基本設計のgray／cyan／green／yellow／redを状態記号・工程名・重要数値に限定して使う。再利用と通常のAI待ちは警告にしない。
- 履歴は最新3件を現在詳細の下に残し、再試行・失敗・取消の短い行をLive上方にも確定出力して理由を失わない。非TTYでは工程／対象の変更・完了・警告を行ログにし、時間だけの反復出力・spinner・画面制御を使わない。
- `NO_COLOR`の存在時は空値も含めて色を使わない。TTYのanimationを色の有無とは分ける。狭い幅では折返し／明示省略を行い、狭い高さでは現在工程・対象・異常理由を優先する。全件が同時に見えると偽らない。
- 原本名・文書hash・全体許可範囲は冒頭へ集約する。requestの対象は現在詳細に表示し、長いID集合は件数と範囲／明示省略で要約する。JSON通知では実対象IDを取得可能にする。

## 制約・対象外

- 別プロセス向けライブ状態、heartbeat、PID管理、新しい進捗状態ファイル、run.jsonへのライブ書込み、汎用event bus／監視サービス／取消engineを追加しない。
- 翻訳品質改善、意味的完全性、全文査読、全viewer／全OS、任意規模で一定応答時間、native処理中の常時animation、SIGKILL時の画面復元・未保存応答の回収・exactly-once課金は保証しない。
- 実Workspace・実行中run／resume・既存attempt／成果物は操作しない。実provider／実認証／追加課金／重み取得は別途承認なしに行わない。書籍本文・画像をfixtureに追加しない。
- 追加依存は表示用Richのみ。独自runner、恒久の広範E2E、performance CI、汎用cache／indexを導入しない。無断install／環境sync／共有環境変更を行わない。

## 実装方針

### A. 通常statusと詳細検査の分離

1. `render.workspace_status(workspace)`の詳細検査と既存内部callerを維持する。CLIだけ、オプションなしstatusを専用の小さい保存記録readerへ振り分ける。新readerは`read_saved_status(workspace)`相当の公開境界とし、複雑な新保存モデルは作らない。
2. 読む対象はrun.json、採用pointer、必要な参照先のimmutable JSON（翻訳snapshot、保存render summary、runがない場合の形式確認用source等）に限定する。PDFはmanifestの保存済み所有／成功／失敗／未処理、翻訳はsnapshotのchunk／answer／errorから集計する。attempt／review履歴／correction全件、Markdown／assets、原本の走査・decode・再構成、unit生成、load_translation／load_preparationの完全再検証を呼ばない。
3. 実際に読むpointerのschema・digest、参照pathのworkspace内包含、参照先JSONの必要field／型とそのbyte hashは検査する。重いdomain検証の代替とはしない。壊れた必要記録は空の成功にせず、入力／hashエラーとして従来のexit2へつなぐ。読まない成果物の破損検知は保証しない。
4. 訳文snapshotがなければunit総数は不明／未集計とする。既存保存記録だけで数えられない成功・再利用・準備総数を0で代用しない。通常statusで今回の新規／再利用数は推測しない。過去renderの件数・検査結果を出すなら保存当時の結果と明示する。
5. runと採用pointer集合を読取り前後で比較し、変化時は最大1回だけ読み直す。なお変化する場合は更新競合を明示し、合成した完了／整合済み結果を出さない。読取り成功のstatusはpartialでもexit0だが、競合で読取り結果を成立させられない場合はexit1とする。writer lockを取得・作成せず、writerを待たせない。
6. 前後pointer一致だけでtransaction整合性を認定しない。翻訳のsource／preparation参照と現在pointer、runの設定との差異を表示し、異なる版の集計はその保存版に帰属させる。準備hashの差だけで翻訳内容が必ず無効と断定せず、厳密な再利用判定は詳細検査へ委ねる。candidate記録も採用済みと混ぜず、詳細確認へ案内する。
7. 出力先は「保存記録上のpath・成果物未検証」、mtimeは「記録file更新時刻」と明示する。最新／有効／実行中／生存中／最後の処理成功と断定しない。状態確認から再送・自動採用・復旧を起動しない。
8. 詳細検査も、採用pointerが検査中に変わったなら整合済み結果を返さず、状態変化による検査不成立として扱う。内部workflowでは既存lockの保護を維持する。保存形式変更やreader lock追加は行わない。
9. `_build()`の共有分類を修正し、`error == "not_processed"`はfailedでなくnot_processedへ入れる。軽量readerも同じ意味にする。取消は取消として説明し、保存情報から実際に送信済みかを捏造しない。既存の部分出力、安全なrender、再利用前検証は弱めない。

### B. 共通の観測通知と保存後集計

- 必要な既存stage APIへ任意keywordの観測callback（既定None）を追加する。既存`BeforeSend`の引数・送信前flushの責務は残す。独立API利用者にCLI表示を強制しない。
- run／reviewが工程の入口・終了・早期returnを、各stageがローカル準備・検証・再利用・回答訂正・分割・保存完了を、inferenceがattempt識別子・preflight・送信前／書込み・応答待ち・終端・通信retryを通知する。データは工程、ID、件数、非秘密設定、固定理由code等の必要metadataだけにする。
- 実requestの送信対象を発行元で投影する。PDF補正はowned／reference別。準備collectは実unitとsection、organizeは収集結果の全体整理。翻訳は実chunkのunit、reviewは明示選択unit／block／画像ページ。現在のcorrected_pages全体fallbackは廃止し、対応不明は不明とする。表示のための追加unit再生成やprompt／保存入力の変更をしない。
- 通信retryとdomain回答訂正を別表示にする。retry回数・3／6秒待機、各stageの訂正上限、入力上限による分割、認証保存drain／timeoutを変更しない。
- requestはattempt／job単位で識別し、Popen失敗・preflight拒否・callback失敗・timeout・取消・例外も含め必ず終端処理する。送信前callbackは到達証明ではない。helperへのgenerate書込み完了後に応答待ちへ移し、複数待ちを件数と対象で表す。
- PDF補正の成功は所有chunk検証・保存とrun manifest更新後、翻訳の成功は_adoptによるsnapshot／pointer保存後に更新する。参照ページを所有成功数へ加算しない。再利用は成功の内訳であり、新規成功と二重加算しない。
- 翻訳の完了／総数は最新採用snapshotのleaf unitを基準とする。分割通知後は総数を「更新中」とし、採用後に置換する。並列jobの分割が残る間は固定総数へ戻さない。保存前は検証・保存中と表示し、active leaf集合を新たな正本として管理しない。
- 準備は収集の検証済み記録と全体整理を区別し、最終採用前に工程全体を成功にしない。再送なしでも検証・再利用を表示する。
- 取消は既存Event／signalの実際の受付を観測できたときだけ受付済みと表示し、process／worker終了・保存処理完了と区別する。signal handlerで描画・I/Oを行わない。reviewの推論前検証を含め、取消表示のためだけに取消方式を再設計しない。即停止できないローカル処理はその事実を残す。

### C. 表示層・最終結果・依存

- 共通の小さいin-memory view stateをlockで保護し、workerから直接Richの画面を作り直さず、snapshotを描画する。永続化・IPC・汎用配信基盤は不要。
- Richは直接依存`rich>=15,<16`としてpyproject.toml／uv.lockへ明記する。調査環境の15.0.0は導入済みだが、transitive依存だけに頼らない。lock更新／必要installはImplementationで許可範囲を確認する。
- `Console(stderr=True)`と`Live`を使い、`redirect_stdout=False`／`redirect_stderr=False`を明示、約4Hzで更新する。既存loggingとの干渉を確認し、無関係なlogging設定を一括変更しない。終了・例外時はcontext／finallyでLiveを閉じ、最終stdoutをその後に出す。
- 表示用文字列はcontrol文字・Rich markupを不活性化する。raw request／回答／本文／画像payload／token／任意のstr(exception)を通知履歴に載せない。認証は検証済みpreflightの非秘密source／typeのみ。Richの描画失敗は表示層で最小の行表示へfallbackし、本来の保存例外を覆わない。送信前通知自体の書込み失敗を隠して送信継続しない。
- cli.py／cli_output.pyでresume／reviewの人向け最終表示とflagsを接続する。reviewは指摘件数と記録先を示し、本文・指摘全文の一覧を通常画面へ追加しない。必要な件数は今回保存した記録から取得し、新しい永続集計を作らない。
- stderr解析側は`--events-json`、resume／reviewのstdout解析側は`--json`を追加する。既存JSONのfield・値の意味を維持し、実request対象修正と必要なunit_ids／phase等は互換的追加と明示する。外部parserの利用実態は未確認であり、互換性検証済みとは主張しない。

### D. 変更先・文書

- 主な変更先：`cli.py`、`cli_output.py`、`run.py`、`review.py`、`preparation.py`、`translation.py`、`pdf_correction.py`、`inference.py`、`render.py`、必要最小の共通進捗／保存記録reader module、pyproject.toml／uv.lockと対応tests。snapshot／lock／Node protocolの変更は予定しない。
- 全caller・import・fixture consumerをImplementation時に再検索する。特にrunの早期return、review／reviseの詳細status、before_send consumer、`ai/tests/run-process.mjs`の通知照合、resume process fixture、CLI JSONを読むtestsと配布smokeを追随させる。production専用test flag／内部workflow mockは増やさない。
- READMEは進捗・status／詳細検査・機械向け出力の入口とリンクだけ更新。詳細CLI・移行は`docs/reference/usage.md`、Python観測境界・保存記録と検証範囲・not_processedの意味は`docs/reference/artifacts.md`へ記載する。
- `docs/design/progress-and-status.md`は本Planの確認済み変更と具体化した責務・指定に限って更新し、未実施結果を実施済みとしない。新ADR／GLOSSARY／別の状態管理文書は不要。
- `CONTRIBUTING.md`は変更flag、チェック選定、直接確認への参照を関連箇所だけ更新する。実動作の新しい具体手順は下記project検証Skillへ置き、READMEへ集約しない。

## 完了条件

1. PDF／EPUBの新規run、部分resume、再利用のみresume、およびreviewで、重い処理前から現在工程・対象・ローカル／AI待機が表示される。保存後成功・再利用・失敗／取消の数値と実保存結果が一致する。
2. 並列待機と動的分割、通信retryと回答訂正、取消受付と終了を混同しない。履歴理由が残り、保存前成功・架空の総数・参照ページの成功加算がない。
3. 人向け既定、stdout JSON、送信前JSON、非TTY／NO_COLOR／狭い端末での分離が成立し、秘密・本文・制御文字を漏らさない。
4. 通常statusが重い再構成を呼ばず、保存記録／過去検査／未検証成果物を明確に区別する。unknown、未処理、失敗、更新競合、旧版参照を正しく扱い、workspaceを変更しない。
5. 詳細status／内部再利用前検証／renderの安全性と既存JSON・保存形式を維持し、not_processed誤分類を共有箇所で修正する。
6. 下記の限定性能目標、正式チェック、Rootの直接確認と検証資産の再実行が成立し、証拠・限界・cleanupを確認できる。関連文書・移行説明が更新済み。

## 動作確認方法

### 保証対象・対象外とTDD

- 自動テスト：小さい保存記録readerの公開境界で、run-only／source-only／翻訳途中、unknown、未処理／失敗、path／hash拒否、pointer変化・参照版差異、重い経路の非呼出しを限定確認する。構造を決める仕様が明確でlocal testが軽いため、このreaderとnot_processed共有分類修正のみ既存`tdd` Skillでtest-firstを採用する。後者の境界はrender_document／workspace_statusのprocessing。巨大fixtureや全caseの固定はしない。
- 観測通知・Rich表示・CLI接続・文書は非TDD。既存testsへ必要最小のassertを追加し、保存後成功、reuse、request終端、並列／split／retry／訂正、flagsと出力安全性を確認する。必要なテストを最後まで先送りする指定ではない。
- 再利用先：`tests/test_run.py`／`test_resume.py`／`test_review.py`／`test_render.py`／`test_cli_output.py`と、`test_inference.py`／`test_preparation.py`／`test_translation.py`／`test_pdf_correction.py`。既存の実Python→実Node helper／public faux provider、network拒否、外部Docling公開converter／page backendの人工fixture、`tests/fixtures/resume_process.py`を優先する。
- 通常テストで全TTY画面・全signal境界・全組合せを恒久自動化しない。長い実時間待ちや大規模E2Eは追加しない。既存の3／6秒retry・30秒drain等を短縮して保証を置き換えない。
- 実LLM品質／実Docling・OCR／model取得／実認証／remote CI／任意book規模／SIGKILLの表示復元は対象外。spinner更新は処理前進の証明ではない。

### 検証資産の再利用と最小補完

- `CONTRIBUTING.md`の「正式チェック」「変更影響から正式実行範囲を選ぶ」「前提共有・比較・cleanup」「render/status」「新規run」「review」「resume」の既存手順は、隔離人工入力・公開CLI・faux・保存照合に再利用できる。過去のIssue実行記録は今回の証拠にしない。
- 現在project検証Skill／Feature Mapはなく、新表示のTTY起動・狭幅／明暗・frame／stderr観測手順が不足する。Implementationで共通`create-verification-skill`を明示利用し、`.pi/skills/verify-project/SKILL.md`と`references/feature-map.md`を最小作成する。必要な小helperだけ`scripts/`へ置き、既存fixture／手順を重複実装しない。Planningでは生成しない。
- 初期Map対象：run／resume／reviewのTTYと行ログ・出力flag、保存後集計／再利用／retry／訂正／取消、軽量statusと詳細検査、両形式small／large性能。既存の人工入力作成から起動・期待結果照合・必要な出力／保存物／request照合・証拠退避・cleanupまでを記載する。
- 現時点でstdlib PTYは利用可能。導入済みpyte／pexpect／playwrightは見つからないが、専用tool不在だけで停止しない。Implementation Rootが現在の操作／観測手段を再確認し、隔離した標準PTY等と必要な小さい接続・観測コードで具体化する。視覚確認は単なるSGR検出と区別し、明暗背景・狭い幅／高さで現在対象と異常理由を実際に照合する。成立した方法だけを手順／必要helperへ保存する。
- Workerは担当範囲のfocused検証と資産作成・変更部分の再実行、証拠・残存状態の報告を担当する。Rootは現在差分を直接読み、最終の保証対象すべてについて以下の代表経路を自ら操作・照合する。Createの一機能成立やWorker成功だけで最終受入れを代用しない。

### Rootの直接確認と正式チェック

1. 人工EPUBと人工PDFの公開CLI新規runを実helper／fauxで操作する。PDFは所有／参照別、EPUBはPDF行なし、prepareのcollect／organize、翻訳・renderの順序、最終出力・request・保存結果を照合する。
2. 部分失敗→resumeと、完成済み→空PATHの再利用のみresumeを確認する。後者はNode／認証／送信不要、attempt増加なし、既存immutable byte不変。翻訳の並列待機・分割は既存の小fixtureで保存数と表示を照合する。
3. 既存故障fixtureを使い、通信retryと回答訂正の各代表、取消受付／drain／終了、例外の各代表を観測する。全signal×境界を直接確認で再網羅しない。保存失敗で成功表示せず、旧pointer／artifactを維持する。
4. reviewのunit選択とPDF page選択を確認し、選択外を送らず、指摘件数／記録先とquality未承認を照合する。TTY代表は新規runとreview、取消代表はresumeで確認し、共通表示を全形式×全条件の積にしない。非TTY、NO_COLOR、狭い端末、明暗背景、stdout／stderr両JSON指定を分けて確認する。
5. statusをrun-only／source-only／翻訳途中／完了／古い参照／候補記録ありで確認する。必要file破損は拒否、読まない成果物の破損は通常statusで検証済みとしない。`--verify`／`--json`では従来の破損拒否・鮮度検査を維持。writer側がpointerを更新する人工条件でも書込み・長いlock待ち・偽の整合認定がない。
6. `CONTRIBUTING.md`に従い、共通Python接続・工程間再利用・依存変更のためcore全pytest、PDF extraの既存4ファイル（test_pdf／test_run／test_review／test_pdf_candidate_acceptance）、Ruff check／format、PDF環境のty、CLI helpを実行する。既存環境を無断syncしない。Node source／protocolは変更予定なしで全Node test反復は既定で追加しないが、実差分がそこへ及べば全Vitest・Biome／oxlint・build／resource鮮度を追加する。
7. Richの直接依存化が配布へ及ぶため、既存wheel buildと`scripts/check_distribution.py`の配布検査、core-onlyでのCLI import／保存-only statusを確認する。必要install・registryアクセスの許可をImplementationで確認し、不足を成功扱いしない。テスト／配布consumerの新しいflags追随も検証する。

### 性能Probeと受入れ目標

Planningでproductionを変更せず、自作source-only保存データ＋保存済みpartial renderを作り、macOS 26.7.1 arm64／Python 3.14.9／Pydantic 2.14.1、空PATH・offline、他test非並行、fresh CLI processで各3回計測した。OS cacheはclearしておらず、実Workspace／翻訳済みbookのbaselineではない。各回exit0、workspace全file hash不変、所有TemporaryDirectoryは削除済み。

| 形式・unit数 | source block数 | 現行の通常status秒（raw 3回） | 中央値 |
| --- | ---: | --- | ---: |
| PDF 100 | 100 | 0.2333 / 0.2288 / 0.2330 | 0.2330 |
| PDF 3,000 | 3,000 | 1.8656 / 1.8855 / 1.8774 | 1.8774 |
| EPUB 100 | 1 | 0.2366 / 0.2346 / 0.2327 | 0.2346 |
| EPUB 3,000 | 1 | 6.9520 / 11.7251 / 6.5806 | 6.9520 |

再作成条件：PDFは`save_source_snapshot`で`p-{i}`というparagraph blockを100／3,000件、textは`Self authored paragraph {i}.`、pageは`i//10+1`、self-authored evidence、source処理completedとして保存。EPUBは`tests/test_epub.py:make_epub`で1 XHTMLのbodyへ同文の`<p id="p{i}">…</p>`を反復し、read_epub→extract_epub_source→公開保存。双方render_documentで訳文なしのpartial出力を1回保存してから計測する。生成／render時間はstatus時間に含めない。詳細JSONの別計測でもPDF／EPUB両方の規模依存を観測したが、通常表示の比較には本表を用いる。

Implementationでは変更前に同じ人工条件で通常statusを再計測し、変更後も各3回のraw値・中央値・min/max・exit・入力規模とfile不変を記録する。目標は各形式の100／3,000 unitで通常status中央値1秒以内、3,000 unitでは同条件beforeの50%以下。外乱で判断不能なら該当条件だけ双方追加2回を行う。未達なら原因と限界を報告し、黙って閾値を緩めない。これは検査責務を分離した軽量経路の応答目標であり、同等の完全検証を高速化したとの主張や任意Workspaceへの保証ではない。

代表的な翻訳済みfaux workspaceでも集計・読取り範囲・raw時間を1回直接確認し、source-only性能だけで翻訳済み全規模を保証しない。恒久の大規模benchmark suiteは作らない。

Rich単体の別Probeでは、15.0.0のLiveをstderr、redirect両方Falseで使い、stdlib PTYの80／36桁・同期CPU処理約0.6秒で各10回の行再描画、stdoutの完全JSON分離、NO_COLOR時のSGRなしを観測した。これは実アプリの操作・明暗視認性・native GIL保持・取消成立の証拠ではない。公式仕様の参照は[Rich Live](https://rich.readthedocs.io/en/stable/live.html)、[Console環境変数](https://rich.readthedocs.io/en/stable/console.html#environment-variables)、[15.0.0実装](https://github.com/Textualize/rich/blob/v15.0.0/rich/live.py)。

### 証拠・cleanup・具体化の裁量

- 既存規約に従い`work/test-verification-<unique>/`へrevision／差分範囲／条件・command／exit／raw時間／期待と観測／未確認と、人工入力だけの必要なstderr・frame／保存物照合を残す。実秘密・書籍・raw環境変数は記録しない。証拠はworkspace外へ退避し、cleanup後も全参照を再読込みする。
- 自分が作った一時入力・workspace・credential・PTY／childのみを識別して回収する。work全体、他人のworkspace、既存venv／model／認証、既存processを削除・停止しない。失敗時もcleanupと残存状態を報告する。
- Implementation Rootは承認済みの保証対象・対象外・期待結果を維持して手段を具体化し、共通Create／関連更新による最小Skill／helper補完を判断できる。予定外のhelper追加やCreate採否変更だけで再計画・追加gateを要求しない。専用tool発見や完成済みharnessを前提条件にしない。
- この裁量は必須コンポーネント代替・権限／認証／安全条件の迂回・無断installを許可しない。明示禁止・規約・正式Planの制約は維持する。公開契約／仕様／設計／保証／期待結果の変更は確認・REPLAN_REQUIRED経路へ戻る。安全に分離・操作できない、不足が許可範囲で解決不能な場合は、試した方法・未確認方法・不足・再開条件を区別してBLOCKEDとし、未成立の手順を成功扱いしない。

## Planning後の扱い

Approveされた全文を`docs/plans/progress-and-status.md`へ正式保存する。その後taskflow-to-ticketに渡し、tracker設定（必要時）・分割・最終本文と保存公開の個別承認を得る。各Work Itemへ正式Plan、保証範囲・検証方法・TDD対象・資産補完裁量と安全限界・文書更新を引き継ぐ。Planningではproductionを実装せず、公開後もImplementationを自動開始しない。