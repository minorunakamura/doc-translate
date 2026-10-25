# 初期artifact／JSONL契約（schema version 1）

Issue #1／#2／#3の実装済み境界を記述します。[基本設計](../design/doc-translate.md)の全成果物を実装済みとする文書ではありません。原資料・翻訳unit／訳文・policy／glossary／context snapshot、attempt／usage／QA集計、manifest、修正JSONの公開CLIは後続Issueです。現時点で架空の修正envelopeやgeneration operationは定義しません。認証operationは以下に実装済みの語彙を記述します。

## 原文snapshot

公開Python APIは`doc_translate.snapshot.SourceSnapshot`と、`save_source_snapshot(workspace: Path, snapshot: SourceSnapshot) -> str`、`load_source_snapshot(workspace: Path, sha256: str | None = None) -> SourceSnapshot`です。返り値のdigestは保存byte列のSHA-256です。省略時は採用参照を読みます。明示digestなら旧版を読み込めます。

すべてのmodelは余分なfieldと非有限数値を拒否します。位置unionは`format`をdiscriminatorとし、文書形式と一致させます。

| field | 現在の契約 |
| --- | --- |
| `schema_version` | `1` |
| `snapshot_id` | 空でないsnapshot識別子。artifact hashとは別 |
| `previous_snapshot_sha256` | 旧snapshotの64桁小文字hex、初版はnull。指定時は空でない`change_reason`必須 |
| `change_reason` | 変更理由またはnull |
| `document` | `format: pdf | epub`、`sha256: 64桁小文字hex`、`artifact: 空でない参照文字列` |
| `blocks` | 原文block配列。配列順が読み順。block IDはsnapshot内で一意 |
| `uncertainties` | 理由・対象・位置・工程・原本欠損か処理問題かを分離して保持（既定空） |
| `processing` | `stage`（非空）、`status: not_processed | completed | failed` |
| `checks` | 機械検査結果配列（既定空） |
| `content_reviews` | 内容確認者・方法・範囲の配列（既定空）。処理完了と別 |

blockは`block_id`／`kind`（非空）、`text`、1件以上の`locations`、`structure`（JSON object、既定空）を持ちます。`structure`は見出し／list階層、table cell／span、caption／脚注／注釈、assets、数式、inline等を保持するためのJSONです。**現在はkindごとの意味、参照先の存在、構造の整合性を検査しません。** adapter実装とともに具体化します。構造objectを保存できたことを抽出・補正・coverage検査の成功にしません。

`uncertainties`の要素は`reason`（非空）、`target_ids`（非空文字列の配列）、`locations`、`stage`（非空）、`origin: original | processing | undetermined`です。`checks`は`name`（非空）／`target_ids`／`passed`（bool）／`details`（既定空文字列）、`content_reviews`は`reviewer`／`method`／`details`（いずれも非空）と`target_ids`です。対象IDの意味検証・内容品質認定はまだ行いません。

### PDF位置

`format: pdf`と文書内1始まり`page`を必須とします。`extraction_ref`と`extraction_snapshot`は両方指定するか両方省略します。抽出refなしで画像から復元した内容は、空でない`evidence`に見える位置・理由を残します。架空のrefは不要です。

任意の`bbox`は`left/top/right/bottom`、`origin: top-left | bottom-left`、`unit`、`reference_image`、`rendering`（JSON object）を伴います。`unit`と`reference_image`は非空です。座標変換・実画像の存在・幾何学的整合性は将来のadapterが検査します。

### EPUB位置

`format: epub`、非空`resource_path`を必須とし、`element_id`または`node_path`＋`node_snapshot`を持ちます。node参照の2fieldは必ず一緒に指定します。ページ・bbox・Docling refは要求しません。resource pathはこのmodelでは参照文字列として保存するだけで、archive pathの安全性はEPUB読込み境界で別に検査します。

最小EPUB例（自作の人工原文、hashは説明用）：

```json
{
  "schema_version": 1,
  "snapshot_id": "source-1",
  "document": {"format": "epub", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "artifact": "original/book.epub"},
  "blocks": [{"block_id": "b1", "kind": "paragraph", "text": "Hello.", "locations": [{"format": "epub", "resource_path": "text/ch1.xhtml", "element_id": "p1"}]}],
  "processing": {"stage": "source", "status": "completed"}
}
```

