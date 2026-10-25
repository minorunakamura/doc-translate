# doc-translate

英語の技術PDF・テキストEPUBを、原本文書への追跡と要確認事項を保ちながら日本語Markdownへ変換するCLIを開発しています。

**現在使えるのはhelper依存の初回導入と、provider／modelの参照です。文書の取得・翻訳・Markdown生成、loginはまだ使えません。** 認証情報や書籍を準備・送信する必要はありません。

## 前提環境と導入

ローカル検証基準はmacOS 26.7.1 arm64、Python 3.14、Node 24.21.0です。Nodeは24.21.0以上を要求します。macOSの最低version、Intel、Linux／Windowsでの動作は未保証です。Node依存の初回導入には導入済みのpnpm 12.10.1またはnpm 11.19.0を使用します。Pi CLI／coding agentは不要です。

配布wheelを入手した場合、uvでインストールできます（`FILE.whl`は入手したwheelのpathに置換します）。まだrelease配布はしていません。

```sh
uv tool install /path/to/FILE.whl
doc-translate --help
# 初回だけ。installed managerを明示して固定lockの依存を取得
# npmを使う場合は pnpm を npm に置換
doc-translate setup-ai --package-manager pnpm
```

`setup-ai`だけが依存packageをregistryから取得します。Nodeやmanager自体の取得、実行時compile、認証、model重み取得、文書送信は行いません。通常の参照操作にはNodeだけが必要で、pnpm／npmやPi CLIは不要です。自動installやnpxへのfallbackはありません。

runtimeは文書workspaceではなく、`~/.local/share/doc-translate/ai/`（`XDG_DATA_HOME`指定時はその下の`doc-translate/ai/`）に保存します。setupのたびにmanager・固定lock・helperに対応する新しいdirectoryを作り、導入と検査に成功したものだけを次回利用対象にします。別managerで既存node_modulesを上書きせず、旧runtime、文書、credentialは削除しません。失敗した導入directoryも残ります。成功時は利用先とmanager／lock hash／helper・library versionをJSONで表示します。

Pythonのcore導入にDoclingやOCR modelは不要です。PDF用依存は別extra `pdf`ですが、依存を入れただけでは文書処理はできません。

## provider／modelを調べる（任意）

これらはIDを調べたいときだけ使う**任意の参照操作**です。初回setup後に毎回実行する手順ではなく、provider／model IDを知っていれば省略できます。

```sh
doc-translate providers
doc-translate models --provider anthropic
doc-translate models --provider google
doc-translate models --provider openai
```

結果は非秘密のJSONです。pi-aiのbuilt-in providerとchat model catalogを読み、model ID、名称、`image_input`（画像入力対応）、`reasoning`、`thinking_levels`を表示します。画像生成機能ではありません。通常levelの省略はprovider既定対応、明示nullは未対応として扱い、`xhigh`／`max`は明示対応時だけ表示します。reasoning非対応modelでは`off`のみです。

認証・token refresh・catalog refresh・LLM通信はしません。全model定義やheaders、credentialは出力せず、Pi CLIの認証設定も読み込み・コピーしません。catalog掲載はaccountの利用権や実providerの品質を保証しません。動的catalogのみのproviderは、built-inのmodel一覧が空になる場合があります。

help／setup／参照の成功はexit code 0、入力・設定不足／依存不一致は2、導入やhelper実行の失敗は1です。引数なし・未実装操作も2で停止します。不足／不一致なら表示された`setup-ai --package-manager npm`（または`pnpm`）を明示実行してください。失敗を空の成功結果とは扱いません。

## 予定している対応範囲（未実装）

- PDF：文字付き・画像のみの英語技術PDF。Docling抽出を原本画像と照合して補正します。任意の実スキャンの完全復元は保証しません。
- EPUB：DRM解除不要のEPUB 2／3、整形式XHTMLのリフロー型。fixed-layout、画像主体、DTBook専用、複数renditionの自動選択、壊れたXML修復、画像OCRは対象外です。
- 出力：日本語Markdown、閲覧用英語Markdown、相対参照assets、原文との対応表、QA・利用量記録。PDF／EPUBの出力生成、同じ組版の再現、図内文字の翻訳は対象外です。

以下は将来の利用イメージであり、**これらのサブコマンドはまだ実装されていません**。

```sh
# 初回認証だけ。IDは選択providerのID
doc-translate login --provider ID --method oauth
# 翻訳、続行、保存済み正本からの再出力、局所修正
doc-translate run INPUT --workspace DIR
doc-translate resume DIR
doc-translate render DIR
doc-translate revise DIR --source FILE --reason TEXT
```

providerはOpenAI固定ではなく、pi-aiを直接利用する予定です。ChatGPTサブスクリプションOAuthも検証対象ですが、実認証・利用権・翻訳品質は未確認です。

PDFのmodel重みは同梱せず、将来明示的なローカル準備手順を提供します。現在は取得不要です。EPUBにPDF依存・OCR modelを要求しない設計です。

## 成果物とエラーの読み方

将来の処理結果では、処理到達範囲・機械検査・内容確認範囲を区別します。要確認箇所は出力の`※`とQAで追跡し、出力ができただけで内容品質承認済みとはしません。現時点ではこれらの成果物・status表示も未実装です。

未実装の文書処理は認証・依存の再導入では使えるようになりません。実書籍や秘密情報をIssueに添付しないでください。
