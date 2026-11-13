# 実障害起点のPDF原文検査・回復：mainからの最小実証

## 目的

実書籍で発生した抽出・補正・接続の状態を起点に、原画像と補正済み原文を照合し、必要な回復を自動採用して、人が確認する箇所を実際に減らせるかを小さく実証する。警告件数の集約や合成テストの検出成功を、実書籍での実用性へ読み替えない。

今回は通常製品への統合ではなく、隔離Workspace限定の実験経路と評価まで。本全体への展開、通常run/resumeへの接続、merge/releaseは結果を受けた別判断とする。Planningでは実装・検証資産作成・実AI送信を行わず、Approved Planとユーザー確認済みWork Itemsを保存・公開して停止する。

## 正式入力・実装の起点

- 対象repository: `minorunakamura/doc-translate`。
- 対象cwd: `/Users/minoru/Documents/mywork/cli-dev/doc-translate`。
- 正式保存先: `docs/plans/pdf-source-quality-feasibility.md`。
- 実装の起点: 調査時のmain `9bb318b668febaf7eccc53023729bde7dc48ce6e`。Implementation開始時にmainと環境を再確認し、移動していれば影響を確認する。旧品質ブランチをmainへmergeしてから始めない。
- 旧ブランチ `fix/65-render-reference-qa`、調査時HEAD `b5aaddb`、旧Plan群・全評価・実書籍Workspaceを保持する。調査時treeはcleanだが、開始時の未コミット変更は独断で破棄・stash・commitしない。
- 新しいmain起点の作業場所へ本Planを同一内容で持ち込み、参照とhashを確認する。旧ブランチを消す・resetする必要はない。Planning中はcheckoutを切り替えない。
- 本Planは独立した正式入力であり、旧4 Planの全契約を継承する差分文書ではない。旧Plan・セッションは失敗分析の資料として扱う。mainの既存製品契約・安全性は維持する。

失敗分析の参照:
- `docs/plans/pdf-source-quality-recovery.md`、後続のfollow-up / h05-replan / failed-evaluation-replan。
- 初期セッション `01a1c4ac-4ed7-7128-ad65-a36d342f1f22`、最終セッション `01a1ce9d-7885-75ef-b25d-ccaa02cc9498`。会話全文をImplementationの必須入力にはしない。
- `work/issue73-prompt3-root-_mi283_3/evaluation-report.md`、過去試行は同reportの参照先。
- 既存Issue https://github.com/minorunakamura/doc-translate/issues/73 。調査時CLOSED/COMPLETEDだが、状態を実用性達成の根拠にしない。再open・本文改変は本Planで要求しない。

## 要件・決定事項

ユーザー確認済み:
1. mainから出直し、旧実装の維持を前提にしない。必要な小部分だけ適合性を確認して再利用する。
2. 今回は最小経路の実証まで。検出補助だけへ目的を縮小せず、回復・自動採用・英語出力への反映を測る。
3. 自作の回帰資料に加え、実書籍の代表範囲を隔離コピーで実AI評価する。元Workspaceは変更しない。具体的な送信対象・model・上限・認証操作を示した実行前の別承認が必要。
4. 段落追加・削除、順序・表・図crop・接続、数値・codeの原本復元を、一律に自動採用不可としない。原画像の根拠・独立再検査・変更範囲の確認が必要。手動修正や根拠不明の変更は別であり、上書きしない。
5. 正常・履歴だけの代表例で不要な確認を求めず、読める原本から復元可能と事前に定めた代表例は、自動採用と英語出力まで到達して初めて実証成功とする。
6. 履歴の保存と現行内容への判定を分離する。過去の抽出異常を消したり、過去自体を「修復済み」に書き換えたりしない。
7. 通常CLIには組み込まない実験用経路を採用する。通常resumeの採用維持や製品版の品質gateを実証したとは呼ばない。旧Planは編集せず、この新Planへまとめる。

### 事実と失敗の構造

対象 `work/design-and-build-great-web-apis` の保存sourceは
`22325d32f4ed4babf3da97b7bfd94ea983b6629e2e805798b2e690c60575f471`。
RootはJSON byteのSHA一致、2,660block・578原文警告を再集計した。

