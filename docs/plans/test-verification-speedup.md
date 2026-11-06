# doc-translate のテスト・検証時間短縮計画

## 目的

ローカル Implementation と CI の検証時間を、必要な接続・状態遷移・中断復旧・安全性・データ整合性の保証を維持して短縮する。テスト実装の不要な前工程の反復を減らすことと、最新 taskflow に合わせた検証分担・再実行手順の明確化を行う。テスト軽量化と正式チェックの実行条件変更は別の判断として扱う。

対象 repository: `/Users/minoru/Documents/mywork/cli-dev/doc-translate`。

## 要件・決定事項

- ユーザー確認済みの性能目標: **候補3ファイル（`tests/test_translation.py tests/test_revise.py tests/test_render.py`）の合計所要時間を、同条件の変更前後各3回の中央値で10%以上短縮**する。正式チェック全体には有意な悪化を生じさせず、全体への実測効果と限界を報告する。CI 全体の短縮率を推測・保証しない。
- 各テストの保証対象と前提作成を分ける。整合した前提の再利用は許容するが、接続や状態遷移自体を検査する経路、取消・復旧・安全性・データ整合性の検査は実際に通す。
- 既存の公開 API、fixture、helper、開発ガイドを優先する。前提再利用のために production code を変えない。
- **正式チェックの対象・実行条件は今回変更しない。** `CONTRIBUTING.md` の core-only 全 pytest、PDF extra 対象 pytest、品質チェックを維持する。最新 taskflow を根拠に full suite を間引かない。
- Worker は対象テストと focused regression、Root は自分で正式チェックと完了条件の直接確認、Reviewer は差分と今回の証拠から必要な追加検証を判断する。実行主体の交代だけを理由に全 suite を重複させない。Worker 報告で Root の実行を代替しない。
- 過去の成功と再利用する手順を区別する。今回の revision・差分に対するコマンド、終了コード、観測、未確認事項、証拠を Review に渡す。変更で影響した保証は変更後に再確認する。

## 制約・対象外

- この Planning では実装を開始しない。Implementation は正式に保存した Approved Plan と確認済み Work Item を入力とする別セッションで行う。
- CI job 分割・並列化、runner 待ちの解消、taskflow 自体の変更、新しい一般的な検証基盤は対象外。
- 新規依存・lock 更新、production のテスト専用 API・時計注入 API・不要な抽象化は追加しない。
- 実 credential の読取り、実認証・実 LLM 送信、model 取得は別途個別承認なしに行わない。offline 設定は全通信を防ぐ firewall ではないため、既存の network 拒否付き faux helper と一時 credential directory を使う。
- 実 Docling/OCR の品質、意味品質、viewer 表示、課金・account 利用権を今回の機械検査の成功から主張しない。
- 開始時および各作業前に未コミット差分を確認し、利用者の変更を巻き込まない。Planning 開始時は clean、確認 HEAD は `522d893adfa8cda817c420ace2fae34cafd9d70c`。
- CodeGraph は Planning で `status` を確認して未初期化。明示許可なしに初期化・再構築しない。
- remote CI 実行・成功は既存規約どおり受入れ必須ではない。計測だけのための commit/push、既存 CI の再実行、merge/release/deploy は要求・実行しない。

## 確認済みの事実・根拠

### CI とローカル計測

指定 run の API 情報とログを直接確認した:
https://github.com/minorunakamura/doc-translate/actions/runs/42207282981

- run HEAD: `44239fa2b5f8fb45484ec618bdfe517ba6ea3362`。created `2026-11-06T11:03:02Z`、job 開始 `11:08:06Z`、job 完了 `11:19:02Z`、run 更新 `11:19:03Z`。
- 全体約16分1秒、開始待ち約5分4秒、job 約10分56秒。開始待ちは別要因。
- core-only pytest: **771 passed / 115 skipped、338.17秒**。
- PDF extra 対象 pytest: **116 passed、164.66秒**。pytest 合計 **502.83秒**。Node は **151 passed、43.73秒**。
- この記録では依存導入よりテストが主要時間を占める。ただしこの run と現在 HEAD は異なり、変更後の同条件比較 baseline にそのまま使わない。

