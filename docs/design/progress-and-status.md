# PDF／EPUB共通 — 進捗表示・送信通知・statusの基本設計

本書はDesign Discussionで合意した基本設計であり、Approved Planではない。具体的な変更範囲・性能目標・検証方法・作業分割は[正式Plan](../plans/progress-and-status.md)に従う。以下の責務具体化はIssue #49（status）／#50（EPUB＋共通表示）／#51（PDF接続）／#52（review接続）の範囲であり、実provider検証を意味しない。

## 目的と対象

目標範囲はPDF・EPUB両方の`run`／`resume`と明示選択`review`で、実行画面だけから次を把握できるようにする。

- 現在の工程と、ローカル処理中かAI応答待ちか。
- 工程ごとの完了状況、保存済み結果の再利用、今回の新規処理。
- 今回AIに送る対象と送信先。
- 失敗、再試行、取消の発生。

大量の送信通知JSONが通常画面を埋める状態を解消し、別ターミナルの`status`は保存済み進捗の確認に集中させる。PDFだけで設計・検証を完結させず、EPUBの実際の工程・処理単位も扱う。

## 合意した役割分担

| 入口 | 役割 |
| --- | --- |
| `run`／`resume`／`review`の実行画面 | 現在工程、ローカル処理／AI応答待ち、進捗、再利用、再試行・失敗・取消を表示する |
| 通常の`status` | 既存の保存データから保存済み進捗を読み、出力再構成を避けて軽量に表示する |
| 明示的な詳細検査 | `status --verify`は詳細検査＋人向け要約、`--json`と`--verify --json`は詳細検査＋従来の完全JSON |

現在工程を別プロセスへ共有するための新しい状態ファイル、永続heartbeat、PID管理は追加しない。既存の`run.json`にライブ状態を書き込む方式への置換も前提にしない。実行画面の現在状態は実行プロセス内で扱い、通常の`status`からその瞬間の工程やプロセスの生存を断定しない。

## 実行画面

### 表示方式と構成

**Richを表示部分だけに使う。** メニューやキーボード操作を備えた本格的なTUIアプリにはしない。工程・集計・再利用の判断はアプリ側が所有し、表示ライブラリに処理の正しさを委ねない。

通常のターミナルでは、上の工程一覧と下の現在処理の詳細欄を同じ場所で更新する。毎回全体を追記して流し続ける表示にはしない。以下の数値・文言は見え方の例であり、固定の総数や性能実績ではない。

```text
✓ 保存データの検証
✓ PDF本文・レイアウトの抽出     保存済み結果を再利用
⠋ PDF補正                     成功 331/346ページ／失敗 0
○ 翻訳準備                    未着手
○ 翻訳                        未着手
○ Markdown出力                未着手

対象：332–336ページ／参照：331, 337ページ
送信先：openai / MODEL · thinking: high
AI応答待ち… 経過 00:28
```

- 上の一覧は、全体のどこまで終わったかを示す。
- 下の欄は、今の処理対象・送信先・待機状態を示す。別の通知画面は増やさず、工程や対象の変更に合わせて更新する。
- 実行中の工程にはspinner animationを付け、完了するとチェックマークへ変える。重い処理に入る前に工程を表示する。
- ローカル処理中は、その工程と経過時間を示す。AIへの送信前の処理と応答待ちを混同しない。
- 再試行・失敗の理由は上書きで消さず、現在詳細の下に最新3件の履歴を残す。再試行・失敗・取消の短い行はLive上方にも確定出力する。
- 取消受付と処理終了を区別する。すぐに停止できない工程を、停止済みと表示しない。

### 状態と色

| 状態 | 表示 | 色の方針 |
| --- | --- | --- |
| 未着手 | `○`＋未着手 | 控えめなグレー |
| 実行中 | spinner＋工程名 | シアン |
| 正常完了 | `✓`＋結果 | グリーン |
| 再試行・部分完了・要注意 | `!`＋理由 | イエロー |
| 失敗 | `!`＋失敗理由 | レッド |

状態記号・工程名・重要な数値を中心に着色し、説明文まで全面着色しない。色だけで状態を伝えず、記号と文言でも判別可能にする。再利用は警告扱いにせず、AI応答待ちも待っているだけで警告色にしない。処理完了の緑は翻訳品質の承認ではない。

`NO_COLOR`指定時は色を使わない。非TTYでは固定位置更新・spinner animation・画面制御を使わず、工程開始・完了・再試行などの読み返せる行ログにする。経過時間だけの更新を大量に流さず、JSONへ色や画面制御文字を混ぜない。

### 形式と数値の意味