同じblockのPDF例では文書の`format`を`pdf`、`artifact`を`original/book.pdf`、位置を`{"format":"pdf","page":1,"extraction_ref":"#/texts/0","extraction_snapshot":"draft-1"}`にします。両形式の実fixtureは`tests/fixtures/`を参照してください。

### 保存・採用・lock

```text
WORKSPACE/
  .writer.lock
  source-snapshot.json           # {"schema_version":1,"sha256":"…"}
  snapshots/source/<sha256>.json  # 不変の原文snapshot
```

保存はmodel再検証→UTF-8 JSON byte列のhash→OS lock取得→同filesystemの一時fileにwrite／flush／fsync→atomic replace／directory fsync→採用参照のatomic更新の順です。artifact確定後に参照を切り替え、旧版を削除しません。既存digestはhashとschemaを検査します。loadもhashとschemaを検査し、不一致を採用しません。原資料本体の取得・コピー・hash照合は本APIの責務ではありません。

`doc_translate.workspace.workspace_lock(Path)`は非blockingのPOSIX flockでwriter重複を拒否します（`WorkspaceLockedError`）。`save_source_snapshot`は自身でlockを取るため、その外側から同じlockを二重取得しません。lock fileの残存は所有を意味せず、process終了でOS lockが解放されます。参照切替前に中断したartifactが残っても、自動で採用しません。一般的なworkflowのresume／attempt復旧は未実装です。

## Python↔Node JSONLの公開codec

Python：`doc_translate.jsonl.JSONLRecord`／`encode_record`／`read_records`。Node：`@doc-translate/ai/jsonl`（`ai/dist/jsonl.js`）の`JSONLRecord`型、`encodeRecord`／`readRecords`。両者が検査する最小envelopeは次の**4fieldのみ**です。

```json
{"schema_version":1,"request_id":"r1","type":"echo","payload":{"text":"日本語"}}
```

- `schema_version`は数値1。booleanのtrueを1とみなさず拒否します。JSON numberの`1.0`と`1`の表記差は要求しません。
- `request_id`と`type`は空でない文字列、`payload`はJSON object（array／null不可）。余分なfield、欠落、非有限数値（NaN／Infinity）を拒否します。
- `payload`内の整数値は、ネストしたobject／arrayも含め、JavaScriptのsafe integer範囲`-9007199254740991`〜`9007199254740991`に限定し、Python／Nodeのencode／decode両方で範囲外を拒否します。整数値を表す有限float（例：`9007199254740992.0`）も同じ制限です。非整数の有限floatや文字列中の長い数字は対象外で、この制限を原文snapshotの`structure`等へ適用しません。
- UTF-8、1recordにつき末尾LF（byte `0x0A`）必須。空行、壊れたJSON、不正UTF-8、未完了末尾を拒否します。U+2028／U+2029はrecord境界ではなく文字列dataです。
- Python `read_records`は**binary file等の行iterable**を受け取ります。各要素がLFで終わる1recordであり、任意chunkの再構成はしません。
- Node `readRecords`は同期／非同期の**任意binary chunk iterable**を受け取ります。UTF-8の途中やLF直前で分割されても再構成します。
- encoderは検証後の1recordをUTF-8＋LFで返します。codecはoperation dispatch、認証、LLM成功判定を行いません。metadata／認証のoperation語彙は以下で定義します。generationは後続Issueです。例のechoはproduction operationではありません。

`node ai/tests/jsonl-process.mjs --byte-chunks`は実際の公開codecをimportするテスト専用processです。Python processテストは日本語・U+2028／2029・分割byte・不正末尾を確認し、inlineの別codecで代用しません。実provider／model／credentialは使用しません。

## metadata helper（Issue #2）

compile済みESM `dist/main.js`をNodeで起動し、stdinへLF-terminated requestを渡します。stdoutは同じ`schema_version: 1`／`request_id`のresponseです。CLIは1 process／1 requestで、正常終了・responseが1件・request ID一致・result payload schemaとversion一致をすべて確認します。stderrや全model定義をそのまま利用者へ返しません。