Planning の既存ローカル環境: Darwin `25.6.0 arm64`、Python `3.14.9`、Node `24.22.1`、pnpm `12.10.1`、uv `0.13.5`、PDF extra 導入済み。CI は `macos-15`、Node `24.21.0` であり条件は一致しない。tool/依存の install や sync は実施せず、PATH に既存 `.venv/bin`、`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` を設定した。

| Planning の観測 | コマンド・条件 | 結果 |
|---|---|---|
| 候補3ファイル | `.venv/bin/pytest tests/test_translation.py tests/test_revise.py tests/test_render.py --durations=30 --durations-min=0.1 -q` | 144 passed、74.08秒 |
| Node 全体 | `pnpm --dir ai exec vitest run --reporter=verbose` | 151 passed、38.71秒 |
| Python 全体 | `.venv/bin/pytest -q --durations=40 --durations-min=0.1`、他のテスト計測と非並行、PDF extra 付き | 913 passed、474.65秒 |

最初の2計測は並行した探索計測であり、性能合否用 baseline ではない。全体計測も現在環境での単回観測であり core-only CI とは混同しない。

個別観測: `test_translation_preserves_finite_adapter_retry_and_all_usage[always]` は9.69秒、inference の実3/6秒 retry 検査は9.53秒。PDF の候補採用・中断復旧は上位に複数存在し、おおむね2～6秒/ケース。遅さだけで前提と判定しない。Node の30秒 grace 検査は30.213秒、heartbeat は11.722秒であり、実時間・別 process の保証を検査している。

### 前提再利用の成立 Probe

一時 directory 内だけで既存 helper と公開 API を使い、終了時に作成物を cleanup した。repository のコードは変更していない。

- 固定 `prepared()` 生成3回は `0.24268 / 0.23368 / 0.23510秒`、中央値約0.235秒。生成済み workspace のコピーと公開 `load_preparation` 3回は約 `0.00435 / 0.00443 / 0.00436秒`、中央値約0.00436秒。
- 生成元を削除してから、コピー上で実 faux helper 翻訳と公開 `load_translation` が completed になることを確認した。
- EPUB の `run` で作った workspace と request log を2つの別 directory へコピーし、生成元を削除した。PATH 空の条件で公開 preparation/translation load、`workspace_status` の current、stale な source replacement の CLI exit2、workspace 全ファイル hash と request log byte の不変を確認した。
- これは移設可能性と前提再利用の成立根拠であり、実装変更後の fixture 分離や malformed 全5ケースを検証済みとは扱わない。Probe の生成/コピー比を suite の短縮率へ外挿しない。

### 実装判断に必要な参照

- `CONTRIBUTING.md`: 「正式チェック（順序を含む）」「文書context・glossary準備」「翻訳・採用snapshot」「Markdown／対応表／QA・render/status」「新規run」「基点付き修正・PDF補正候補採用」「fixture・CI」「resume／障害復旧」。
- `.github/workflows/ci.yml`: core-only 全件、PDF extra の3ファイル、Node 品質、wheel/別環境検査、offline 設定。
- `tests/test_preparation.py`: `source`、`run`。`tests/test_translation.py`: `prepared`、`prepare_blocks`、`translate` とそれらの全 caller。
- `tests/test_revise.py`: `test_source_replacement_rejects_malformed_whole_snapshot_before_adoption`、関連する実 revise/resume の成功・履歴保持テスト。
- `tests/test_render.py`: `save_source`、`epub_workspace`、実CLI render/status、partial/split/usage を検査するケース。
- `tests/test_run.py`: `helper`、`run_args`。`tests/test_resume.py`: `requests`、`saved_files`、`assert_preserved`。`tests/fixtures/resume_process.py`: OS replace 境界の既存故障注入。
- `src/doc_translate/preparation.py::load_preparation`: source/evidence/hash/attempt/実request の対応まで再検証するため、表面上の snapshot JSON だけの再利用は不十分。
- `ai/tests/generation.test.ts`、`ai/tests/generation-process.mjs`、`ai/src/generation.ts`: 非協調 provider の30秒 grace。`ai/tests/credentials.test.ts`、`credentials-process.mjs`、`ai/src/credentials.ts`: proper-lockfile の stale interval を越す heartbeat と別 process 排他。
- `pyproject.toml`、`ai/package.json`: 現行テスト/品質コマンドと依存。`docs/plans/doc-translate-initial.md` の「TDDと公開テスト境界」「正式チェック」と remote CI 改訂、`docs/design/doc-translate.md` §12/14/15/19: 製品の安全性・保存・接続契約と検証限界。
- 確認した最新 taskflow: `/Users/minoru/Documents/mywork/pi-dev/pi-taskflow/skills/taskflow-planning/SKILL.md` と `references/playbooks/performance.md`、`../taskflow-implementation/SKILL.md` §3～8。プロジェクト内の規約を正本にし、開発ガイドの実行時にこの絶対パスを必須にしない。