- PDFは保存データ検証、本文・レイアウト抽出、PDF補正、翻訳準備、翻訳、Markdown出力を実処理に沿って表示する。
- EPUBは本文・構造抽出、保存データ検証、翻訳準備、翻訳、Markdown出力を実処理順に表示する。PDF補正の行やPDFページ数は表示しない。
- 総数を正確に把握できる工程だけ完了数／総数を出し、全工程共通の架空の進捗率は作らない。残り時間の推定は必須にしない。
- 成功、再利用、失敗、未処理を区別する。再利用は成功の内訳であり、成功数へ二重加算しない。送信回数・訂正回数を完了数にしない。
- 検証・保存前に成功とせず、PDFの参照専用ページを所有ページの完了数に含めない。
- 翻訳の動的分割でunit／chunk数が変わる場合、初期総数を固定して誤った進捗を表示しない。分割後は総数を更新中とし、採用snapshotのleaf unitで更新する。別jobの分割が残る間は固定総数へ戻さない。
- 並列実行の表示・集計を混線させず、複数のAI応答待ちを単一リクエストの待機と誤表示しない。
- spinnerや経過時間の更新は処理前進・正常動作の証明ではない。総数が分からない検証・抽出は工程と経過時間を示し、完了数を捏造しない。
- 保存結果だけで推論不要のresumeも、検証と再利用を示して終了できるようにする。

## 送信通知と出力の責務

通常画面では、大量の送信通知JSONに代えて、下の詳細欄から今回の送信対象を把握できるようにする。

- PDF補正は実際の対象ページと参照専用ページを分ける。
- 翻訳準備・翻訳は、実際に送るunit／chunkや処理内容を示す。準備の情報収集と文書全体の整理も区別する。
- 文書全体の処理対象と、個々のリクエストの送信対象を混同しない。ページ対応不明時に全ページ一覧で代用しない。
- provider／model／thinking、必要な非秘密の認証情報は確認できるようにする。原本名・hashなどの文書共通情報は毎回繰り返さず、冒頭への集約を基本とする。
- 通信上の再試行と、回答不備への訂正要求を区別する。送信直前の通知だけで、providerへの到達や回答成功を保証しない。
- 機密本文・画像payload・認証token・不要な秘密情報は表示しない。

進捗はstderr、最終結果はstdout。正式Planの合意に従いrun／resume／reviewは人向け既定、明示`--json`で従来の最終JSONを得る。`--events-json`は独立してstderrを従来用途の送信前JSONだけにする。reviewの通常stdoutは対象・確認記録の保存結果・今回の指摘件数・記録先・品質未承認を示し、本文／指摘全文は出さない。Richを理由に他コマンドの出力方式まで変更しない。

