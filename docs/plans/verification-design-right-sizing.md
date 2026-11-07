# doc-translate の検証設計・実行範囲の適正化

## 目的

旧Taskflow由来の検証設計を、現在の保証・前提作成・公開境界・Agent分担の考え方に合わせて見直す。製品の保証を維持し、検査対象でない前工程の反復と、変更の影響に無関係な全件再実行を減らす。

対象cwd: `/Users/minoru/Documents/mywork/cli-dev/doc-translate`。

主たる作業は検証コードのRefactoringと検証規約の変更。発見した正式実行対象の漏れは関連する修正として扱う。固定の秒数・短縮率を達成するPerformance案件にはしない。時間は実測比較して効果・限界を報告し、改善未確認を高速化成功と表示しない。

## 要件・決定事項

1. PDFとEPUBの両方が対象。全テストの一律再実装ではなく、計測と具体的な反復が確認できた群から変更する。
2. 保証対象、公開境界、必要な整合前提、検査する操作を分離する。接続・状態遷移・拒否・取消・復旧・実時間そのものが保証対象なら、その実経路を残す。
3. 前提再利用は同一入力・同一整合条件の今回の実生成物に限定し、各ケースへ独立コピーする。公開loadによる再検証、seed不変、変更非伝播、元pathに依存しない読込みを確認する。
4. ユーザー確認済み方針: 変更の影響が小さく閉じていることを確認できれば、Rootの正式確認も関連テストに限定できる。広い変更・影響不明では全件実行する。test/docかproductionかというファイル種別だけで判定しない。CIの全件検証は維持する。
5. 正式PDF実行対象へ `tests/test_pdf_candidate_acceptance.py` を追加する。core-onlyでskipされる48ケースの実行漏れを補う。追加による時間増と、同じ保証範囲内の軽量化を分ける。
6. 固定の秒数・短縮率目標は置かない。合格条件は保証維持、不要な工程の削減、実行範囲の妥当性、実行漏れの解消。時間比較は必須の報告であり、短縮量を稼ぐために範囲を広げない。
7. 新しいPlanとWork Itemsに分ける。関連Issue [#39](https://github.com/minorunakamura/doc-translate/issues/39) と `docs/plans/test-verification-speedup.md` は過去の別契約として保持し、更新・reopen・closeしない。既存初期Planの歴史も書き換えない。

## 制約・対象外

- Planningでは実装しない。Work Item公開後もImplementationを自動開始せず、merge/release/deployしない。
- production code・製品仕様・artifact契約・trust boundary・エラー処理は変更しない。test専用の公開API、validator/generate/saveのmock、hashの後付け整合、恒久cache、hardlink、新規依存、汎用builder/runner/自動影響解析基盤を追加しない。
- 実credential・実認証・実LLM・model取得・利用者/共有環境変更は無断で行わない。offline変数だけで全通信遮断とはみなさず、既存network拒否付きfaux helperと一時人工credentialを使う。
- 追加installの今回の許可は、Planning専用の一時core環境を既存local cacheからofflineで作ることに限る。この環境はcleanup済み。Implementationでの追加installは改めて必要性と許可を確認する。既存 `.venv` を無断syncしてcore/PDFを切り替えない。
- CodeGraphは未初期化。初期化・再構築しない。開始時・作業前の差分を確認し、未コミット変更を保持する。commit/stash/破棄しない。
- Nodeの30秒grace、10.5秒以上のheartbeat、Pythonの実3/6秒retry、PTY・signal・別processの必要な保証をfake timer/mockで置換しない。
- Node JSONL行列のprocess統合、重複tscの配置変更、CI並列化、配布検査の再設計、Taskflow本体改修は今回行わない。
- 任意PDF/OCR・実LLMの意味品質、viewer表示、account利用権、課金、remote CI成功は今回のoffline検証から主張しない。remote実行のためのcommit/pushは要求しない。

## 確認済みの事実・根拠

### 現在の状態と一次資料

Planning確認HEADは `1b6a1d2151adb4cc8d85b06e1d764775afc7ae6c`、開始時と調査終了時の作業ツリーはclean。#39はCLOSEDで、固定preparedとmalformed sourceの共有は現コードに存在する。ユーザーが言及した変更はこの時点では未コミット差分として存在しなかった。Implementationでは現在状態を取り直す。

判断根拠:
- `CONTRIBUTING.md`: 正式チェック、検証分担・再実行・証拠、準備/翻訳/render/run/review/revise/resume、fixture・CI、認証のTDD記述。
- `.github/workflows/ci.yml`、`pyproject.toml`、`ai/package.json`、`scripts/check_distribution.py`: core全件、PDF対象3ファイル、品質チェック、build/配布の条件。
- `docs/reference/artifacts.md`: 固定版・根拠・実行記録、翻訳・採用snapshot、修正・PDF候補の保存契約。
- `docs/design/doc-translate.md` §12–15、`docs/plans/doc-translate-initial.md`「TDDと公開テスト境界」「正式チェック」: 安全性、保存・接続、歴史的なTDD指示。
- `preparation.py::load_preparation`、`translation.py::load_translation`: source、evidence、unit、attempt、実request/answer、旧revisionまで整合を再検証する。表面のsnapshot JSONだけのコピーでは不足。
- `snapshot.py::save_source_snapshot/load_source_snapshot`: 不変artifact保存後のatomic pointer切替、hash/schema検査。
- `run.py::source_adoption_status`: next_operationは現在workspaceから絶対pathを組み立てる。保存済み候補やrender summaryの移設は、単純コピーだけで同等とは決めない。

現行Taskflowは導入先 `/Users/minoru/Documents/mywork/pi-dev/pi-taskflow/skills/` のplanning/implementation、双方のRefactoring・Performance・Bug Fix Playbookを確認した。今回の種別・合格条件は上記ユーザー判断に基づく。旧Performance Planの10%目標や一律test-firstは継承しない。Taskflow導入先への固定絶対pathをプロジェクトの実行依存にはしない。

### 今回の観測値

Darwin arm64、Python 3.14.9、Node 24.22.1、pnpm 12.10.1、uv 0.13.5。pytest 9.1.1、Pydantic 2.14.1、Pillow 13.0.0。PDF環境はDocling 2.142.1/core 2.104.0。coreは別の一時環境を `UV_PROJECT_ENVIRONMENT=<今回専用path> uv sync --offline --locked --dev` で生成し、Docling不在を確認した。

各テスト計測は非並行、PATHに対象venv/bin、`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`、既存build済みhelper、人工入力/faux条件。以下は各1回の観測であり統計的benchmarkではない。

| 対象・コマンド | 結果 | 外側wall time |
|---|---|---:|
| core環境の `pytest -q -rs --durations=0 --durations-min=0` | 799 passed / 115 skipped | 268.49秒 |
| PDF環境の `pytest tests/test_pdf.py tests/test_run.py tests/test_review.py tests/test_pdf_candidate_acceptance.py -q --durations=0 --durations-min=0` | 164 passed | 217.10秒 |
| `pnpm --dir ai exec vitest run --reporter=verbose` | 151 passed / 5 files | 39.30秒 |
| 下記の代表49ケースのfocused実行 | 49 passed / 71 deselected | 33.36秒 |

3テストphaseの合計は524.89秒。Biome/oxlint、Ruff check/format、ty、CLI help、PDF公開importは別途約4.68秒。これは実測したofflineチェックphaseの合計であり、依存install、明示build、wheel、clean npm/pnpm導入、CI開始待ちを含む「正式工程すべて」の総時間ではない。未計測部分を0としない。

探索的なPDF付き全pytestと旧PDF3ファイル実行では、shellのbackground起動でSIGINT無視が継承された条件で、採用pointer前後のSIGINT計2件がtimeoutした。成功baselineから除外する。SIGINTを通常のPython handlerに戻した後の上記PDF4ファイルは164件成功し、該当2件も成功した。製品不具合と断定せず、今後もsignal条件を記録し、background起動だけでSIGINTが無視される状態を持ち込まない。失敗時間を差し引いて成功時間を捏造しない。

PDF4ファイルのsetup/call/teardown合算では、test_runが約96秒、test_pdf_candidate_acceptanceが約92秒、test_reviewが約22秒。候補採用publication中断12ケースは約39秒だが、停止点と再開操作そのものを検査している。長さだけで削減対象にしない。

coreのskip理由とCI選択を照合し、candidate acceptanceの48ケースすべてがDocling不在でskipされ、PDF側3ファイルにも含まれないことを確認した。CONTRIBUTINGはPDF依存テストを別ファイルへ追加したときCI/手順にも追加する契約であり、今回この漏れを補う。

### 最小Probe

人工PDFの既存converter/page-backend fixture、start_pdf、実CLI/Node/fauxで完成状態を1回生成。生成約0.641秒、独立コピー＋公開draft/source/preparation/translation loadが約0.029/0.028秒だった。元rootを別名へ移してから、コピーで公開loadと不正cell refの実revise exit2・byte/request不変を確認し、一方のコピーの変更がseed/他方へ伝播しないことも確認した。

これは移設可能性の根拠であって、新fixtureや全caseの変更後検証ではない。生成/コピー比をsuite短縮率へ外挿しない。Probe workspace・人工credential・model stub・script、一時core環境はcleanup済み。人工ログは `/tmp/doc-translate-planning-xlzs0E/` に残したが、永続可用性は保証せず、Implementationの正式入力や成功証拠として依存しない。

## 実装方針

### 1. 優先候補を狭く変更する

| 優先候補 | 現在の保証・公開境界／必要前提 | 無駄の根拠・変更案 | 残す実経路／リスク |
|---|---|---|---|
| PDF evidence20＋list2: test_pdf_candidate_acceptanceのsource_revision_validates_table_cell_evidence_against_original_scope / source_revision_validates_list_parent_depth_across_pages | 実CLI reviseの原scope/ref/legacy byte/list深さの受理拒否。所有2–3・参照4の原本/draft/image/補正/採用source/完成runと履歴が必要 | 同一start_pdfを22回反復し、この群だけ約16.5秒。対象22ケース限定で今回の完成seedを1回生成し、workspace/初期request logを独立コピー | legacy化・damageはコピー後。全parameter、正常採用と拒否、PATH空、旧byte/pointer/無送信を維持。他のstart_pdf callerは変えない。function scopeのpdf_input/conversion/monkeypatchをmodule fixtureへ直接渡すとscope mismatchになる点に注意 |
| EPUB reviewのinvalid_scope7＋empty/rejected_answers4 | 実CLI reviewの選択範囲拒否、回答scope/usage/品質未承認。同一chosen/secret/heading入りEPUBと完成訳が必要 | 毎回epub_workspaceでprepare2件＋translation1件まで実行。PDF環境でこの群約5.1秒。専用入力の実生成seed/独立コピーを対象11ケースだけに使う | 実review/実helper、空findings、生成失敗、out-of-scope、request増分と保存確認を毎回残す。他入力やPDF画像選択へ共通seedを転用しない |
| EPUB reviseのpatch拒否7＋QA target9 | 実CLI reviseのstale/ID/marker/schema/reason拒否、QA target正常採用/拒否。同じdefault make_epubの完成状態が必要 | 既存malformed_source_seed/workspaceと同一入力を16回再生成。既存の局所seed/コピーをこの群へ再利用 | damage/QA付与、実revise、exit/byte/request確認は各ケースで実行。正常patch→履歴→resume、原文変更→再翻訳などは既存実経路を残す |

代表49ケースは上記6 test関数のparameter集合。比較コマンドは次を使う。

```sh
pytest tests/test_pdf_candidate_acceptance.py tests/test_review.py tests/test_revise.py \
  -k 'source_revision_validates_table_cell_evidence_against_original_scope or source_revision_validates_list_parent_depth_across_pages or review_invalid_scope_never_sends or review_empty_and_rejected_answers_keep_usage_without_quality_approval or translation_patch_rejects_invalid_inputs_without_changing_workspace or epub_source_validates_qa_targets_before_adoption' \
  -q --durations=0 --durations-min=0
```

新しい共有基盤を作らず、既存test-only helperと各module内のfixtureを使う。PDF seedはfunction scopeの外部converter fixtureが有効な最初のconsumerで生成し、module内の局所fixtureが所有して後続consumerへ渡すなど、既存fixture scopeを変えない最小構造とする。seedとconsumerのrootを分け、helperが書き換えるfaux.json/request logを共有しない。全体共通conftestやsession-wide cacheへ昇格しない。

workspaceのoriginal/draft/image/source/preparation/evidence/attempt/translation/chunk/history/pointer/run/render等の必要なファイルを欠落させずコピーする。旧履歴やhash対象内容を間引かない。公開load、初期log byte一致、seed byte不変、コピー分離を小さいcheckで確認する。新fixtureの代表consumer単独とmodule全体の両方を実行する。

### 2. 前提再利用・境界変更・重複整理を分ける

- **前提再利用**: 上記49ケースだけ。検査対象の実操作を削除しない。
- **検証境界の変更**: 今回はCLI revise/reviewや公開loadの境界を変えない。parser/private helper直接呼出への置換、実process保証の同process化をしない。公開境界だからといって各caseで取得から翻訳まで反復する必要もない。
- **重複整理**: Worker/Root/Reviewerの役割を分け、同じ差分・条件でRootの必要な正式実行が済んだ後に、担当交代だけを理由とした全件再実行を要求しない。修正後の影響部分、操作前後、異なる拒否条件・環境の検査は重複扱いしない。
- **保留**: pending_pdf_candidateの移設、候補publication中断の前工程共有、可変render/split/partial/usage、失敗→resume、認証/取消、Node build/JSONL最適化は変更しない。現在は移設や境界変更の追加検証より狭い候補で目的を満たす。時間目標不足を口実に広げない。
- coreとPDF環境で重なるケースも、optional依存の有無という異なる条件を確認している。今回は単純重複として削らない。

### 3. 正式チェックの実行条件を明示改訂する

更新先はCONTRIBUTINGの正式チェックと検証分担。現行条件を調査中に省略したことにはせず、Implementationの規約更新前は現行条件に従う。

**変更ごとのRootの選定手順**
1. 公開契約・変わる動作/前提・影響するcallerを確認する。helper import、pytest_plugins、fixture consumer、Node/Python接続、設定・収集条件まで辿る。
2. 影響を閉じて説明できる変更では、変更ケースのfocused確認、影響module/consumerの回帰、必要な品質チェックと実入口での直接確認をRoot自身が実行する。テスト本体や専用fixtureの局所変更が典型。productionの局所変更もファイル種別だけで除外せず、同じ根拠を要求する。
3. 不変条件や影響を閉じられない変更では全件へ戻す。shared fixture/helper、共通schema/hash/保存/lock、JSONL/helper接続、工程間の再利用・状態遷移、依存/lock/build/配布、pytest収集・CI・検証規約の変更は、広い影響を前提に関連する全件phaseを選ぶ。狭くできる根拠がなければ省略しない。
4. 選んだコマンド、選択/除外理由、保証、環境、skip理由を既存結果報告に残す。自動selectorや新しい台帳は作らない。

PythonのRuff check/formatは安価な現行全体コマンドを維持し、Python変更時の型検査は既存PDF付き環境で行う。Node変更時は既存Biome/oxlintを維持し、局所testの限定実行と、shared helper/source/依存変更時のVitest全件を区別する。helper source変更時は明示buildとPython同梱resourceの鮮度確認も維持する。install/buildは鮮度・環境の必要条件から判断し、毎編集の自動同期・取得を追加しない。

今回の規約・複数fixture変更の最終受入れは、core全pytest、PDF4ファイル、Node全Vitest、既存品質チェック・CLI help・PDF公開importをRootが実行する。新しい限定実行方針を、この改訂自体の全体確認の省略に使わない。

CIはcore全pytestを維持し、PDF pytestを4ファイルへ拡張する。既存順序・offline・品質/build/配布検査・失敗時停止は維持し、skip/continue-on-errorを追加しない。ci.ymlとCONTRIBUTING双方を更新し、48ケースのPDF実行が選択集合に入ることを照合する。

### 4. 文書更新・TDDの適用

- **CONTRIBUTING.md**: 正式チェックの対象・実行条件、PDF4ファイル、実行範囲の判断例、Worker/Root/Reviewer分担、前提共有の境界、今回の実測比較/証拠/cleanup、signal条件を更新する。過去Issueの実行記録は書き換えない。
- 同書の認証節「承認済みの公開seamでTDDを行います」は、当時の実装方針と今後の規約を区別する説明へ直す。今後は要求/保証→公開境界/整合前提/最小経路→対象別TDD採否の順とする。既存Approved Planで明示されたTDDは勝手に解除せず必要なら再計画する。
- 準備/翻訳/render/run/review/revise/resume各節のコマンドは境界別の再現入口であり、同じ担当への毎回の全コマンド実行義務ではないことを整合させる。Issue #39専用の10%計測式を今後一律の規約にしない。
- READMEは既存CONTRIBUTINGリンクを確認するだけとし、利用者文書README/docs/reference/usage.mdやartifact契約/基本設計/GLOSSARY/歴史的Planは変更しない。新しいADR・開発文書一式は作らない。

## 動作確認方法

### 保証対象・対象外とTDD

- 49ケースの受理拒否、byte/hash/pointer/history、入力scope、無送信/usage、品質未承認、seed/load/分離と、残した実接続・取消/復旧を保証する。各ケース・parameter・主要assertを削除/skip/弱体化しない。
- fixture Refactoring・規約更新は非TDD。変更前に成功する既存テストと必要最小のcharacterization checkで等価性を確認する。過去のTDD実装を理由にテストを削除しない。
- CI対象漏れの修正も非TDD。原因はcoreのDocling skipとPDF選択集合の欠落で確認済み。新しいYAML検査runnerや大規模CI用テストを作らず、既存testのcollection・skip理由・設定選択・PDF実行を修正前後で照合する。
- 新しい製品機能・不具合修正へ範囲を広げない。TDD Skillを一括指定しない。別の承認対象でTDDを採用するときだけ既存tdd Skillを使う。
- 実credential/LLM/Docling変換/OCR/意味品質/viewer/他OS/remote CIは今回の直接確認・自動化対象外。恒久E2E/CI基盤、長い追加シナリオは新設しない。

### 実行と証拠の分担

**Worker**
- 編集前にshared helperの全caller/import/plugin/fixture consumerを確認する。特にstart_pdfはtest_reviewからも使われるため、便利さのために既存helper契約を変えない。
- 変更consumerの代表単独、49ケース、変更moduleの回帰と必要な品質チェックを実行する。共有処理に影響が出たら範囲を広げる。Rootと同じ全件一式を機械的に課さない。
- 変更群ごとに保証/前提/除いた工程/残す実操作、実行command/exit/観測/未確認、seed生成回数・request増分・cleanupを報告する。

**Root**
- 現在差分・全完了条件を直接確認し、上記最終正式チェックを自ら実行する。core skip115やPDF164等の件数だけでなく、node ID/parameterとskip理由・実行集合を照合する。意図した追加check以外でケースが消えていないことを確認する。
- pytestの成功とは別に、既存helper/公開CLIを使う人工EPUBと人工PDFで今回の前提を実生成し、別rootのコピーから公開load、seed/他コピー非伝播を自ら確認する。元rootを一時的に退避し、コピーが元pathへ隠れて依存していないことを観測する。
- コピー上の不正revise exit2とbyte/request不変、少なくとも正常reviseの採用/旧版保持、reviewの範囲拒否/無送信と正常の選択request・品質未承認を、変更群に対応する実入口から直接照合する。PDFは既存外部converter/page-backend fixture、review等は実Node/fauxを使い、内部workflow/validatorをmockしない。全パラメータを直接確認で重複再生しない。
- 検証規約の判断例として、閉じたEPUB test本体変更と閉じたPDF test本体変更では対象module/必要consumerで確認可能な理由、共有保存/fixture・影響不明では全件へ戻す理由を差分/callerに照らす。架空の製品変更や自動selectorを実装しない。

**Reviewer**
- 独立したfresh Correctness/Ponytail Reviewへ正式Plan、現在差分、要求との照合、Rootの正式/直接結果、時間raw値、実行範囲の根拠、未確認・cleanupを渡す。Root結果を担当交代だけで全件再実行しない。不足する具体的保証の追加検証は妨げない。
- 修正後はRootが影響したチェック/直接確認/計測を再実行する。共有部分なら全件も実行する。変更前の成功を影響した変更後の保証に流用しない。

### 比較条件・効果判定

1. Implementation開始時にrevision/未コミット差分、OS/architecture、Python/Node/tool/dependency、core/PDF、build鮮度、PATH/offline、signal handler、cold/warm条件を再確認する。Planning値を新しい差分のbaselineへ無条件転用しない。
2. 49ケースは同じPDF付き環境・同じコマンドで変更前後各1回のfresh processをまず比較する。seed生成と公開load/追加checkも含める。判断がばらつきに左右される場合だけ双方を追加2回、raw値・中央値・min/maxで報告する。固定秒数/短縮率の合否を設けない。
3. 全体はcore全件、PDF4ファイル、Node全件を同条件で変更前後各1回。正式チェックと一致すれば兼用し、計測のためだけに全suiteを多数回反復しない。外乱/失敗/判断不能は区別し、必要部分だけ追加確認する。品質チェックも別計測する。
4. 比較は「同じ保証範囲の時間」「従来のPDF3ファイル条件との差（48ケース追加）」「編集直後の49ケース」「Taskflowで不要な全件再実行をしなくてよい条件」を別に扱う。未実行phaseを0とせず、Node非変更時などの非実行理由も示す。install/build/配布/CI待ちは混ぜない。
5. **成功閾値は構造と保証の観測で定める**: 対象ごとの同一前提の実生成をinvocation内の1回へ減らし、各consumerの対象操作・受理拒否・整合性・隔離を維持する。PDF48ケースが正式実行集合へ入り、限定実行/全件実行の判断例が規約と実際の依存に一致する。時間短縮の主張は同条件の観測で支持される範囲だけに限定する。
6. 所要時間が改善しない/悪化した場合は、保証強化による増分、seed/loadコスト、ばらつき等を分けて原因と限界を報告する。固定目標達成のために追加最適化しない。説明できない悪化やfixtureの新しい不安定性は解消または案を縮小し、有意な改善を見込めないと分かった候補を無理に導入しない。目的/保証/範囲を変える必要があればREPLAN_REQUIREDへ戻る。

### 検証資産・安全性

CONTRIBUTINGの環境/build/正式チェック、人工run→load、render/status、revise patch/review、resumeの手順、tests/fixtures、各test helper、ai/testsのnetwork拒否付きprocessを再利用する。project固有の検証Skill/Feature Mapは現状存在しないが、通常テストと上記具体手順/操作手段はある。新しいseedの扱い・選択根拠・signal/cleanupの不足だけをCONTRIBUTINGと関係fixtureへ補う。新しいSkill/Map一式を既定では作らない。

Implementation Rootは現物を再確認し、承認済み保証/対象外/期待結果を維持して、追加installや共有/利用者環境変更なしに隔離できる標準機能の操作・観測を具体化できる。必要な小さいhelperの作成・保存・再利用は一律禁止しない。既存資産の関連更新を優先し、実際に不足するなら共通create-verification-skillを明示readして最小補完する。未予定のSkill/helperというだけでは再計画/追加gateにしない。必須共通コンポーネントの独自代替、権限/認証/安全条件/保証の迂回は不可。安全に成立不能・必要な許可や共通Skillが不足なら理由と再開条件を示してBLOCKED、正式Plan/製品契約/保証の変更が必要ならREPLAN_REQUIREDとする。未成立の手順を成功扱いしない。

今回のrevision/差分範囲/条件/command/exit/raw時間/期待と観測/未確認/cleanupを既存ignore済みwork配下の今回専用directoryへ最小保存する。生環境変数、credential、秘密入りstderrは記録せず、再利用手順と今回の実行記録を区別する。Worker/Rootは各自が作ったprocessを回収し、所有する一時workspace・人工credential・許可された一時環境だけcleanupする。既存venv・model・利用者workspace・work全体は削除しない。証拠はworkspace外へ退避し、cleanup後にログ/出力の参照を再確認する。更新した手順は起動からcleanupまで再実行して成立を確認するが、それだけでRootの全完了条件受入れを代替しない。

## 完了条件

1. 優先49ケースで不要な同一前提生成がinvocation内1回へ減り、各parameter/主要assert/公開CLI/実helperと必要な接続・安全性を維持している。変更群の保証・前提・除いた操作・残す実経路を説明できる。
2. 新/再利用fixtureが単独consumerとmodule実行で成立し、公開load、移設、初期log、旧byte/hash/pointer/history、seed不変/コピー分離を確認できる。
3. PDF48ケースを含む正式実行対象とcore/PDFのskip・選択が整合し、EPUBの必要な検証を維持している。CI/CONTRIBUTINGの対象が一致する。
4. 変更影響に応じたRootの実行範囲と全件へ戻す条件、Agent間分担、対象別TDD採否、文書/手順/証拠/cleanupが更新されている。歴史的Plan/Issueの契約を無断変更していない。
5. Root自身の最終正式チェック、実入口からの直接確認、同条件の前後計測/工程削減の観測と限界報告が成立する。時間短縮未確認を短縮成功としない。固定秒数目標を課さない。
6. Implementationの独立Correctness/Ponytail Review、採用指摘への対応、設定に応じたtaskflow_code_review gateを完了する。過去のApprove/成功を新しい差分へ流用しない。

## 保存・引継ぎ

新しい正式保存先は `docs/plans/verification-design-right-sizing.md`。明示Plannotator Approve後に本文一致を確認して保存する。Work Itemの分割、最終本文と保存/公開先はtaskflow-to-ticketで別々に承認を得る。各Work Itemは本Planの正式パス/解決可能な参照、保証・対象外・検証方法・非TDD・資産/分担/安全限界を維持する。#39は関連元として参照するだけで更新しない。Work Items公開後は停止し、Implementationは別セッションとする。
