# PDF原文の代表標本準備（Issue #79）

[Approved Plan](../plans/pdf-source-quality-feasibility.md)の実装方針1の評価準備だけを提供する。通常製品の機能ではなく、AI検査、回復、採否、英語出力への反映、実評価runnerは未提供。警告件数は誤り件数ではない。手順成立や人工回答の受理をAI精度・自動採用成功・確認負担削減と呼ばない。

## 入口と境界

repository rootで既存PDF extra付き`.venv`、pytest、Pillow、Node、既存build済み`ai/tests/pdf-correction-process.mjs`を使う。追加install/sync/build、実Docling/OCR、実認証/preflight/AI、通常run/resumeは使わない。Nodeは既存no-network付きfauxだけで、人工preflightはその内部に閉じる。resource鮮度は[開発ガイド](../../CONTRIBUTING.md#正式チェック順序を含む)に従い確認し、不足時は停止する。

```sh
export PATH="$PWD/.venv/bin:$PATH" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# 今回承認された非公開evidence領域。既存Workspaceではない新しい0700親を使う。
umask 077
EVIDENCE=$(mktemp -d "$PWD/work/issue79-preparation-XXXXXX")
git rev-parse HEAD > "$EVIDENCE/revision.txt"
git status --short > "$EVIDENCE/dirty.txt"
git diff > "$EVIDENCE/scope.diff"
.venv/bin/python .pi/skills/verify-project/scripts/pdf_source_feasibility.py synthetic \
  --output "$EVIDENCE/synthetic"
# 書籍の読取りは対象とprivate保存先が承認されている場合だけ。
.venv/bin/python .pi/skills/verify-project/scripts/pdf_source_feasibility.py book \
  --workspace work/design-and-build-great-web-apis --output "$EVIDENCE/book"
```

`--output`は未作成で、直上の親は0700。既存出力、既存Workspace配下、元との同一/包含、symlink経路を拒否する。CLIはignore済み`work/`内だけを受け付ける。上書き・hardlink・自動再開はしない。書籍の例外値には本文が入り得るため、CLIは例外型だけ表示する。失敗directoryは成功とせず保持し、原因確認は非公開領域内だけで行う。通常stdout/Git/Issueへ本文・画像・raw回答を出さない。

### 自作対照

`pdf_source_samples.py`は`tests/test_pdf.py`のconverter/page backend人工fixtureを局所再利用し、既存共有fixtureを変更しない。自作文書をPillowで実際に描いたPDF/PNGを生成し、公開`acquire_pdf`→公開`correct_pdf`（実Node/faux、明示的な人工応答）→公開`assemble_pdf_source`を通す。白紙画像に意味正解を置かず、完成済sourceの手書きだけでもない。モデル重みは人工fixtureの前提検査用で、実OCRは起動しない。

4組8例を用意する。

- 不正charspan由来のtargetなし履歴がある現在正常本文と、実欠落/追加・表cell・数値/codeの変更がある対照。同じ組の2ページ目は測定値が塗り潰された原本で、隠れた正解値を置かない。
- 空本文の正常図と、OUTLETを失うcrop＋図内文字を本文に重複させた対照。
- 本文の跨ページ正常接続と、欠落＋矛盾した接続の対照。
- codeの跨ページ正常接続と、構造上は接続できても命令行が欠ける対照。警告なしでも現在正常とは限らない。

`synthetic-inputs/sample-NN/`は取得/補正履歴と組立済`current-source.json`、原本/PNGを保持するがsource採用pointerを作らない。入力名に正常/欠陥ラベルを置かず、設計した人工variantと比較提案は`review-only/proposed-comparisons.json`へ分離する。この表はRoot照合前の提案であり、確定正解ではない。補正回答は品質判断ではなく、正常/欠陥の現在状態を作る人工応答である。

### 実書籍の読取専用準備

元source SHA-256は`22325d32f4ed4babf3da97b7bfd94ea983b6629e2e805798b2e690c60575f471`に固定する。具体的chunk digestはhelperの`BOOK_CHUNKS`に固定し、無断で範囲を広げない。

| 所有ページ | 許可reference | 選定上の役割（画像による正解ではない） |
| --- | --- | --- |
| 36–40 | 35, 41 | targetなし履歴、空図、本文接続 |
| 81–85 | 80, 86 | その他の判読不確実性、正常対照候補 |
| 181–185 | 180, 186 | code接続の片側、targetなし履歴 |
| 186–190 | 185, 191 | code接続の相手と成功接続、空図、本文接続、その他 |

4範囲各5所有ページ、計20所有。後2範囲は接続を切らず**必ず一緒に組み立てて照合**する。10所有ページを5ページrequestと偽らない。本helperは送信requestを作らず、将来の実送信scope/cap承認は別。61–65は65/66の成功接続を切り、閉包に66–70も必要なので初期標本に含めない。実書籍の本文失敗接続の代表性には限界が残る。

公開`load_source_snapshot`後、4chunkを一度の公開`assemble_pdf_source`へ渡す（全70chunkの個別load反復はしない）。元sourceと部分sourceを`fragment(chunk_sha256, original block_id)`の系列で対応させ、本文/kind/locationsとID対応後の警告multisetを比較する。ID振り直しを内容変更に数えず、部分再組立で障害が消える資料は拒否する。全元fileのhash/mtimeを前後照合する。

コピーは選択chunk、全20所有と必要referenceのPNG、現在block/fragment/警告に限定。原本PDF・raw全体・source全体は複製せず、元artifact/hash参照を残す。`book-inputs/`は**単独で公開load可能なWorkspaceではない**。元の固定hash参照が再検証に必要で、後続の実評価用隔離Workspace作成を提供したことにはしない。

## 表と証拠の読み方・送信前停止

- `book-inputs/page-N.json`：所有/reference区分、PNG、現在の接続済block（跨ページ全文を含む）/構造/fragment/警告。reference-onlyページの現在block一覧が空でも、画像contextを検査済みとはしない。
- `correspondence.json`：元ID↔部分IDとfragment系列。`references.json`：元workspace/source/draft/chunk hashと4範囲、切断不可の接続group。
- `review-only/page-review.json`：全画像とページ別内容への索引。`root_image_review=pending`、現在状態・回復可能性・期待確認要否/採否はnullから開始する。自作の表も同じく未固定。
- `observations.json`：操作/集計/未採用/実送信0/cleanup。`hashes.json`：保持全fileのSHA-256とcleanup後readback対象。`original-inventory.json`と`preservation.json`：書籍元fileのhash/mtime不変。

Rootは**全標本の原画像と現在内容を直接照合**し、正常/現在欠陥/回復可能/判定不能、期待確認要否/採否と理由を、別の非公開review資料へ固定する。既にhash化した入力・証拠を後から書き換えない。期待と正解を将来の評価model入力へ渡さず、`review-only`を丸ごと入力に混ぜない。referenceは所有結果の代用品ではない。人照合なし、Agentの見落とし/相関誤り、人工応答の限界を残す。実書籍に復元可能な実欠陥を確認できない場合、合成例で実書籍回復成功を代用しない。

ここで停止する。実AI/auth/preflight、検査・回復・採否・通常CLI統合は行わない。将来は対象画像/本文/範囲/hash、固定期待、model/config、全retry横断cap、認証操作と保存先を提示して別承認を得る。このIssueの準備完了を新方式の有効性証明にしない。

## 再実行とチェック

[verify-project](../../.pi/skills/verify-project/SKILL.md)の「PDF原文標本準備」から、既存の保存-only/公開PDF取得補正手順と本helperだけを選ぶ。明示実行の小さいself-checkは以下。Skill内の専用テストなので通常製品のCI収集集合・共有fixtureを変更しない。

```sh
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_feasibility.py::test_pdf_source_samples_use_public_acquire_correction_assembly -q
.venv/bin/pytest .pi/skills/verify-project/scripts/test_pdf_source_feasibility.py -q
.venv/bin/pytest tests/test_pdf.py tests/test_pdf_source.py -q
```

代表consumer単独fresh process→新module→関連公開境界の回帰を実行し、Rootは全体Ruff check/format、既存PDF extraのty、影響moduleと別invocationの直接操作・全画像照合、Reviewerは独立受入を担当する。共有fixture/src/Nodeへの影響が広がれば[開発ガイドの選定](../../CONTRIBUTING.md#変更影響から正式実行範囲を選ぶ)へ戻る。無関係な全Map/全pytest/PDF4/Vitest/buildを自動追加しない。

自作の一時PDF/model/credential/workspaceだけをTemporaryDirectoryでcleanupし、同期helper childは既存generateで回収する。保持資料はその外側へcopyして全fileをhash再読する。書籍ではchild/一時Workspaceを作らず元はread-only。command/exit、revision/dirty/diff、環境/resource hash、期待/観測/失敗/未確認を同じ非公開evidence親に記録し、終了後に再読する。実AI意味品質、実OCR、通常run/resume、全冊、他OS/remote CIは未確認。
