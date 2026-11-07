# PDF補正の白紙ページ明示とページ網羅性エラーの限定再試行

## 目的

白紙ページや本文ページをAIが回答から丸ごと省略した際、既存の検証を維持して拒否し、指示の明確化と有限の訂正生成で回復できるようにする。対象はPDF先頭から数える所有ページの結果網羅性であり、ページ内の全段落や意味品質の保証ではない。

## 要件・決定事項

- 全所有ページについて結果と根拠を返す。参照ページは従来どおりcontext専用とする。
- 白紙を画像で確認した場合は既存の `exclusions` に `text: ""`、白紙と判断した理由、実ページ番号・`ref: null`・見た位置・確認根拠を持つ `evidence` を記録する。空回答や抽出textの欠如だけでは白紙にしない。判断不能なら既存のuncertaintyで不確実性を明示する。
- 補正は差分や要約の返却ではない。対象範囲の見える原語本文・見出し・構造を省略せず返し、見えない内容は創作しない。
- ページ網羅性の検証で不足した場合だけ、不足ページと固定の検証理由をAIへ返す。初回込み最大3回の生成呼出し（訂正は最大2回）とし、各回とも対象chunk全体を返させて全検証を実行する。欠落応答の部分採用や応答同士のmergeはしない。
- 各生成呼出し内の既存transport retryは変更しない。既知のlibrary-heuristic transientに限る最大3 attempt・待機3/6秒を維持する。このため訂正を含む理論上の上限は最大9 underlying attemptであり、「必ず3送信まで」とはしない。coverage訂正のための追加backoffは設けない。この上限は分割しない1回のcorrect_pdf呼出しごとであり、既存のlength分割や後日のresumeを跨ぐ生涯予算ではない。過去の失敗記録から永続予算を復元する状態は追加しない。
- 上限でも欠落が残れば従来どおりchunk全体を失敗にする。schema不正、他のdomain不正、認証・通信・保存・timeout・取消等へ新しい訂正生成を広げない。
- 初回と各訂正回の実request、全attempt、raw回答、観測済みusageを保存する。不明usageを0にしない。
- 既存の成功chunk、旧指示での検証済み保存応答、訂正成功した保存応答を、必要な再検証を経て無送信で再利用する。policy・設定・実入力の変更まで同一とみなさない。
- `CorrectionAnswer`／chunkのschema、公開API、CLI、`CORRECTION_POLICY`、所有5ページ分割、length/input limit時の既存二分処理は変更しない。

## 確認済みの事実・原因

### 再現

調査対象は `work/design-and-build-great-web-apis`。保存済み応答に対して読み取り専用で現行のschema／helper終了／画像対応／domain検証を行い、次の不足と同一エラーを再現した。検証用のstatus差替えはメモリ内だけで行い、保存済みrejected応答は一切変更していない。

| 所有範囲 | 不足ページ | 報告された内容 |
| --- | --- | --- |
| 1–5 | 2 | 白紙 |
| 146–150 | 146、147 | 本文・見出し |
| 236–240 | 239 | 本文・見出し |

エラーは `Owned page result coverage is incomplete; blank pages need explicit evidence/reason`。AIの省略が直接原因であり、今回のprobeでもschema・正常終了・画像対応は通過した。ページ内容の意味判断は依頼時の画像／抽出text確認に基づき、Planningで書籍本文の再査読はしていない。

現状は所有346ページ中331ページ・67chunkが成功、15ページが失敗、未処理は0。対象の3 rejected記録はいずれも1 attemptである。保存sourceに手動revisionはなく、pending candidateもない（調査時点）。

### コード上の原因と再利用の条件

