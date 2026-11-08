# PDF／EPUBのrun・resume性能計測と安全性を維持した高速化

## 目的

PDF・EPUBの初回run／resumeについて、ローカル処理・AI待ち・再利用・保存・出力の時間を分けて測り、主要な待ち時間を最小の変更で削減する。計測→ローカル改善→AI実行・再利用改善→比較・採否判断を一つのPlanで扱う。Implementationは別の新しいセッションで行う。

段階ごとの再Planningは要求しない。承認した仕様・保存契約・保証の範囲内での実装調整、比較、採否判断はImplementation内で行う。仕様・責務・保証の変更が必要な場合だけ通常の確認／REPLAN_REQUIREDへ戻る。

## 要件・決定事項

- PDF／EPUB双方のrun／resumeを対象とする。PDF取得・OCR・layout・ページ画像・補正／接続、共通の読込み・検証・準備・翻訳・再利用・snapshot保存・Markdown／QAを区別する。
- 原文追跡用のsnapshot-bound IDとAI request内のIDを分離する。連番だけの変化を、本文や意味のある文脈変更と区別する。この方針はユーザー確認済み。
- 番号分離後も、version付き実request、config、回答、現在の原文との対応を検証する。ID・本文だけで流用せず、文書context／glossary／policy／schema／provider／model／thinking／options等の正当な変更による無効化を維持する。
- 既存の成功結果・手修正・candidate adoption・immutable artifact・全attempt／raw回答／観測usage・有限retry・取消drain・atomic保存・writer排他を保護する。
- 並列数は1・2・4を比較する。比較前に既定2／4と決めない。結果を示して採用値を判断し、判断材料が不足する間は既定を変更しない。実provider性能はoffline比較から断定しない。
- 実AI評価は入力・送信量・provider等・予算・停止条件の個別承認後だけ実行する。Plan承認は実送信承認ではない。
- 本Planの「未取り込みページの追加処理」は、保存済みPDF補正で原文へ取り込めなかったページを後のresumeで処理すること。PDFファイル修復やbackup復元を意味しない。
- HEAD `94306061ed14238cbc238a9bc9360d697b0e946d` と親 `234552ccfaed15fae3cc94ce8c73d4eb6b1f5302` に、PDF全所有ページ・白紙明示、厳密な旧request再利用、coverage限定最大3回答の修正が存在する。修正済みの前提で保護し、欠落不具合の再修正を抱き合わせない。

### 根拠となる正式資料

- `docs/design/doc-translate.md` §3、7–10、13–17：責務、所有／参照、unit、準備、再利用、保存、retry／concurrency、品質と利用量。
- `docs/reference/artifacts.md`「再開」「基点付き修正・PDF補正候補採用」「共通翻訳unit生成」「文書context・glossary準備」「翻訳・採用snapshot」「PDF補正chunk」「PDF原文snapshot組立」：現在の公開・保存契約。
- `CONTRIBUTING.md`「正式チェック」「変更影響から正式実行範囲を選ぶ」「前提共有・比較・cleanup」、準備／翻訳／render／run／PDF補正／resumeの各検証境界。
- `docs/plans/pdf-correction-page-coverage-retry.md`：直近修正の背景・安全契約。歴史的な331ページ成功／15ページ失敗を現在状態と取り違えない。

## 調査結果・baseline

調査revisionは上記HEAD。macOS 26.7.1 arm64、Python 3.14.9、Node 24.22.1、pydantic 2.14.1、Docling 2.142.1。CodeGraphは未初期化であり、初期化せず一次ソースを直接確認した。外部API／依存仕様の追加調査は今回不要だった。

### 確認済みの事実

1. `preparation.py:256–266,392–464`：headingでgroupを分け、EPUBではresource block境界でも分ける。文字数上限はなく、collectは逐次、最後に全体organizeを1件送る。翻訳のgroupは同境界に既定12,000原文文字を加える。
2. `preparation.py:167–211`：各callでattemptをglob／sortし、到達した候補をread・hash検証する。`translation.py:246–272,382–403,553–611`：開始時読込みに加えて、候補走査・request再生成／parse・各callでの再探索がある。
3. `units.py:386–392,501–535,660–669,975–984`：unitは読み順の連番。heading contextにはPDF／EPUBのblock ID、EPUBではnode_pathも入る。`pdf_source.py:86–116`のblock／cellも組立順連番であり、前方の追加が後段へ波及し得る。
4. `translation.py:169–181`：文書context／glossary／policyを各翻訳requestに含む。collectの再利用改善と、全体固定入力変更後の翻訳再利用は別問題。
5. `render.py:150–215`：uncertainty→block→unitの走査があり、保守的上限はQ×B×U。`_build`には正本／準備／訳／review／usageのload、日英生成、QA等も含む。
6. `render_elapsed_seconds`は`_build()`までで、後段のhash・出力write・render pointer確定を含まない。`resume_document`は続行前に`workspace_status`経由でもbuild・整合確認を行う。これを未計測時間のすべての原因とは断定しない。
7. `run.py:724–827`：PDFは成功chunk／attempt回収後、pendingを最大5ページずつ逐次処理する。参照は許可された隣接ページ。pendingの穴により所有集合が連続とは限らない。length／input_limit時は二分する。
8. `workspace.py`のlock貸出は同一threadの一stage限定。`correct_pdf`へ単純なThreadPoolを足すだけでは成立しない。既存翻訳は親lock下でworkerがimmutable attempt／chunkを保存し、親がsnapshot／pointerを更新する構造である。

