# コードレビュー指摘（2026-11-25）の修正と整理

## 目的

2026-11-25 のコードレビューで挙がった12件の指摘を、独立して着手できる成果に分けて直す。対象は `minorunakamura/doc-translate` の main（調査時 `60ee807`）。利用形態は「1人が自分のMacで1冊ずつ、数百ページ・数時間」を前提とする。

狙いは次の4つ。

- 意図しないAIへ文書を送らない。止めたいときに止まる。失敗の原因が後から分かる。
- workspaceと共有ディレクトリが際限なく膨らまない。
- 配布前（0.1.0）のうちに、使っていない互換と重複を捨てて保守範囲を小さくする。
- テストと文書を、実行環境に左右されず読める大きさにする。

## 要件・決定事項

各項目は1つの成果（Work Item の候補）であり、番号はレビューの指摘番号に対応する。ユーザーが決めた事項は「決定」と明記する。

### 1. 翻訳snapshotの容量を抑える（#105 の計測後）

- 事実: `translation._adopt`（`src/doc_translate/translation.py:754-772`）はchunkが1件終わるたびに全体snapshotを新しいファイルとして保存する。`work/design-and-build-great-web-apis` では chunk 411 に対し約4MBのファイルが804個、計3.26GBある（複数回の実行の合計）。
- 決定: 修正のIssueを新しく作り、計測Issue #105 を blocker にする。削除方式と合格値は #105 の計測結果で決める。#105 の本文・状態・範囲は変えない。
- 要件: 1回の `run`／`resume` で増える翻訳snapshotの容量を、#105 の計測値を基準に減らす。途中で強制終了しても、最後に確定した参照から再開できることを保つ。
- 消してはいけないもの: `translation-snapshot.json` が指すsnapshot、`review`／`revise`／`render` の保存記録が参照するsnapshot、`previous_snapshot_sha256` でたどる履歴。過去の実行が残した既存ファイルは自動で消さない。
- `docs/reference/artifacts.md:399` の「旧版も保持」の記述を、新しい保持範囲に合わせて直す。

### 2. 環境変数を `DOC_TRANSLATE_*` に改名する

- 事実: `run` は `PI_PROVIDER`／`PI_MODEL`／`PI_REASONING_LEVEL` を既定値に使う（`src/doc_translate/inference.py:70-76`）。pi agent が同じ名前を設定するため、pi の中から実行すると指定していないAIへ文書が送られる。`resume` と `review` はこれらを読まない。
- 決定: `DOC_TRANSLATE_PROVIDER`／`DOC_TRANSLATE_MODEL`／`DOC_TRANSLATE_THINKING` に改名する。`PI_*` は読まない。旧名を受け付ける移行期間は設けない。
- 設計書 `docs/design/doc-translate.md:229` と `docs/reference/usage.md` を同時に直す。これは設計書の記述を置き換える合意済みの変更である。

### 3. PDF読み取り中でも Ctrl-C で止まる

- 事実: `run._cancellation`（`src/doc_translate/run.py:190-201`）はSIGINT／SIGTERMでフラグを立てるだけである。`acquire_pdf`（`src/doc_translate/pdf.py:184`）は取消しの引数を持たず、フラグを見ない。Probeでは、同じ仕組みの下でブロッキング処理にSIGINTを送っても最後まで走った。
- 要件: PDF読み取り（docling変換、ページ画像化）の途中でSIGINT／SIGTERMを1回送ると、数秒以内にexit code 1で終わる。確定済みの保存物と参照は壊さない。次の `resume` で続きから進む。
- 方式は実装で選ぶ。フラグを確認できない区間では既定のシグナル動作に任せる方法を第一候補とする。取消し用の新しい永続状態は作らない。

### 4. 旧PDF補正ルール（v1〜v16）の再現をやめる