## 実装方針

### 1. 保証と時間を対応させる

変更前に現在 HEAD の収集結果と `--durations=0 --durations-min=0` 等の既存 pytest 出力で、候補の node ID ごとの setup/call/teardown、ファイル合計、suite wall time を取得する。Vitest は既存 verbose reporter を使う。計測表は既存の ignore 済み `work/` 配下の今回専用 directory に置き、恒久 benchmark runner や timing 依存の pytest 合否判定を追加しない。

各変更テストについて「保証対象／必要な整合条件／取り除く前工程／実経路を保持する対照／変更前後時間」を記録する。テストの削除・skip・parametrize 間引き・assert の弱体化で時間を稼がない。

### 2. 固定 prepared の生成反復を減らす

`tests/test_translation.py::prepared()` が作る同一 source・固定 glossary/context/policy の前提を対象とする。

- pytest の module scope 等、**1回の pytest invocation 内だけ**で既存 `source` → 実 `prepare_document` → 実 Node helper/faux を1回実行して seed を生成する。
- 各 consumer には seed workspace の独立した writable copy を function scope の `tmp_path` に渡す。source、準備 snapshot、evidence、関連 attempts と pointer を含め、公開 `load_source_snapshot/load_preparation` で整合を確認する。
- seed は変更しない。copy が seed のファイルや他ケースと共有される hardlink、前回実行の成果を読む永続 cache、手作業で捏造した準備 artifact は使わない。人工 credential directory を使い、利用者 credential は扱わない。
- `prepared()` の全 caller を確認して必要な引数・fixture 受渡しを更新する。`prepare_blocks()` と `translate()` の呼出契約を便宜的に変えない。
- `test_translation_adopts_and_reuses_fixed_inputs_without_sending_local_payload` は少なくとも1本の実準備→実翻訳→公開load/無送信再利用の対照として残す。
- 入力変更、attempt 改変、保存失敗、取消、再開などの**検査対象となる操作自体**はコピー後に現行どおり実行する。別入力の準備をコピーして hash/evidence を後付けで合わせない。
- fixture 固有の小さい characterization check として、独立コピーの片方の変更が seed/他方を変えないこと、生成元以外の path から公開loadが成立することを確認する。既存テストで十分観測できる部分は重複テストを増やさない。

### 3. EPUB source 拒否の5ケースで共通前提を再利用する

`tests/test_revise.py::test_source_replacement_rejects_malformed_whole_snapshot_before_adoption` の unknown target / inactive schema / tree / envelope / stale を対象とする。

- 既存 `make_epub`、`helper/run_args` と実 CLI seam で1回生成した完成 workspace を、ケースごとに新規 `tmp_path` へコピーする局所 fixture を使う。
- manifest、original、source/preparation/translation、attempt、出力等の必要な既存ファイルを保持する。`requests()` が読む初期 request log もコピーし、既存の「3件から増えない」の保証を維持する。すべての path の移設可能性を確認し、hash 対象の内容を書換えて辻褄を合わせない。
- 各コピーへ damage を与え、実 `main(['revise', ...])`、exit2、全既存 byte 不変、推論 request 増分0を確認する。状態生成や成功 adoption のテストをこれらの拒否テストで代替しない。
- translation patch、実修正→resume、PDF候補・中断復旧など、異なる整合条件・状態遷移のテストはこの固定 seed へ一括移行しない。

### 4. render と長時間保証の扱い

- `tests/test_render.py` は性能比較・回帰確認の対象とするが、現在の第一案では実装変更しない。可変 source の `prepare_blocks()`、render の split/partial/failure usage、stale/atomic/writer、危険markup/asset、空PATH実CLIは維持する。
- 実装時の計測で**同一入力・同一整合前提**の反復が明確になった場合だけ、既存 helper による局所的な前提再利用を選べる。入力別 artifact の合成、大きな汎用 builder/cache、実 split/usage 経路の置換は行わない。効果不足を理由に保証を減らさない。
- Node の30秒 grace と10.5秒以上待つ heartbeat は維持する。fake timer や短い timeout への置換は別 process・実時間保証を同等に示さず、今回は採用しない。Python の有限 retry の実3/6秒待機も維持する。
- 上位に現れた PDF 状態遷移・中断復旧を、前工程という一語でまとめて短絡しない。本計画の小さな前提再利用で目標未達なら、計測結果を示して達成不能/再検討を報告し、無断で対象を拡張しない。

