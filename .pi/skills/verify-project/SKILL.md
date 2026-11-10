---
name: verify-project
description: Verify doc-translate PDF/EPUB run/resume, offline performance and render load, scoped review progress, TTY and output flags, and saved-only status through isolated public operations with evidence and cleanup.
---

# doc-translateの実動確認

今回関係する機能だけを[Feature Map](references/feature-map.md)から選ぶ。対象は軽量status／`--verify`／`--json`・必要記録破損拒否・保存不変、およびEPUB run／resumeの共通進捗・TTY／行ログ・出力flag。PDF run／resumeの工程・所有／参照と保存後ページ集計はMapのPDF節、明示範囲reviewの検証→AI確認→保存→終了検査・今回の指摘件数／品質未承認・出力flagsはreview節に限定して扱う。全Map実行・製品修正・installの許可ではない。通常test・正式チェック・Root／Reviewerの受入判断を代用しない。

Issue #53の統合受入れでは新手順を重複生成せず、Mapの既存「保存記録と詳細検査」「EPUB run／resumeと共通進捗」「PDF run／resumeの工程・所有ページ進捗」「明示範囲reviewの共通表示」を選ぶ。Rootが別invocationで各入口・TTY画像・故障代表・性能を直接照合し、[開発ガイドの#53節](../../../CONTRIBUTING.md#進捗表示statusの統合受入れissue-53)から正式チェックとwheel配布検査へ進む。これらは再実行の導線であり、統合成功の記録ではない。実provider／実認証・実Docling／OCR・意味品質・任意規模／全端末・他OS・SIGKILL復元・remote CIは保証しない。

## 前提と操作

repository rootをcwdにし、既存の`.venv/bin/python`とCLIを使う。依存同期／追加install／実認証／実LLM／実Docling・OCR／model取得／利用者workspaceは使わない。既存環境でimportと`status --help`が成功しない場合は、不足・試した方法・未確認範囲を報告して止める。勝手な環境更新や危険な代替をしない。helperやNodeのbuildはstatus-only操作に不要。

statusはMapの「保存記録と詳細検査」のshellとPythonを順に実行する。EPUBは「EPUB run／resumeと共通進捗」、PDFは「PDF run／resumeの工程・所有ページ進捗」、reviewは「明示範囲reviewの共通表示」の既存faux＋標準PTY手順を使う。PDFのみ既存PDF extraとDocling公開converter／page backend人工fixtureが必要で、実変換／OCRは起動しない。helper `scripts/capture_progress.py`は既存Pythonで起動し、stdout／stderr.ansi／frames.json／frames.htmlをworkspace外へ保存する。SGR保持の人工frameを既存Chromeで幅・明暗背景込みに実表示し、現在対象・異常理由の読取りとscreenshotを照合する。SGR検出／HTML生成だけで視認性合格とせず、観測条件と全端末等の未確認を記録する。既存の自作fixtureを公開saveから隔離workspaceへ保存し、実CLI renderで初期出力を作る。対象pathと保存source hashを記録し、statusの各指定を空PATHで操作する。processの存在だけを成功とせず、stdout・exit・file hash／mtimeを期待と照合する。異常時は今回のworkspace／fixture／source版を再確認し、期待値を緩めて製品不具合を隠さない。

## 形式別の性能計測とrenderer索引（Issue #54／#55）

今回の依頼がbaseline／renderer索引・render負荷の場合だけMapの「形式別run／resume性能とrender負荷」を選び、[性能reference](../../../docs/reference/performance.md)の起動→操作→期待値→証拠→cleanup手順を使う。既存PDF extra・実compiled Node helper／公開faux・network拒否・人工入力だけで、公開CLI run→fresh process resumeと公開save後のrender全体を測る。`scripts/performance_baseline.py`を既存Pythonで起動し、未作成のignore済みwork直下を`--evidence`へ指定する。固定recipeの通常before／after各3回はrepeatability controlであり、高速化を意味しない。diagnosticのprofile／counter passは別にし、停止budget・unknown・親／helperメモリ範囲を記録する。準備不足やresource hash不一致では停止し、install／buildしない。performance driver自体は準備ID分離・新並列・取消最適化の検証を含まない。準備ID分離の確認だけは末尾のIssue #58節を別に選ぶ。既存status／進捗／review手順は変更しない。