| request type | payload | 正常response type／payload |
| --- | --- | --- |
| `providers` | `{}` | `result`、`helper_version`、`library_version`、`providers: [{id,name,login_methods}]` |
| `models` | `{"provider":"anthropic"}` | `result`、`helper_version`、`library_version`、`provider`、`models: [{id,name,image_input,reasoning,thinking_levels}]` |

`builtinModels()`で登録したcollectionの同期`getProviders()`／`getProvider()`／`getModels()`だけを読みます。`login_methods`はinteractive login可能なmethodだけを`api_key`、`oauth`の順で列挙する配列です。`auth.apiKey.login`があれば`api_key`、`auth.oauth`があれば`oauth`を含めます。ambient-only providerは空配列であり、認証不要・未対応という意味ではありません。provider公式の環境変数・profile等の認証手順を使い、loginを呼びません。認証の可用性・credentialの存在・利用権を調べるfieldではありません。対象はchat modelのbuilt-in catalogです。`image_input`は画像入力対応のbooleanであり画像生成ではありません。`thinking_levels`は`off/minimal/low/medium/high/xhigh/max`の順で、reasoning非対応なら`off`のみ。reasoning対応ではmapのnullを除き、省略された標準levelはprovider既定、`xhigh/max`は明示mapがある場合だけ対応として表示します。実requestのthinking実効値の保証ではありません。

未知providerは`error`／`{"code":"unknown_provider"}`、未対応type・余分なpayload field・不足payloadは`error`／`{"code":"invalid_request"}`でexit 2です。不正envelope／不完全LFはstderrの固定エラーでexit 2、成功はexit 0です。metadata操作はcredentialを読まず、認証・refresh・generation・document送信をしません。

例：

```json
{"schema_version":1,"request_id":"r1","type":"providers","payload":{}}
```

## 認証helper（Issue #3）

同じ`dist/main.js`を1 process／1認証操作で起動します。全recordは上記envelopeと同じrequest IDを使い、stdin／stdoutのJSONLはログや文書artifactへ保存しません。認証URL・device codeを含む通知は秘密に準じて扱います。CLIのstdoutは成功時の非秘密result payloadのみです。

| 方向／type | payload |
| --- | --- |
| Python→Node `login` | `{"provider":"openai","method":"oauth"}`。methodは`oauth`または`api_key`で、当該providerの`login_methods`にあるものだけ |
| Python→Node `logout` | `{"provider":"anthropic"}` |
| Node→Python `prompt` | `{"prompt_id":"1","prompt":{...}}`。promptは`type`（`text`／`secret`／`select`／`manual_code`）、`message`。text／secret／manual_codeは任意`placeholder`、selectは`options: [{id,label,description?}]` |
| Python→Node `response` | `{"prompt_id":"1","value":"人工入力"}`。selectのvalueはoption ID。他は入力文字列であり秘密を含み得る |
| Node→Python `prompt_cancel` | `{"prompt_id":"1"}`。callback完了や取消で保留promptを終了。行を入力しなくても復帰する |
| Node→Python `notify` | `{"event":{...}}`。eventは以下のpi-ai公開認証通知 |
| Node→Python `result` | loginは`{"operation":"login","provider":"openai","method":"oauth"}`、logoutは`{"operation":"logout","provider":"anthropic"}`のみ。credentialは返さない |
| Node→Python `error` | `{"code":"storage_failed"}`等。codeは`invalid_request`／`unknown_provider`／`unsupported_method`／`cancelled`／`auth_failed`／`storage_failed`のいずれか。raw例外やstderrを返さない |

notify eventは`info`（`message`、任意`links: [{url,label?}]`）、`progress`（`message`）、`auth_url`（`url`、任意`instructions`）、`device_code`（`userCode`、`verificationUri`、任意`intervalSeconds/expiresInSeconds`）です。Pythonは認証通知をcontrolling TTYにのみ表示します。promptの`signal`はNode側で取消に使用し、wireへ送信しません。入力・選択はすべてTTYの非echo入力とし、pipe入力やechoへのfallbackはありません。`response`がcallback取消と交差した場合、古いprompt IDへの応答はNodeで無視します。

