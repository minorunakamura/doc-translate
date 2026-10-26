# doc-translate

英語の技術PDF・テキストEPUBを、原本文書への追跡と要確認事項を保ちながら日本語Markdownへ変換するCLIを開発しています。

**現在使えるのはhelper依存の初回導入、provider／modelの参照、login／logoutです。文書の取得・翻訳・Markdown生成はまだ使えません。** 参照だけなら認証情報や書籍を準備・送信する必要はありません。実providerでの認証・推論は未検証です。

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

`setup-ai`だけが依存packageをregistryから取得します。Nodeやmanager自体の取得、実行時compile、認証、model重み取得、文書送信は行いません。通常の参照・認証操作にはNodeだけが必要で、pnpm／npmやPi CLIは不要です。自動installやnpxへのfallbackはありません。

runtimeは文書workspaceではなく、`~/.local/share/doc-translate/ai/`（`XDG_DATA_HOME`指定時はその下の`doc-translate/ai/`）に保存します。setupのたびにmanager・固定lock・helperに対応する新しいdirectoryを作り、導入と検査に成功したものだけを次回利用対象にします。別managerで既存node_modulesを上書きせず、旧runtime、文書、credentialは削除しません。失敗した導入directoryも残ります。成功時は利用先とmanager／lock hash／helper・library versionをJSONで表示します。

Pythonのcore導入にDoclingやOCR modelは不要です。PDF用依存は別extra `pdf`です。PDF取得のPython APIは使えますが、翻訳・補正や文書処理CLIにはまだ接続していません。

## provider／modelを調べる（任意）

これらはIDを調べたいときだけ使う**任意の参照操作**です。初回setup後に毎回実行する手順ではなく、provider／model IDを知っていれば省略できます。

```sh
doc-translate providers
doc-translate models --provider anthropic
doc-translate models --provider google
doc-translate models --provider openai
```

結果は非秘密のJSONです。`providers`はID・名称と、選択できる`login_methods`（`api_key`／`oauth`の配列）を表示します。`models`はpi-aiのbuilt-in chat model catalogを読み、model ID、名称、`image_input`（画像入力対応）、`reasoning`、`thinking_levels`を表示します。画像生成機能ではありません。通常levelの省略はprovider既定対応、明示nullは未対応として扱い、`xhigh`／`max`は明示対応時だけ表示します。reasoning非対応modelでは`off`のみです。

認証・token refresh・catalog refresh・LLM通信はしません。全model定義やheaders、credentialは出力せず、Pi CLIの認証設定も読み込み・コピーしません。catalog掲載はaccountの利用権や実providerの品質を保証しません。動的catalogのみのproviderは、built-inのmodel一覧が空になる場合があります。

help／setup／参照／認証の成功はexit code 0、入力・設定不足／依存不一致・未知provider／未対応login methodは2、導入・認証・保存・helper実行の失敗や取消は1です。引数なし・未実装操作も2で停止します。不足／不一致なら表示された`setup-ai --package-manager npm`（または`pnpm`）を明示実行してください。失敗を空の成功結果とは扱いません。

## providerの認証と削除

初回setup後、選択providerの`login_methods`にあるmethodだけを指定します。以下はそれぞれの認証方式を使う場合の例であり、両方を順に実行する手順ではありません。

```sh
# ChatGPTサブスクリプションOAuthを選ぶ場合
# 表示された認証URL・device code等に従って認証
doc-translate login --provider openai --method oauth
# Anthropic API keyをこのアプリへ保存する場合（入力はterminalで非echo）
doc-translate login --provider anthropic --method api_key
# このアプリの当該providerの保存credentialだけを削除
doc-translate logout --provider anthropic
```

