# 開発ガイド

対象はIssue #1の初期基盤、Issue #2のsetup-ai／offline metadata、Issue #3の認証store／login・logout、Issue #4の再利用可能な推論adapter、Issue #5のEPUB安全読込み境界です。全文機能の計画は[正式Plan](docs/plans/doc-translate-initial.md)、責務は[基本設計](docs/design/doc-translate.md)、現在の公開契約は[artifact契約](docs/reference/artifacts.md)を参照してください。READMEは利用者向けです。

## 構成・環境

- `src/doc_translate/`：PythonのCLI、アプリ所有Node runtime導入／検査、TTY認証UI（`auth.py`）、推論adapter（`inference.py`）、EPUB安全reader（`epub.py`）、原文snapshot、workspace OS lock、JSONL codec。
- `ai/src/`：TypeScriptのJSONL codec、非秘密metadata／認証／generation helper（`dist/main.js`）、`AuthDialog`、`FileCredentialStore`、独立推論のpreflight／stream接続（`generation.ts`）。`ai/tests/`の`.mjs`は公開APIとcompile済みhelperを使うテスト専用processです。
- `tests/fixtures/`：自作JSONと外部認証fake／PTY process、推論protocolの故障注入proxy。EPUB readerの人工ZIPは`tests/test_epub.py`で生成します。本文block抽出、PDF入力adapter、文書pipeline、翻訳・出力・再開のCLI操作は未実装です。
- `scripts/check_distribution.py`：sourceと明示compile済みresource／wheelの一致、および別venvにinstallしたwheel CLIからのclean npm／pnpm setupとNode-only catalog smokeを検査します。runtime導入は公開`setup-ai`自身が行います。

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

Python packageは`ai/dist`を同梱するため、fresh checkoutでは`uv sync`より先にhelperの明示buildが必要です。既存editable環境で同梱resourceを更新する場合はbuild後に`uv sync --locked --dev --reinstall-package doc-translate`を使います。pytestのprocess契約もcompile済み公開`@doc-translate/ai/jsonl`を使います。Python側はbinary file行iterator、Node側は任意binary chunkのcodecです。警告をquiet／包括的disableで隠さず修正します。

`ai/tsconfig.build.json`の`skipLibCheck`はpi-ai 1.4.0配布declarationのJSON import属性欠落と、@google/genaiのoptional MCP SDK型参照不足に限定した互換性対応です。依存declaration自体の型検査は省きますが、自作sourceの型検査とoxlintのtype-aware／type-check／warning 0は維持します。実helperと両managerのclean installでruntime互換性を別途検査します。

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

この検査は一時directoryで再compileして`ai/dist`と比較し、wheel内のdist／manifest／両lockをbyte比較します。core-onlyの別venvへwheelをinstallし、workspace外の別々のclean data directoryで実CLI `setup-ai --package-manager MANAGER`を実行します（依存packageのregistry取得あり）。内部で使用する固定コマンドは次のとおりです。

- npm：`ci --omit=dev --ignore-scripts --no-audit --no-fund`
- pnpm：`install --prod --frozen-lockfile --ignore-scripts`

続くoffline smokeはNodeだけをPATHに置き、source treeのnode_modules／Pi CLI／managerなしで実CLI `providers`／`models --provider anthropic|google`、公開codecのUTF-8分割byte round-trip・末尾不正の拒否とpi-ai／proper-lockfileのimportを確認します。認証・refresh・推論はしません。Vitestの実helper processはnetwork transportを拒否した状態でもcatalogを参照します。pytestは公開CLI→runtimeとPython↔実Node helper境界を検査し、package manager／失敗process境界だけをfake化します。依存不足を自動取得するruntime hookはありません。wheelにはhelper sourceやnode_modulesを含めません。sdistは明示build済みdistと再build用source／manifest／lockを含みます。

Python側の別環境確認（repository rootで実行、`TMP`は新規一時directory）：

```sh
TMP=$(mktemp -d)
WHEEL="$PWD/dist/doc_translate-0.1.0-py3-none-any.whl"
uv venv --python 3.14 "$TMP/core"
uv pip install --python "$TMP/core/bin/python" "$WHEEL"
(cd "$TMP" && core/bin/doc-translate --help)
(cd "$TMP" && core/bin/python -c 'import importlib.util; import doc_translate.snapshot, doc_translate.jsonl, doc_translate.inference, doc_translate.epub; assert importlib.util.find_spec("docling") is None')
uv venv --python 3.14 "$TMP/pdf"
uv pip install --python "$TMP/pdf/bin/python" "$WHEEL[pdf]"
(cd "$TMP" && pdf/bin/python -c 'from docling.document_converter import DocumentConverter; from docling.datamodel.pipeline_options import OcrMacOptions; from docling_core.types.doc import DoclingDocument; from ocrmac import ocrmac')
```