### 実Workspaceの読み取り専用集計

対象 `work/design-and-build-great-web-apis` の保存JSON・file statだけを読み、run／resume／renderや正本のdomain再処理、原本／画像の出力は行っていない。

| 項目 | 集計 |
| --- | ---: |
| PDF補正record | 73（validated 70、rejected 3） |
| 準備record／内部attempt | 808／809 |
| 翻訳record／内部attempt | 804／804 |
| 全record／内部attempt | 1,685／1,686 |
| 現版collect／organize／unit | 407／1／2,358 |
| 現版collectの原文文字数 min／median／max | 6／941／42,058 |
| 現版collectで500文字未満 | 101件 |
| 準備・翻訳attempt総byte | 11,210,969／18,264,473 |
| 翻訳snapshot件数／総byte／最大byte | 802／3,261,065,799／4,580,690 |
| 保存render_elapsed_seconds | 645.313491／318.241465 |

旧2,228 unitと現2,358 unitのmarker本文のmultiset重複は2,227件。IDを除いてもheading context込みの重複は1件だった。診断としてunit IDとheading block IDだけを除くと、現407 collectのうち379件の投影が旧groupと一致した。これは安全な再利用率でも、379件分の翻訳を省ける証拠でもない。

固定入力はpolicy、glossaryのbook／version／styleが同じで、document contextとtermsが異なった。termsは旧394→現353、追加73・削除114・value変更19・value同一261。意味的妥当性や変更原因は未評価であり、この翻訳再実行をすべて不要としない。

現版の成功collectのelapsed合計は7,686.893秒、median 17.971秒、max 108.088秒、organizeは81.791秒。呼出しdurationが不変、retry／rate limit／ローカル処理を無視した順次投入の計算は、concurrency 1／2／4／8で7,768.683／3,928.054／2,012.821／1,092.087秒。これはscheduleの参考試算であり、実測改善・実provider予測・採用根拠の代替にしない。

依頼時の初回約5時間09分／追加処理resume約3時間22分、各工程概算は歴史的な参考値とする。未記録区間を関数へ推測配賦せず、合計attempt時間を利用者wall timeと同一視しない。

### 隔離Probeの実測

すべて自作入力、既存環境、追加installなし、非並行・foreground、所有TemporaryDirectory内で実行してcleanup済み。実Workspace・実credential・provider・OCRは使っていない。

**A. 共通rendererの負荷**

各N個のparagraph block、unit、block-targeted uncertaintyを持つsource-only snapshot。textは `Self-authored queue record {i}.`、uncertaintyは各blockを指す。PDF locationはpage 1＋自作evidence、EPUB型locationはown.xhtml＋element_id。これはEPUB reader経由の実archive試験ではなく、共通rendererの計算量を比べる型付き入力である。sourceは公開save、計測は公開`render_document`呼出し全体。各repeatで新しい一時workspace、保存後に計測、各3回。

| format型 | N | render全体raw秒 | median秒 |
| --- | ---: | --- | ---: |
| PDF | 50 | .035378／.018559／.018345 | .018559 |
| PDF | 200 | .666243／.663029／.663175 | .663175 |
| PDF | 500 | 10.350560／10.347177／10.346086 | 10.347177 |
| EPUB | 50 | .019593／.018167／.018482 | .018482 |
| EPUB | 200 | .667658／.664702／.666151 | .666151 |
| EPUB | 500 | 10.246425／10.469727／10.421874 | 10.421874 |

500件PDFの別cProfile実行では全体24.384秒、`_Markdown.__init__` 24.267秒、対象generator約124,999,500回だった。profile overhead付き時間を上表と混ぜない。実書籍のrender全時間への寄与率は未計測。

**B. 自作EPUBの実CLI run／無変更resume**

`tests/test_epub.py:make_epub`で、単一XHTMLにN組の`<h1>Section {i}</h1><p>Original queue record.</p>`を置く。既存`tests/fixtures/resume_process.py`を境界停止なしで使い、`ai/tests/run-process.mjs`の実Node helper／公開faux／no-networkを明示する。delayなし、各操作はfresh Python process、各3回。配布済みruntime導入や実providerのbenchmarkではない。

| sections／units | run raw秒（median） | 無変更resume raw秒（median） | 新規request／resume追加 |
| --- | --- | --- | ---: |
| 2／4 | .793832／.795548／.800337（.795548） | .293870／.290756／.289095（.290756） | 5／0 |
| 20／40 | 4.257846／4.274812／4.228570（4.257846） | .599241／.607797／.617448（.607797） | 41／0 |

各resumeで原本・snapshot・attemptの既存byte hash不変とcompletedをassertした。途中の試行で存在しない`resume --json`を指定してexit 2となったが、修正後の上記成功値とは分離し、一時物は回収した。正式再現は`run --json`、`resume DIR`を使う。

**B2. PDF外部変換fixtureの公開CLI run／無変更resume**

既存 `tests/test_pdf.py` の `conversion`／`local_models`、`tests/test_run.py` の `pdf_input`／`helper`／`run_args` を一時状態で使い、公開 `cli.main` を操作した。所有2–7（6ページ）と2–36（35ページ）。後者は同じ200×100 pointの自作ページ・短い自作textを追加した。公開converter／page backendだけを置換し、PNGは従来の長辺1800 px。各repeatは新規workspace、同じwarm Python process内で、fixture／import準備後にmain全体を計測。各3回、実Node／faux／no-network、delayなし。実OCR／layout／model／runtime導入性能ではない。

