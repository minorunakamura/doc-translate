# doc-translate

英語の技術PDF・テキストEPUBを、原本文書への追跡と要確認事項を保ちながら日本語Markdownへ変換するCLIです。

`run`で取得から準備・翻訳・出力まで実行し、中断後は`resume`で続行します。保存済みの原文・訳文は`revise`で修正し、`render`で推論なしに再出力できます。`status`で処理範囲・QA・成果物を確認し、必要な範囲だけ`review`で追加LLM照合できます（自動修正・全体品質承認はしません）。

**自作の小さな文字付きPDF・画像のみPDF・EPUBで、OpenAI OAuthの実認証、実LLMによる準備・翻訳（PDFは補正も）と、限定したviewerでの表示を確認済みです。** 出力到達と内容品質の承認は別です。実token refreshは未観測で、任意文書の完全復元・意味品質や他のaccount／modelの利用権は保証しません。確認範囲と限界は[開発ガイドの統合受入れ記録](CONTRIBUTING.md#統合受入れの限定確認issue-18)を参照してください。

## 前提環境と導入

対象環境はmacOS 26.7.1 arm64、Python 3.14、Node 24です。Nodeは24.21.0以上を要求します。macOSの最低version、Intel、Linux／Windowsでの動作は未保証です。Node依存の初回導入には導入済みのpnpmまたはnpmを使用します。今回のローカル確認環境はPython 3.14.9／Node 24.22.1／pnpm 12.10.1／npm 11.19.1で、以前の確認基準Node 24.21.0／npm 11.19.0とは異なります。Pi CLI／coding agentは不要です。

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

Pythonのcore導入にDoclingやOCR modelは不要です。EPUBのrunはcoreだけで使えます。PDFのrunには別extra `pdf`と、後述のローカルmodel準備が必要です。

初回の基本手順は **①`setup-ai`を1回 → ②選んだproviderへ必要なら`login`を1回 → ③`run`** です。認証済みの通常利用は文書ごとの`run`だけでよく、`providers`／`models`はIDを調べたい場合だけの任意操作です。

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

help／setup／参照／認証の成功はexit code 0、入力・設定不足／依存不一致・未知provider／未対応login methodは2、導入・認証・保存・helper実行の失敗や取消は1です。引数なし・未知の操作も2で停止します。不足／不一致なら表示された`setup-ai --package-manager npm`（または`pnpm`）を明示実行してください。失敗を空の成功結果とは扱いません。

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

OAuth tokenのTTL判定・refresh・認証変換はpi-ai／providerに任せ、通常は保存credentialを再利用します。refresh失敗時にambient API keyや別の認証modeへ自動fallbackしません。OpenAI OAuthと実provider推論は自作小入力で確認しましたが、実token refreshは未観測です。ChatGPTサブスクリプションは共有利用枠であり、API課金や全modelの利用権、無制限利用を保証しません。

認証の入力・選択はcontrolling TTYで非echoにし、pipeからの秘密入力やechoへのfallbackは受け付けません。OAuth認証URL・device codeの確認URLがHTTP(S)の絶対URLなら、macOSの既定ブラウザで自動的に開きます。起動できない場合もterminalに表示したURLを手動で開いて継続できます。補助リンクは自動で開かず、ブラウザの起動だけでは認証成功になりません。認証URL・device codeの表示はterminalに限り、stdoutの成功JSONは`operation`・`provider`・login時の`method`だけです。URL・code・token・入力内容をログやterminal録画へ保存したり、Issueへ添付したりしないでください。helperのraw stderr／provider例外も出力しません。

Ctrl-C／SIGTERM／入力EOF等の取消ではchildを回収し、進行中のcredential保存を待つため強制kill前に最大30秒のgraceを設けます。入力中の未送信文字を破棄してechoを復元します。SIGKILLやdisk障害では保存・terminal復元を保証できません。破損JSONや保存失敗を空credential・成功とは扱わず、取消中の保存失敗もエラーにします。エラー時は保存先・権限・空き容量等を確認し、必要なら再loginしてください。秘密入りファイルの内容を診断ログに貼らないでください。

## 保存済み正本の再出力・状態確認

既存の原文snapshotを保存したworkspaceを指定します。訳文snapshotがある場合は保存時の入力対応を再検査して使います。新規文書からは後述の`run`でworkspaceを作成します。

```sh
doc-translate render DIR
doc-translate status DIR
```

両操作とも取得・補正・準備・翻訳を呼びません。Node・認証・PDF extra・OCR model・外部通信は不要です。`render`は日本語／閲覧用英語Markdown、相対参照assets、source map、QA、usage、summaryを生成し、stdoutのJSONにworkspace相対の成果物pathを表示します。元snapshot・旧出力は削除しません。

- `render`のexitは0＝指定された保存範囲が出力まで到達、1＝部分出力または保存等の実行失敗、2＝入力・hash・設定エラーです。訳文なし／部分失敗なら原文を残した部分出力とし、日本語側に`※`を付けます。現在原文と元snapshotが違う旧訳は自動流用しません。
- `status`は検査成功なら部分出力でも0、入力／hashエラーは2、その他の実行失敗は1です。`render_current: false`は未出力またはsummary全体が現在状態に未追随であることを示します。`adopted_output_current`は既存出力が採用原文・訳文の入力に対応し、訳文がstaleでないかを別に示します。どちらも内容品質の承認ではありません。旧成果物がある場合は`previous_artifacts`で場所を確認できます。
- `※`は表示用の目印だけです。理由・原本欠損か処理問題か・元位置・block／unit・出力anchorをQAで確認してください。コード／数式内部や保存済み原文／訳文には混ぜません。
- summaryは出力到達／未処理／失敗、機械検査、原文の内容確認記録と訳文未確認範囲、usageを区別します。PDFの所有範囲を全文とみなさず、`pages_with_source_blocks`は本文の位置だけを示します。内容品質承認ではありません。
- code・数式・図内文字は原文を保持します。単純表はMarkdown、結合表は安全なHTMLです。安全なMathML／画像を使い、表示不能な構造や危険なassetは不活性な原データ参照とQAを残します。外部画像は読込みません。
- usageは準備・補正・翻訳・reviewの保存済み全attemptを失敗／retry込みで分離します。未観測の利用量・全体wall time・実請求額は不明で、0や推計総額に置き換えません。今回のrender時間は別記録です。

HTML table・数式・MathMLの実表示はviewerに依存します。自作3文書をPandocでHTMLへ変換しChromeで限定確認しました。PDF数式はMathMLで表示され、EPUBの許可属性外のMathMLは安全判定を緩めず不活性な原データ参照とQAへ戻りました。他viewerでの表示は未確認です。hash不一致は正本・assetの改変や保存障害を調べ、旧出力を確認してください。破損を無視した再採用や自動LLM再送はしません。

## 文書を翻訳する（通常利用）

- PDF：文字付き・画像のみの英語技術PDF。Docling抽出を原本画像と照合して補正します。任意の実スキャンの完全復元は保証しません。
- EPUB：DRM解除不要のEPUB 2／3、整形式XHTMLのリフロー型。fixed-layout、画像主体、DTBook専用、複数renditionの自動選択、壊れたXML修復、画像OCRは対象外です。Docling・OCR・補正LLMは使いません。
- 出力：日本語Markdown、閲覧用英語Markdown、相対参照assets、原文との対応表、QA・利用量記録。PDF／EPUBの出力生成、同じ組版の再現、図内文字の翻訳は対象外です。

`PROVIDER`／`MODEL`は利用権のある正確なID、`DIR`は**まだ存在しない**workspaceへ置換してください。空directory・file・symlink（danglingを含む）も既存workspaceとして拒否します。自動上書き・自動install・別modelへのfallbackはありません。

```sh
# 初回setup・必要なloginを済ませた後は、文書ごとにこの操作だけ
# EPUBはPDF用model directory不要
doc-translate run own.epub --workspace work/epub-book \
  --provider PROVIDER --model MODEL --thinking off
# PDF：下記ローカル準備を済ませ、画像対応の補正modelを指定
doc-translate run own.pdf --workspace work/pdf-book \
  --provider PROVIDER --model TEXT_MODEL --thinking off \
  --correction-provider VISION_PROVIDER --correction-model VISION_MODEL \
  --correction-thinking high --artifacts-path /absolute/path/to/local-models \
  --pages 2-6 --reference-pages 7-7
```

各設定fieldの優先順位は`--correction-*`／`--translation-*` → 共通`--provider/--model/--thinking` → `PI_PROVIDER/PI_MODEL/PI_REASONING_LEVEL`です。準備は翻訳設定を使い、EPUBでは補正設定を使いません。不足・未知model・非対応thinking／画像能力は送信前に停止します。providerはOpenAI固定ではありません。ChatGPTサブスクリプションOAuthでの実認証・小範囲推論を確認済みですが、任意account／modelの利用権や意味品質を保証しません。

PDFの`--pages A-B`は1始まり・両端含む所有範囲です。省略時は実PDF全ページが所有対象になります。既定の送信許可も所有範囲内だけで、外側は`--reference-pages A-B`で明示したページだけ参照専用に許可します。補正は所有5ページごとに、許可範囲内の前後各1ページだけをcontextとして使います。離れた参照指定を含むローカル取得範囲は広くなることがありますが、それはproviderへの送信許可を増やしません。参照ページは翻訳対象に追加せず、部分範囲をPDF全文処理完了と表示しません。EPUBは文書全体を対象にし、ページ指定を受け付けません。

送信直前、stderrへ入力名・SHA-256・所有／参照の許可範囲・そのrequestの送信範囲と内容・段階・provider／model／thinking・実preflightの認証source／typeをJSONで表示してから送ります。tokenやcredentialは表示・workspace保存しません。画像／抽出下書きはPDF補正だけに送り、準備・翻訳には保存したunit／context／glossary等だけを送ります。実行コマンド自体が指定範囲の送信操作です。権利・機密性・送信先を事前に確認してください。

利用者指定の訳語は`--glossary FILE`で自動準備結果より優先できます。既存の採用glossary形式の小例：

```json
{"version":"1","book":"自作文書","style":"本文はですます調。","terms":{"transaction log":"トランザクションログ"}}
```

取り込むのは検証済み`terms`だけです。今回の採用`book`は入力fileのstem、`version`は`1`、`style`はrunの固定文体policy（本文はですます調、見出しは自然な体言止め可）から生成します。

runはstdoutにsummary JSON／workspace相対の成果物pathを返します。exit **0＝指定範囲の出力到達（要確認があっても0）、1＝処理失敗／部分出力、2＝入力・設定エラー**です。`processing.status`と未処理／失敗、`uncertainty_count`／QAを別々に確認してください。補正失敗時は成功chunk・原本／下書き／画像／attemptを残し、得られたsourceがあれば準備・翻訳の失敗時も原文を残した部分出力を試みます。sourceを1つも採用できなければMarkdownは作らず、保存済み原本・下書き等のpathを返します。`scope.run`は意図した所有／参照範囲と補正成功／失敗／未処理ページ、非秘密設定・エラー分類を保持します。

### 中断・失敗からの再開

```sh
doc-translate status ./work/book
doc-translate resume ./work/book
# 明示変更する場合だけ指定（段階別 > 共通 > 保存済み設定）
doc-translate resume ./work/book --glossary ./terms-v2.json --translation-thinking high
# 文体を変更する場合だけ、version/textのJSONを指定
doc-translate resume ./work/book --policy ./style-v2.json
# PDF取得をやり直す必要があり、保存済みlocal model pathが使えない場合
doc-translate resume ./work/book --artifacts-path /path/to/prepared-models
```

resumeは保存原本・範囲・非秘密設定を基準に、artifact hashと回答／入力対応を検査し、未処理・失敗・明示変更で無効になった部分だけを送信します。元の外部INPUTは不要です。取得済みPDFならlocal model directoryも不要で、EPUBにPDF依存は要求しません。完了済みで推論不要ならNode／認証も不要です。**resumeではambientな`PI_*`を設定変更として採用しません。** `--provider/model/thinking`と段階別指定、`--glossary`／`--policy`は明示変更として保存されます。policyは例えば`{"version":"2","text":"本文はですます調。見出しは体言止め。"}`というデータで、実行コードではありません。glossary／文体変更ではPDF取得／補正をやり直しません。補正設定の明示変更は再補正の対象になりますが、取得入力が同じならPDFを再取得しません。ページ範囲を拡大するoptionはありません。

SIGINT／SIGTERMは新規requestを止め、実行中helperの取消・認証保存drainを待ちます（強制killまで原則30秒）。SIGKILL・disk障害では応答・usageやOAuth refresh保存を保証できません。**送信済み応答を失うとusageは不明で、再送により重複課金される可能性があります。exactly-once課金は保証しません。** token保存に失敗した場合は必要に応じ再loginしてください。

旧原本・snapshot・attempt・出力は削除しません。保存途中の一時fileや未検証回答は成功として採用せず、確定artifactだけを再検証します。hash不一致は送信前に明示停止し、破損fileを上書きしません。別writerが稼働中ならresume／直接stage／renderは拒否され、process終了後はOS lockが解放されます。exit codeはrunと同じ0／1／2です。未処理／失敗や次の候補採用待ちが残れば部分出力のままです。

## 明示範囲を内容照合する（送信あり）

```sh
# IDsはstatusの出力／source mapにある現在のunit ID。comma-separatedで明示します
# split済みなら unit-1.part-1 等の現在のIDを指定します（親IDの自動展開はしません）
doc-translate review ./work/book --units unit-1,unit-2
# PDFだけ：指定ページ画像と、その範囲内の原文block／対応訳を照合します
doc-translate review ./work/book --pages 2-3
# この操作だけの送信先変更。保存済みrun／段階設定は書き換えません
doc-translate review ./work/book --units unit-1 --provider PROVIDER --model MODEL --thinking off
# 指摘をQAと表示用の※へ反映（推論なし）
doc-translate render ./work/book
```

- `--units`と`--pages`はどちらか1つが必須です。unit一覧は入力順でなく文書順に送ります。unitモードは選択した原文・対応訳・選択unit内のinline保護内容、表の行列情報、固定policy／glossaryだけを送り、他unit本文を含む祖先contextや文書全体context、ページ画像は送りません。
- PDFページモードは保存済み送信許可範囲・ページ画像内だけです。指定外の画像・抽出下書きは送りません。code／formula／図など訳文unitのないblockやページ画像だけにも位置付き指摘を残せます。block・table cell・unitが選択外に跨る場合は、全体本文／訳文を漏らしたり黙ってskip／範囲拡大したりせず、必要なページ範囲を示してexit 2で止まります。
- 現在の採用原文・準備・設定に対応する保存済み訳文が必要です。未知ID・未訳／staleの場合も送信せず、条件を表示します。必要な翻訳は利用者が別途`resume`してください。候補採用待ちだけを理由に拒否せず、有効な採用版のhashに結び付けます。
- 設定はこの操作の明示`--provider/--model/--thinking` > 保存済みの該当段階設定（unitsはtranslation、pagesはcorrection）。`PI_*`で保存設定は変わりません。該当設定がない場合は不足fieldを明示指定してください。別provider／modelへ自動fallbackしません。送信直前に原資料・source／translation hash、対象ID／ページ／画像、送信先と実認証source／typeをstderrへ表示します。
- stdoutのJSONに`review.status`・不変artifactのpath／hashと保存-only statusを返します。exit 0は範囲の照合記録が保存できたこと、1は推論／保存等の失敗、2は入力・範囲・設定エラーです。指摘があっても記録成功なら0です。元原文／訳文・採用参照は変更せず、修正は次の`revise`で明示します。
- findings・確認者（provider/model）・独立LLMという方法・範囲・usageを保持します。QA／statusの`content_review.translation_records`は現行source／translation hashに対応する記録だけ、`review_history`には旧版も残します。原文block／unitと訳文unitの未確認範囲を別記し、機械検査と内容確認は別です。空findingsやmodelの自己申告で`meaning_quality_approved`をtrueにしません。修正後の旧reviewは現行確認済みには数えません。
- `review`はQAの自動再出力をせず、`render_current: false`なら保存-onlyの`render`で反映できます。失敗・未採用の回答／usageも保存し、未観測usageを0としません。繰返すreviewは新しい独立呼出しです。応答／保存を失った後の再送による重複課金は保証できません。

## 原文・訳文を修正する（推論なし）

`revise`は保存済みworkspaceに対するオフライン操作です。原本PDF／EPUBやMarkdownを直接編集する操作ではなく、利用者が用意したJSONを検証して新しい版として採用します。Node・認証・PDF extra・model・外部通信は不要で、再翻訳を自動開始しません。旧版・変更理由・元の回答は保持します。

通常の原文修正は**全snapshot置換**です。`DIR/source-snapshot.json`の`sha256`を基点に、`DIR/snapshots/source/<そのhash>.json`の全体をコピーして編集し、次のenvelopeの`source`へobjectとして入れます（`{}`は差込み位置であり、空のままでは使えません）。hashは説明用の文字列でなく、参照fileの実際の64桁値に置換します。

```json
{
  "schema_version": 1,
  "base_source_sha256": "現在のsource参照のsha256",
  "source": {}
}
```

PDFではblock本文・構造・位置・根拠・assetsの整合を保ちます。EPUBの本文は`blocks[].structure.tree`内のnode内容が正本で、`blocks[].text`だけを直しても本文修正になりません。原本のhash／pathは変更しません。処理完了の自己申告で失敗ページを消すこともできません。

訳文修正は**変更unitだけのpatch**です。`DIR/translation-snapshot.json`のhashと`status`の`source_snapshot_sha256`を基点にします。`unit_id`・保護markerは現在の訳文snapshotの`chunks[].answer.translations[]`からコピーし、`text`を編集します。以下は保護markerのないunitの例です。

```json
{
  "schema_version": 1,
  "base_source_sha256": "現在のsource参照のsha256",
  "base_translation_sha256": "現在のtranslation参照のsha256",
  "translations": [{"unit_id": "unit-1", "text": "確認した訳文です。", "uncertainties": []}]
}
```

```sh
# source.json／translation-patch.jsonは上記の形式で利用者が用意したfile
# どちらか一方だけを実行
doc-translate revise ./work/book --source ./source.json --reason '原本と照合して原文を訂正'
doc-translate revise ./work/book --translations ./translation-patch.json --reason '訳語を確認して訂正'
# 原文修正後は必要な準備・翻訳・QA／出力を更新（必要な場合だけ送信あり）
doc-translate resume ./work/book
# 訳文だけの修正なら推論なしで再出力できる
doc-translate render ./work/book
```

成功はexit0、不正schema・古い基点・未知ID・不正参照等は採用前にexit2、保存等の実行失敗は1です。訳文patchは現在の原文・準備・設定に対応する訳文だけに使えます。staleなら先に`resume`し、現在の版からpatchを作り直してください。旧訳・手修正訳の再利用はIDだけでなく実際のrequest全体の一致で決まり、曖昧な分割／接合はしません。codeだけの変更で本文の翻訳入力が同じなら本文を再送しません。

### PDF補正候補の比較と明示採用

通常利用で手修正は必須ではありません。ただし、利用者が原文を訂正した後に不足ページが回復したり補正設定が変わったりして、新しい機械生成原文が取り込み済み基点と異なると、`resume`は**準備・翻訳の前で停止**します。自動併合・旧訂正の自動転写はしません。機械生成内容が変わらなければ訂正版を保持して続行します。

1. `doc-translate status ./work/book`の`source_adoption`を確認します。`H`は現在採用中の訂正版、`B`は取り込み済み機械基点、`C`は新候補です。それぞれの`path`／`sha256`、`old_inputs`／`new_inputs`、`page_changes`で範囲・設定・追加／再組立ページを確認し、**全snapshotと原本位置を照合**してください。同じ`pdf-N`／`unit-N`でも同じ内容とは限りません。
2. `envelope`のfileを作業用にコピーします。そのままの候補Cを採用するか、`source`全体へ必要な旧訂正を利用者が反映した統合版Kにするかを決めます。`base_source_sha256`と`candidate_sha256`は変えません。保管済みの候補・雛形は直接編集しません。
3. コピーを`revise --source`で理由付き採用し、`resume`します。

```sh
# HASHはstatusのcandidate_sha256に置換。表示されたenvelope pathを使う
cp ./work/book/candidates/source/HASH.envelope.json ./adopt-source.json
# 必要ならadopt-source.jsonのsourceを編集してKにする
doc-translate revise ./work/book --source ./adopt-source.json --reason '新補正と旧訂正を原本に照合して採用'
doc-translate resume ./work/book
```

表示の`path`／`envelope`はworkspace相対ですが、CLIの`DIR`／`--source FILE`等は実行時のcurrent directory基準です。上例のようにworkspaceを先頭に付けるか絶対pathを使ってください。`next_operation`は絶対path付きの実行例で、`--reason TEXT`の`TEXT`を実際の判断理由に置換します。編集したKを使う場合は`--source`もコピー先へ変更します。

採用待ちは`source_adoption.status: pending`、全体は`processing.status: partial`です。`resume`／`render`はexit1、検査成功の`status`はexit0、入力不正はexit2です。`status`／`render`はHだけを使い、Cを黙って出力しません。`scope.adopted_source`は採用済み範囲、`scope.run`は新補正の進捗です。新候補の全ページ成功は取込み・翻訳完了ではありません。採用版への出力が現在入力に対応していても、候補待ちやsummary更新待ちは別に残り得ます。

古い基点／候補・入力世代違いは拒否されます。通常の原文再修正だけでは候補採用になりません。再度`resume`して現在版に対応する雛形を確認し直してください。同じ入力の再確認では成功済み補正を再送せず、partial候補では未成功ページだけを再試行します。中断後もまず`status`→`resume`で再検証し、hash不一致は破損を調べてください。候補・旧snapshot・理由・元回答は削除しません。

## PDFのローカル準備

PDFのrunには以下のローカル取得準備が必要です。取得だけの公開Python API（[引数・保存契約](docs/reference/artifacts.md#pdf抽出下書きissue-7)）ならNode／provider認証／LLMは不要ですが、runでの補正・準備・翻訳には必要です。抽出下書きは正しい原文や補正済み原文ではありません。EPUBにはこの準備は不要です。

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

処理結果では、処理到達範囲・機械検査・内容確認範囲を区別します。要確認箇所は出力の`※`とQAで追跡し、出力ができただけで内容品質承認済みとはしません。

エラー時も元workspaceを保持し、summaryのpathからQA・attemptを確認してください。実書籍や秘密情報をIssueに添付しないでください。

ソースからの導入・開発・検証手順は[開発ガイド](CONTRIBUTING.md)を参照してください。