- 事実: `pdf_correction.py` は補正ルール18世代（`_RULE_VERSIONS`、現行は `adopted-edge-target-v17`）をすべて再現する。`adopt_owned_ranges`（`src/doc_translate/pdf_adoption.py:2652`）は1374行、`layout_version` による分岐が35か所ある。版分岐は `pdf_correction.py:518-538, 855-891` と `pdf_source.py:104,121,133,135` にもある。
- 決定: 現行ルール以外の再現を削除する。旧ルールで補正したPDF workspaceを `resume` したときは、AIへ送る前にexit code 2で止まり、新しいworkspaceで `run` し直すよう案内する。自動の再補正はしない（黙って課金が起きるため）。
- 維持するもの: 旧ルールのworkspaceでも `render` と `status` は動く（補正chunkを再検証しないため。原文候補の採用待ちがある場合を除く）。現行ルールの挙動は変えない。
- 旧ルール名を使うテスト（`tests/test_pdf_correction.py` に約50か所）と、verify-project Feature Map の旧版に関する節（#97〜#101 の「第3版・第4版」など）を、現行ルールだけの内容に直す。
- 既知の影響: `work/design-and-build-great-web-apis` の補正chunk 70件はすべてv1であり、`resume` できなくなる。

### 5. 失敗の原因をworkspaceに残す

- 事実: 想定外の例外では `"translation failed; inspect saved source and attempts"` のような固定文だけを保存する（`src/doc_translate/run.py:745-755` 付近）。`src` に `logging` も `traceback` もない。
- 要件: 想定外の例外で工程が失敗したとき、例外の種類・メッセージ・tracebackをworkspace内のファイルへ書き、その場所を端末に1行で示す。
- 書いてはいけないもの: 認証情報、書籍の本文、AIの生の回答。既存Planの「失敗時に生回答やログを修復しない」方針は変えない。

### 6. 設定不足のエラーを、足りないフラグ名で示す

- 事実: provider などが未指定だと、pydantic の `3 validation errors for GenerationConfig ...` がそのまま表示される（`src/doc_translate/inference.py:79-86`）。
- 要件: 足りない設定ごとに、フラグ名と環境変数名（項目2の新しい名前）を示す1つのエラーにする。exit code 2 は変えない。

### 7. Node helper との境界を薄くする（3つの成果）

- 7a 未使用の5 option を削除する。
  - 事実: `temperature`／`maxTokens`／`transport`／`cacheRetention`／`thinkingBudgets` には設定手段がなく、常に `null` で保存される。
  - 決定: 削除する。既存workspaceの読み替えは足さない。**翻訳済みの既存workspaceは、PDF・EPUBを問わず `render`／`status --verify`／`resume` が失敗するようになる**（保存済みchunkの設定と現在の設定の完全一致を検査するため。`src/doc_translate/translation.py:1072`）。ユーザーはこの影響を承知して選んだ。
  - 要件: 旧workspaceに当たったときは、pydantic の生エラーではなく「古い形式のworkspaceなので新しく `run` し直す」案内でexit code 2 にする。
- 7b npm に一本化する。
  - 決定: pnpm 対応をやめる。`ai/pnpm-lock.yaml`、`ai/pnpm-workspace.yaml`、`setup-ai --package-manager` の選択肢、`scripts/check_distribution.py` の pnpm 経路を削除する。`--ignore-scripts` 相当の安全策は npm 側で保つ。設計書 `docs/design/doc-translate.md:255` と `docs/reference/usage.md:16` を直す。
- 7c Python側の再検証を簡素にする。
  - 事実: `_validate_final`（`src/doc_translate/inference.py:293-356`）は helper が検査済みの出力を再検査する。エラーコード表が3か所に重複している。
  - 決定: Python側の検査は、回答のschema検証と `request_id` の一致に絞る。エラーコード表は1か所にまとめる。
  - 維持するもの: 認証失敗・保存失敗・入力上限の区別、送信前の確認（`before_send`）、取消し。

### 8. `_store`／`_read`／`_atomic_write` を1か所に集める

- 事実: 同じ役割の関数が `pdf.py:101`、`translation.py:138`、`preparation.py:133`、`snapshot.py:225` にある。`run.py` は `translation._store` を借りている。
- 要件: 1つのモジュールに公開名で集め、各所はそれを使う。保存形式、hash検証、atomicな書込みは変えない。循環importを避けるためだけの関数内importは、解消できる範囲で減らす。

### 9. SIGINTを使うテストを、起動方法によらず通す