`login_methods: []`のambient-only providerにはloginしません。AWS Bedrock等はprovider公式の環境認証手順（[AWSの認証設定](https://docs.aws.amazon.com/sdkref/latest/guide/standardized-credentials.html)など）に従って環境変数・profile等を準備します。環境認証を使う場合、logoutは環境変数やprovider側の認証を削除・失効させません。

保存先は`$XDG_CONFIG_HOME/doc-translate/`、変数が未設定または空なら`~/.config/doc-translate/`です。directoryは0700、`credentials.json`は0600で、文書workspaceやexport、Pi CLIのauth.jsonとは別です。OpenAI OAuthの初回loginで安定したinstallation UUIDを`installation-id`（0600）に作成・再利用します。logoutはこのUUIDや別providerのcredentialを消しません。これらのファイルを共有・repositoryへ追加しないでください。

OAuth tokenのTTL判定・refresh・認証変換はpi-ai／providerに任せ、通常は保存credentialを再利用します。refresh失敗時にambient API keyや別の認証modeへ自動fallbackしません。ただし文書の翻訳操作は未実装で、実tokenのrefresh・利用権・推論の成功は未確認です。ChatGPTサブスクリプションは共有利用枠であり、API課金や全modelの利用権、無制限利用を保証しません。

認証の入力・選択はcontrolling TTYで非echoにし、pipeからの秘密入力やechoへのfallbackは受け付けません。認証URL・device codeはterminalにのみ表示し、stdoutの成功JSONは`operation`・`provider`・login時の`method`だけです。URL・code・token・入力内容をログやterminal録画へ保存したり、Issueへ添付したりしないでください。helperのraw stderr／provider例外も出力しません。

Ctrl-C／SIGTERM／入力EOF等の取消ではchildを回収し、進行中のcredential保存を待つため強制kill前に最大30秒のgraceを設けます。入力中の未送信文字を破棄してechoを復元します。SIGKILLやdisk障害では保存・terminal復元を保証できません。破損JSONや保存失敗を空credential・成功とは扱わず、取消中の保存失敗もエラーにします。エラー時は保存先・権限・空き容量等を確認し、必要なら再loginしてください。秘密入りファイルの内容を診断ログに貼らないでください。

## 予定している対応範囲（未実装）

- PDF：文字付き・画像のみの英語技術PDF。Docling抽出を原本画像と照合して補正します。任意の実スキャンの完全復元は保証しません。
- EPUB：DRM解除不要のEPUB 2／3、整形式XHTMLのリフロー型。fixed-layout、画像主体、DTBook専用、複数renditionの自動選択、壊れたXML修復、画像OCRは対象外です。
- 出力：日本語Markdown、閲覧用英語Markdown、相対参照assets、原文との対応表、QA・利用量記録。PDF／EPUBの出力生成、同じ組版の再現、図内文字の翻訳は対象外です。

以下は将来の利用イメージであり、**これらのサブコマンドはまだ実装されていません**。

```sh
# 翻訳、続行、保存済み正本からの再出力、局所修正
doc-translate run INPUT --workspace DIR
doc-translate resume DIR
doc-translate render DIR
doc-translate revise DIR --source FILE --reason TEXT
```

providerはOpenAI固定ではなく、pi-aiを直接利用します。ChatGPTサブスクリプションOAuthも実動検証対象ですが、実認証・利用権・翻訳品質は未確認です。

EPUBにPDF依存・OCR modelは不要です。PDF取得だけを使う場合の準備は次節を参照してください。

## PDF取得だけを使う場合のローカル準備

PDFの取得は公開Python APIから利用できます（[引数・保存契約](docs/reference/artifacts.md#pdf抽出下書きissue-7)）。Node／provider認証／LLMは不要です。抽出下書きは正しい原文や補正済み原文ではありません。翻訳や補正を行いたいだけなら、未実装の文書CLIのために重みを取得する必要はありません。

PDF依存を含むwheelを明示導入します。

```sh
uv tool install '/path/to/FILE.whl[pdf]'
```

固定依存はDocling 2.142.1／docling-core 2.104.0／OCRMac 1.0.1です。CPU、Apple Vision OCR（`en-US`／`vision`／`accurate`）で動作し、別OCRへのfallbackはありません。現在の実動確認はmacOS 26.7.1 arm64／Python 3.14のみです。

重みは同梱せず、次の**固定revisionの必要ファイルだけ**を利用者が準備します。packageのMIT licenseを重みやAppleのOS frameworkへ一括適用しません。

| 用途 | repo／revision | 必要ファイル | 条件 |
| --- | --- | --- | --- |
| Heron layout | `docling-project/docling-layout-heron` / `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | `config.json`, `preprocessor_config.json`, `model.safetensors` | [Apache-2.0 model card](https://huggingface.co/docling-project/docling-layout-heron/blob/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8/README.md) |
| TableFormer V1 accurate（v2.3.0） | `docling-project/docling-models` / `fc0f2d45e2218ea24bce5045f58a389aed16dc23` | `model_artifacts/tableformer/accurate/tm_config.json`, `model_artifacts/tableformer/accurate/tableformer_accurate.safetensors` | [CDLA-Permissive-2.0 model card](https://huggingface.co/docling-project/docling-models/blob/fc0f2d45e2218ea24bce5045f58a389aed16dc23/README.md) |
| OCRMac／Apple Vision | OCRMac codeはMIT、認識はmacOSのVision framework | 別のOCR重み取得なし | AppleのOS利用条件。VisionをMITとは扱いません |

`MODELS`には利用者指定のlocal artifact directoryを指定します。既存の該当snapshotがあれば、cacheを改変せず次の構造のsymlinkで用意できます。`HERON_SNAPSHOT`／`TABLE_SNAPSHOT`は上表revisionのsnapshot directoryに置換してください。

```sh
MODELS=/absolute/path/to/local-models
mkdir -p "$MODELS"
ln -s "$HERON_SNAPSHOT" "$MODELS/docling-project--docling-layout-heron"
ln -s "$TABLE_SNAPSHOT" "$MODELS/docling-project--docling-models"
```

未準備で、modelの利用条件・取得量を確認して**明示取得する場合だけ**、PDF環境の`hf`で以下を実行できます。これはネットワークから重みを取得する操作です。変換中には実行されません。`main`や全model downloaderは使用しません。

```sh
hf download docling-project/docling-layout-heron \
  --revision 8f39ad3c0b4c58e9c2d2c84a38465abf757272d8 \
  --include config.json preprocessor_config.json model.safetensors \
  --local-dir "$MODELS/docling-project--docling-layout-heron"
hf download docling-project/docling-models \
  --revision fc0f2d45e2218ea24bce5045f58a389aed16dc23 \
  --include 'model_artifacts/tableformer/accurate/tm_config.json' \
  'model_artifacts/tableformer/accurate/tableformer_accurate.safetensors' \
  --local-dir "$MODELS/docling-project--docling-models"
```

変換時はHub／Transformersをofflineにし、明示local pathのみを使います。不足ファイルは必要pathを示して停止し、自動download・remote services・external pluginsを使いません。固定repo／revisionと実ファイルhashを保存しますが、任意に差し替えたlocalファイルがupstream revisionと一致することを自動認証するものではありません。取得失敗やページ画像欠落は保存済み下書きへのdigest付きエラー、charspan不一致はclipしない要確認checkとして残ります。実行成功も原文品質の承認ではありません。

## 成果物とエラーの読み方

将来の処理結果では、処理到達範囲・機械検査・内容確認範囲を区別します。要確認箇所は出力の`※`とQAで追跡し、出力ができただけで内容品質承認済みとはしません。現時点ではこれらの成果物・status表示も未実装です。

未実装の文書処理は認証・依存の再導入では使えるようになりません。実書籍や秘密情報をIssueに添付しないでください。