既存stderr JSON解析にはrun／resume／reviewの`--events-json`、resume／reviewのstdout JSON解析には`--json`を明示する。従来の通常statusで得た詳細検査結果は`--verify`で確認でき、`status --json`の詳細検査＋完全JSONは維持する。移行方法は[CLIリファレンス](../reference/usage.md#cliの表示とjson出力)を参照する。外部parserの実利用は未確認であり、互換性検証済みとは主張しない。公開契約への変更が必要なら影響を示してユーザーと確認する。

### #50で具体化した責務

共通`ProgressEvent`は任意observerへ同期通知する小さいmetadataで、永続化／IPC／汎用event busは持たない。発行元が既存unit／phaseを投影し、GenerationInput・prompt／context・保存requestへ表示用fieldを加えない。BeforeSendの3引数とflush責務はそのまま、CLIの小さいwrapperだけでunit_ids／phaseを渡す。collectは実unitとsection、organizeは文書全体の収集結果整理（unit_ids空、原文unit全文再送なし）、翻訳は実chunkのunitとする。PDFのcorrected_pages全体への誤fallbackは除く。

view stateはlock＋snapshot、workerはstate-onlyとする。main threadの工程start時の同期描画と表示threadの周期描画は、render_lock下でsnapshotを描画する。`Console(stderr=True)`、Liveのredirect両方False、約4Hz更新、finally終了を明示する。NO_COLORは値ではなく存在で判定し、Textによるmarkup不活性化とcontrol文字escapeを使う。表示失敗は表示層だけの小さい行fallbackとし、送信前JSON書込み失敗を隠して送信しない。raw例外／回答／本文／秘密を履歴へ流さない。

requestは一意なattempt／jobを持ち、Popen失敗・preflight拒否・callback失敗・timeout・取消・例外でも終端通知する。helperへのgenerate書込み完了でのみ待機へ移る。翻訳は既存_adoptの保存後にsnapshot leaf unitを集計し、別jobのsplitが残る限り総数を更新中とする。別のactive-leaf正本・新unit生成・取消方式／retry変更は不要。EPUBは実順序、resumeは再利用前検証を最初に示し、PDF行やページ数は出さない。PDF補正固有工程は#51、明示範囲reviewは#52で接続する。

### #51で具体化したPDF責務

PDFの新規runも最初はvalidateを示し、draftのhash／version／必須check／画像被覆とscopeを既存loadで検証する。再利用可能なら抽出再実行なしを表示し、必要時だけextractを開始する。correction開始後にimmutable chunk／orphan成功応答を既存条件で再検証・回収し、run manifest保存後に成功／再利用内訳を発行する。新しい成功も所有chunk検証・保存とmanifest保存が両方終わった後だけ反映し、参照ページは加算しない。所有分母は固定、補正のlength／input_limit分割を翻訳split集合へ混ぜない。

correction observerは実owned／reference tupleとgenerate lifecycleを投影し、coverage repairは拒否記録保存後、検証済み応答は成功反映前のsavingとして表示する。BeforeSendの引数やprompt／context／保存schemaは変えない。source組立はassemble、採用前の比較／保存はvalidateで表示する。RuntimeErrorの早期returnや候補待ちで停止工程をdoneにせず、後段を未着手に保つ。取消受付と補正終了／後段の部分出力も分ける。document headerの所有意図（全ページなら抽出後確定）・追加参照許可と個別requestの実所有／隣接参照を区別する。EPUBへPDF stage／ページmetadataを出さない。

### #52で具体化したreview責務

reviewは重い`_inputs`・選択範囲検証・準備／review履歴検証の前から開始を表示する。工程順は保存データ・選択範囲の検証→AIによる内容確認→確認結果の保存→終了時の保存データ詳細検査とし、run／resumeの抽出・翻訳工程を表示しない。headerは明示選択、requestは既存selected／blocks／imagesのunit／block／画像ページを投影し、所有／参照として偽装しない。追加unit生成やprompt／context／保存request変更は行わない。

generateのrequest lifecycle・retry・非秘密設定／検証済みauthをscoped observerで再利用する。指摘の有無は処理成功と別で、空指摘でも品質未承認。attemptと受理記録のatomic保存後に保存完了、従来workspace_statusの詳細検査後だけ終了とする。最終件数は今回artifact／hashに一致する返却履歴から取り、集計の永続化やAPI戻り値変更は不要。推論前ローカル処理のsignal境界は維持し、取消表示だけの新engineは作らない。独立stdout／stderr JSON flags、表示state lock・安全escape・Live終了・fallbackは共通とする。

## 通常statusと詳細検査

通常statusは保存済み進捗の確認を目的とし、Markdown・対応表・QAの再構成を避ける。AI通信や成果物の書換えは行わない。

表示する情報は既存保存データで把握できる範囲とし、例えば成功・未処理・失敗、記録の最終更新時刻、記録された成果物の保存先を示す。総数不明や未集計を0で代用しない。ファイル更新時刻を最後の処理成功時刻と同一視しない。

保存記録を読んだことと、artifact／出力の整合性を検証したことを区別する。「保存記録上の成功」を、成果物の正常性・鮮度や現在も実行中であることの証明にしない。検証した範囲と、保存された過去の検査結果であるかを明示する。

未処理・処理中・実際の失敗を混同しない。特に保存chunkの`error: "not_processed"`を、それだけで実際の失敗へ分類しない。ライブ状態を共有しないため、保存データから現在処理中の対象を推測で作らない。

重い整合性検査は必要時に明示実行できる責務として残す。run／resumeの再利用前検証やrenderの安全性は弱めない。実行中の保存更新と競合しても、異なる世代を混ぜて整合済みと断定したり、状態確認のためにwriterを長時間待たせたりしない。正式Planに従い、通常statusだけ公開`read_saved_status`へ分離する。run／pointer・必要immutable JSONのschema／型／包含path（symlink含む）／byte hashを検査するが、読まない成果物は未検証と明示する。run／pointerを前後比較し変化時は最大1回再読、なお変化する競合はexit1、必要記録破損はexit2、読取り成功はpartialでもexit0とする。前後一致もtransaction整合保証ではない。

訳文のsource／preparation参照と現在pointer、runのtranslation／policy／user_termsとの差を表示し、保存版へ帰属させる。preparation hash差だけで訳文無効とは断定しない。候補は採用済みと混ぜず詳細へ案内する。`status --verify`は既存詳細検査の人向け表示、`--json`および`--verify --json`は従来の詳細完全JSONとする。詳細検査中のpointer変化も不成立にし、内部`workspace_status`の完全検証は維持する。共有render分類も`not_processed`をfailedから除く。保存schemaとsnapshotの既存failed契約は変えない。

## コード上の根拠と計画時点の記録

以下はソース上の責務と計画時点の問題の区別であり、Issue #53の統合実動・性能検証の結果ではない。Issue #49で通常CLI statusのみ`read_saved_status`へ分離し、下記の詳細`workspace_status`は維持している。

- [`cli.py`](../../src/doc_translate/cli.py)の`_announce()`は送信直前にstderrへJSONを出す。計画時点であったprepare／translationのPDF送信対象の`run["corrected_pages"]`fallbackは#50で除き、対応不明を全ページで代用しない。
- [`run.py`](../../src/doc_translate/run.py)の`run_document()`／`resume_document()`／`_continue_run()`が形式固有の取得と共通の準備・翻訳・renderをつなぐ。EPUBにPDF補正はない。
- [`render.py`](../../src/doc_translate/render.py)の`workspace_status()`は`_build()`を呼ぶ。`_inputs()`によるunit生成・検証、`_Markdown`による出力再構成、`_usage()`によるattempt走査を通り、保存出力のhash検査も行う。
- 計画時点では同じ`_build()`の失敗分類は`chunk.error`の有無でunitを集めていた。既存の[`translation.py`](../../src/doc_translate/translation.py)は未処理chunkへ`"not_processed"`を入れるため、未処理が失敗に含まれる経路があった。Issue #49で共有分類を修正し、公開render／詳細statusの人工snapshot回帰を置いている。実Workspaceの検証を意味しない。
- 翻訳には並列処理と動的な分割がある。準備には情報収集と全体整理がある。既存の送信直前callbackだけでは工程全体や再利用は通知されない。

元の依頼では、実PDF Workspaceのstatusが30秒で応答せず、unit再生成・モデル検証・Markdown関連付けのスタックを観測したと報告されている。これは利用者からの報告で、人工入力のbaselineではない。計画時のPDF／EPUB人工100／3,000 unitの実測値と再作成条件は正式Planに記録し、変更後の比較・統合受入れとは区別する。

既存契約・検証手順は[CLIリファレンス](../reference/usage.md)、[artifact契約](../reference/artifacts.md)、[開発ガイド](../../CONTRIBUTING.md)を参照する。本書は正式Planに沿って実装した責務を反映するが、全完了条件の検証済み宣言ではない。

## 計画時点の調査項目と統合検証

以下はPlanningで具体化した調査項目であり、今後仕様を決める未決事項ではない。採用した範囲・性能目標・方法は正式Planに従い、実装後の成立は別に検証する。

- PDF・EPUB両方について、重い処理前の表示位置、工程通知の接続、再利用・成功・失敗・取消・動的分割の集計根拠を確認する。
- 通常statusに必要な読取り範囲と詳細検査の責務、世代競合・破損・古い情報の扱い、既存JSON利用への互換性と移行を具体化する。
- 許可範囲の人工Workspace等でstatusの小規模・大規模baselineを取り、両形式の規模依存性を調べ、測定可能な応答性能目標を提案する。未測定値をbaselineにしない。
- Richの適合性、狭い端末・明暗の背景・非TTY／`NO_COLOR`、重いローカル処理中の更新可否、並列通知と異常終了時の表示の扱いを確認する。単なるspinner更新を処理前進の検証としない。
- 新規run、部分完了resume、再利用のみのresume、訂正／通信retry、取消・異常終了、送信対象、stdout／stderr、保存・整合性・出力の回帰を保証する範囲と方法を選ぶ。
- 既存のテスト・検証手順を優先し、必要な文書更新、検証手段の不足と最小補完、TDD採否を対象別に定める。実装後の確認をPlanningで検証済みとしない。

Issue #53の統合確認は[検証Skill](../../.pi/skills/verify-project/SKILL.md)の既存status／EPUB／PDF／review手順と、[開発ガイド](../../CONTRIBUTING.md#進捗表示statusの統合受入れissue-53)の正式チェック・配布検査を使う。本書だけで未実施の実動・性能・配布確認を成功扱いにしない。

合意した利用体験・責務・制約を変える必要が出たら、根拠と影響を示してユーザーと確認する。通常の実装詳細をすべて再承認させることも、未確認の仕様変更を実装詳細として確定することもしない。

## 対象外と安全上の制約

- 翻訳品質の改善、意味的完全性の保証、全文の人間査読は対象外。
- PDF／EPUB別々の進捗基盤、汎用監視サービス、別プロセスへのライブ状態共有は追加しない。
- Planningではproduction codeを実装せず、実Workspace・attempt・既存成果物を書き換えたり再処理したりしない。実行中のrun／resumeを停止・再起動しない。
- 実provider通信、追加課金を伴う検証・再処理は別途承認が必要。書籍本文・画像をfixtureとしてリポジトリへ追加しない。
- 追加依存は表示用のRichを基本とし、既存構造を活用する。無断installや共有環境の変更は行わない。
- この設計書の保存はPlan承認、チケット公開、実装開始の許可ではない。
