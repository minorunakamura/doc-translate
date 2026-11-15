# PDF原文の代表標本準備（Issue #79）

[Approved Plan](../plans/pdf-source-quality-feasibility.md)の実装方針1の準備契約を以下に保持する。Issue #80の隔離offline検査→回復→自動採否→英語出力は末尾の追加節を使う。通常製品の機能でも実評価runnerでもない。警告件数は誤り件数ではない。人工回答による契約成立をAI精度・実書籍の回復成功・確認負担削減と呼ばない。

## 入口と境界

repository rootで既存PDF extra付き`.venv`、pytest、Pillow、Node、既存build済み`ai/tests/pdf-correction-process.mjs`を使う。追加install/sync/build、実Docling/OCR、実認証/preflight/AI、通常run/resumeは使わない。Nodeは既存no-network付きfauxだけで、人工preflightはその内部に閉じる。resource鮮度は[開発ガイド](../../CONTRIBUTING.md#正式チェック順序を含む)に従い確認し、不足時は停止する。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# 今回承認された非公開evidence領域。既存Workspaceではない新しい0700親を使う。
umask 077
EVIDENCE=$(mktemp -d "$PWD/work/issue79-preparation-XXXXXX")
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git status --short > "$EVIDENCE/dirty.txt"
git diff > "$EVIDENCE/scope.diff"
.venv/bin/python .pi/skills/verify-project/scripts/pdf_source_feasibility.py synthetic \
  --output "$EVIDENCE/synthetic"
# 書籍の読取りは対象とprivate保存先が承認されている場合だけ。
.venv/bin/python .pi/skills/verify-project/scripts/pdf_source_feasibility.py book \
  --workspace work/design-and-build-great-web-apis --output "$EVIDENCE/book"
```

`--output`は未作成で、直上の親は0700。既存出力、既存Workspace配下、元との同一/包含、symlink経路を拒否する。CLIはignore済み`work/`内だけを受け付ける。上書き・hardlink・自動再開はしない。書籍の例外値には本文が入り得るため、CLIは例外型だけ表示する。失敗directoryは成功とせず保持し、原因確認は非公開領域内だけで行う。通常stdout/Git/Issueへ本文・画像・raw回答を出さない。

### 自作対照

`pdf_source_samples.py`は`tests/test_pdf.py`のconverter/page backend人工fixtureを局所再利用し、既存共有fixtureを変更しない。自作文書をPillowで実際に描いたPDF/PNGを生成し、公開`acquire_pdf`→公開`correct_pdf`（実Node/faux、明示的な人工応答）→公開`assemble_pdf_source`を通す。白紙画像に意味正解を置かず、完成済sourceの手書きだけでもない。モデル重みは人工fixtureの前提検査用で、実OCRは起動しない。

4組8例を用意する。

- 不正charspan由来のtargetなし履歴がある現在正常本文と、実欠落/追加・表cell・数値/codeの変更がある対照。同じ組の2ページ目は測定値が塗り潰された原本で、隠れた正解値を置かない。
- 空本文の正常図と、OUTLETを失うcrop＋図内文字を本文に重複させた対照。
- 本文の跨ページ正常接続と、欠落＋矛盾した接続の対照。
- codeの跨ページ正常接続と、構造上は接続できても命令行が欠ける対照。警告なしでも現在正常とは限らない。

`synthetic-inputs/sample-NN/`は取得/補正履歴と組立済`current-source.json`、原本/PNGを保持するがsource採用pointerを作らない。入力名に正常/欠陥ラベルを置かず、設計した人工variantと比較提案は`review-only/proposed-comparisons.json`へ分離する。この表はRoot照合前の提案であり、確定正解ではない。補正回答は品質判断ではなく、正常/欠陥の現在状態を作る人工応答である。

### 実書籍の読取専用準備

元source SHA-256は`22325d32f4ed4babf3da97b7bfd94ea983b6629e2e805798b2e690c60575f471`に固定する。具体的chunk digestはhelperの`BOOK_CHUNKS`に固定し、無断で範囲を広げない。

| 所有ページ | 許可reference | 選定上の役割（画像による正解ではない） |
| --- | --- | --- |
| 36–40 | 35, 41 | targetなし履歴、空図、本文接続 |
| 81–85 | 80, 86 | その他の判読不確実性、正常対照候補 |
| 181–185 | 180, 186 | code接続の片側、targetなし履歴 |
| 186–190 | 185, 191 | code接続の相手と成功接続、空図、本文接続、その他 |

4範囲各5所有ページ、計20所有。後2範囲は接続を切らず**必ず一緒に組み立てて照合**する。10所有ページを5ページrequestと偽らない。本helperは送信requestを作らず、将来の実送信scope/cap承認は別。61–65は65/66の成功接続を切り、閉包に66–70も必要なので初期標本に含めない。実書籍の本文失敗接続の代表性には限界が残る。

公開`load_source_snapshot`後、4chunkを一度の公開`assemble_pdf_source`へ渡す（全70chunkの個別load反復はしない）。元sourceと部分sourceを`fragment(chunk_sha256, original block_id)`の系列で対応させ、本文/kind/locationsとID対応後の警告multisetを比較する。ID振り直しを内容変更に数えず、部分再組立で障害が消える資料は拒否する。全元fileのhash/mtimeを前後照合する。

コピーは選択chunk、全20所有と必要referenceのPNG、現在block/fragment/警告に限定。原本PDF・raw全体・source全体は複製せず、元artifact/hash参照を残す。`book-inputs/`は**単独で公開load可能なWorkspaceではない**。元の固定hash参照が再検証に必要で、後続の実評価用隔離Workspace作成を提供したことにはしない。

## 表と証拠の読み方・送信前停止

- `book-inputs/page-N.json`：所有/reference区分、PNG、現在の接続済block（跨ページ全文を含む）/構造/fragment/警告。reference-onlyページの現在block一覧が空でも、画像contextを検査済みとはしない。
- `correspondence.json`：元ID↔部分IDとfragment系列。`references.json`：元workspace/source/draft/chunk hashと4範囲、切断不可の接続group。
- `review-only/page-review.json`：全画像とページ別内容への索引。`root_image_review=pending`、現在状態・回復可能性・期待確認要否/採否はnullから開始する。自作の表も同じく未固定。
- `observations.json`：操作/集計/未採用/実送信0/cleanup。`hashes.json`：保持全fileのSHA-256とcleanup後readback対象。`original-inventory.json`と`preservation.json`：書籍元fileのhash/mtime不変。

Rootは**全標本の原画像と現在内容を直接照合**し、正常/現在欠陥/回復可能/判定不能、期待確認要否/採否と理由を、別の非公開review資料へ固定する。既にhash化した入力・証拠を後から書き換えない。期待と正解を将来の評価model入力へ渡さず、`review-only`を丸ごと入力に混ぜない。referenceは所有結果の代用品ではない。人照合なし、Agentの見落とし/相関誤り、人工応答の限界を残す。実書籍に復元可能な実欠陥を確認できない場合、合成例で実書籍回復成功を代用しない。

ここで停止する。実AI/auth/preflight、検査・回復・採否・通常CLI統合は行わない。将来は対象画像/本文/範囲/hash、固定期待、model/config、全retry横断cap、認証操作と保存先を提示して別承認を得る。このIssueの準備完了を新方式の有効性証明にしない。

## 再実行とチェック

[verify-project](../../.pi/skills/verify-project/SKILL.md)の「PDF原文標本準備」から、既存の保存-only/公開PDF取得補正手順と本helperだけを選ぶ。明示実行の小さいself-checkは以下。Skill内の専用テストなので通常製品のCI収集集合・共有fixtureを変更しない。

```sh
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_feasibility.py::test_pdf_source_samples_use_public_acquire_correction_assembly -q
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_feasibility.py -q
.venv/bin/pytest tests/test_pdf.py tests/test_pdf_source.py -q
```

代表consumer単独fresh process→新module→関連公開境界の回帰を実行し、Rootは全体Ruff check/format、既存PDF extraのty、影響moduleと別invocationの直接操作・全画像照合、Reviewerは独立受入を担当する。共有fixture/src/Nodeへの影響が広がれば[開発ガイドの選定](../../CONTRIBUTING.md#変更影響から正式実行範囲を選ぶ)へ戻る。無関係な全Map/全pytest/PDF4/Vitest/buildを自動追加しない。

自作の一時PDF/model/credential/workspaceだけをTemporaryDirectoryでcleanupし、同期helper childは既存generateで回収する。保持資料はその外側へcopyして全fileをhash再読する。書籍ではchild/一時Workspaceを作らず元はread-only。command/exit、revision/dirty/diff、環境/resource hash、期待/観測/失敗/未確認を同じ非公開evidence親に記録し、終了後に再読する。実AI意味品質、実OCR、通常run/resume、全冊、他OS/remote CIは未確認。

## 隔離offline実験（Issue #80）

準備とは別の`offline`入口。実AI評価はIssue #81で別承認後に行う。通常run/resume、manual revise、手書きmanifest/proof、旧品質moduleは使わない。production src/tests共有fixture/Node/依存は変更しない。

### 起動と再実行

既存環境・resource鮮度を確認してからrepository rootで実行する。以下の`INPUT`は#79で固定した**人工**準備directory（`hashes.json`と`synthetic-inputs/sample-01..08`を持つ親）へ置換し、`INPUT_SHA`は承認済みmanifest hashを指定する。毎回新しい0700 evidence親と未作成出力を使う。固定期待ファイルはこの入口へ渡さない。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
umask 077
EVIDENCE=$(mktemp -d "$PWD/work/issue80-offline-XXXXXX")
INPUT=/absolute/path/to/fixed/synthetic-preparation
INPUT_SHA=approved_sha256_of_hashes_json
REVISION=$(git rev-parse HEAD)
git status --short > "$EVIDENCE/dirty.txt"
git diff > "$EVIDENCE/scope.diff"
printf '%s\n' "$REVISION" > "$EVIDENCE/revision.txt"
.venv/bin/python .pi/skills/verify-project/scripts/pdf_source_feasibility.py offline \
  --workspace "$INPUT" --input-sha256 "$INPUT_SHA" --revision "$REVISION" \
  --cap 80 --output "$EVIDENCE/trial" > "$EVIDENCE/entry.log" 2>&1
# 失敗時もexitを記録して停止し、既存結果を上書きしない。
printf 'entry exit=%s\n' "$?" > "$EVIDENCE/exit.txt"
```

`--cap 80`はこの人工操作の総予約上限であり実送信許可ではない。通常の人工回答は18予約（8初回検査、6所有chunk再補正、4独立再検査）。入力manifestの全hash、HEAD、出力分離を確認し、8例各1trial・最大1回復roundだけを実行する。追加trialは消費/失敗を保持して別に判断する。CLIにprovider/auth/実送信optionはない。`pdf_source_offline.py`の明示的人工応答adapterと現行requestの送信前捕捉だけが、既存`pdf-correction-process.mjs`の実Node/faux/no-networkへ接続する。実runtime/preflight/認証には接続しない。

### 実際の境界と採否

`pdf_source_trial.py`は公開load/correct/assemble/generate/save/units/renderだけを接続する局所補助。`trial(source, output, digests, budget, command)`は固定chunkを受ける内部実験入口で、人工fixtureの正解に依存しない。後続書籍では#79の固定chunk/原本hash・`correspond`によるfull sourceとの同等性を再検証し、scope/config/送信資料と`command`の実接続を**別承認**する。現CLIは書籍を送信しない。

- **独立コピー**：公開chunk/draft loadで検証したchunk、生成attempt、draft、原本PDF、raw、draftの全PNGをimmutable依存閉包としてbyte copyする。hardlink、pointer/auth/runコピー、manifest編集はしない。公開loadが全draft画像を検証するため、送信範囲外の依存PNGもコピーに必要な場合がある。実書籍の閉包サイズ/機密性は#81承認資料に含める。#79のbook-inputsだけでは単独loadできない。
- **baseline / candidate / adopted**：baselineへ公開saveした後は全hash/mtime不変。候補は別directoryの公開correct_pdf（各最大5所有ページ、元chunkの許可referenceを維持）、公開assembleで作る。policyを初回と区別して旧成功のreuseを回復と数えない。candidateにsource採用pointerを作らない。
- **検査**：初回と別requestのreinspectionに原PNGと現在source、再検査ではbaselineも渡す。再生成chunk全体と接続境界を含む全選択範囲を検査する。期待/正解はrequestへ渡さない。生成理由/履歴は未信頼data。`Inspection`は全page、全block、全cellの判定・正確な現行全文quote、画像hash/位置/観測、構造、図crop/構成要素/重複、両側接続と所属、欠落/局所不読の前後anchorを要求する。
- **自動gate**：範囲外/偽quote/欠落coverage/不読page/履歴対応不明/不正構造を拒否する。空図のquoteは空のまま画像根拠で検査する。初回の具体的欠陥と再検査の全before/after対応groupを照合し、正常blockの内容/位置/構造と相対順、正常/不読cellを保持する。追加/削除/結合/分割/順序/表/図crop/数値/code復元を一律禁止しない。未対応変更、独立再検査に残る欠陥は不採用。
- **局所不読**：history-layout page2の隠れた値は不読のまま残す。page1復元はpage2のlabel/所属/不読領域と近傍が不変で独立再検査済みの場合のみ許可する。`remaining_gaps`を残し`whole_sample_clean=false`とする。履歴のみの正常page1に不要な修正を求めず、page2を全体正常へ読み替えない。旧履歴はbaseline/元artifactに不変で保持する。
- **保存**：gate成立後だけ新規`saving/`へ公開save、公開load readback、空PATHの別Pythonでload/units/renderを実行する。source SHA、unit参照、全block出力coverage、英語本文/cell/code、原PNGのcrop byteと生成assetを照合する。翻訳対象unitありはsource-only `partial`、空図のみでunitなし・coverage成立・source completedなら`completed`が既存仕様。exit0だけを成功にしない。全照合後だけdirectoryを`adopted/`へ公開する。
- **停止**：拒否/取消/auth/storage/cap異常は追加送信を止める。saveのreplace後fsync例外でもpointerが切替済みの可能性があるため、`saving/`を失敗出力として残してreadback可否/hashを記録し、baseline不変と区別する。失敗出力をadoptedと表示しない。成功/失敗の自動再開・通常resume保持は提供しない。

同じ`SendBudget.before_send`を全case/工程/coverage retry/transport retryで共有する。configと承認request全文（coverage訂正feedback以外）、scope/policy/schemaを照合し、送信直前予約を計数する。画像byte/hashは呼出側の`arm`と公開correctのloadで検証する。既存coverage最大3生成・transport最大3attemptを変更しない。preflightはcallbackより前なので、cap=0も実認証を無副作用にする仕組みではない。no-sendはfaux内に限定する。

### 観測・証拠・限界

`execution.json`はrevision/helper hash、設定、上限、実AI0。`*-authorization.json`はprompt/schema、maxTokens/timeout、画像hash/bytes、scopeと残予約。`sends.json`は全実request/configと予約、`*-attempts.json`は独立検査の全attempt/usage（補正attemptはcandidate内の公開記録）、`*-inspection.json`は回答。`sample-NN/result.json`と`cases.json`は採否/残件/保存失敗readback、`candidate-source.json`と`adopted/readback.json`で候補と出力を区別する。`input-inventory.json`、`observations.json`、`hashes.json`で元不変/cleanup後readbackを照合する。予約は課金確定ではなく、usage未観測を0にしない。

正常対照03/05/07はbaseline、02/04/06/08は人工復元を採用可能、01/02はpage2残件という期待を別途照合する。これは人工応答での契約確認であり、画像認識・意味判断はAIに依存する。機械validatorは観測文が真実か証明しない。実AI前にはこの資料に加えて固定期待（modelへ非送信）、provider/model/thinking、認証/更新操作の非秘密種別、対象原本/本文、全工程cap、価格/usage不明、保存先/cleanupを#81で提示する。送信前資料を作れることと実送信承認は別。

```sh
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_trial.py::test_pdf_source_trial_public_pipeline_and_independent_readback -q
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_trial.py .pi/skills/verify-project/scripts/test_pdf_source_feasibility.py -q
.venv/bin/pytest tests/test_pdf.py tests/test_pdf_correction.py tests/test_pdf_source.py tests/test_units.py tests/test_render.py -q
```

小self-checkは偽本文/code/cell quote、空図の正否、両側接続、scope/正常内容/局所不読保持、coverage retry/transport cap、取消、人工auth失敗、保存replace前/後、独立再検査失敗を扱う。Rootは別invocation・正式Ruff/format/PDF extra tyと必要consumerを再実行し、Reviewerが独立確認する。全core/Node/他Mapは変更影響なしなら自動追加しない。

全失敗/command+exit/版/dirty/diff/期待観測は非公開Workspace外へ残す。人工資格情報/回答controlは所有TemporaryDirectoryのみcleanup、同期childはwait済み。pytestの所有basetempは必要証拠退避後だけ削除し、既存workを削除しない。cleanup後に全保持hash/参照を再読する。書籍本文/画像/回答はstdout/Git/Issueへ出さない。未確認は実書籍操作/実AI意味品質、実OCR、通常CLI/全冊/手動revision統合/中断replay/他OS/remote CI。今回の成功でこれらを完了扱いしない。

## 固定実評価の送信前準備（Issue #81）

今回の入口は**sample-01だけ**。[正式Plan](../plans/pdf-source-quality-feasibility.md)の先頭限定節・§4・Issue #81完了条件と[改訂Work Item](https://github.com/minorunakamura/doc-translate/issues/81#issuecomment-6877899293)を先に読む。旧9case／cap198／許可内helper変更や追加trialごとの再承認手順は使わない。他7標本・実書籍は別承認待ち。通常run/resume、無人repair loop、正解／schema／validator／`normalized`／`decide`／製品変更は対象外。

`pdf_source_evaluation.py`のCLIは資料作成で停止し、実runtime/auth/preflight/AIへ接続しない。現行requestの捕捉だけは既存Node/faux/no-networkの人工preflightを使う。準備・実行双方で固定sample-01のchunk・所有1,2・参照なし・画像2枚・configを照合する。固定正解はhash確認だけでmodel／採否へ渡さない。repository root、既存PDF extra／Node／installed resource鮮度を確認し、不足時はBLOCKED、install/sync/buildしない。正式Plan SHAは`d5f9adae55e8adb0e29c36c1b712e49977c424f7538e10ebd51739a43fba38c9`。`fixed_guard`の保護hashを現在値に置換して通さない。既存入力／旧結果は再作成しない。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
umask 077
# この許可の固定親。別の親にして累計をresetしない。
AUTH="$PWD/work/issue81-sample01-urr6jl4s"
EVIDENCE=$(mktemp -d "$AUTH/preparation-XXXXXX")
printf '%s\n' '{"provider":"openai","model":"gpt-6.1-terra","thinking":"medium","options":{"timeoutMs":1200000,"maxTokens":null}}' > "$EVIDENCE/config.json"
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git status --short > "$EVIDENCE/dirty.txt"
git diff > "$EVIDENCE/scope.diff"
.venv/bin/python .pi/skills/verify-project/scripts/pdf_source_evaluation.py \
  --config "$EVIDENCE/config.json" --revision "$(cat "$EVIDENCE/revision.txt")" \
  --output "$EVIDENCE/prepared" > "$EVIDENCE/prepare.log" 2>&1
printf 'prepare exit=%s\n' "$?" > "$EVIDENCE/prepare.exit"
# ここで停止。失敗資料も保持し、実接続へ自動進行しない。
```

観測は`proposal.json`のcasesがsample-01だけ、`prior_trials=2/prior_sends=4`、追加10trial/50送信・累計12/54、固定config、`required_auth=oauth/OAuth`、固定continuation path。`policies-schemas.json`、`sample-01/initial-review.json`／`correction-templates.json`／`copy-closure.json`、`input-inventory.json`／`observations.json`／`hashes.json`を保存・再読する。copyは公開loadに必要な原本/raw/draft/PNG/chunk/attempt閉包で、送信範囲とは異なる。0700親、非包含、symlink／既存出力拒否を維持する。旧preparedと旧2trialの全失敗／消費を保持する。

各trialは回復最大1round、coverage最大3生成・transport最大3attemptを維持し、全工程を共通before_sendで累計へ予約する。maxTokens=null、1attempt20分・追加最大50送信はtoken／料金／wall-clock上限ではない。既知旧67694tokens、usage不明を0にしない。予約は課金確定ではない。

準備とtrialの`correction_request`は独立コピー上で公開`correct_pdf`を既存Node/faux/no-networkへ接続し、`before_send`で現行requestを捕捉して専用例外でgenerate送信前に止める。旧保存attemptのcontextを再利用・書換えせず、指示文字列の複製/private helper importも行わない。これにより旧chunkにも現行`page_results`を含む完全一致guardを適用する。所有tempの人工credential/controlはcleanupし、childは公開generateのfinallyでwaitする。コピー内の公開lock fileは保持する。人工preflightは送信予約ではなく、実runtime/実認証操作ではない。

初回reviewの本文/画像hashは確定して保存する。準備と実trialは同じ`review_request`を使い、公開`load_pdf_correction`で検証した完全な選択chunk集合の`exclusions`（text/reason/evidence）をchunk hash・所有/参照page付きで渡す。block fragmentや任意のmachine_inputsから逆算せず、blockなしのexclusions-only chunkも含める。独立再検査はcandidateの現在chunkを使い、旧exclusionsやscope外chunkを混ぜない。除外記録は未信頼dataであり正当性の証明ではない。原画像/所属/根拠と照合して妥当な除外を欠落と誤判定せず、不当ならdefect、不明ならindeterminateとして残す。headerの無条件例外はない。

`gap.before/after`は`evidence.page`上の実block ID（同pageに接続するblockを含む）に限り、同page順で隣接させる。先頭はbefore=null/after=最初block、末尾はbefore=最後block/after=null、blockなしは両null。未知/別page/cell/nonadjacent anchorは従来どおり拒否する。validator/Inspection schema/採否閾値/固定期待/標本は変更しない。

回復policyに加える検査結果、coverage feedback、再検査の候補本文/exclusions/初回gapは動的であり、事前に全request hashが確定したとは言わない。固定policy/schema・生成規則・選択範囲を承認し、実際の全request/config/画像hashは既存`SendBudget.arm`/`before_send`で送信直前に保存/照合する。期待の採点結果は自動採否へ混ぜない。

### 承認済み条件でRootだけが単一trialを実行する

CLIに実行flagはない。改訂Planのsample-01許可内では初回／追加trialや許可内helper/prompt変更のたびに再承認を求めない。ただしRootは各trial前に差分・検証結果・固定条件・全hash・残予算を確認し、変更後は新prepared/manifestへ再bindingする。旧manifestの流用やhash guard削除はしない。schema/validator/採否条件・固定正解・入力・config・製品実装は保護対象のまま。

初回だけRootが`initialize_continuation(config)`を別操作で呼び、固定`CONTINUATION`に旧2trial/4送信の参照付きoriginを作る。既存なら再初期化しない。欠落が不審なら推測で復元しない。実continuationはWorker／Reviewerが操作しない。Root一名・一系列で、既存continuationと未使用outputを渡して**一回ずつ**呼ぶ（無人loop禁止）。以下は実送信用で、無送信確認では実行しない。

```python
from pathlib import Path
from pdf_source_evaluation import CONTINUATION, run_approved

run_approved(Path(PREPARED), MANIFEST_SHA, Path(OUTPUT), CONTINUATION)
```

repository rootの既存Pythonで`PYTHONPATH=.pi/skills/verify-project/scripts`を指定する。`PREPARED`と`MANIFEST_SHA`はRootが現資料を確認して確定し、`OUTPUT`は固定AUTHORIZATION下・continuation外の0700親に置く未作成path。上記呼出にinitializationを混ぜない。`production_command`は`None`で公開generate/correct_pdfの検証済みinstalled runtimeを使い、手動Node overrideやfauxを品質評価に使わない。

既存アプリOAuth preflight・通常refresh/saveだけを許可し、子環境のambient API keyを除外する（固定openaiでは少なくとも`env -u OPENAI_API_KEY`）。共通SendBudgetは全工程/retryで予約前に`oauth/OAuth`を強制する。不一致はcancel・非秘密`auth-rejected.json`保存・無送信停止。過去attemptは現在の認証証明ではない。Pi auth参照／コピー、新login、手動credential変更、API key／別provider fallbackは禁止。preflightはbefore_sendより前なのでcap=0を無認証dry-runにしない。

- 全操作でlockを保持し、runtime前にも取消・累計・固定条件を照合する。番号・出力・版付きmanifestを独立した`continuation/start-NN.json`へexclusive fsync/readbackしてからtrial directoryを作り、同じ内容を`trial-NN/start.json`にも保存する。再読では双方の存在・内容一致・連番を必須とし、末尾directory欠落や独立開始保存直後の中断でも返却せずruntime前に停止する。0送信の失敗も1trialとして消費する。この契約導入前の独立開始記録なしの追加trialは厳密な再開入口では拒否する。旧実ledger・prepared・結果・stopはそのまま保持し、開始前証拠の後付け／移行で通さない（Plan originの旧2trial/4送信参照は変更しない）。
- 全送信は`trial-NN/send-NN.json`へ予約をdurable保存してから進む。送達不明・失敗予約も返却しない。出力の`sends.json`と予約を照合し、結果manifest/start hash付き`completion.json`を保持する。再invocation・修正・新出力で累計をresetしない。
- 完全なtrialでも未分類なら`RootReviewRequired`で待つ。Rootは全記録→原因／安全不変確認→許可内最小修正（不要なら具体的理由）→関係テスト／正式チェック／無送信直接確認→新資料／残数照合を経て、`classify_trial(CONTINUATION, number, "continue", analysis)`を一度記録する。analysisに証拠参照を含める。品質拒否や追跡可能な有限通信失敗だけを継続対象にし、cancel flagを解除して途中再送しない。
- 取消・上限・auth・scope/config/保護hash・storage/readback・記録欠落／矛盾・元変化・秘密露出・並行不明・未知原因では停止し、人間の明示確認を待つ。安全marker `safety-stop.json`に削除／解除APIはない。lock競合側もmarkerを残し、active側は次予約前に止まる。marker／予約／失敗を削除して再開しない。保存不能でも送信しない。
- 固定条件・証拠が正常で許可内helperのimplementation bindingだけが古い場合は新資料を準備し直す。未使用output条件違反も開始前拒否であり、保護対象やmanifest破損をこの扱いに混ぜない。

Rootは全画像・全回答・候補・採用／未変更部分・公開保存／別process英語出力を固定期待へ直接照合する。不要指摘／変更0、2header不要復元0、局所indeterminate保持、**回復不要でbaseline保存→別process再読→英語出力**を満たしたら`classify_trial(..., "stop", analysis)`で停止し追加trialしない。`whole_sample_clean=false`は期待どおりの残件であり、実行完了だけを品質成功にしない。確認箇所／重複除去page／判断数・時間／usage既知不明・原因／修正／検証／結果・累計／残数は採点用の非送信記録に分離する。

### 人工入力の無送信直接確認

**実AUTH／continuationから分離した新sandbox**だけで行う。公開generate/correct/save/validatorと既存実Node/faux/no-networkを使い、人工OAuth metadata以外は実認証へ接続しない。実runtime到達はassert拒否する。人工標本生成は8例だがprepare/trial対象はsample-01だけ。実入力／旧結果／実消費は変更しない。

repository rootで以下の新規0700親を用意し、続くPythonをエディタで`$DIRECT/sandbox.py`へ保存する（一時検証scriptであり恒久helperへ追加しない）。ログ／scriptはsandboxのhash対象外へ置く。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
umask 077
DIRECT=$(mktemp -d "$PWD/work/issue81-direct-XXXXXX")
mkdir -m 700 "$DIRECT/sandbox"
export EVIDENCE="$DIRECT/sandbox"
```

```python
import json, os, subprocess, sys, tempfile, threading
from pathlib import Path
from unittest.mock import patch
import pdf_source_evaluation as e
from pdf_source_feasibility import synthetic, inventory, finish, sha, write_json
from pdf_source_offline import CONFIG, ArtificialProcess
from pdf_source_trial import SendBudget
from doc_translate.pdf_correction import load_pdf_correction
from doc_translate.pdf_source import assemble_pdf_source
from doc_translate.workspace import workspace_lock

os.umask(0o077)
phase = sys.argv[1]
assert phase in {"complete", "interrupt"}
root = Path(os.environ["EVIDENCE"]).resolve()
assert root.stat().st_mode & 0o077 == 0
assert not root.is_relative_to(e.AUTHORIZATION)
samples = synthetic(root / "samples") if phase == "complete" else root / "samples"
source = samples / "synthetic-inputs/sample-01"
fixed = json.loads((source / "current-source.json").read_bytes())
digest = fixed["blocks"][0]["structure"]["fragments"][0]["chunk_sha256"]
chunk = load_pdf_correction(source, digest)
expected = root / "artificial-expectations.json"
if phase == "complete":
    write_json(expected, {"non_send_canary": "ARTIFICIAL_EXPECTATIONS_NOT_SENT"})
    write_json(root / "input-before.json", inventory(source))
old = {}
for number, count in enumerate((1, 3), 1):
    path = root / f"artificial-old-{number}.json"
    if phase == "complete":
        write_json(path, [{"artificial_reservation": True}] * count)
    old[str(path)] = sha(path.read_bytes())
# Sandbox constants only; protected implementation/Plan guards remain enabled.
e.AUTHORIZATION, e.CONTINUATION, e.SAMPLE = root, root / "continuation", source
e.CHUNK, e.IMAGES = digest, [p.image.sha256 for p in chunk.page_images]
e.EXPECTATIONS, e.EXPECTATIONS_SHA = expected, sha(expected.read_bytes())
e.SOURCE_SHA = sha((source / "current-source.json").read_bytes())
e.BASELINE_SHA = sha(
    assemble_pdf_source(
        workspace=source, chunk_sha256s=[digest], snapshot_id="issue80-baseline"
    )
    .model_dump_json()
    .encode()
)
e.CONFIG_SHA, e.OLD_SENDS = sha(CONFIG.model_dump_json().encode()), old
before = json.loads((root / "input-before.json").read_bytes())


def forbidden_runtime(*args, **kwargs):
    raise AssertionError("Real runtime forbidden in sandbox")


real_callback = SendBudget.before_send


def artificial_oauth(self, config, request, preflight):
    preflight = preflight.model_copy(
        update={
            "auth": preflight.auth.model_copy(
                update={"type": "oauth", "source": "OAuth"}
            )
        }
    )
    return real_callback(self, config, request, preflight)


with patch("doc_translate.ai_runtime.installed_runtime", forbidden_runtime):
    prepared = root / "prepared"
    if phase == "complete":
        e.prepare(
            prepared,
            CONFIG,
            subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        )
        e.initialize_continuation(CONFIG)
    manifest = sha((prepared / "hashes.json").read_bytes())
    result = root / "trial-03"
    if phase == "complete":
        with tempfile.TemporaryDirectory(prefix="issue81-direct-owned-") as temporary:
            process = ArtificialProcess(
                Path(temporary),
                "history-layout",
                False,
                {p.page: p for p in chunk.page_images},
            )
            with (
                patch.object(e, "production_command", process),
                patch.object(SendBudget, "before_send", artificial_oauth),
            ):
                e.run_approved(prepared, manifest, result, e.CONTINUATION)
            observation = json.loads((result / "observations.json").read_bytes())
            assert (
                observation["cumulative_trials"],
                observation["cumulative_reservations"],
            ) == (3, 5)
            report = json.loads((result / "sample-01/result.json").read_bytes())
            assert (
                report["status"] == "baseline-verified"
                and report["remaining_gaps"] == 1
            )
            assert report["readback_exit"] == 0 and not report["whole_sample_clean"]
            assert report["selected_sha256"] == report["baseline_sha256"]
            assert (
                "ARTIFICIAL_EXPECTATIONS_NOT_SENT"
                not in (result / "sends.json").read_text()
            )
        assert not Path(temporary).exists()
        e.classify_trial(
            e.CONTINUATION,
            3,
            "continue",
            "Artificial counter check only, not semantic success; input and retained baseline/readback verified before intentional interruption",
        )
    else:
        # Fresh Python process rereads the SAME completed cumulative record.
        state = e.Continuation(e.CONTINUATION, CONFIG, threading.Event())
        with workspace_lock(e.CONTINUATION):
            state.reconcile()
            assert (state.trials, state.sends) == (1, 1)  # plus old 2/4 = 3/5
            state.start(
                root / "interrupted-trial-04",
                prepared,
                manifest,
                json.loads((prepared / "proposal.json").read_bytes()),
            )
            state.reserve({"artificial_reservation": "interrupted before delivery"})
        with patch.object(e, "production_command", forbidden_runtime):
            try:
                e.run_approved(
                    prepared, manifest, root / "must-not-reset", e.CONTINUATION
                )
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("Incomplete trial was refunded")
            try:
                e.run_approved(
                    prepared, manifest, root / "must-stay-stopped", e.CONTINUATION
                )
            except ValueError as error:
                assert "Safety stop" in str(error)
            else:
                raise AssertionError("Safety stop was cleared")
        assert (e.CONTINUATION / "start-04.json").read_bytes() == (
            e.CONTINUATION / "trial-04/start.json"
        ).read_bytes()
        assert (e.CONTINUATION / "trial-04/send-01.json").exists()
        assert (e.CONTINUATION / "safety-stop.json").exists()
        assert not (root / "must-not-reset").exists()
        assert not (root / "must-stay-stopped").exists()
    assert inventory(source) == before
    e.verify_manifest(result, sha((result / "hashes.json").read_bytes()))
    if phase == "interrupt":
        finish(
            root,
            {
                "real_ai_sends": 0,
                "artificial_completed_cumulative": [3, 5],
                "artificial_interrupted_cumulative": [4, 6],
                "safety_stop_retained": True,
                "input_unchanged": True,
            },
        )
        e.verify_manifest(root, sha((root / "hashes.json").read_bytes()))
print(phase, "sandbox checks passed; real AI 0")
```

以下は**別々のPython invocation**。completeのexit0と保存証拠を確認してからinterruptへ進む。途中失敗は保持し、そのsandboxで再実行／再初期化しない。

```sh
PYTHONPATH=.pi/skills/verify-project/scripts .venv/bin/python "$DIRECT/sandbox.py" complete > "$DIRECT/complete.log" 2>&1
printf 'complete exit=%s\n' "$?" > "$DIRECT/complete.exit"
# exit0とtrial-03の公開保存／別process英語readbackを確認してから次を実行
PYTHONPATH=.pi/skills/verify-project/scripts .venv/bin/python "$DIRECT/sandbox.py" interrupt > "$DIRECT/interrupt.log" 2>&1
printf 'interrupt exit=%s\n' "$?" > "$DIRECT/interrupt.exit"
```

期待は人工origin2/4→完了3/5、次Pythonで同じ累計再読→開始／予約中断4/6→追加無送信停止。`trial-04`はcompletion欠落を意図的に残し、返却も修復もしない。公開generateは同期childをfinallyでwaitし、公開保存の英語readbackは別process。所有TemporaryDirectoryの人工credential/controlだけcleanupし、sandbox入力／全結果／安全marker／証拠は保持・再読する。原画像／本文／raw／秘密をstdoutやGitへ出さない。candidate/exclusions-only/page-local gap/legacy/retryは下記回帰で別確認する。人工OAuth／faux成立は実認証・意味品質の証明ではなく、未実行の手順を成功扱いにしない。

### focused確認と限界

```sh
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_evaluation.py::test_pdf_source_evaluation_review_exclusions_match_preparation_and_candidate -q
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_trial.py -k 'exclusions_only or page_local_gap' -q
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_evaluation.py::test_pdf_source_evaluation_prepare_no_auth_and_public_faux_connection -q
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_evaluation.py .pi/skills/verify-project/scripts/test_pdf_source_trial.py .pi/skills/verify-project/scripts/test_pdf_source_feasibility.py -q
.venv/bin/pytest tests/test_pdf_correction.py tests/test_pdf_source.py -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ruff check .pi/skills/verify-project/scripts/{pdf_source_evaluation,pdf_source_trial,test_pdf_source_evaluation,test_pdf_source_trial}.py
.venv/bin/ruff format --check .pi/skills/verify-project/scripts/{pdf_source_evaluation,pdf_source_trial,test_pdf_source_evaluation,test_pdf_source_trial}.py
.venv/bin/ty check
.venv/bin/ty check .pi/skills/verify-project/scripts/{pdf_source_evaluation,pdf_source_trial,test_pdf_source_evaluation,test_pdf_source_trial}.py
```

専用回帰では旧2/4→残10/50、独立12trial/54送信上限、0送信開始、全工程/coverage/transport retry、再invocation・予約中断、記録欠落/保存不能/並行/取消、安全停止保持と未知/品質/通信の区別、新bindingと保護対象変更拒否を確認する。既存の偽quote/別page/正常metadata/局所不読/exclusions-only/候補分離/legacy request/OAuth限定/storage/別process英語readback回帰は維持する。fauxや人工OAuthを実認証・意味品質の証明にしない。

Rootは現差分の正式check・関係公開consumer・上記手順の別invocationと全画像/固定期待照合、Reviewerは独立確認/Approveを担当する。共有fixture/製品を変更しなければ通常全pipeline/Node/build/全Mapを一律追加しない。未実施・安全停止は評価完了でなく、上限内未達FAILED、環境不足BLOCKED、保証/許可範囲変更REPLAN_REQUIREDを区別する。他7標本/実書籍/全冊/実OCR/日本語品質/通常run-resume/実課金/人の作業時間短縮は未保証。人照合なし/Agent相関誤り・同一標本調整の限界を明記する。本文/回答はprivate内限定、所有temp/人工credential/childだけcleanup/wait後に保持証拠のhashを再読し、旧全結果を削除しない。