| 保存状態 | 件数 | 旧方式の問題・今回の検証対象 |
|---|---:|---|
| Unsafe/invalid charspan、targetなし | 384、76ページ | 検査requestのwarningsへ渡らないが停止候補になる。履歴と現行内容を分ける |
| picture対象の警告 | 148 | 図内文字の保持を本文転記義務と混同しない |
| 上記のうち空本文pictureだけが対象 | 59 | 非空本文引用を要求する警告解消条件では、正しい図でも判定不能 |
| 接続段階の警告 | 43 | 逆方向の記録と実際の接続箇所を区別。本文/code・chunk境界を検証 |
| その他の内容不確実性 | 3 | 判読困難等を原画像で調べ、安易に解消しない |

件数は誤りの確定数ではない。警告のない箇所も正常とは限らない。

旧ブランチの一次情報:
- `source_checks.py::_jobs/_validate_answer`: targetなしwarningの非送信、警告解消の非空block本文引用要件。
- `source_quality.py::_inspect_projection/_source_gate`: 対応するwarning判定がなければ履歴候補が残り、unresolved/indeterminateがblockingへ入る。
- `source_recovery.py::_diff_error/recover_source_quality`: block数・kind/evidence順・metadata不変と保護内容不変により、欠落・表・図等の復元を許可しない。回復選定はfindings中心で、全問題表現を扱えていない。
- 最終既知24ケースでは重大変異13/13検出、正常誤停止0/8まで改善したが、改善候補13/15に対し実採用1/15。警告分類の不一致も残った。調整済み集合であり、実書籍での有効性証明ではない。

安全制限を守るテストの成功と、目的を達成する設計かどうかは別だった。今回は主要状態との対応・正常対照・採用可能性を先に確認し、評価資料を実装に都合よく変更しない。

### mainの再利用境界とPlanningのProbe

mainには旧source_checks/source_quality/source_recoveryはない。一方、以下は利用できる:
- `pdf.py::load_pdf_draft`、原本・raw・画像の認可/hash検証。
- `pdf_correction.py::correct_pdf/load_pdf_correction`: 最大5所有ページと許可参照、画像付き生成、domain/coverage検証、有限retry、保存成功の厳密reuse。correct_pdfはchunk保存だけでsourceを採用しない。
- `pdf_source.py::assemble_pdf_source`: 検証済みchunkから接続原文を返し、採用・書込みはしない。
- `inference.py::generate`: review段階の画像入力と構造化応答、既存認証/runtime、before_send・取消・attempt情報。
- `snapshot.py::save_source_snapshot/load_source_snapshot`、`units.py::generate_translation_units`、`render.py::render_document`: 採用参照のatomic保存、再読、原文出力。
- `revise.py`のCAS・構造検証は意味品質の保証ではない。実験scriptがreviseを呼んだだけで安全な自動採用としない。

Rootのread-only ProbeはmainのsrcをTemporaryDirectoryからimportし、実書籍sourceを公開load、36–40ページの既存chunkを公開assembleで再検証した。49block・20警告、exit0、保存/生成なし、一時物cleanup済み。先行の全70chunkを個別loadするProbeは90秒timeoutで未成立。全件互換性・速度・新方式の成立を確認したことにはしない。

## 制約・対象外

- 元の実書籍Workspace、旧成果物/immutable/attempt/評価、既存process/lock/認証/venv/modelを変更・削除しない。旧trialを再起動・resetしない。
- productionのrun/resume、CLI options、品質DB・永続projection・自動回復manager、旧品質形式の移植/互換、汎用評価runner、専用server/UIは作らない。
- 実Docling/OCRの再実行・精度改善、全346ページ評価、翻訳生成・日本語品質、EPUB拡張、任意資料への精度保証は対象外。
- 新依存・無断install/sync/build/model取得・別provider fallbackは行わない。既存runtime不足等は許可を確認する。
- 合格のための正解/閾値変更、失敗例の除外、正常箇所の改変、未検査を問題なしにする処理は禁止。
- 当面production sourceを変更しない。公開APIで表現できない製品契約変更が必要なら、実験helperによる内部状態改ざんで迂回せずREPLAN_REQUIREDとする。
- `b24e679`等の独立修正もまとめて移植しない。既存rendererの不具合が実証を阻害した場合は、原因と別修正の必要性を報告し、検査を無効にして通さない。

## 実装方針

### 1. 実障害を分類し、評価前に標本と期待結果を固定する

実書籍全体の保存メタデータは読取専用で集計する。元warning位置→draft/ref/原画像→現行block/fragment/接続の対応を調べ、履歴、現行欠陥、正常、判定不能を画像と現行原文から判別する。warning理由文だけで分類を確定しない。