core-onlyでEPUB用位置のsnapshot保存・読込みと、`read_epub(Path)`による安全読込み・package／spine／fallback／URI解決をpytestで確認しています。`uv run pytest tests/test_epub.py`は人工ZIPでpath・ZIP／XML／fallback上限・entity／DOCTYPE・固定layout・暗号化／font難読化・対象外本文の受理／拒否を検証します。namespace展開名の長さ／累積量・単一tagの属性数、深いpathと祖先衝突の区切り、申告size＋prefix CRCによるstored／deflate切捨て・deflate終端・data descriptor／空member、複数font／manifest alias／暗号化recordも公開`read_epub`経由で検査します。性能回帰は固定の時間閾値やprivate helper mockを使わず、祖先prefix再構築と暗号化recordごとのmanifest全走査を避ける実装と公開受理／拒否の回帰で確認します。原byteを保持するreaderまでで、本文block抽出・snapshot生成・CLI接続は未実装です。Node／Docling／OCR・外部取得は不要です。画像主体の一般判定は保証せず、本文textがなく画像のみのspineを停止する範囲です。APIと固定上限は[artifact契約](docs/reference/artifacts.md)を参照してください。PDF extraの別環境importはmodel実行ではありません。

## 認証の実装・検証境界

公開storeは`FileCredentialStore(directory?)`の`read/list/modify/delete/getDeviceId/drain`です。既定directoryは`$XDG_CONFIG_HOME/doc-translate`（未設定・空なら`~/.config/doc-translate`）。directoryは0700、credential JSON／installation UUIDは0600です。proper-lockfileの共通identityでファイル全体のread→callback→atomic保存／deleteとUUID初回作成を排他します。`modify`のundefinedは変更なし、OAuthの追加fieldは保持、`list`はprovider／typeだけです。壊れたJSON・保存失敗を空扱いしません。

login／refresh／TTL判定・認証変換はpi-ai 1.4.0の公開APIに任せます。OpenAI OAuthは安定UUIDと`agentName: "doc-translate"`を渡します。`providers[].login_methods`は公開interactive loginの有無だけを示し、ambient-onlyにlogin methodを作りません。OAuth refresh失敗後のAPI key fallback／request overrideは追加しません。

Pythonの`authenticate`はCLIから実helperへJSONLを送り、controlling TTYだけで非echo入力し、認証URL・device codeをterminalへ表示します。認証record／入力／URL・code／raw stderrやprovider例外をログへ残しません。SIGINT／SIGTERM／EOFで取消し、prompt取消・終了で入力queueをflushしてechoを復元します。進行中のstore callback／保存は取消から保護し`drain`、Pythonは強制kill前に30秒待ってchildを回収します。取消後の`storage_failed`も非秘密の保存エラーと再login案内を表示します。SIGKILL／disk障害時の保存・TTY復元は保証できません。

承認済みの公開seamでTDDを行います。`ai/tests/credentials.test.ts`は実storeと公開`createModels/createProvider/getAuth`で同時refresh・失敗時のno fallback・保存維持を検査し、Nodeの実processでも排他・heartbeat・UUID・drainを確認します。`ai/tests/auth.test.ts`は公開provider／実helper境界、`tests/test_auth_cli.py`は公開CLI・実helper／外部fake・PTYで非echo、callbackによるprompt取消、SIGINT／SIGTERM／EOF、未送信secretの破棄、取消中の保存失敗とchild回収を検査します。実credentialを読み込まず、人工credentialとnetwork拒否で検証します。実認証・実token refresh・推論・文書送信は未検証で、別途個別承認が必要です。

## 推論adapterの実装・検証境界

`doc_translate.inference.generate`は文書workflowとは独立したPython APIです。段階指定→共通指定→`PI_PROVIDER/PI_MODEL/PI_REASONING_LEVEL`で設定を解決し、文書を含まないpreflightで正確なmodel／thinking・画像能力・認証sourceを確認してから、明示policy／text／context／glossaryと必要なPNGだけを送ります。各試行は新しいNode processで、Pi CLI設定・履歴・tools・探索・agent loopを使いません。`run/resume`等のCLIは追加していません。APIの型・固定code・JSONLは[artifact契約](docs/reference/artifacts.md)を参照してください。