| 所有ページ数 | run raw秒（median） | 無変更resume raw秒（median） | 新規request／resume追加 |
| --- | --- | --- | ---: |
| 6 | .906139／.890209／.880035（.890209） | .179161／.182310／.271160（.182310） | 5／0 |
| 35 | 3.097299／3.102331／3.102555（3.102331） | .779276／.780353／.779033（.779276） | 10／0 |

resumeはcompleted、旧原本／snapshot／attempt／correctionのbyte hash不変をassertした。MonkeyPatchを復元し、今回作成した一時入力／人工model file／workspace／credentialを削除した。EPUBのfresh Python subprocess値と直接速度比較しない。Implementationの前後比較では同じ起動条件を揃え、fresh processへ変更する場合はbeforeも再取得する。

**C. ID分離の小さい投影Probe**

PDF合成snapshotと実EPUB抽出で、Alpha／Betaの2 groupの前にPrefaceを追加して3 groupにした。現在の投影は元2 groupとも不一致。request内連番と、headingのblock ID／node_pathを位置から分離する候補投影では、Beta groupが一致し、Alphaは直前heading contextが変わるため不一致のままだった。本文／heading text変更は不一致、元snapshotは不変。最初の仮説「元2 groupとも一致」は成立せず修正した。これはprojectionの成立確認であり、本番実装・raw回答binding・意味品質の検証ではない。

**D. 既存回帰の限定確認**

次の4件が11.43秒で成功。変更後の検証・承認には流用しない。

- `test_preparation.py::test_preparation_reuses_fixed_version_without_resending`
- `test_translation.py::test_translation_reuses_same_marker_input_after_code_and_preparation_metadata_changes`
- `test_pdf_candidate_acceptance.py::test_front_page_recovery_shifts_block_and_unit_ids_without_transferring_manual_edits`
- `test_run.py::test_resume_pdf_repairs_coverage_only_for_failed_ownership_and_retains_all_usage`

### 仮説・未計測事項

attempt探索、繰り返しunit生成／serialize／load、全snapshot保存、EPUB再抽出、usage／review読み込み、画像検証の実run全体での比率は未確定。PDF実取得・OCR／layout・画像生成の今回baselineと実providerの並列性能・rate limit・品質・料金も未計測。これらの数値を捏造せず、第1段階で安全な合成／既存fixture baselineを補う。実取得・実providerでしか確認できない項目は個別承認が得られるまで未測定とする。

## 制約・対象外

- 今回はPlanningのみ。production code、検証資産の生成、実アプリへの資産成立確認はImplementationで行う。
- 実Workspace・既存成果物を書き換えない。既存processを停止／再起動しない。lockを削除しない。
- 書籍本文・画像をfixture／Issue／公開証拠へ追加しない。自作資料・人工credentialだけを使う。
- 実provider通信・追加課金・モデル取得・追加install・利用者／共有環境変更は無断実行しない。
- status軽量化、進捗表示、送信通知UIは別Planning。共有renderer／validator変更の回帰は確認するが、statusの設計変更はしない。新並列設定に必要なCLI/API説明は性能機能の範囲に限定する。
- model／thinking引下げ、画像情報削減、用語準備の省略、validator削除は採用しない。
- 全面的な保存形式置換、既存artifactの書換え／一括migration／GC、永続cache、汎用監視・benchmark runner・独自復旧manager・E2E／CI基盤は作らない。
- snapshot確定頻度を下げて現在の中断復旧保証を弱めない。3.26 GBの保存量は計測対象だが、履歴削除やcheckpoint保証変更は本Planの最適化に含めない。
- 全文意味品質、任意スキャン品質、実課金のexactly-once、viewer／他OS／remote CI、別Planningの自動品質検査・回復機能を完了条件にしない。
- 既存の未追跡 `docs/design/progress-and-status.md`、`docs/plans/progress-and-status.md` は保持し、今回変更しない。

## 実装方針

### 第1段階：比較可能な計測を確立する

1. 既存の公開CLI/API・合成入力・実helper／fauxを再利用して、変更前revisionのPDF／EPUBを計測する。製品の通常出力・正本schemaへ性能logを混ぜず、証拠はworkspace外の今回専用directoryへ置く。
2. 最初は外側wall timer、`time.process_time`／`resource`、別実行のcProfile、既存attemptを使う。必要なfile read／hash／schema／unit生成／候補照合／保存回数の観測だけ小さく補う。計測のためにvalidator／generate／saveの結果をfakeにしない。profileやcounter付き実行は通常時間比較と分ける。
3. run前処理、resume事前整合、取得／回収／組立、準備collect／organize、翻訳前照合／翻訳、snapshot保存、render build／出力保存を区別する。AI呼出しにはtransport・待ち・生成・terminal／終了確認が含まれ、provider内部計算時間とはしない。待機／並列timeline、訂正／transport retry、active上限、送信text／画像bytes、calls／attemptsを別に記録する。
4. 読込み回数／論理bytes／hash／schema／domain検証回数、候補件数／照合回数、reuse hit／missと理由（本文、追跡ID、heading位置、実context、固定入力、config、失敗／破損等）、unit／group／chunk／履歴数、snapshot write数／bytesを記録する。実際に取得できない物理I/O等はunknownとし、file sizeやopen回数を物理read量に偽装しない。
5. メモリは親とhelper子の観測方法・単位・対象を明記する。`ru_maxrss`の親／最大childを同時RSS合計と呼ばない。追加依存なしで取得できないaggregateは未計測とする。
6. 初期比較条件は同一revision／差分、入力hash、Python／Node／PDF依存・helper build、config、offline、worker数、cache状態。processはfresh、warm filesystem cacheはその旨を記録し、OS cacheを無断flushしない。通常時間は同じ条件で前後3回、raw値・median・min/maxを保存する。計測は他のbenchmarkと非並行。外乱で判定不能なときだけ前後同数を追加し、失敗runを成功時間へ混ぜない。