- 事実: `tests/test_run.py::test_pdf_adoption_source_pointer_is_only_commit_despite_remaining_candidate_pointer` の `[before-SIGINT]` と `[after-SIGINT]` は、SIGINTを無視する親から起動すると `TimeoutExpired`（15秒）で必ず失敗する。再現方法は `( trap '' INT; .venv/bin/pytest <そのテスト> )` またはシェルの背景実行（`&`）で、どちらも 2 failed, 2 passed。CPU負荷を8プロセスかけた前面実行では4件とも通った。原因は負荷ではない。子プロセスが「SIGINT無視」を継承し、Pythonが既定のhandlerを入れないためである。
- 要件: テスト側で子プロセスのSIGINT動作を既定に戻し、背景実行・SIGINT無視の親・前面実行のどれでも通るようにする。`tests/test_resume.py` の `external_cli` を使う他のSIGINTテストも同じ条件で確認する。製品コードの挙動は変えない。

### 10. MITライセンスを付ける

- 決定: MIT。`LICENSE` を追加し、`pyproject.toml` と `ai/package.json` に記載する。著作権者名は git の author（minoru）を既定とし、別名にする場合は実装時にユーザーへ確認する。
- 事実: 依存は MIT（pydantic、rich、docling、pi-ai、proper-lockfile）、MIT-CMU（Pillow）、BSD-3-Clause/Apache-2.0（pypdfium2）、PSFL（defusedxml）で、MITでの配布と矛盾しない。`CONTRIBUTING.md:794-799` のlicense節を合わせて直す。

### 11. 古いAI runtimeを消す

- 事実: `setup-ai` は版ごとに `~/.local/share/doc-translate/ai` へ導入するが、古い版を消さない（`src/doc_translate/ai_runtime.py:228-262`）。現在2版で197MBある。
- 要件: `setup-ai` が成功して現行版を確定した後、同じディレクトリ内の現行版以外を削除する。失敗時は何も消さない。別プロセスが使用中の版を壊さないよう、既存のlockの範囲内で行う。doc-translate が作ったディレクトリ以外には触れない。

### 12. 文書を整理する（2つの成果）

- 12a 旧計画の削除と `--help` の短縮。
  - 決定: `docs/plans/` のうち、closed Issue だけが参照する計画と旧改訂版（`-r2`〜`-r6`）を削除する。削除前に、README・CONTRIBUTING・設計書・コード・テスト・skill・open Issue から参照されていないことを1件ずつ確認する。closed Issue は path と SHA-256 を記録しているので、git履歴から復元できる。
  - 消さないもの: `docs/plans/review-output-recovery-and-preparation.md`（open の #102〜#107 が参照）、本Plan、他の文書やコードから参照されている計画。
  - `--help` の各説明を、1項目につき1〜2行に短くする。詳細は `docs/reference/usage.md` へ送る。
- 12b `docs/reference/artifacts.md`（187KB）と `CONTRIBUTING.md`（130KB）の圧縮。
  - 決定: 本文の取捨まで行う。
  - 要件: 現在の契約と手順だけを残す。Issueごとの経緯、過去の版の説明、重複した説明を削る。目安は各ファイルを現在の半分以下とする。届かない場合は残した理由を報告する。
  - 項目4・7・1 が同じ文書を書き換えるため、それらの後に行う。

### 13. PDF補正ルールの境界方針を決める（検討のみ）

- 背景: 直近9コミットはすべて、特定の紙面に合わせた分岐の追加である。「AIの補正結果を決定的なルールで上書きする」層が最大の保守コストになっている。
- 決定: 検討Issueを作る。成果は方針の文書であり、実装はしない。
- 要件: 「ルールを足して直す範囲」と「ルールを足さず要確認箇所（`※`）として出す範囲」の判断基準を、既存の実例（#97〜#111）に当てはめて示す。採否はユーザーが決める。

## 制約・対象外

