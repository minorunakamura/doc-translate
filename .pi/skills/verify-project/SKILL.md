---
name: verify-project
description: Verify doc-translate EPUB run/resume progress, TTY and output flags, and saved-only status through isolated public CLI operations, with evidence and cleanup.
---

# doc-translateの実動確認

今回関係する機能だけを[Feature Map](references/feature-map.md)から選ぶ。対象は軽量status／`--verify`／`--json`・必要記録破損拒否・保存不変、およびEPUB run／resumeの共通進捗・TTY／行ログ・出力flag。PDF補正／review新表示の完成は後続Issueで、ここへ広げない。全Map実行・製品修正・installの許可ではない。通常test・正式チェック・Root／Reviewerの受入判断を代用しない。

## 前提と操作

repository rootをcwdにし、既存の`.venv/bin/python`とCLIを使う。依存同期／追加install／実認証／実LLM／実Docling・OCR／model取得／利用者workspaceは使わない。既存環境でimportと`status --help`が成功しない場合は、不足・試した方法・未確認範囲を報告して止める。勝手な環境更新や危険な代替をしない。helperやNodeのbuildはstatus-only操作に不要。

statusはMapの「保存記録と詳細検査」のshellとPythonを順に実行する。EPUBは「EPUB run／resumeと共通進捗」の既存faux＋標準PTY手順を使う。helper `scripts/capture_progress.py`は既存Pythonで起動し、stdout／stderr.ansi／frames.json／frames.htmlをworkspace外へ保存する。SGR保持の人工frameを既存Chromeで幅・明暗背景込みに実表示し、現在対象・異常理由の読取りとscreenshotを照合する。SGR検出／HTML生成だけで視認性合格とせず、観測条件と全端末等の未確認を記録する。既存の自作fixtureを公開saveから隔離workspaceへ保存し、実CLI renderで初期出力を作る。対象pathと保存source hashを記録し、statusの各指定を空PATHで操作する。processの存在だけを成功とせず、stdout・exit・file hash／mtimeを期待と照合する。異常時は今回のworkspace／fixture／source版を再確認し、期待値を緩めて製品不具合を隠さない。

## 証拠と後始末

- 実行ごとに既存ignore済み`work/test-verification-<unique>/`を作る。Git ignoreは秘密保護境界ではない。秘密値・実書籍・未加工の個人情報・不要raw logは保存しない。本文は既存の自作fixtureのみ。
- revision、dirty状態、差分、前提、command／exit、期待と観測、未確認を記録する。stdout／stderrと保存hashの証拠はworkspace外へ保存する。古い成功を今回の証拠に流用しない。外部送信・commit・自動保持期限管理はしない。
- Python例は所有するTemporaryDirectoryだけをfinallyで回収し、起動した短命childはsubprocess.runで回収する。名前によるprocess一括終了、work全体／他workspace／venv／credential／model削除は禁止。失敗でも所有物だけcleanupし、残存状態を報告する。
- cleanup後、実行記録とそこから参照する全証拠を実際に再読する。失敗・cleanup未成立・前提不足を完了扱いにしない。今回の成否は証拠directoryに記録し、Mapを成功履歴として使わない。

## 再利用

このrepositoryでPiを起動してproject trustを確認する。標準の`.pi/skills/`探索を使い、追加／更新後は新セッションか`/reload`を行う。`/skill:verify-project EPUB progress`または`/skill:verify-project status`で明示呼び出しするか、このSKILL.mdを明示readする。自動選択自体は保証しない。通常の関連変更では関係する手順／Mapだけを更新して再実行し、無関係な全Map点検を要求しない。共通Maintainはユーザー明示実行時の別用途で、今回の再実行には不要。