**入力規模**：
- 上記render負荷N=50／200／500をそのまま再現する。
- EPUBは上記2／20 sectionsを再現し、成長確認用200 sections／400 unitsを追加する。単一resourceと複数resourceを代表例で分け、EPUB固有境界も確認する。
- PDF runは`tests/test_pdf.py`の外部公開converter／page backend fixtureと`tests/test_run.py`の合成入力を再利用。まず上記6／35ページを再現し、成長確認は同じ自作形状を必要に応じ最大350ページへ拡張する。画像条件は従来1800 px、外部modelはfake conversion境界以外では使わない。必要量と停止条件を先に確認し、大規模は制御した待ちの比較と区別する。実OCR／Docling速度とは呼ばない。
- 該当fixtureの準備・画像生成・コピー時間をend-to-endに含めた値と、保存済み状態からの操作時間を区別する。毎回全組合せを回さず、性能は代表大規模、境界回帰は最小入力を優先する。

### 第2段階：同じ結果を得るローカル処理を改善する

**優先1：rendererの関連付け索引**
- `render.py`のblock／unit／cell／uncertainty関連付けを、現在入力から一度作る索引で解決する。EPUB node範囲、PDF cell、page review、block全体と局所targetの違いを維持する。
- source／unit順、重複除去、QA対象、anchor、日英本文、safe assets／参照検査を変えない。`next(blocks)`等の同種の再探索も同じ索引で解消できる範囲だけ扱う。
- statusの共有経路に効果が及ぶことは許容するが、status専用cache／軽量実装／表示変更は追加しない。

**優先2：operation内attemptカタログと候補索引**
- `preparation.py`、`translation.py`の重複read／parse／照合を減らす。永続indexは作らない。
- preparationは従来の辞書順・一致時停止・到達した破損でのエラーを維持するlazy catalogを基本とする。従来到達しない後方recordまでeager loadして拒否条件を変えない。translationの開始時全読込み等、既存の事前破損拒否は弱めない。
- lookup keyは実request／config／方式等の指紋とし、指紋一致だけで受理せず元recordと全入力を比較する。規定repair、adjustment／length replay、orphan成功回収、candidate選択順、全runの検証を維持する。
- 今回operation内で検証したimmutable recordだけを再利用する。新規保存後にcatalogへ登録し、別operationは読み直す。mutable pointerや外部pathを永続cacheしない。
- 同一v2 request＋configが同時missする場合は、operation内でin-flight結果を共有して重複送信しない。共有するのはraw成功／失敗結果であり、現在sourceへのbindingは各利用箇所で検証する。usageは実attemptを二重計上しない。
- 全履歴snapshot／PNGの一括常駐はしない。unit／source／preparationの重複処理が計測上支配する場合だけ、digestに結び付いた同一公開load内の検証済み値を最小共有する。別callでの破損検知や保存保証を迂回しない。

### 第3段階A：request内IDを分離して再利用を局所化する

#### 送信と追跡の分離

- 公開`SourceSnapshot`／`TranslationUnit.unit_id`、locations、source／document／snapshot hash、block／cell／node対応、split元座標は追跡側に残す。
- 新しく生成する準備collect／翻訳requestには、そのrequest内だけの順序付きalias（例 `request-unit-1`）を使う。aliasは全単射で、異なるunitへの重複割当・回答IDの欠落／余分／順序違反を拒否する。
- heading_pathは順序・level・保護済みtextを保持し、block_idとEPUB node_pathは追跡binding側に分離する。任意のcontext fieldを再帰的に削除・正規化しない。既存のlist／table／caption等のcontextは今回そのまま維持し、そこに残るID差によるmissは許容する。意味関係の再設計を抱き合わせない。
- collectの全体section番号も送信内容とbindingを分離する。organizeは元のgroup順を維持し、実際に送ったwire collectionsを保存runから再構成して照合する。global IDの変化だけをorganize入力差へ再混入させない。
- groupアルゴリズムと翻訳12,000文字上限はまず変更しない。batch変更とID分離の効果を混ぜない。

#### 版管理・load・既存結果

