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

今回の依頼がbaseline／renderer索引・render負荷の場合だけMapの「形式別run／resume性能とrender負荷」を選び、[性能reference](../../../docs/reference/performance.md)の起動→操作→期待値→証拠→cleanup手順を使う。既存PDF extra・実compiled Node helper／公開faux・network拒否・人工入力だけで、公開CLI run→fresh process resumeと公開save後のrender全体を測る。`scripts/performance_baseline.py`を既存Pythonで起動し、未作成のignore済みwork直下を`--evidence`へ指定する。固定recipeの通常before／after各3回はrepeatability controlであり、高速化を意味しない。diagnosticのprofile／counter passは別にし、停止budget・unknown・親／helperメモリ範囲を記録する。準備不足やresource hash不一致では停止し、install／buildしない。ID分離・新並列・取消最適化など未実装の将来機能は追加／検証済みとしない。既存status／進捗／review手順は変更しない。

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