### 5. 検証規約・再現手順の最小更新

更新先は既存の **`CONTRIBUTING.md`** とする。正式チェック節の近くに taskflow の分担・再実行・計測を記載し、準備/翻訳/render/revise の関係する節へ必要な補足だけを行う。過去の Issue #18 等の実行結果は現在の成功に上書きしない。既存 Approved Plan や基本設計の歴史を性能改善のために書換えず、製品仕様・artifact 契約は変更しない。

現物確認では、起動・操作・大部分の期待結果は既にある:

- 正式チェックの環境・build順序と core/PDF 境界。
- `render-source.pdf.json` を使う Node不要の直接 render/status。
- `make_epub` と `run-process.mjs` を使う人工 run、source/translation patch、resume の直接手順。
- 実 process の中断復旧 helper と期待する保存hash/request不変。

不足は、Worker/Root/Reviewer の分担、今回の revision と証拠の対応、終了コードや期待 artifact の機械的照合、生成/コピー前提の使い分け、同条件計測、作成物の安全な cleanup を一続きで実行できる説明である。これだけを既存手順に補う。

- 起点 repository、必要な既存環境、PATH/offline、一時 directory、実 faux helper の指定、入力・操作・期待 exit/JSON/artifact、秘密を含まない証拠取得、cleanup を明記する。
- 正常ではないが期待どおりの exit1/2 と、コマンド失敗を区別する。shell の `set -e` 等で期待 exit を取りこぼさず、JSONと保存物も照合する。
- `work/test-verification-<run>/` 等の今回専用・既に ignore 済みの場所に、revision、差分範囲、環境、依存状態、コマンド、終了コード、時刻/所要時間、結果、期待/観測、未確認条件の最小証拠を保存する。再利用手順と今回の記録を分ける。
- 生 credential、raw環境変数、秘密入りstderrを記録しない。証拠を workspace 外へ残した上で、自分が `mktemp`/`TemporaryDirectory` で作った workspace/人工credential/process だけを cleanup する。利用者の `work/` 全体や既存環境は削除しない。cleanup 後に証拠が参照できることを確認する。
- 各機能節の focused 実行と正式 full 実行は「同一担当へ毎回すべてを要求するリスト」ではなく、役割と時点を明記する。ただし正式 full の必須条件は変えない。

新しい検証 Skill/harness/Feature Map の新設は現時点では不要。Implementation Root は現在の手段を再確認し、承認済み保証・対象外・期待結果を変えず、追加 install や共有/利用者環境変更なしに安全に隔離できる最小の操作・観測方法を具体化できる。必要な小さい helper の作成・保存・再利用は一律に禁止せず、既存 helper/手順の関連更新を優先する。実際に資産不足で共通 Create を使うなら `create-verification-skill` の実ファイルを先に読み、安全境界と初期対象の成立確認に従う。未予定というだけで追加 gate にしないが、必須コンポーネントが利用不能なら独自代替せず不足と再開条件を報告する。安全・認証・権限上成立しない場合は BLOCKED、保証/仕様/正式 Plan の変更が必要なら REPLAN_REQUIRED。期待値を緩めて解決しない。

### 6. 変更範囲

| 対象 | 予定 |
|---|---|
| `tests/test_translation.py` | 固定 prepared の invocation 内 seed/隔離コピー、consumer 更新、必要最小の分離確認、実経路対照の維持 |
| `tests/test_revise.py` | malformed source 5ケースの局所的共通前提化と consumer 更新 |
| `tests/test_render.py` | 基本は変更せず計測/回帰。前節の条件を満たす小さな再利用だけ許容 |
| `tests/test_preparation.py`、`tests/test_run.py`、`tests/test_resume.py`、`tests/fixtures/` | 既存helper/fixtureの再利用元。原則変更不要。fixture共有が本当に必要な場合のみ最小の test-only helper 抽出と全caller更新。汎用fixture基盤を作らない |
| `CONTRIBUTING.md` | 分担、前提再利用境界、比較計測、今回の証拠/照合/cleanup 手順 |
| `.github/workflows/ci.yml` | job構成/テスト範囲は不変。必要なら既存 pytest コマンドへ標準 `--durations` 出力を追加するだけ。正式チェックの緩和・並列化はしない |
| `pyproject.toml`、`ai/package.json`、lock類、production、配布資材、README、既存設計/初期Plan | 現時点で変更不要。新しい marker/skip、依存、export、production API は追加しない |