- wire方式v2は実request内と保存artifactで明示し、未知versionは拒否する。v1の正本・準備・訳・raw attempt・manual履歴は従来の生成／検証規則で読めるよう保持する。旧artifactを変換・再hash・上書きしない。
- 必要な準備／翻訳保存schemaだけ互換的にv2を追加し、v1 loaderを維持する。v2の準備evidence／訳chunkは、参照snapshotのunitsからalias→追跡ID対応を再構築できる情報を保持する。保存mappingの自己申告だけを信用しない。v1／v2混在時も各runの方式・raw回答・bindingを再検証する。
- 再利用優先順位は、現在の採用済みmanual／accepted結果、適合する保存attempt、新規生成の順。tier内は決定的な既存順を維持する。v1でも現在入力と完全一致する結果は先に使い、方式追加だけを理由に再送しない。
- v1 requestのIDを無視してv2扱いすることはしない。旧結果しかなく番号が変わった入力はmissとなり得る。生成が必要な不足分にv2を使い、その後の変更で恩恵を得る。新しい切替コマンドや一括migrationは不要。
- v2自動回答の再利用は、版付きwire request＋config全体一致、status／preflight／正常terminal／helper終了／raw回答schema検証、現在unitsへの順序付き全単射、現在unitsでのmarker／引用／根拠検証をすべてAND条件にする。
- 準備evidenceは現在の追跡ID／位置へ結び、整理後の根拠語検証、利用者terms優先、固定入力hashを維持する。固定document context／glossary／policyが変われば翻訳の広い無効化は維持する。意味的に同じらしいglossaryやcontextを同一と推定しない。
- **手修正訳のIDずれ追従は拡張しない。** 現在の追跡対応と全実入力が一致する従来の再利用を維持する。alias一致・似た本文・位置一致だけで別unitへ移さない。IDずれの自動回答再利用がmanual優先を越えない。
- splitは親本文・元hash・元座標・part順・不可分markerを検証する。aliasだけで分割操作を移さず、対応不明ならmissとして必要範囲を処理する。旧訳の推測分割・接合はしない。
- PDF H/B/Cと明示採用の境界は変えない。pending candidate中は準備・翻訳を送らず、原文手訂正を自動移植しない。

### 第3段階B：準備とPDF補正を制限付き並列化する

- 公開APIとrun／resumeへ、準備・PDF補正それぞれの同時実行上限を最小追加する。CLI名は `--preparation-concurrency`／`--correction-concurrency`、許可値は1・2・4。設定はrun manifestへ保存し、resume未指定時は保存値、旧manifestのfield欠落は従来の1。EPUBへのcorrection指定は入力エラー。既存翻訳concurrency 2は変更しない。
- 採用既定値の変更前は1を維持する。比較後、後述の採否手順でユーザーへ結果を示す。新規2／既存1等の案を合意済みとして実装しない。
- 準備collectは独立jobとして制限付き投入し、回答・run対応は元group順へ戻す。organizeは全group成功・保存後に一度だけ行う。失敗・取消で旧採用pointerを保ち、他jobの保存済み成功はresumeで回収する。
- PDFは公開`correct_pdf`のlock wrapperを維持し、既存処理を内部coreへ最小分離する。run親がOS workflow lockを保持し、workerは独立所有jobのimmutable attempt／chunkだけを保存する。親だけがmanifest・進捗・source採用を更新する。thread-local貸出をthreadへ広げず、新しいack queue／汎用schedulerは作らない。
- 各jobは既存どおり、そのgenerateの全attemptを保存してから同jobの次のcoverage訂正へ進む。所有5ページ・許可隣接参照・1800 px・policy／schema・全domain検証・最大3回答・transport retryを維持する。
- 実active呼出し数を上限以下にし、length／input_limit分割後も超過させない。成功所有を再投入せず、所有範囲の重複を拒否する。完了順が変わっても最終chunk／ページ／source順は固定する。空回答や部分coverageを並列化のためにmergeしない。
- 取消・設定／認証／保存失敗を観測したら新規投入／新しい送信許可を止め、進行中childへ取消を伝播しdrain／回収する。競合時に既に開始していた呼出しまで「送信ゼロ」と保証しない。通常の局所失敗と全体停止を区別し、既存成功分とraw／usageは保持する。
- 同一requestの重複jobはin-flight共有で抑えるが、独立所有PDF jobはscopeが異なることを確認する。共有構造はoperation内・thread-safeとし、processを跨ぐ新しい管理状態を作らない。

### 第3段階C：他候補の比較と採否

| 候補 | 効果／コストの見込み | 本Planでの扱い |
| --- | --- | --- |
| render索引化 | 合成Probeで支配箇所を再現、低リスク | 優先実装・前後計測 |
| attempt索引／同一実行の再利用 | 多重I/O／照合を減らす、メモリ増に注意 | 優先実装・read数と時間を確認 |
| request内ID分離 | 以後の番号ずれによる再collect／翻訳を減らす、互換性検証が必要 | 上記の限定契約で実装 |
| 準備並列化 | 待ちを重ねる。件数・送信量は基本不変 | 1／2／4を実装・比較して採否 |
| PDF補正並列化 | 初回補正のAI待ちを重ねる。画像／rate limit負荷が増える | 分割・品質条件を変えず実装・比較 |
| 準備の本文量別group／batch | 101件の小入力等を減らせる可能性。文脈・上限・再送量・品質が変わる | 文字数分布・上限・見込みcalls／tokens／retry単位を算出して比較。今回の既定方式には採用しない |
| PDF画像縮小・所有単位変更・model／thinking変更 | 品質と失敗率の追加評価が不可欠 | 比較候補として根拠と必要評価を記録し、今回採用しない |
| snapshot保存回数削減・永続cache | 保存量は大きいが復旧／破損検知に影響 | 今回採用しない。既存保証を維持し計測結果を残す |