標本は「targetなしcharspan」「空本文を含む図」「本文/codeの接続」「その他の判読不確実性」と、警告なしの対照を含める。件数の大きい種類を避けず、複雑な例を含める。ページ番号候補は39（空図）、46/215等（charspan多数）、9–11/61–62等（接続）、81/83/190（その他）だが、これは選定候補であって画像内容の正解ラベルではない。

最小の閉じた既存chunk範囲を選び、原則4つ以内の小範囲、各最大5所有ページと許可された隣接contextを初期規模とする。必要な接続がこの規模で閉じなければ、黙って切断・省略しない。代表性と送信規模を見直し、具体的範囲は送信承認で確認する。全冊への自動拡張はしない。

原本画像・保存source・旧chunk、検査に渡す投影、期待する現在状態、回復可能性、確認が残る理由、選定理由を対応させる。block IDの振り直しを内容変更とせず、同じIDを恒久identityにも使わない。部分再組立で実障害が消えたり境界が変わったりする場合は、元と同等のケースと数えない。

自作回帰例は実状態の構造を再現する。少なくとも:
- 不正charspanから対象なし履歴が残るが、画像由来の現行本文は正常な対照と、現行本文に本当の欠落がある対照。
- 空本文の正常図と、図crop/図内文字の欠落・重複がある対照。
- 本文/codeの正常な接続と、実際に欠落/誤接続する対照。
- 段落追加/削除・表・数値/codeの原本復元と、判読不能の例。

既存のconverter/page backend人工fixture→公開correct_pdf→assembleで発生する履歴・構造を使う。完成済みの都合よい検査入力だけを捏造しない。すべてを別PDFにする必要はなく、少数の対照で保証する。実書籍本文/画像をGit管理fixtureにコピーしない。

Rootが評価modelとは別に原画像と全標本の期待内容を直接照合し、正解/可否を実行前に固定する。事前のAgent照合は実運用の人手負担とは分けて計上する。人による正解確認は必須追加しないが、人照合なし・Agentの見落とし/相関誤りという限界を残す。判断できない原画像は無理に正解を置かない。実書籍に特定の実欠陥が見つからない場合、合成例の成功でその種類の実書籍回復成功を代用しない。

### 2. 隔離限定の最小実験経路

`.pi/skills/verify-project/scripts/pdf_source_feasibility.py`相当の一つの限定helperを中心に、必要な小さい補助だけを置く。旧3品質module・campaign管理一式をコピーしない。データ/結果/生成attemptは既存ignore済みworkの今回専用directoryに置き、repositoryに新しいworkflow stateや管理台帳を作らない。

入口は、固定した標本・mainの版・入力hash・実行設定を受け取る実験操作。元Workspaceは読取りに限定し、出力先との同一/包含、symlink経由の逸脱、既存結果への上書きを拒否する。既存の利用者Workspaceを実験先にしない。通常run.jsonや採用proofを手書きで捏造しない。

経路:
1. 元artifactを検証し、必要な参照を持つ独立コピーを作る。入力baseline、候補、採用出力を区別し、共有書込み/hardlinkを使わない。実験先はsource-onlyとし、通常run/resumeのWorkspaceとして配布しない。
2. 原画像と現在の接続済み原文・構造を、公開generateのreview段階で別requestとして照合する。画像・文字・構造・接続の検査範囲と未検査/判定不能を明示する。補正AIの自己申告を照合結果にしない。
3. 現在の具体的な問題と所有範囲を特定できる場合だけ、公開correct_pdfで当該chunkを再生成し、既存domain/coverage検証を通す。回復理由は未信頼data。回復requestは固定したpolicy等で初回requestと区別し、元成功のreuseを回復実行と数えない。候補を通常入力コピーへ混入させない。
4. 変更chunkと接続に必要な範囲を公開assembleで再組立し、原画像・候補内容を生成とは別requestで独立再検査する。対象だけでなく正常部分・境界・図表・codeを含む、変更の影響範囲全体を比較する。
5. 実験用の採否判定をコードで行う。要件を満たす候補だけ採用出力へ公開save_source_snapshotで保存し、別processのload・units・renderで、採用hashと英語Markdown/図assetsが候補の実内容を使ったことを確認する。候補生成用Workspaceのpointerがあるだけでは採用成功にしない。
6. 不採用/エラー/取消ならbaselineを保持し、具体的に残る確認箇所と理由を出す。人が候補を選択/編集した場合は手動採用として別計上し、自動採用率へ入れない。