- `src/doc_translate/pdf_correction.py` の `_correction_request` はscope・schema・図所属を示すが、白紙の具体的な返し方と本文の完全返却を明示していない。
- `_validate_answer` はblock／table cell／exclusion／uncertaintyのevidenceで所有ページを被覆することを要求する。これは維持すべき契約であり、現在のエラーは正当な拒否である。
- `correct_pdf` はschema通過後のdomainエラーを記録して直ちにraiseし、不足情報を使う回復経路がない。
- `src/doc_translate/inference.py::generate` は限定transport retryだけを行い、PDF domain validationを担当しない。ここにPDF固有処理を追加しない。
- `_saved_correction` はrequest全体の一致を要求する。一方、`run.py::_pdf_source` の成功chunk再利用とmachine入力の整合には補正policy一致が使われる。policy自体を変更すると既存成功分を無効化し得る。
- `translation.py::_repair_request/_matches_request/_call`、`preparation.py::_call` に、限定domainエラーの訂正生成・実request記録・訂正済み結果再利用の先例がある。方式を参考にし、共通retry基盤の抽出はしない。
- `render.py::_usage` は補正attempt directoryの全記録をhash検査して収集するため、各回のimmutable記録を残す既存方式で失敗分も利用量に残せる。

根拠文書は `docs/design/doc-translate.md` §3・6・7・13–15・17、`docs/reference/artifacts.md` のPDF補正chunk／再開、`CONTRIBUTING.md` の関連検証境界。過去の実書籍実験は今回の成功証拠にしない。

Planningで既存テストを限定実行し、白紙の受理／拒否、保存応答回収、policy/config/reference変更時の非再利用が5件成功。公開CLIの部分PDF resume・adaptive reply回収も2件成功した。これは変更前の確認であり、実装後の検証・承認として流用しない。

## 制約・対象外

- 実Workspace、保存済みattempt、原本、既存成果物は調査専用。今回のImplementationでも実データの再処理は行わず、復旧手順の整備までとする。実送信・追加課金・既存成果物再生成は別途ユーザー確認が必要。
- 既存の稼働processを停止・再起動せず、lockを削除しない。検証で自分が起動した隔離childの回収は所有範囲内で行う。
- 書籍本文・画像をfixtureやIssueへ追加しない。合成PDF/raw/PNG/回答と人工credentialだけを使う。
- ページ内の全段落の網羅性、画像の意味的白紙判定、訳文品質、進捗表示不具合、EPUB処理は対象外。
- 新しい依存、設定項目、retry CLI、新しい永続管理対象、復旧manager、恒久E2E基盤は追加しない。通常のimmutable attemptへの検証理由追加は既存記録の補足とする。
- GLOSSARY／ADRの追加や既存設計文書の一括改訂は不要。用語・主要責務は既存 `GLOSSARY.md` と `docs/design/doc-translate.md` を維持する。

## 実装方針

### 1. 指示の明確化と不足ページの識別

主変更は `src/doc_translate/pdf_correction.py` に閉じる。

- `_correction_request` のcontextに、全所有ページの結果、白紙exclusionの具体的な形、全文／構造返却、画像確認と創作禁止の指示を追加する。既存policyと図所属指示はそのまま保持する。
- 既存のcoverage判定箇所から、固定code／理由と不足ページ一覧を持つ専用の `ValueError` subclassをraiseできるようにする。一般の `ValueError` やエラー文字列の部分一致でretryを判定しない。既存の例外互換とcoverage拒否を維持する。
- evidence検証とcoverage集合収集は維持し、coverage例外の判定だけをdomain検証末尾へ移す。不正table／図所属等と欠落が同居する回答は、他domainエラーで終了し、coverage訂正を行わない。拒否条件は減らさない。
- 不足ページは実際の所有ページと検証済みevidenceから求め、安定した順序で返す。AIの前回答本文・任意の例外文を指示として貼り戻さない。

### 2. coverage専用の訂正生成と保存

- `correct_pdf` のgenerate→domain検証→記録を最大3回に限定する。毎回の生成結果または `InferenceError` を、次の呼出し前・chunk保存前に記録する。
- 訂正requestは初回base requestから組み立て、contextに固定の訂正指示と不足ページ／検証理由を加える。feedbackは未信頼dataとして扱い、同じ所有・参照・projection・画像を使う。前回feedbackを際限なく累積しない。
- 訂正でも `request.policy` は変更しない。これによりrun／sourceのpolicy照合と保存済み成功分を維持する。
- `_record` を最小拡張し、失敗回の検証code／不足ページ／理由と実requestを保存する。既存記録に新fieldがないことを許容する。各recordにはそのgenerateが返した全attemptとfinal/raw usageだけを既存形式で保持し、過去の回答分を累積再保存してusageを二重計上しない。generate内部attemptの逐次永続化や応答喪失時の記録保証を新設しない。
- 専用coverage例外以外は記録後に従来どおり終了する。上限ではcoverage例外を伝播し、CLIでは従来の `answer_invalid`／所有chunk全体のfailedになる。保存失敗時は次の生成へ進まない。取消・送信前通知・lockの既存経路を維持する。
- 全検証に成功した最後の応答だけからchunkを作る。途中の失敗回答や、画像欠落を成功扱いしない。