全候補を同時実装する計画ではない。選ばなかった候補も理由・期待効果・必要な追加品質評価を残す。batch等を採用したくなった場合は、今回の固定group・保証範囲を変えるため通常の設計確認へ戻る。これは計測結果による通常の並列数選択とは区別する。

### 第4段階：同条件比較と採用判断

1. 第1段階と同条件で再計測し、下記の性能・契約条件を照合する。単なるcalls減／延べ時間減を利用者時間の短縮成功としない。
2. offlineの制御遅延比較では、安全条件を満たす1／2／4の時間・resource・failure／retryを示す。性能差が測定のばらつき以下なら、小さい並列数を優先する。既定値は結果提示後のユーザー判断とし、その判断はImplementation内の通常対話で扱う。Planを一から作り直す工程ではない。
3. 実AI評価が許可された場合だけ、同じ自作原文・model／thinking／options・scopeで準備とPDF補正を比較する。意味品質・用語根拠／一貫性・欠落・訂正率・rate limit・token／参考料金を時間と併記する。真の料金・provider内計算時間を推測しない。
4. 実AI評価が未承認なら「未実施」とし、offline合格を実provider速度・品質の確認済みにしない。実AIを必要とする採用判断は保留、既定は従来値とする。opt-inの並列機能・機械的整合確認と、実providerの既定値推薦を区別して報告する。
5. 合格しない候補は見送り理由を記録する。性能未達を成功表示せず、契約・保証を緩めて採用しない。承認範囲内の調整／比較／見送りで再Planningは要求しない。

### 文書・変更影響

- 主変更：`run.py`、`preparation.py`、`translation.py`、`pdf_correction.py`、`render.py`、必要最小の`units.py`／`cli.py`。公開ID生成、`workspace.py`のlock契約、PDF取得設定は原則変更しない。
- caller／consumer：`revise.py`のmanual artifact生成、`review.py`の歴史snapshot load、`pdf_source.py`、render_assets、statusを、v1／v2・並列変更が及ぶ範囲で確認／関連更新する。config・schema・helper・exportsの実callerをImplementationで再検索し、漏れを残さない。
- `docs/design/doc-translate.md` §8／9／14／15にID分離、厳密再利用、並列責務と理由を関連更新する。今回確認済みの設計判断を既存設計へ残し、専用ADR／GLOSSARYを増やさない。
- `docs/reference/artifacts.md`に版・binding・互換load・設定保存・失敗／取消／再利用契約、`docs/reference/usage.md`に並列設定・追加利用量・性能／品質の限界を更新する。
- 計測仕様と比較方法は新規`docs/reference/performance.md`へ置き、今回のbaseline条件／raw値／未測定を識別する。実行ごとの詳細logは追跡外の専用証拠directoryへ置き、文書をlog台帳にしない。
- `CONTRIBUTING.md`は正式チェック・合成benchmark・検証資産への導線を更新する。READMEには必要な利用説明／詳細への短いリンクだけを追加し、詳細を集約しない。
- Python core／PDF extra、Node JSONL／helper、wheel同梱resource・runtime構成は維持する。新依存・lock更新・production Node変更は予定しない。既存Node faux fixtureのdelay／request時刻／active数／故障観測は必要分だけ拡張し、既存consumerの入力互換を保つ。

## 完了条件

1. 形式・入力規模・run／resume状態ごとの計測条件、raw値、時間内訳、logical I/O／検証／照合／保存回数、reuse／miss理由、メモリの観測対象と未測定が再現可能に残る。
2. 上記N=500の共通renderで、**同環境の変更前に対する中央値50%以下**をPDF／EPUB型双方で満たし、本文・source map・QA・assetsの同等性を確認する。非決定的な経過時間・出力directory digest等だけは比較から区別し、安全検査やQA対象を除外しない。N=50／200のrawと増加率も示す。
3. 保存attemptの元file read／hashは、一つのoperationで同じ元recordを繰り返さず、到達した元recordについて原則各1回。repair／候補追加も含め再利用の正しさ、破損拒否、同時重複送信なしを確認する。共有loadが別途検証する正当な回数は分け、実行全体が1readと偽らない。
4. attempt-heavy代表workloadは、変更前に探索の寄与を確認して同条件比較する。探索区間medianを**変更前の70%以下**とする。支配率が小さい場合も、read数削減と全体時間を分けて報告し、無根拠な全体短縮率を主張しない。
5. v2の自動結果は、同じwire input／configで追跡IDだけがずれた成功範囲を無送信再利用できる。本文・意味context・固定入力・config／schema等が変わった範囲は必要に応じて無効化する。旧v1記録の緩い転用、manualの別unit転写、candidate待ちの下流送信はゼロ。
6. PDF／EPUBの制御遅延付き独立collect、PDF補正の独立jobについて、十分なjob数（最低12、各500 ms待ち）でconcurrency 2の対象stage medianを1の**75%以下**、4を1の**55%以下**にする。process起動・実検証・保存も対象stage時間に含める。実activeは設定以下、calls／総入力・回答・順序は同じで、品質を削らない。これは実providerの短縮率ではない。
7. ローカル最適化後の代表run／無変更resumeが、同条件before medianに対し**max(before×1.10、before＋0.05秒)**を超えて悪化しない。新AI request方式による冷えたcache／互換loadの費用は別に測り隠さない。resourceは親／helper peak・worker数・保持したrecord／画像量を報告し、無制限投入・全snapshot常駐をしない。
8. 初回、無変更resume、局所失敗／未取り込みページ追加、一部原文変更、policy／glossary／model等変更、順序逆転、取消、有限retry、保存失敗後のresumeを、下記の分担で確認する。無変更resumeの新規AI送信0、旧immutable byte保持、正常な出力／QA／provenanceと旧形式loadを維持する。
9. 数値目標未達は調整または未達として報告し、計測方法や保証の無断変更で合格にしない。工程単位の性能合格と全体の契約受入れを両方確認する。
10. 関連文書・再実行資産・正式チェック・Root直接確認・Reviewを完了し、段階4で各候補の採用／見送り／実provider未確認・既定値判断を明示する。実AI評価未承認の場合はその限定を残し、実provider推薦の完了と混同しない。