採用出力は今回の実験の結果である。通常resumeでの保持、手動revisionとの統合、後段翻訳の増分更新・中断replayは未実証と明記する。実験を成功させるためにmanual reviseやmanifest編集で自動判定を飛ばさない。

### 3. 履歴と現在の判断、証拠と採否

歴史的記録は不変のまま参照する。過去のcharspan異常そのものをresolvedにする必要はない。一方、歴史と判断しただけで現行内容を通過させない。位置/ページ等から影響範囲を特定し、その範囲の原画像↔現在内容の独立照合に具体的な根拠があり、現在の重大問題・未検査・判定不能が残らない場合、歴史記録だけを確認要求へ昇格させない。

範囲対応が不明、画像不読、跨ページの投影が不完全な場合は、その範囲を確認対象に残す。全ページへ送信した、findingsが空、文字数が同じというだけでは検査成功にしない。

証拠は対象に合わせる:
- 既存本文/code/cell: 対応する現行の引用と原画像のページ/位置/観測。
- 欠落/追加・除外: 引用のない内容を創作せず、原画像の該当範囲と現行側に存在しない事実、前後/所属を示す。
- 図: 原PNG、実crop座標と保持範囲、構成要素・caption/本文との重複を確認する。空picture.textに非空本文引用を要求しない。
- 接続: 両側の許可画像と接続後全文、脚注/codeの所属を確認する。
範囲外画像/target、偽quote、根拠不足、欠落した検査範囲、不正構造は拒否する。検査応答の自動補修や部分救済で通さない。

自動採用には、構造検証の成功、指摘内容の原本に沿った復元、独立再検査の成立、影響範囲内の説明できる変更、範囲外/無関係な正常内容の保持が必要。旧方式のblock数一致・非空既存quoteだけを唯一の変更許可方法にしない。段落・表・図等の構造変化は、原画像上の位置/所属と前後内容から確認する。対応を確定できない変更は採用しない。

数値/codeも原本復元対象だが、「原本と違っても保存値を守る」から「原本に対して正常な内容を守る」への実験上の変更である。意味品質を機械validatorだけで保証できるとはしない。全変更箇所は採点時にRootが原本・正解へ照合するが、この事後採点を自動の採否入力に使わない。手動revisionがある実入力は実験の自動上書き対象外とし、除外を隠さない。

### 4. 有限の評価と実送信承認

最初の実評価は固定設定で各標本1trial、回復最大1round。検査→回復→再検査を繰り返す独自repair loopは作らない。既存correct_pdfのcoverage最大3生成、generateの最大3transport attemptは維持する。

実行前に、資料と対象原画像/本文/所有・参照範囲/hash、期待結果、script/prompt/schema/config、provider/model/thinking、既存認証の非秘密種別と通常更新を含む操作範囲、各工程と全retry横断の総送信cap、maxTokens/timeout、参考利用量・価格と不明、保存先/cleanupを提示して承認を得る。旧campaignのmodel・cap・許可は転用しない。preflightが認証へ触れる場合、その操作も事前に許可を得る。

送信は同じbefore_sendで全工程・全case・retry横断のscope/config/capを照合し計数する。画像byteは送信を組み立てる側で検証する。通常補正のうち実証に必要な限定再補正以外、prepare/translation、範囲外データは送らない。before_sendより前にpreflightがあるため、no-send確認は実runtime/認証へ接続しない既存faux等で行う。

取消・認証・保存異常・上限で新規送信を止め、消費と全結果を保存する。途中結果を削除/上書きして再開せず、追加trialは既存消費・失敗を含めて追加承認する。予約を課金確定と呼ばず、usage不明を0にしない。通常OAuth自動更新を手動の認証変更と混同しない。

### 5. 成功条件と確認負担の測定

評価前に固定する表には、各標本の履歴、現在の正解、正常/回復可能/判定不能、期待する確認要否・採否を持たせる。modelへ正解や期待verdictを渡さない。