### 3. request互換性と無送信回復

- 再利用時のrequest照合は、現行base、今回追加する指示だけを含まない厳密な旧base、現行baseに規定の訂正contextを加えた形に限定する。
- 旧形式の受理は今回の指示追加差分だけに限定する。任意のcontext削除・policy無視・schema無視などの緩い正規化はしない。feedbackも規定のcode・所有範囲内の不足ページ・固定理由を持つ形だけを認識する。
- どの形式も `_saved_answer` によるstatus/config/preflight/正常終了/実画像の検査と、`_validate_answer` の全検査を必須とする。rejected記録は再利用しない。
- `correct_pdf` と `_recover_pdf_correction` から使う照合を揃える。訂正成功後、attempt保存済み・chunk保存前に中断した場合も無送信で回収する。
- `load_pdf_correction` の保存request／scope／projection／画像／raw回答／採用chunkの再検証を維持する。
- `run.py` の本番変更は原則不要。公開CLI回帰で成功chunk再利用・失敗分だけの再実行・候補採用保護を確認し、既存仕様を変更しない。

### 4. テストfixture・文書

変更先は `tests/test_pdf_correction.py`、`tests/test_run.py` と、複数回答やrequest履歴・人工usageの観測に必要な既存 `ai/tests/pdf-correction-process.mjs`／`ai/tests/run-process.mjs` の最小部分。既存fixtureの入力互換を維持する。processごとに回答indexがリセットされる点と現行requestログが上書きされる点を踏まえ、訂正requestに応じた回答選択と全送信履歴・人工usageの保持を最小補完する。必要なら既存 `tests/test_resume.py` の保存hash／履歴helperを再利用するが、無関係な共有fixture改修はしない。

文書は以下を関連更新する。

- `docs/reference/artifacts.md` のPDF補正chunk：指示、coverage訂正、記録、旧／訂正request再利用、失敗契約。
- `docs/reference/usage.md` のPDF／resume：最大回数、追加利用量、対象外エラー、成功分再利用、後段処理も進む復旧上の注意。
- `CONTRIBUTING.md` のPDF補正chunk・resume検証境界：今回の合成再現の起動・操作・期待結果・証拠・cleanupを具体化する。
- READMEは既に上記詳細へのリンクがあるため詳細を重複追加しない。

## 完了条件

1. 白紙の明示exclusion付き応答は公開correct/loadで受理され、無言の省略は上限まで拒否される。空textを自動白紙化しない。
2. 本文ページ欠落→不足情報付き訂正→完全応答の順で成功し、採用chunkは完全な最終回答だけに由来する。
3. 欠落が続く場合は生成呼出し3回で終了し、chunk未保存、CLIでは従来のfailed ownership／部分出力となる。
4. 各回のrequest・raw回答・全attempt・観測usageが不変記録として残り、status／QAの補正usageにも失敗分を含めて現れる。
5. 無関係なエラー・transport retry・length縮小・取消・保存失敗の既存契約が変わらない。
6. 旧成功chunk／旧成功保存応答／訂正成功保存応答の再利用と中断回収が無送信で成立し、実入力・設定の変更や不正保存回答は誤再利用しない。
7. 合成WorkspaceのCLI resumeで成功分を再送せず、失敗分の回復と再resume無送信を確認する。旧immutable成果物を保持する。
8. 正式チェックとRoot自身の直接確認、関連文書更新を完了し、実データ未変更・未送信・未再生成を報告する。

## 動作確認方法

### 保証対象・自動テスト・TDD

公開seamは次の2つ。Plan Reviewの承認によりこの限定seamを確認する。

- `correct_pdf(...) -> load_pdf_correction(...)`：白紙／ページ欠落の受理・拒否、訂正request、有限回数、保存と再利用。実LLMの意味判断やrun全体はこのseamだけでは保証しない。
- 公開CLI `run/resume/status`：failed ownership、成功chunk無再送、旧成果物保持、後段への接続とusage表示。実Docling/OCR・実provider・全文品質は保証しない。