- 実provider・実認証・実課金・実書籍の変更は行わない。`work/` 以下の既存workspaceは読取専用。検証は人工入力と faux provider で行う。
- 未コミットの変更を独断で commit／stash／破棄しない。追加installと共有環境の変更は、ユーザーの許可なしに行わない。
- 既存の認証、送信前確認、取消し、hash検証、atomicな保存、手動訳の優先は維持する。
- open の #102〜#107 とその正式Planは変更しない。
- 対象外:
  - Node helper の廃止とPython SDKへの置換え（ユーザーが選ばなかった）。
  - PDF補正ルールの新規追加・作り直し（項目13は方針の検討まで）。
  - 既存workspaceを新形式へ変換する移行ツール。
  - `preparation.py` の旧wire形式（1／2）の読込み。既存Planが「緩めない」と定めており、今回は触れない。
  - `.pi/skills/verify-project/scripts/test_*.py` の扱い。現在のmainで実行すると 2 failed, 34 passed, 63 errors になる（`Protected implementation changed: REPLAN_REQUIRED`）。過去の実験を守るためのテストとみられるが、残すか消すかは未調査であり、今回は決めない。`testpaths` とCIにも追加しない。
  - テスト全体の所要時間（約690秒）の短縮。`docs/plans/test-verification-speedup.md` の範囲であり、今回は扱わない。

## 実装方針

- 成果は上の項目単位で分け、各Issueは単独でmainへ入れられる大きさにする。
- 依存関係:
  - 項目1は #105 の完了後に着手する。
  - 項目6は項目2の後（エラー文に新しい変数名を出すため）。
  - 項目12bは項目1・4・7a・7b の後。
  - 項目2・6・7a・7c は `src/doc_translate/inference.py` を、項目1・8 は `src/doc_translate/translation.py` を同時に触る。同時に進めず順に入れる。
  - それ以外は独立している。
- 新しい抽象化、設定項目、永続状態は足さない。削除と既存の仕組みの再利用を優先する。
- 互換を切る項目（4・7a）は、旧workspaceに当たったときの案内文とexit code 2 を必ず実装し、テストで固定する。
- 文書の更新先:
  - `docs/design/doc-translate.md`: 環境変数名（項目2）、npm一本化（項目7b）。
  - `docs/reference/usage.md`: 環境変数、`setup-ai`、取消し、旧workspaceの案内、失敗記録の場所。
  - `docs/reference/artifacts.md`: 保存形式と保持範囲の変更（項目1・4・5・7a）。
  - `CONTRIBUTING.md`: チェック手順とlicense節。
  - `README.md`: 導入コマンドとライセンスの記載。
  - `.pi/skills/verify-project/`: 関係する節。
  - 項目13の成果: `docs/design/` に方針文書を置く。

## 完了条件

- 項目1: #105 と同じ条件の計測で、1回の実行が増やす翻訳snapshotの容量が基準より減っている。強制終了後の `resume`、`review`、`revise`、`render` が通る。参照中のsnapshotが残っている。
- 項目2: `PI_PROVIDER` などを設定し `DOC_TRANSLATE_*` とフラグを指定しない `run` が、AIへ送らずexit code 2 で止まる。`DOC_TRANSLATE_*` を設定すれば動く。`src` に `PI_PROVIDER`／`PI_MODEL`／`PI_REASONING_LEVEL` の参照が残っていない。
- 項目3: PDF読み取り中にSIGINTを1回送ると、数秒以内にexit code 1 で終わり、その後の `resume` が成功する。
- 項目4: `_RULE_VERSIONS` と版分岐が現行ルールだけになっている。旧ルールのworkspaceで `resume` が送信0件・exit code 2・案内文になり、`render` と `status` は動く。現行ルールのテストは変更前と同じ結果になる。
- 項目5: 例外を注入した実行で、workspace内に種類・メッセージ・tracebackが残り、端末がその場所を示す。記録に認証情報と本文が含まれない。
- 項目6: 未設定で実行すると、足りないフラグ名と環境変数名が表示され、pydantic の文言が出ない。
- 項目7a: 5 option がPython・Node の型、保存物、テストから消えている。旧workspaceで案内文とexit code 2 になる。
- 項目7b: pnpm のlockと設定がなく、`setup-ai` が npm だけで成功する。配布チェックが通る。
- 項目7c: エラーコード表が1か所になっている。不正なschemaと `request_id` 不一致を拒否する。エラー種別の区別が変わっていない。
- 項目8: `_store`／`_read`／`_atomic_write` の定義が1か所になっている。保存物のbyteが変更前と一致する。
- 項目9: `( trap '' INT; pytest <対象> )`、背景実行、前面実行のすべてで対象テストが通る。
- 項目10: `LICENSE` があり、`pyproject.toml` と `ai/package.json` の記載と一致する。配布チェックが通る。
- 項目11: 古い版がある状態で `setup-ai` を実行すると現行版だけが残る。失敗させた場合は古い版が残る。
- 項目12a: 削除した計画への参照が、リポジトリと open Issue に残っていない。`--help` の各説明が1〜2行になっている。
- 項目12b: 2つの文書が現行の契約と手順だけになり、リンク切れがない。大きさが目安に届くか、届かない理由が報告されている。
- 項目13: 判断基準と実例への当てはめを書いた方針文書があり、ユーザーが採否を判断できる。
- 共通: `uv run ruff check .`、`uv run ruff format --check .`、`uv run ty check`、関係する pytest、Node側を触った場合は Node側の正式チェックが通る。