実証成功には、合成例と実書籍の結果を別々に示したうえで、次を満たすこと:
- 事前に正常・履歴だけと確定した全代表例で、不要な確認要求0、不要な自動変更0。
- 選定された既知の重大欠陥で、見逃し0。未検査/判定不能を検出成功に含めない。
- 事前に復元可能と定めた代表例が、候補生成だけでなく自動採用→再読→英語出力まで到達する。少なくとも実書籍の復元可能な実欠陥1例でこれを示す。該当例を確認できない場合は、その実証は未達であり合成例だけで代用しない。
- 採用後の新しい重大誤り0、正常箇所の変更0。未採用候補が採用出力へ混入しない。
- 事前の判定不能例は例外として残り、根拠のない通過/創作をしない。
- 主要種類の未対応を隠さず、全体の成功率だけで覆い隠さない。

人の確認対象は、warning件数・カード数と別に「実際に画像/原文の照合または採否判断が必要な箇所、重複しないページ数、必要な判断数」で数える。同じ画面にまとめただけを削減にしない。旧全冊577等を標本の改善率の分母にしない。固定標本の処理前/後の確認対象を同じ規則で比較し、分類ごとの分母・実施/未実施/検出/拒否/候補/採用/残件、送信数・時間・usageを示す。実測していない人の作業時間短縮は主張しない。

条件未達なら失敗/未実証とし、どの種類で成立しないか、モデルの失敗・判定契約・生成・採用制限・資料不足を分けて報告する。無制限のprompt調整・campaign反復をしない。結果が否定的でも報告成果は残せるが、実証成功や製品完成へ格上げせず、本格展開を開始しない。

## 文書・保存先

- 本Planは承認後、上記正式パスへ保存。旧Planは変更しない。
- Implementationで `docs/design/pdf-source-quality-feasibility.md` に実験の契約・起動入口・評価表の読み方と限界を簡潔に置く。今回の結果そのものは非公開workへ分離する。
- `CONTRIBUTING.md` に実験手順への短い導線とチェック選定を関連更新する。READMEの既存開発導線を使い、製品機能として宣伝しない。基本設計/usage/artifacts/GLOSSARYへ未提供機能を書き込まない。
- mainの `.pi/skills/verify-project/SKILL.md` と `references/feature-map.md` に今回の実証経路だけを追加。必要なhelperと合成fixtureは同Skillのscriptsまたは既存tests/fixturesの適切な場所へ最小配置する。
- 生の実書籍画像/本文、AI回答に含まれる書籍内容は公開Issue・Git・通常stdoutへ出さない。承認された今回の非公開作業領域だけに、必要な範囲と検証可能な参照を保存する。ignoreだけを機密保護とせず、アクセス範囲/権限を確認する。公開報告は集計・方式・限界・非機密の参照に限定する。

## 完了条件

1. 実障害分類・標本選定理由・原画像との事前照合・期待結果・正常対照が揃い、合成資料の構造が実状態に対応する。
2. main起点の最小経路が、元データ不変・scope/保存/取消を守り、検査→回復→再検査→自動採否→採用保存/再読/英語出力まで実操作できる。
3. 個別送信承認の範囲内で実書籍を含む評価を行い、全結果/全試行・確認負担・成功/失敗を上記条件で報告する。承認不足・環境不足・未実施は完了にしない。
4. 実証成功か未達かを明示し、全冊の実用性や通常run/resumeの完成と混同しない。未達の場合は本格展開へ進めない。
5. 関係チェック・検証手順の再実行・Root直接確認・必要なReview・証拠とcleanupを確認する。元Workspace/旧ブランチ/旧結果の保持を報告する。

## 動作確認方法

### 自動テスト・TDD

今回のFeature/実現性実証は非TDDを基本とする。新方式の有効性は未知であり、旧方式の拒否をmainで再現するRedや、全実AI経路のtest-firstを要求しない。非TDDでも小さい契約テスト/self-checkは必須。

保証対象は、範囲/根拠の検証、履歴と現在判断の分離、採否・保存、送信境界。既存 `tests/test_pdf.py/test_pdf_correction.py/test_pdf_source.py/test_units.py/test_render.py` と実Node/faux/no-networkを優先する。
- targetなし履歴＋正常現在内容は履歴を保持して確認不要。実欠落を併存させた対照は検出/停止対象。
- 空図は画像根拠を許し、別blockのquoteや範囲外画像は拒否。画像が読めなければ通過しない。
- 段落追加・図crop・接続・数値/codeの正しい候補を採用可能にし、範囲外/無関係変更・独立検査失敗の対照は不採用にする。
- 原本hash破損、元Workspaceを出力先にする入力、上書き/逸脱、予算/取消/保存失敗を拒否し、採用出力/旧byteを守る。