renderer索引の変更前後は別invocationの最初のbeforeラベル3回をprimaryに固定し、後半afterラベルのcontrolと分ける。driverは日英Markdownと生成assetsの全byteも保存するため、QA／source mapとともに直接照合する（非決定elapsed秒と派生directory digestだけを分離）。PDF／EPUB型N500のmedian50%以下、N50／200の増加率、代表run／resumeの上限は[Issue #55比較](../../../docs/reference/performance.md#renderer索引の比較issue-55)に従う。外乱時に承認した対称追加を行う場合も初回未達・全raw・合算判定を保持し、controlの有利な値へ置換しない。

## 証拠と後始末

- 実行ごとに既存ignore済み`work/test-verification-<unique>/`を作る。Git ignoreは秘密保護境界ではない。秘密値・実書籍・未加工の個人情報・不要raw logは保存しない。本文は既存の自作fixtureのみ。
- revision、dirty状態、差分、前提、command／exit、期待と観測、未確認を記録する。stdout／stderrと保存hashの証拠はworkspace外へ保存する。古い成功を今回の証拠に流用しない。外部送信・commit・自動保持期限管理はしない。
- Python例は所有するTemporaryDirectoryだけをfinallyで回収し、起動した短命childはsubprocess.runで回収する。名前によるprocess一括終了、work全体／他workspace／venv／credential／model削除は禁止。失敗でも所有物だけcleanupし、残存状態を報告する。
- cleanup後、実行記録とそこから参照する全証拠を実際に再読する。失敗・cleanup未成立・前提不足を完了扱いにしない。今回の成否は証拠directoryに記録し、Mapを成功履歴として使わない。

## 再利用

このrepositoryでPiを起動してproject trustを確認する。標準の`.pi/skills/`探索を使い、追加／更新後は新セッションか`/reload`を行う。`/skill:verify-project performance`／`/skill:verify-project PDF progress`／`/skill:verify-project EPUB progress`／`/skill:verify-project review`または`/skill:verify-project status`で明示呼び出しするか、このSKILL.mdを明示readする。自動選択自体は保証しない。通常の関連変更では関係する手順／Mapだけを更新して再実行し、無関係な全Map点検を要求しない。共通Maintainはユーザー明示実行時の別用途で、今回の再実行には不要。

## preparation保存attempt探索（Issue #56）

preparationのbefore／after比較だけを依頼された場合はMapの「preparation保存attempt探索」を選ぶ。[性能referenceの#56節](../../../docs/reference/performance.md#preparation保存attempt探索の比較issue-56)の小さい専用helperを既存Pythonで実行する。公開save→実Node／faux→prepare/loadで100 groupの整合seedを生成し、adopted／unadopted／独立loadをfresh processで測る。primaryはunadoptedのinclusive `_call`合計に固定し、公開load・全体wall／CPU／RSS・別diagnosticを分ける。afterは保持した`seed/`の全byteを再利用し、実追加requestゼロ・旧immutable不変を照合する。before段階の依頼はRootの直接確認で停止し、承認後の最適化段階では同seedでafterを取得する。`tests/test_preparation.py`のcatalog回帰とselfcheckで到達record各read/hash1回、新規保存登録、別operation再検証、辞書順・lazy破損境界を照合する。raw memoは受理結果を共有せず、実request/config/repair/raw回答/根拠検証を残す。手順成立・before取得だけを最適化合格にしない。前提hash不一致では停止し、追加install／build・翻訳pipeline生成・汎用runner追加はしない。所有一時物だけcleanupし、保持した人工seed／証拠全fileをreadbackする。再利用はproject trust確認後、新セッションまたは`/reload`、`/skill:verify-project preparation performance`か実ファイルread。

## translation保存attempt探索（Issue #57）

catalogの候補はrequest text／先頭unit IDで取得し、その後に全configを照合する。等値configの`temperature=-0.0`／`0.0`をJSON byteキーで分離しない。公開API回帰`test_translation_recovers_orphan_with_equal_signed_zero_config`は正負両方向のpointer除去後無送信回収・旧immutable保持・現在configと新旧loadを検査する。レビュー修正前の性能値は修正後sourceの値として使わず、Rootが同seed／同入力で再計測する。

翻訳attempt-heavyのbefore／after比較はMapの「translation保存attempt探索」を選び、[性能referenceの#57節](../../../docs/reference/performance.md#translation保存attempt探索の計測issue-57)を使う。既存Pythonで`translation_baseline.py --evidence work/issue57-<unique> --size 2`の接続確認後、既定100 groupを非並行実行する。公開save／prepare／translate／loadと既存実Node／faux／no-network／CONFIGを使い、翻訳100 attemptのseed全byteを保持する。primaryはunadoptedの初回attempt glob→Executor.__enter__までのinclusive pre-job探索proxyで、公開translate全体や内部previous loadとは分ける。通常各3回＋別diagnostic、worker submit／実追加request0、immutable／seed不変、completed・回答対応をassertする。thread-local診断で探索とpublic_load／storeの正当なread/hash/parseを分離する。承認後のafterは同seedコピー・同script・同境界で比較し、catalog候補索引でも全実request/config/raw/domain検証・eager破損拒否を維持する。新規attemptのatomic保存後登録とthread-safeな辞書順snapshot取得は`tests/test_translation.py`で確認する。v2の同一wire＋configの競合miss共有は末尾のIssue #59節と回帰で確認する。安全前提／停止予算／所有物cleanup・全証拠readbackはreferenceに従い、他Mapや正式チェックの成功へ拡張しない。project trust確認後、新セッションまたは`/reload`、`/skill:verify-project translation performance`か実ファイルreadで再利用する。

## 準備request内IDと追跡binding（Issue #58）

準備collect／organizeのwire v2・v1厳密互換を確認するときだけMapの「準備request内IDと追跡binding」を選ぶ。既存環境・実Node helper／公開faux・no-networkで合成PDFと実EPUB reader／extractorの自作入力を公開prepare→loadへ通す。Mapのshell／Pythonを順に実行し、前方追加による後続group無送信reuse、raw aliasと現在追跡位置、整理の実wire、利用者terms、旧byte不変を証拠と照合する。v1は変更前に生成した原byte fixtureからloadし、方式追加だけによる再送がないことを別に確認する。binding／version／再hash根拠・repair／orphanは指定focused回帰で補完する。全unitの回答根拠必須化や根拠順制約は追加しない。新helper／汎用harness・install／build・翻訳v2／並列を追加しない。通常の正式チェック、Rootの別invocation、Reviewer判断、実provider意味品質は代用しない。所有TemporaryDirectory／人工credential／childだけcleanupし、workspace外の証拠を再読する。project trust確認後、新セッションまたは`/reload`、`/skill:verify-project preparation IDs`か本ファイルreadで再利用する。

## 翻訳request内IDと追跡binding（Issue #59）

翻訳wire v2／追跡binding・v1厳密互換を確認するときだけMapの「翻訳request内IDと追跡binding」を選ぶ。repository rootで同節のshell／Pythonを実行し、既存`test_preparation.id_source/id_answers/run`と`test_translation.translate`を公開prepare→translate→load／reviseへ接続する。PDF合成sourceと実EPUB reader／extractor、自作本文・人工credential・既存実Node/faux/no-networkだけを使う。raw alias／現在追跡ID・prefix reuse／実入力miss・manual優先と非転写・v1原byteを照合し、同じwireでも現在bindingを省略しない。全体性能・新並列設定・実provider品質は対象外。前提不足／helper hash不一致で停止し、install／sync／buildしない。証拠はworkspace外の`work/issue59-<unique>/`へ保存し、所有TemporaryDirectory／childだけcleanup後に全証拠を再読する。正式チェック・Root独立操作・Reviewer gateは別。project trust確認後、新セッションまたは`/reload`、`/skill:verify-project translation IDs`か本ファイルreadで再利用する。

## 準備collectの制限付き並列（Issue #60）

準備の1・2・4、run manifest保存／resume継承、同一wireのin-flight共有、順序、停止／回収を確認するときはMapの「準備collectの制限付き並列」を選ぶ。repository root・既存Python／Node／compiled helperのhash一致と人工入力／credential／no-networkだけを使い、`scripts/preparation_concurrency.py --evidence work/issue60-<unique>`で公開save→prepare→load→無変更prepareを実操作する。12独立group各500ms、各設定通常3回のstage全体・実跨process start/end・active／calls／入力／raw回答と順序を照合する。callback内のprocess-local counterをaggregate activeと呼ばない。失敗したrunは成功統計へ混ぜない。Mapの別の小さい直接操作で局所失敗／Event取消／OS replace保存障害からの再実行を確認する。旧pointer／immutable保持・成功回収・organizeは全group保存後一回が期待値である。公開prepare単体のsignal取消はMapの小さいsubprocess回帰で再実行し、親Python PIDだけへのSIGTERM（既定1／並列4）とSIGINT、drain／attempt保存／子reap、元handler復元・caller Event・非main thread呼出しを照合する。通常回帰・Rootの別invocation直接確認・全正式phase・Reviewer gateは別。既定1の変更、PDF補正並列、実provider速度／意味品質・rate limit・課金保証は対象外。所有child／一時workspaceだけcleanupし、workspace外の証拠全参照をreadbackする。project trust後の新セッションまたは`/reload`、`/skill:verify-project preparation concurrency`か本ファイルreadで再利用できる。

## PDF補正の制限付き並列（Issue #61）

PDFの`--correction-concurrency 1|2|4`、run保存／resume継承／旧欠落1、分割後active・逆順・取消／保存失敗からの成功回収だけを確認するときはMapの「PDF補正の制限付き並列」を選ぶ。repository rootの既存PDF extra付きPython／Node／compiled helper全10file hash一致・自作入力／人工credential／no-networkを使う。`scripts/preparation_concurrency.py --feature pdf-correction --evidence work/issue61-<unique>`は既存の計測／証拠／所有child回収を再利用し、小さい`pdf_correction_concurrency.py`で公開runの取得済み同一seed→fresh Pythonの公開resume→loadを接続する。12独立job各500ms、1／2／4各3回のstage時間（Node起動／実検証／保存込み）、実跨process start/end／active終了0、calls／全input／raw回答／source順・無変更resume送信0／旧immutable不変を照合する。Mapの別invocationで親signal取消／OS replace保存例外／通常局所失敗からの成功回収も実操作する。最大5所有ページ・許可隣接参照・1800px・coverage最大3回答／有限transport retryを変更しない。既定1・公開correct_pdf lock契約を保持し、EPUBの明示1も拒否する。実provider速度・意味品質・OCR／layout・rate limit／課金／他OS・remote CIは対象外。所有TemporaryDirectory／childだけcleanupし、証拠全参照をreadbackする。正式回帰・Rootの別workspace直接確認／全正式phase・Reviewer gateは別である。project trust後、新セッションか`/reload`、`/skill:verify-project PDF correction concurrency`または実ファイルreadで再利用する。

## 統合性能比較・採否（Issue #62）

renderer／catalog／v2／準備・PDF補正並列を合わせたbefore／afterだけを依頼された場合はMapの「統合性能比較・採否」を選び、[性能referenceの#62節](../../../docs/reference/performance.md#統合beforeafterと採否issue-62)の5コマンドを非並行foregroundで実行する。`performance_comparison.py`は既存recipe／公開operation／invoke／計数を接続する専用比較で、汎用runnerではない。checkoutを切替えず所有TemporaryDirectoryの`git archive 8f19b4b src`と現srcをchildのPYTHONPATH先頭へ固定し、`performance_revision.py`で実import path/hashを検査・記録する。各EPUBは一度生成した全byte、attempt-heavyは旧srcの公開prepare→translateが生成した同じv1 seed全byteを両sideへ渡す。現fixtureのv2専用_projectionを旧版へshimせず、seed生成だけ同じ実Node argv／回答を公開APIへ接続する。

通常primary各3回と別diagnostic、cold v1/v2 fresh runとwarm無変更resumeを別系列で残す。render N500<=50%、探索<=70%、代表run/resume<=max(before×1.10,before+.05秒)、現並列helperの12job×500msでc2<=75%／c4<=55%・active上限/終了0・全入力/raw/順不変を照合する。秒閾値はpytestにしない。renderは全Markdown/map/QA/assets byte一致、独立fresh PDF runだけelapsed由来のchunk/source snapshot digestを元値保持で分離し、本文・QA全対象/順/checks・mapの残りを比較する。親／reaped childのru_maxrssは生涯peak／最大childでありstage同時aggregate RSSではない。

新しいignore済み`work/issue62-<unique>/`へcommand/exit/imports/hash/raw/CPU/resource・診断・出力と採否／未達／未確認を残す。所有childのみdrain/waitし、作ったTemporaryDirectoryだけcleanup後、証拠全参照をreadbackする。最終helper hashでのsmall `--smoke`は手順成立だけで代表性能を代用しない。実AI未承認なら未実施・provider推薦保留、準備／PDF補正既定1・翻訳2のまま。正式Plan／設計／progress／production／既定値は変更せず、追加install/sync/build・実送信を許可しない。Workerのfocused／性能とRootの正式全phase・別workspace直接操作／独立Reviewの分担はreferenceに従う。project trust後、新セッションか`/reload`、`/skill:verify-project integrated performance`または本ファイルreadで再利用する。