## 動作確認方法

### 保証対象・境界・TDD

Performance作業として**非TDD**。既存の不変条件／characterization、必要な新契約の小さい回帰、同条件before／afterを使う。非TDDはテスト不要でも、最後にまとめて追加する指定でもない。

- `prepare_document → load_preparation`：collect／organize入力、引用・用語根拠、v1／v2、alias対応、固定版、訂正／usage、無送信reuse。
- `translate_document → load_translation`：実入力全一致、順序付きbinding、marker／split、manual、部分成功・取消・orphan回収。
- `correct_pdf → load_pdf_correction`と`assemble_pdf_source`：所有／参照、PNG／draft、coverage訂正、順序、immutable保存、並列上限。
- 公開CLI run／resume／reviseと保存-only render／status／review load：形式別の接続、設定の保存優先、H/B/C・manual保護、旧成果物、出力／QA。
- 小さいテストだけでCLI／Node／保存の結合保証を代用しない。全条件の直積、新しい恒久E2E群、大きなfakeやテスト専用production APIは作らない。

### 最小の自動回帰

既存 `tests/test_preparation.py`、`test_translation.py`、`test_units.py`、`test_render.py`、`test_pdf_correction.py`、`test_pdf_source.py`、`test_run.py`、`test_resume.py`、`test_revise.py`、`test_review.py`、`test_pdf_candidate_acceptance.py`、`test_source_snapshot.py`、`test_inference.py`を再利用する。

追加は次の具体的リスクに絞る。

- v1の保存byte・load・manual／split履歴、混在run、未知version、改ざんbinding／再hashした不正根拠を検査。
- PDF／EPUBの自作前方追加で、番号のみの後続入力はreuse、直前heading等の実context変更はmiss。重複本文／異なる文脈を取り違えず、同じrequestの並列missを二重送信しない。
- 自動reuseを許してもmanualのIDずれ追従はしない。現在の手修正優先、candidate採用前停止、既存の前方ページ追加・構造変更回帰を維持する。
- prepareの結果完了順を逆転させてもcollect／run／organize順は安定。1 job失敗／取消／record保存失敗後に新規投入を止め、成功recordからresumeする。
- PDFの所有非重複・許可隣接参照・coverage最大3回答・有限transport retry・分割後active上限。保存障害後の旧pointer保持、完了順変更とsource順、失敗分だけの再処理を確認。
- renderer索引化でEPUB node局所範囲、PDF cell／list／caption／page review、未訳／stale、unsafe assets／参照、QA順を維持する。
- 性能の秒数閾値は独立benchmarkで確認し、通常pytestを時間依存の不安定suiteへ変えない。最小の計数assertと契約回帰だけ恒久化する。

### 直接確認と再利用する資産

既存資産は`CONTRIBUTING.md`の準備・翻訳・render・run・resumeの具体例、`tests/test_epub.py:make_epub`、PDF外部converter／page backend fixture、`tests/fixtures/resume_process.py`、`ai/tests/{run,preparation,translation,pdf-correction}-process.mjs`、`no-network.mjs`。Nodeのnetwork拒否・人工credentialを使う。HF offlineだけを全通信遮断とみなさず、実PDF変換が必要な場合は別の通信遮断と明示許可済みlocal modelを確認する。

現状、project-local検証Skill／Feature Mapはない。既存の小さい操作例は適合するが、大規模性能比較、wire binding、並列active／取消／resumeの履歴不要な一連手順が不足している。そのためImplementationで共通`create-verification-skill`を読み、**今回の性能・再利用・並列run／resumeだけ**を初期対象に最小補完する。

- 既存規約がなければ`.pi/skills/verify-project/SKILL.md`と`references/feature-map.md`。選ぶ機能は「形式別run／resume計測」「render負荷」「ID対応と無効化」「制限付き並列・取消／保存・再開」。
- 必要な自作入力／操作／観測の小さいhelperだけを保存し、既存faux・公開CLI/APIに接続する。汎用runner／fixture管理基盤は作らない。既存例と重複する説明は参照する。
- 起動、対象／build確認、操作、期待値、証拠、所有process／一時物のcleanupを具体化し、少なくとも一機能で生成手順自身を実行してCreate成立を確認する。その成功だけで全完了条件を受け入れない。
- 証拠は既存規約どおり追跡外の`work/<今回専用directory>/`、一時workspaceとは別に置く。版／差分／tool条件、入力hash・規模、command／exit、raw時間、計数、期待／観測、未実施、cleanup結果を残す。実秘密・書籍本文／画像・不要なraw logは保存しない。
- 別セッションはrepository cwd・project trust、新セッションまたは`/reload`後に`/skill:verify-project`または実ファイルreadで利用する。自動選択自体は保証しない。