軽く明確なlocal failing pathがあるため、**補正APIの訂正成功・上限停止・旧／訂正結果再利用**に限定して既存 `tdd` Skillによるtest-firstを採用する。1ケース→失敗確認→最小実装の縦の順で進める。promptの表現・文書・fixture補完と既存CLI回帰は非TDDとし、必要な確認は省略しない。

最小の追加／既存回帰は次の保証に絞る。全条件の直積や全エラーの新規process試験は作らない。

- 白紙の明示受理、空回答／ページ省略の拒否、空抽出を持つ本文ページを自動除外しないこと。
- 複数ページの一部欠落から、正しい不足番号と固定理由を受け取った訂正で回復すること。訂正でも全owned結果を必要とし、同一画像・参照許可・原文を使うこと。
- 欠落継続で3回停止、途中でschema／別domainエラーになれば追加訂正なし、coverage欠落と不正table等の複合不正も追加送信なし、観測済み全raw回答／usage保持。
- coverage拒否後の取消で次の送信なし、拒否record保存失敗でも次の送信なし。独自signal handlerは追加しない。
- 旧base／新base／訂正成功の無送信再利用、訂正成功attempt保存後のchunk保存失敗からの回収。保存故障は既存OS replace境界で注入する。
- policy/config/reference/projection/画像差異・不正status/terminal/domain/hashを誤再利用しないこと。既存の関連テストを優先する。
- 合成PDFの部分失敗→resumeで失敗所有範囲だけ回復→再resume無送信。旧成功chunkと原本／draft／attemptのbyte/hashを照合し、statusのfailed/not_processedと失敗・訂正分usageを確認する。
- 認証・protocol・timeout・取消・heuristic retryの保証は既存 `tests/test_inference.py` 等を再実行し、PDF固有loopから無関係なretryが追加されない代表ケースだけを補う。

### 再利用する検証資産と直接確認

- `CONTRIBUTING.md` の「正式チェック」「PDF補正chunkの検証境界」「新規runの検証境界」「resume／障害復旧の検証境界」「前提共有・比較・cleanup」を正式な再現入口にする。
- `tests/test_pdf_correction.py` の合成draft/raw/PNGと、上記2つの既存Node fixtureの `no-network.mjs`／公開faux provider／実runHelperを使う。PDF取得が必要なCLI確認は `tests/test_pdf.py` の外部公開converter/page backend fixtureを使う。generate／validator／保存helperのmockは追加しない。
- 専用の検証Skill／Feature Mapは現状なく、既存の具体的手順とhelperで隔離操作が可能。今回の不足は訂正応答と履歴の観測箇所であり、既存fixtureとCONTRIBUTINGの関連更新で補完する。新しい検証Skill／runnerのCreateは現時点では予定しない。
- Rootはpytestの成功だけで済ませず、別の一時Workspaceで公開correct/loadを直接操作し、欠落→訂正成功と上限停止、訂正成功記録からの無送信回収を確認する。既存helperと合成入力を使い、保存request・raw回答・chunk・usageをliteral期待値と照合する。CLI接続は合成PDFの既存公開entryを使用し、必要なDocling外部fixtureだけを用いる。
- Workerは限定seamの失敗→成功、fixture代表consumer単独／module回帰、関連文書の手順を確認して報告する。Rootは現在差分を直接読み、選定した正式チェックと上記直接確認を自ら実行する。更新した操作方法は再実行して成立を確認する。Reviewerはこれらを根拠に不足する保証を独立確認する。
- 秘密なしのcommand/exit、revision/diff、環境・build鮮度、期待／観測、各回のrequestと保存hash、未確認、cleanupを `work/<今回専用の証拠directory>/` に残す。書籍本文・実credentialは保存しない。所有する一時Workspace／人工credential／childだけを回収し、既存Workspace・venv・modelを削除しない。cleanup後に証拠を再読して参照を確認する。

### 正式チェック

既存環境・build済みhelperを確認し、無断install／sync／model取得をしない。