有限の代表ケースに限り、全直積・大規模E2E/新CI基盤を作らない。fakeの意味判断成功は本物の認識・安全性・確認負担削減の証明にしない。実験の評価用正解と生成/判定コードを接続しない。

### 実入口の直接確認

Workerは公開APIを使う実験入口を、新しい隔離データで一通り実行し、入力/送信/保存/採否/出力とcleanupを記録する。Rootは別invocationの人工入力で直接操作し、candidateと採用sourceの区別、hash、英語Markdownと図assets、無送信再読、不採用時baseline不変を確認する。

実AIは承認された担当が一度実行し、Rootは全原画像/固定正解・回答・候補・採用出力・未変更箇所を直接照合する。担当交代を理由に追加送信しない。構造assertやテスト成功だけで意味判定を受け入れない。必要な画像/対照表示には既存browser等を使い、外部resourceを遮断する。専用HTMLアプリの新設はしない。

英語出力はsource-only renderの既存仕様を使い、翻訳未実施によるpartialと、原文の欠落/不正を区別する。単なるexit0や生成ファイルの存在を成功にしない。

### 検証資産・不足・補完裁量

mainのverify-project/Feature Mapの「保存記録と詳細検査」「PDF run/resumeの工程・所有ページ進捗」のうち、保存-only・公開取得/補正・scope/環境/cleanupの該当手順を再利用する。全Map・性能測定・通常翻訳フローは今回の必須ではない。

mainには今回の現行内容判定・構造回復の採否・実書籍標本評価の手順がない。旧ブランチの品質helperは方式と依存が異なるため、そのまま採用しない。共通 `create-verification-skill`（確認時 `/Users/minoru/Documents/mywork/pi-dev/pi-skills/skills/create-verification-skill/SKILL.md`、再開時に公開配置を確認）で既存verify-projectへ最小関連更新を行う。初期対象は「固定標本→offline成立→送信承認前停止→承認内評価→採用出力/残る確認箇所→証拠/cleanup」。Planningでは資産生成も実アプリ成立確認も行わない。

Implementation Rootは現在の手段を再確認し、承認済み保証/対象外/期待結果を維持して、追加install・共有環境変更なしに安全に隔離できる標準機能、小さい接続/観測helper、その保存・再利用を具体化できる。予定外のSkill/helperだけでは追加承認や再計画を要求しない。必須component・権限・認証等が実際に不足し許可内で成立不能なら、試した失敗/未確認/不足/再開条件を示してBLOCKED。独自代替・無断install・期待値緩和で回避しない。製品契約・保証・明示制約変更はREPLAN_REQUIRED。

### 正式チェックと分担

mainの `CONTRIBUTING.md` の変更影響による選定とphase順序に従う。既存環境/build鮮度を確認し、無断同期しない。
- helperの小さいself-check/関連pytest、変更したfixtureの代表consumer単独とmodule、Python全体Ruff check/format、既存PDF extraのtyを基本にする。
- 共有fixture/保存/推論接続への波及があれば、core全pytest・PDF4ファイル等の関係full phasesへ広げる。Node shared helperを変えるなら全Vitest・Biome/type-aware oxlint。Node production/配布は変更予定なしで、必要なら影響と許可を再確認する。
- Workerはfocused検証・手順再実行・証拠、Rootは現在差分・選定した正式チェック・別invocation・全結果の受入れ、Reviewerは今回の要求/証拠から不足保証を独立確認する。旧Review/合格を流用しない。
- 通常run/resumeの製品統合、全冊、実OCR、remote CI/他OSは未確認として残す。

### 証拠・cleanup

今回専用workのWorkspace外へ、revision/dirty/diff・入力と出力hash・環境/build・command/exit・期待/観測・承認/全消費・採否/残件・未確認を保存する。実書籍内容は前述の非公開範囲だけに置く。人工資格情報と実認証を分離し、秘密値を記録しない。

所有TemporaryDirectory・人工credential・childだけをcleanup/waitし、元Workspaceや保持対象の評価結果を削除しない。失敗時も同様に行い、cleanup後に参照と保持結果を再読する。元ファイルのhash/mtime不変を確認する。全結果を正解/不合格を含め保持し、有利なtrialのみ残さない。
