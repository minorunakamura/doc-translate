# doc-translate

英語の技術PDF・テキストEPUBを、原本文書への追跡と要確認事項を保ちながら日本語Markdownへ変換するCLIを開発しています。

**現在は初期基盤段階です。文書の取得・翻訳・Markdown生成はまだ使えません。** 現在実行できるCLI操作はhelpだけです。認証情報や書籍を準備・送信する必要はありません。

## 前提環境と導入

ローカル検証基準はmacOS 26.7.1 arm64、Python 3.14、Node 24.21.0です。macOSの最低version、Intel、Linux／Windowsでの動作は未保証です。Node依存の初回導入にはpnpm 12.10.1またはnpm 11.19.0を使用する予定です。Pi CLI／coding agentは不要です。

配布wheelを入手した場合、uvでインストールしてhelpを確認できます（`FILE.whl`は入手したwheelのpathに置換します）。まだrelease配布はしていません。

```sh
uv tool install /path/to/FILE.whl
doc-translate --help
```

引数なし・未実装の操作はexit code 2で停止します。helpのexit codeは0です。Pythonのcore導入にDoclingやOCR modelは不要です。PDF用依存は別extra `pdf`ですが、依存を入れただけでは文書処理はできません。現在のCLIはmodel取得・推論・自動installを行いません。

## 予定している対応範囲（未実装）

- PDF：文字付き・画像のみの英語技術PDF。Docling抽出を原本画像と照合して補正します。任意の実スキャンの完全復元は保証しません。
- EPUB：DRM解除不要のEPUB 2／3、整形式XHTMLのリフロー型。fixed-layout、画像主体、DTBook専用、複数renditionの自動選択、壊れたXML修復、画像OCRは対象外です。
- 出力：日本語Markdown、閲覧用英語Markdown、相対参照assets、原文との対応表、QA・利用量記録。PDF／EPUBの出力生成、同じ組版の再現、図内文字の翻訳は対象外です。

以下は将来の利用イメージであり、**いずれのサブコマンドもまだ実装されていません**。

```sh
# 初回だけ。installed managerで依存を導入（認証・推論とは別）
doc-translate setup-ai --package-manager pnpm
# 初回認証だけ。IDは選択providerのID
doc-translate login --provider ID --method oauth
# 翻訳、続行、保存済み正本からの再出力、局所修正
doc-translate run INPUT --workspace DIR
doc-translate resume DIR
doc-translate render DIR
doc-translate revise DIR --source FILE --reason TEXT
```

providerはOpenAI固定ではなく、pi-aiを直接利用する予定です。ChatGPTサブスクリプションOAuthも検証対象ですが、実認証・利用権・翻訳品質は未確認です。Pi CLIの認証設定を自動読込み・コピーしません。

PDFのmodel重みは同梱せず、将来明示的なローカル準備手順を提供します。現在は取得不要です。EPUBにPDF依存・OCR modelを要求しない設計です。

## 成果物とエラーの読み方

将来の処理結果では、処理到達範囲・機械検査・内容確認範囲を区別します。要確認箇所は出力の`※`とQAで追跡し、出力ができただけで内容品質承認済みとはしません。現時点ではこれらの成果物・status表示も未実装です。

`Workflow operations are not implemented yet`は現在の制限を示すエラーです。認証・依存の再導入では解決しません。実書籍や秘密情報をIssueに添付しないでください。