正常resultと正常終了の両方で成功（exit 0）とし、CLIは要求operation／provider／methodとの完全一致も確認します。未知provider・未対応method・不正requestはexit 2、それ以外の認証・保存失敗／取消はexit 1です。不正JSONL、ID不一致、未完了末尾、秘密fieldを足したresult等を成功扱いせず、raw dataは表示しません。

SIGINT／SIGTERM／stdin EOF／stdout pipe切断はhelperのAbortSignalへ伝えます。prompt取消・終了時にPythonは入力queueをflushしてechoを復元します。Nodeは進行中のstore操作を`drain`してからresult／errorを返し、保存失敗は取消より優先して`storage_failed`とします。Pythonも取消後のhelper終了出力から同一requestの`error/{"code":"storage_failed"}`を確認し、非秘密の保存失敗・再login案内を表示します。childを回収し、強制kill前に30秒のgraceを確保します。SIGKILLやdisk障害では保存・terminal復元を保証しません。

### アプリ所有credential（workspace外）

`FileCredentialStore(directory?)`の公開操作は`read/list/modify/delete/getDeviceId/drain`です。既定directoryは`$XDG_CONFIG_HOME/doc-translate/`（未設定・空なら`~/.config/doc-translate/`、0700）。provider IDをkeyとする`credentials.json`と、OpenAI OAuth loginで初回作成・再利用するUUID v4の`installation-id`を0600で置きます。Pi CLIのauth.json等は読込・コピーせず、workspace／exportへもコピーしません。

credentialはpi-aiの`api_key`（任意`key`／provider環境設定`env`）または`oauth`（`refresh/access/expires`と追加field）です。`list`はprovider ID／typeのみ。proper-lockfileの共通identityでファイル全体のread→callback→atomic保存／deleteとUUID作成を排他します。`modify` callbackのundefinedは変更なし、追加OAuth fieldは保持、callback開始後の保存はcaller取消から保護します。`drain`は進行中操作を待ち、保存失敗を通知します。破損JSON／UUID・保存失敗を空扱いせず、秘密を含まないエラーと必要な再login案内で停止します。logoutは指定providerの保存credentialだけを削除し、他provider・UUID・ambient環境認証は変更しません。

login・OAuth TTL判定／refresh・API認証変換はpi-ai／provider所有です。OpenAI OAuth loginは`getDeviceId`と`agentName: "doc-translate"`を渡します。保存OAuthのrefresh失敗後にambient API keyや別modeへfallbackせず、旧credentialを勝手に空にしません。pi-ai 1.4.0の進行中refreshはcaller取消から切り離した15秒timeoutで保存を保護します。人工store／provider／実process・PTYでのoffline検証であり、実認証・実token refresh・推論の成立を示すものではありません。generation operationは未実装です。

## アプリ所有runtimeの導入記録

`setup-ai --package-manager npm|pnpm`はinstalled managerだけを起動し、選択lockとcompile済みresourceを新しいruntimeへコピーします。`~/.local/share/doc-translate/ai/`（`XDG_DATA_HOME`指定時はその下）の`MANAGER-LOCK_HASH_PREFIX-HELPER_VERSION-.../`ごとに別node_modulesを使います。runtimeごとの`runtime.json`は`package_manager`、`package_manager_version`、`lock_sha256`、`helper_version`、`helper_sha256`、`library_version`を記録します。helper hashはmanifestとdistのpath／byteを含みます。認証情報は含みません。

依存導入・固定version検査・実helper catalog検査が成功してから`current` symlinkをatomic replaceします。通常参照は`current`の記録と同梱resource／選択lock／直接production依存versionを照合してからNodeを起動します。不足・不一致はsetup案内で停止し、自動installや実行時compileはしません。失敗したsetupは既存runtimeを変更せず、旧runtime・失敗directory・workspace・credentialを削除しません。推移依存tree全byteの改変検知までは行わず、helper import／process失敗も成功扱いしません。