Workerは担当変更のfocused回帰・benchmark・資産関連部分の再実行を行い、command／exit／期待／観測／raw値／未確認／cleanupを報告する。Rootは差分を直接読み、以下の正式チェックと、別の新しい隔離workspaceでの公開CLI/API操作を自ら行う。

Root直接確認は、PDF／EPUB各1つの代表run→無変更resume、新方式で原文前方追加後のreuse／正当miss、手修正／candidate保護、準備とPDFの逆順完了＋取消／保存失敗からの再開を選ぶ。自動テストの全parameterを二重再生する義務ではないが、資産実行やWorker成功で代替しない。更新した手順・helperは再実行する。

cleanupは今回作ったPID／handleを回収し、今回の一時workspace・人工credentialだけを削除する。証拠は残し、cleanup後に記録と参照先をreadbackする。既存Workspace・venv・model・work全体は削除しない。失敗・取消でも所有物を回収し、残存は報告する。

### 正式チェックとruntime

`CONTRIBUTING.md`の実行範囲選定とphase順序を維持する。今回は共通schema／工程間reuse／保存・並列に及ぶため、最終受入れは関連full phaseへ戻す。

- 既存build・環境鮮度を確認する。無断install／sync／helper build・環境切替はしない。
- Python：Ruff check／format、core-only全pytest・CLI help、PDF extraのty／公開importと現行PDF4ファイル `tests/test_pdf.py tests/test_run.py tests/test_review.py tests/test_pdf_candidate_acceptance.py`。追加したPDF依存テストの配置が現行4ファイル外ならCI実行集合にも漏れなく接続する。補正／source／units／preparation／translation等は必要な環境でskipを成功扱いせず確認する。
- Node fixture変更時：既存Biome、type-aware oxlint、全Vitest。production Nodeや依存／同梱resourceに変更が出るなら規約どおり明示build・配布検査の必要性を再確認する。未変更なら毎回のwheel再導入やremote CIを追加義務にしない。
- standalone／moduleのfixture consumer、収集node ID／parameter／skip理由で範囲漏れを確認する。担当交代だけで同一条件の全suiteを機械的に反復しない。

### 実AI評価の承認事項と限界

必要な範囲は、request内ID分離後の準備／翻訳の用語・文脈品質、実providerの並列2／4での速度・rate limit・retry／usage、PDF補正の文字・code・表・図・跨ページ接続の品質である。batch・画像縮小・model変更を既定採用する評価は今回含めない。

Implementation Rootが、送信する自作入力のhash、PDF所有／参照、EPUB resource、request数／入力bytes／画像数、config／認証source、最大attempt／時間／参考費用上限、比較条件、停止・cleanupを提示して個別承認を得る。既存書籍や現在の実Workspaceを使う承認は含まない。providerが費用のhard capを保証しない場合は参考予算と厳密上限の違いを明記する。

評価では同じ自作原文と固定条件で現在方式／ID分離後、parallel 1／2／4を必要最小限比較する。機械的な引用・marker・coverage検証に加え、原資料と用語・訳文・補正結果を直接標本照合する。初回と再利用時の呼出し数・token／参考costも区別する。小標本を全文品質や全providerへの保証にしない。追加反復や予算超過見込みは停止して確認する。

### Implementationでの最小補完裁量と限界

Implementation Rootは現物の検証手段を再確認し、承認済みの保証対象・対象外・期待結果を維持して操作・観測方法を具体化できる。追加installや利用者／共有環境変更なしに隔離して既存標準機能を安全に試すこと、小さい接続／観測コードと再実行に必要なhelperの保存・再利用は、共通Create／関連更新の最小補完に含む。

Skill／helper追加だけで再計画や追加gateを要求しない。「予定なし」は禁止ではない。一方、必須Skill・権限・認証・安全条件が実際に不足し、許可範囲で成立不能なら、試した失敗・未確認手段・不足と再開条件を分けてBLOCKEDとする。独自代替、無断install、秘密保護／所有cleanupの迂回、未成立手順の成功扱い、期待値の緩和はしない。仕様・公開／保存契約・保証変更は通常の確認／REPLAN_REQUIREDを使う。

## 段階・依存と引継ぎ

正式なWork Item分割はApproved Plan保存後にtaskflow-to-ticketで別途確認する。実装・検証・Reviewしやすい成果として、少なくとも「baseline／検証手段」「render改善」「attempt探索改善」「準備ID分離」「翻訳ID分離とmanual保護」「準備並列」「PDF補正並列」「統合比較・採否」が識別できるようにする。これを固定件数や直列依存として強制しない。

baselineは性能比較の前提。ID分離とcatalogの同時miss対策は接続時に必要。PDF並列は準備ID変更の完了を本当のblockerとしない。最後の統合比較・採否は採用する変更と対応検証が揃ってから行う。各成果の途中で残る要件を明示し、途中成果を全体完了としない。

Approved Planは承認後に`docs/plans/pdf-epub-performance.md`へ本文一致で保存する。Work Itemsはtracker設定・分割・最終本文／保存公開の個別承認後に公開し、各項目からこのPlanを参照できるようにする。今回のPlanningではImplementation、Worker起動、commit／merge／release／deployを開始しない。