Pythonは回答JSONを別に解析してcallerのPydantic model（domain validatorを含む）で検証します。ID／根拠・送信許可範囲・座標説明の意味検証、artifact保存・採用は後続の文書callerが所有します。画像は完全decode・実寸・hash等を検証して元byte列と順序を保持し、縮小・再encode・保存しません。公表入力上限超過は`input_limit`で停止します。`inputLimits.images.resize.maxBytes`はraw PNGではなくcanonical base64 payloadのASCII byte量に適用します。

成功にはNode terminal／stop reason・送信先・toolCallなし・Python schema検証・正常process終了がすべて必要です。workflow retryは最終`generation_failed`のlibrary heuristicによるretryableなerrorだけに初回込み最大3試行・待機3／6秒です。schema／認証／保存／timeout／取消はretryしません。libraryへ`maxRetries:0`を渡しますが、内部retryは観測できないため不明のままです。未観測usageも0ではなく不明とします。timeout／取消はchildへ伝え、stream終了を最大30秒待ち、協調的providerのterminal／最終message／usageを失敗のまま保持します。終了すれば直ちに待機を終え、credential refresh／保存のdrainも維持します。親は原則30秒のgraceでchildを回収し、非協調providerのterminal／usageは保証しません。drainで上書きされた失敗は、保持されたretry metadataだけを理由に再試行しません。

`ai/tests/generation.test.ts`と`generation-process.mjs`は公開fauxProvider／createProviderと実HTTP adapter＋injected fetchで実効入力・terminal・usage・retry設定・取消／refresh drainを検査します。`tests/test_inference.py`は実helperを使ってPython側の独立validation・domain validator・有限retry・画像保持・timeout／signal取消を検査し、`ai/tests/inference-provider.mjs`は人工認証／取消process、`tests/fixtures/inference_process.py`はprotocol故障注入だけを担当します。全Node fixtureは上記Biome／oxlintの対象です。実provider通信・実credential・実token refresh・account利用権・課金・翻訳品質・全adapterの内部retry回数は未検証です。offlineテストやwheelのimport成功をこれらの成功と扱いません。

## lock・依存更新とlicense

Pythonは`pyproject.toml`変更後に`uv lock`、Nodeは共通`ai/package.json`を変更して`pnpm --dir ai install --lockfile-only --ignore-scripts`と`npm --prefix ai install --package-lock-only --ignore-scripts --no-audit --no-fund`で両lockを更新します。依存更新はregistry取得の許可範囲内で行います。既存node_modulesへ別managerを上書きせず、上のwheel clean install検査を両managerで再実行してください。両managerの推移依存が完全一致するとは仮定しません。

現在の主依存：Pydantic／Docling／OCRMac／pi-ai／proper-lockfileはMIT、PillowはMIT-CMU。推移依存にはcertifiのMPL-2.0、Torchの複合license、Node側のMIT／Apache-2.0／BSD等があります。lockは解決versionの記録でありlicense承認の代替ではありません。今回のnative tool確認はmacOS arm64に限定しています。更新時は実際の配布物のlicense／NOTICEとnative条件を再確認します。Apple VisionのOS条件やmodel重みをpackageのMITに含めません。重みは未取得・未同梱です。project全体のlicense宣言・releaseは今回の対象外です。

## fixture・CIの境界

fixtureは自作の小文書／人工応答だけを使い、書籍の切出し、実credential、token、重みを追加しません。`sample/`はローカル資料としてignoreします。テストはofflineで、依存installだけをネットワーク処理と区別します。Hub／Transformersのoffline設定は他の全通信まで遮断するfirewallではありません。実provider送信・大型取得は別途個別承認が必要です。

CIはpush／pull_requestで上記checksとwheel導入を実行する設定です。secrets、pull_request_target、continue-on-errorは使いません。今回および後続Work Itemではremote CIの実行・成功確認・実行ref／run URLの取得・記録を受け入れ条件にせず、そのためのcommit／pushも必須ではありません。既存CI設定・smoke検査と、各Work Itemの責務に対応するローカル正式チェック・build／別環境導入検証は維持します。remote CIの未実行だけでは未完了・blockedにせず、未実行をremote成功とも表示しません。§18の過去実験はpi-ai直接経路の検証実績ではありません。