変更する shared helper は全callerと import/pytest plugin 範囲を検索してから編集する。前提の再利用は生成済み fixture を渡す仕組みに限定し、validator/generate/save の mock に変えない。

## 動作確認方法

### TDD 方針

今回の Performance/検証文書整理には TDD を要求しない。既存の成功する tests、必要な小さい characterization check、不変条件、同条件 baseline/再計測を用いる。過去の初期実装 Plan に対する TDD 方針を本作業の red test 義務へ広げない。非 TDD は回帰確認不要を意味しない。

### 性能比較

1. Implementation 開始時の変更前 revision と差分・Python/Node/OS/architecture・依存環境・extra・helper build状態・実行コマンドを記録する。依存導入/build時間、テスト時間、CI開始待ちを別々に扱う。
2. 候補3ファイルは**他の suite と並行させず**、同じ既存の PDF extra 付き環境・同じPATH/offline・同じ入力・同じ計測オプションで、変更前後それぞれ fresh pytest process を3回実行する。各 invocation が seed を生成するコストも含める。意図的な warm-up を計測外に隠さず、依存/model downloadは行わない。
3. raw値・中央値・min/max・node別 setup/call/teardown を記録し、`after_median <= before_median * 0.90` を合格とする。追加の分離確認のコストも含める。node別だけ改善して suite が未達なら合格にしない。
4. core-only 全 pytest と PDF extra の既存3ファイルは、同じ環境条件の変更前後各1回をまず比較する。Node全体も基準を変えず比較し、初回importやcold/warm状態を明記する。正式実行と条件が一致した計測は兼用し、計測用というだけで同じチェックを増やさない。
5. 正式各 phase の時間比が `after/before > 1.05`、またはばらつき・外乱で判断不能の場合のみ、同条件で双方の追加2回を取り、3回中央値で再評価する。中央値でも5%超の悪化が残れば非劣化条件は未達。候補3ファイルの10%短縮だけで全体の悪化を受け入れない。
6. fresh core-only と PDF extra の切替は既存 `CONTRIBUTING.md` の sync手順を維持する。baseline のために安全条件・依存を勝手に変更しない。追加installや共有環境変更に許可が必要なら確認し、許可範囲で成立不能なら不足・再開条件を報告する。
7. CI実行が別途存在する場合は、対応 revision、同じ job/テスト範囲、環境差、各step時間と開始待ちを分けて報告する。過去の502.83秒やローカル474.65秒との単純な割合比較を改善証明にしない。remote未実行は明記し、CI成功や短縮を推定しない。

### 保証維持・直接確認

- 変更前後の pytest collection とケース一覧、skip理由を照合する。原則として既存の node ID/パラメータ/主要assertを維持し、追加checkや fixture都合の名称変更は対応を示す。単なる passed件数一致だけで保証維持としない。
- 固定前提の公開loadによる整合、コピー分離、seed不変を確認する。fixtureを使うケースの単独実行とモジュール実行の両方で成立することを確認する。
- 実準備→翻訳→再利用の対照、実 revise/resume 成功経路、拒否全5ケース、render/statusの空PATH、split/usage、取消/drain、hash破損、atomic/writer、中断復旧は既存testsで確認する。新しい包括的E2E、長時間待機ケースの追加、全組合せの自動化は行わない。
- Root は pytest 成功とは別に、既存ガイドと更新した手順から今回専用の人工 EPUB を実CLI seam＋実Node/fauxで run する。保存物・公開load・出力と request を観測し、固定済み workspace のコピーを元 directory から切り離しても成立すること、別コピー間の変更非伝播を自ら確認する。
- コピー上で不正 source replacement を実 CLI `revise` に渡し、exit2、採用pointer/既存byte不変、追加送信なしを確認する。既存ガイドの render/status と小さな人工訳文 patchも使い、正常の期待 exit0/partialのexit1 と JSON/日本語出力・QAを照合する。実LLMの意味品質は対象外。
- 更新した手順は会話履歴を参照せず、起動→操作→期待結果照合→証拠取得→cleanupまで Root が再実行し、不成立/未確認を区別する。fixture成立だけで全完了条件の受入れを済ませない。

