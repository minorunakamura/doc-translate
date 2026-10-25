# 開発ガイド

対象はIssue #1の初期基盤です。全文機能の計画は[正式Plan](docs/plans/doc-translate-initial.md)、責務は[基本設計](docs/design/doc-translate.md)、現在の公開契約は[artifact契約](docs/reference/artifacts.md)を参照してください。READMEは利用者向けです。

## 構成・環境

- `src/doc_translate/`：PythonのCLI、原文snapshot、workspace OS lock、JSONL codec。
- `ai/src/`：TypeScriptのJSONL codec。`ai/tests/jsonl-process.mjs`は公開exportを使うテスト専用echo processであり、production推論helperではありません。
- `tests/fixtures/`：自作JSON。入力adapter、setup-ai、metadata、認証、generation、翻訳・出力・再開操作は未実装です。
- `scripts/check_distribution.py`：sourceと明示compile済みresource／wheelの一致、およびwheelからの別clean npm／pnpm導入を検査します。runtime用installerではありません。

実動基準：macOS 26.7.1 arm64、Python 3.14.9、Node 24.21.0、pnpm 12.10.1、npm 11.19.0。CIはGitHub-hosted macos-15 ARM64、Python 3.14／Node 24です。別OSやmacOSの最低versionは未保証です。

## 正式チェック（順序を含む）

必要なtoolは事前に導入します。以下の依存installだけはregistry接続を使います。model重み・認証・LLMは不要です。

```sh
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
pnpm --dir ai install --frozen-lockfile --ignore-scripts
pnpm --dir ai build
uv sync --locked --dev
pnpm --dir ai exec biome format . --error-on-warnings
pnpm --dir ai exec oxlint --type-aware --type-check --deny-warnings .
pnpm --dir ai exec vitest run
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
uv run doc-translate --help
```

Python packageは`ai/dist`を同梱するため、fresh checkoutでは`uv sync`より先にhelperの明示buildが必要です。pytestのprocess契約もcompile済み公開`@doc-translate/ai/jsonl`を使います。Python側はbinary file行iterator、Node側は任意binary chunkのcodecです。警告をquiet／包括的disableで隠さず修正します。

PDF extraはcore-onlyと区別して確認します。

```sh
uv sync --locked --dev --extra pdf
uv run python -c 'from docling.document_converter import DocumentConverter; from docling.datamodel.pipeline_options import OcrMacOptions; from docling_core.types.doc import DoclingDocument; from ocrmac import ocrmac; print(OcrMacOptions(lang=["en-US"], framework="vision", recognition="accurate"))'
uv run pytest
```

これは依存解決・import確認であり、実Docling変換、Apple Vision OCR、重みを使うlayout／table処理の成功を意味しません。実OCR／LLM検証は後続Issueで明示承認・local model準備後に行い、未実行を成功にしません。

## wheelの明示buildと導入検査

```sh
pnpm --dir ai build
uv build
uv run python scripts/check_distribution.py dist/doc_translate-0.1.0-py3-none-any.whl
```

この検査は一時directoryで再compileして`ai/dist`と比較し、wheel内のdist／manifest／両lockをbyte比較します。wheel resourceだけを取り出した別々のclean directoryで、次を実行します（依存packageのregistry取得あり）。

- npm：`ci --omit=dev --ignore-scripts --no-audit --no-fund`
- pnpm：`install --prod --frozen-lockfile --ignore-scripts`

続くoffline smokeはNodeだけをPATHに置き、source treeのnode_modules／Pi CLIなしで公開codecのUTF-8分割byte round-trip・末尾不正の拒否とpi-ai／proper-lockfileのimportを確認します。provider catalog呼出しや推論はしません。依存不足を自動取得するruntime hookはありません。wheelにはhelper sourceやnode_modulesを含めません。sdistは明示build済みdistと再build用source／manifest／lockを含みます。

Python側の別環境確認（repository rootで実行、`TMP`は新規一時directory）：

```sh
TMP=$(mktemp -d)
WHEEL="$PWD/dist/doc_translate-0.1.0-py3-none-any.whl"
uv venv --python 3.14 "$TMP/core"
uv pip install --python "$TMP/core/bin/python" "$WHEEL"
(cd "$TMP" && core/bin/doc-translate --help)
(cd "$TMP" && core/bin/python -c 'import importlib.util; import doc_translate.snapshot, doc_translate.jsonl; assert importlib.util.find_spec("docling") is None')
uv venv --python 3.14 "$TMP/pdf"
uv pip install --python "$TMP/pdf/bin/python" "$WHEEL[pdf]"
(cd "$TMP" && pdf/bin/python -c 'from docling.document_converter import DocumentConverter; from docling.datamodel.pipeline_options import OcrMacOptions; from docling_core.types.doc import DoclingDocument; from ocrmac import ocrmac')
```

core-onlyでEPUB用位置のsnapshot保存・読込みはpytestで確認しています。EPUB取得経路はまだありません。PDF extraの別環境importはmodel実行ではありません。

## lock・依存更新とlicense

Pythonは`pyproject.toml`変更後に`uv lock`、Nodeは共通`ai/package.json`を変更して`pnpm --dir ai install --lockfile-only --ignore-scripts`と`npm --prefix ai install --package-lock-only --ignore-scripts --no-audit --no-fund`で両lockを更新します。依存更新はregistry取得の許可範囲内で行います。既存node_modulesへ別managerを上書きせず、上のwheel clean install検査を両managerで再実行してください。両managerの推移依存が完全一致するとは仮定しません。

現在の主依存：Pydantic／Docling／OCRMac／pi-ai／proper-lockfileはMIT、PillowはMIT-CMU。推移依存にはcertifiのMPL-2.0、Torchの複合license、Node側のMIT／Apache-2.0／BSD等があります。lockは解決versionの記録でありlicense承認の代替ではありません。今回のnative tool確認はmacOS arm64に限定しています。更新時は実際の配布物のlicense／NOTICEとnative条件を再確認します。Apple VisionのOS条件やmodel重みをpackageのMITに含めません。重みは未取得・未同梱です。project全体のlicense宣言・releaseは今回の対象外です。

## fixture・CIの境界

fixtureは自作の小文書／人工応答だけを使い、書籍の切出し、実credential、token、重みを追加しません。`sample/`はローカル資料としてignoreします。テストはofflineで、依存installだけをネットワーク処理と区別します。Hub／Transformersのoffline設定は他の全通信まで遮断するfirewallではありません。実provider送信・大型取得は別途個別承認が必要です。

CIはpush／pull_requestで上記checksとwheel導入を実行する設定です。secrets、pull_request_target、continue-on-errorは使いません。workflowを公開して起動した後、実行refとrun URLを記録し、remote成功を確認してください。設定追加だけではremote CI受け入れ完了ではありません。§18の過去実験はpi-ai直接経路の検証実績ではありません。
