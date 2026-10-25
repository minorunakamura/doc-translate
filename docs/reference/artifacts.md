# 初期artifact／JSONL契約（schema version 1）

Issue #1の実装済み境界を記述します。[基本設計](../design/doc-translate.md)の全成果物を実装済みとする文書ではありません。原資料・翻訳unit／訳文・policy／glossary／context snapshot、attempt／usage／QA集計、manifest、修正JSONの公開CLIは後続Issueです。現時点で架空の修正envelopeやhelper operationは定義しません。

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
- encoderは検証後の1recordをUTF-8＋LFで返します。codecはoperation dispatch、認証、LLM成功判定を行いません。`type`のoperation語彙とpayload schemaは後続Issueで定義します。例のechoはproduction operationではありません。

`node ai/tests/jsonl-process.mjs --byte-chunks`は実際の公開codecをimportするテスト専用processです。Python processテストは日本語・U+2028／2029・分割byte・不正末尾を確認し、inlineの別codecで代用しません。実provider／model／credentialは使用しません。