1. 編集ごとのfocused testと変更module、fixtureの全consumerを確認する。Python補正は `test_pdf_source.py`／`test_units.py`／review等にも利用されるため、最終的には関連するfull phaseへ戻す。
2. `CONTRIBUTING.md` の順序に従い、Node fixture変更時は既存Biome、type-aware oxlint、全Vitestを実行する。
3. core-only環境の全pytestとCLI help、PDF extra環境の `ty check`・公開import・`pytest tests/test_pdf.py tests/test_run.py tests/test_review.py tests/test_pdf_candidate_acceptance.py`、全体Ruff check／formatを実行する。PDF extra環境での補正／source／units／inferenceの関連moduleもskipで代替せず確認する。
4. 既存venvをcore/PDFに無断切替しない。必要な別環境・install許可がない場合は不足と再開条件を示す。Node production source・依存・同梱resource・build契約は変更しないため、今回のwheel再導入／配布検査とremote CIは既定の追加対象にしない。実差分がそこへ及ぶなら規約どおり範囲を見直す。

実LLM、実credential、実Docling/OCR、書籍再処理、viewer、他OS、remote CI、exactly-once課金はこの検証の対象外。既存テストの成功をこれらの成功と表示しない。

### Implementationでの最小補完裁量と限界

Implementation Rootは検証手段の現物を再確認し、このPlanの保証対象・対象外・期待結果を維持したまま、起動・入力・観測方法を具体化できる。隔離可能で追加installや利用者・共有環境の変更を伴わない標準機能の試用、小さい接続／観測コード、その再実行に必要なhelperの保存・再利用を許容する。

既存資産で不足する場合は、共通 `create-verification-skill` の実ファイルを読んで最小Create／関連更新を判断する。「予定なし」は禁止ではなく、Skill／helper追加だけで追加承認や再計画を要求しない。ただし必須Skill等が実際に不足すれば独自代替を作らない。実送信禁止、実Workspace保護、秘密保護、所有対象だけのcleanup、無断install禁止を維持する。許可範囲で成立不能なら、試した失敗・未確認手段・不足と再開条件を区別してBLOCKEDとする。仕様・公開契約・設計、正式な保証・期待結果の変更が必要なら既存の確認／REPLAN_REQUIRED経路へ戻り、未成立の方法を成功扱いしたり期待値を緩めたりしない。

## 既存Workspaceの復旧手順（実装後・別途承認して実行）

1. 更新済みコードと検証結果を確認する。稼働writerを停止せず、競合中なら待つ。Workspaceの削除・lock削除・run/attemptの手編集をしない。
2. `doc-translate status work/design-and-build-great-web-apis --json` で現在の保存状態を再確認する。調査時のfailed 15ページ、成功331ページ・67chunk、保存設定、candidate/revisionの有無と異なる場合は現在の状態を優先する。
3. 実送信前に、所有・参照範囲、保存済みprovider/model/thinking、認証、追加利用量と後段再実行を提示し、ユーザーの実行確認を別途得る。実料金額や成功を保証しない。
4. 設定を上書きせず `doc-translate resume work/design-and-build-great-web-apis` を1回実行する。別の `run` や新規Workspaceへの再生成は不要。
5. 調査時と状態が同じなら、補正の新規所有送信は1–5／146–150／236–240の3組、参照はそれぞれ[6]／[145,151]／[235,241]。その各組にcoverage訂正が最大2回追加され得る。欠落した4ページだけを抜き出して既存不完全回答とmergeはしない。成功331ページは再補正せず、検証可能な保存draftを再取得しない。
6. resumeは補正だけで停止しない。source更新に伴う準備・翻訳・出力更新があり、課金は3組の補正だけではない。既存immutable成果物は残すが、run／採用pointer／renderは更新される。手動source revisionが後から追加されていれば既存のcandidate確認・明示採用で停止する契約を守る。
7. 終了後に再度statusを確認し、failed/not_processed、candidate、後段の処理状態、全補正attemptとusage、旧成果物hashを照合する。失敗が残れば記録を保持して報告し、自動でresumeを繰り返さない。再送は重複課金の可能性があり、未保存応答のusageは不明のままとする。

このPlanのImplementationはコード・合成検証・文書・復旧案内までで完了とし、この実Workspace復旧の実行を完了条件にしない。