## 動作確認方法

- 保証するのは上の完了条件である。実providerでの動作、実PDFでのdocling／OCRの品質、実書籍の再翻訳は保証の対象外とする。
- TDD の採否（採用する場合は既存の `tdd` Skill を使う）:
  - 採用するのは、軽い失敗経路がある次の項目とする。
    - 項目2と6: `run` の設定解決。公開インターフェースはCLIの `main`。
    - 項目4と7a: 旧workspaceでの停止と案内。CLIの `main(["resume", ...])`。
    - 項目5: 例外注入後の保存内容。`run`／`resume` の公開入口。
    - 項目11: `setup-ai` 後のディレクトリ内容。
  - 採用しないのは次の項目とする。
    - 項目7c・8（リファクタリング）と、項目4・7a の削除部分: 既存テストと保存byteの前後比較で確認する。
    - 項目1（性能）: #105 の計測を基準に、前後を同じ条件で比べる。
    - 項目3: 別プロセスへのシグナル送信による直接確認と、既存の `external_cli` を使う最小の回帰テストで確認する。
    - 項目9: 再現コマンドの前後比較で確認する。
    - 項目7b・10・12・13: 配布チェックと文書の確認で行う。
- 自動テストは `CONTRIBUTING.md` の正式チェック（64〜101行）に従い、差分が触る caller と fixture の利用先を選ぶ。保存形式や共有fixtureを触る項目（1・4・7a・8）は core の全pytestを実行する。新しいテスト基盤、恒久E2E、CIの追加はしない。
- 直接確認は verify-project Skill（`.pi/skills/verify-project/SKILL.md`）と Feature Map の関係する節を使う。
  - 「保存記録と詳細検査」: 項目1・5・7a。
  - 「PDF run／resumeの工程・所有ページ進捗（#51）」と「PDF補正の制限付き並列（#61）」: 項目3・4。
  - 「形式別run／resume性能とrender負荷（#54）」: 項目1。
  - 不足している手順は、PDF読み取り中のシグナル送信、旧workspaceでの停止案内、`setup-ai` 後の掃除、環境変数の取り違えである。これらは起動→観測→対照→後片付けの手順を、共通の `create-verification-skill` で既存資産へ最小限だけ補う。
- 分担:
  - Worker は focused test と手順の再実行を行う。
  - Root は新しい隔離workspaceで現行の製品入口を直接操作し、command・exit code・表示・保存物・送信件数を照合する。失敗も記録する。
  - 証拠に書籍の本文と認証情報を含めない。
  - 後片付けは、自分が作った一時ディレクトリ・人工credential・子プロセスだけを対象にする。
- Implementation Root は、承認された保証対象・対象外・期待結果を保ったまま、現在の手段を確認して手順を具体化してよい。追加installや共有環境の変更なしで安全に補える最小のhelperは Root の裁量で作成・保存できる。権限などで成立しない場合は、理由と再開条件を付けて BLOCKED とする。契約・保証・設計の変更が必要な場合は REPLAN_REQUIRED とする。
- Plan の承認、Issue 分割の承認、Issue 本文と公開の承認はそれぞれ別に得る。Implementation は別のセッションで行う。