### 正式チェックと担当

- **Worker**: 変更した fixture consumer と対象テスト、関連 preparation/units/inference、実 revise/run/resume/render の必要な focused regression、必要な lint/format を実行する。shared helper の変更で影響が広ければ範囲を広げるが、Root と同じ全正式チェックを常に一式要求しない。差分・コマンド/終了コード・未確認・証拠・cleanupを報告する。
- **Root**: Worker後の現在差分を読み、自ら `CONTRIBUTING.md` の正式チェックを実行する。core-only 全 `pytest`、Ruff check/format、CLI help、PDF extra の `ty check`・公開import・`tests/test_pdf.py tests/test_run.py tests/test_review.py` を維持する。既存準備済み環境の利用/依存同期と Node build の必要性も現物確認する。Node source/test/fixture/configを変更した場合は Biome/oxlint/Vitest、helper source変更なら明示buildを行う。比較上維持対象の Node長時間保証も今回の結果を取得する。
- **配布検査**: 既存のCI wheel/別環境検査は維持する。依存/lock/build/package resourceを変更しない本作業を理由に、npm/pnpm clean install・実LLM統合受入れを何度も追加しない。これらの影響が生じたら既存正式条件に従って実行し、未承認の範囲変更はしない。
- **Reviewer**: 最新 taskflow の独立した Correctness/Ponytail Review を維持し、正式 Plan、実際の差分、保証対応表、Rootの正式結果/直接観測、比較のraw値、証拠と未確認条件を受け取る。全suiteの機械的再実行ではなく、不足する具体的保証に応じて追加実行する。既存証拠は実行主体が異なるだけで失効させないが、変更後の影響部分を過去の成功で済ませない。
- **修正後**: Root が影響した保証・正式チェック・直接確認を再実行する。shared fixture 等の広い影響ではfull suiteも実行し、無関係な全チェックの反復と必要な再検証を区別する。最終差分が性能計測後に変われば影響した計測もやり直す。

## 完了条件

1. 変更した各テストの保証対象と前提作成、維持した実経路の対応が説明でき、テスト削除/skip/期待値緩和、production変更で保証を回避していない。
2. 固定 prepared と EPUB source拒否ケースが、実経路から今回生成した整合seedと独立コピーで成立する。seed・別ケース不変、単独実行、公開load、request増分の期待を確認できる。
3. 同条件・各3回の候補3ファイル合計中央値が10%以上短縮し、正式phaseの非劣化条件を満たす。未達を隠さず、必要なら範囲/手段の再検討へ戻る。
4. 正式チェックの対象・実行条件を維持し、Root自身の正式実行と直接確認が成立する。Worker報告・過去CI成功・fixture生成だけで代替していない。
5. `CONTRIBUTING.md` に検証分担と再実行・計測、既存手順の不足（期待結果照合・証拠・cleanup）が補われ、会話履歴なしで再実行できる。新しい検証基盤を不要に導入していない。
6. 今回の revision・条件・raw時間・コマンド・終了コード・期待/観測・未確認範囲・cleanup結果を Review へ引き継ぎ、証拠は cleanup 後も解決できる。remote未実行、CIとの環境差、品質保証の限界を明記する。
7. Implementation の独立Reviewと、設定に応じた `taskflow_code_review` gate を完了する。実装の開始、merge/release/deploy はこの Planning の完了に含めない。

## Planning 後の引継ぎ

承認後の正式保存先は既存配置に合わせて `docs/plans/test-verification-speedup.md`。保存内容を readback して承認本文との一致を確認し、`taskflow-to-ticket` で小さな成果として分割/保存先/最終本文・公開を個別確認する。fixture変更と対応する検証規約・計測結果は切り離して受入れ不能にせず、正式 Plan を各 Work Item から参照する。Planning 調査ログを正式入力へ昇格させず、重要な事実と確認方法は本 Plan と repository の一次情報から解決する。Work Item 公開後も Implementation は自動開始しない。