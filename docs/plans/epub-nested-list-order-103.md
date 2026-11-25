# #103 改訂: EPUBの入れ子リストの順序を、旧方式の互換なしで直す

## 目的

EPUBの入れ子リストで、親項目の「子リストの後の文」が子リストより前に訳出される不具合（#103）を直す。

元の Plan `docs/plans/review-output-recovery-and-preparation.md`（SHA-256 `cc847266…bcc2`）の「3｜EPUB」は、旧い分け方で作ったworkspaceを旧い分け方のまま読み続けることを求めている。本改訂はこの互換だけを外し、実装を新しい分け方1通りにする。対象は `minorunakamura/doc-translate` の main（調査時 `60ee807`）。

本改訂は #103 だけに適用する。#103 については、本改訂が元の Plan の「3｜EPUB」と、完了条件・動作確認方法のうち所見3に関する記述より優先する。#102・#104〜#107 は元の Plan のままとし、元の Plan のファイルは変更しない。

## 要件・決定事項

### 変えないこと（元の Plan のまま）

- 新規EPUBの `li` では、子リストの前と後を原本の順で別々に翻訳できるようにする。子の後の文は親項目の続きとして表示する。
- 翻訳入力・英語出力・日本語出力・source-map のそれぞれで、「前→子→後」が1回ずつ正しい順に出る。
- 番号・開始番号・深さ・親アンカーは1回だけ出る。inline保護、属性、source-map 上の位置、複数の子リスト、前または後が空の場合、対象外の単位を維持する。
- 原文の抽出結果（source snapshot）は書き換えない。`units.py` と `render.py` を対で直す。
- 保存済みの訳や手動訳を、文字数で切る・ずれたIDへ流す・黙って再翻訳することはしない。

### 変えること（合意済みの設計変更）

- 事実: 準備・翻訳・出力は、保存済みの原文から翻訳の単位を毎回作り直し、保存済みの訳と1件ずつ突き合わせる（`src/doc_translate/translation.py:1042-1048`、`src/doc_translate/preparation.py:787-800`）。親項目を2単位に分けると、該当するリストを含む旧workspaceでは突き合わせが合わなくなる。
- 決定1: 分け方は新しい1通りだけを実装する。「適用方式を記録して、旧方式の保存済み資産をその方式で検証・表示する」（元の Plan）は実装しない。方式を表す新しい保存項目は足さない。
  - 理由: 配布前（0.1.0）であり、#117・#118 で旧workspaceの互換を切ると決めている。`work/` にEPUBの実書籍workspaceはない。
- 決定2: 突き合わせが合わないworkspaceでは、`resume`／`render`／`status --verify` が、AIへ送る前にexit code 2で止まり、「保存済みの訳と現在の文の分け方が合いません。新しいworkspaceで `run` し直してください」という趣旨の案内を表示する。現在の内部向け文言（`Translation source correspondence mismatch` など）をそのまま出さない。
  - 旧方式かどうかは判別しない。破損で突き合わせが合わない場合も同じ案内になる。
  - 自動の再翻訳はしない（黙って課金が起きるため）。
- 決定3: #118 を #103 の blocker にしない。どちらを先に入れてもよい。

### 影響

- 該当するリスト（子リストの後に親の文が続く項目）を含むEPUBの旧workspaceは、`resume`／`render`／`status --verify` が案内付きで止まるようになる。
- 該当するリストを含まないEPUBのworkspaceと、PDFのworkspaceは、単位が変わらないので従来どおり読める。これを回帰として確認する。
- 入れ子リストの結合を期待している既存テスト（`tests/test_units.py` など）は、新しい順序の期待に直す。

## 制約・対象外

- 実provider・実認証・実課金・実書籍の変更は行わない。`work/` 以下は読取専用。検証は人工EPUBと faux provider で行う。
- 未コミットの変更を独断で commit／stash／破棄しない。追加installと共有環境の変更は、ユーザーの許可なしに行わない。
- 既存の認証、送信前確認、取消し、hash検証、atomicな保存、手動訳の優先は維持する。
- 対象外:
  - 旧workspaceを新しい分け方へ変換する移行。
  - `dt`／`dd`、表、PDFのリストの分け方の変更。
  - `translation.py`／`preparation.py` の旧wire形式（1／2）の読込み。元の Plan が「緩めない」と定めており、触れない。
  - #102・#104〜#107 の内容。

## 実装方針

- `units.py`: EPUBの `li` が子リストを持ち、その後に親の文が続く場合、前の部分と後の部分を別の単位にする。単位の順は「前→子リストの各項目→後」とする。版の分岐は作らない。
- `render.py`: 後の部分を、同じ親項目の中の子リストの後に出す。番号と親アンカーは前の部分にだけ付ける。
- 案内: 単位の突き合わせが合わないことを検出している既存の箇所で、利用者向けの案内とexit code 2 に揃える。新しい例外階層や保存項目は足さず、既存のエラー表示の経路（`src/doc_translate/cli.py:399-409` 付近）を使う。
- `src/doc_translate/translation.py` を触る他のIssue（#121、#125）と同時に進めない。
- 文書の更新先:
  - `docs/reference/artifacts.md`: 入れ子リストの単位の分け方。
  - `docs/reference/usage.md`: 突き合わせが合わないときの案内と対処。
  - `.pi/skills/verify-project/` の「EPUB run／resumeと共通進捗（Issue #50）」: 関係する部分。

## 完了条件

- 人工EPUB（親 `li` に「前の文・子リスト・後の文」を入れたもの）で、翻訳入力・英語出力・日本語出力・source-map のそれぞれに「前→子→後」が1回ずつ正しい順で出る。
- 番号・深さ・親アンカーが1回だけ出る。inline保護と属性が保たれる。複数の子リスト、前または後が空の場合も正しく出る。
- `units.py` と `render.py` に、旧い分け方を再現する分岐と、方式を表す保存項目がない。
- 旧い分け方で作った、該当リストを含むworkspaceで、`resume`／`render`／`status --verify` が送信0件・exit code 2・案内文になる。内部向けの文言が出ない。
- 該当リストを含まないEPUBのworkspaceとPDFのworkspaceで、保存済みの訳の再読と無変更 `resume`（送信0件）が変更前と同じ結果になる。
- `uv run ruff check .`、`uv run ruff format --check .`、`uv run ty check`、関係する pytest と core の全pytestが通る。

## 動作確認方法

- 保証するのは上の完了条件である。実providerでの訳文の品質と、実書籍での再翻訳は保証の対象外とする。
- TDD（既存の `tdd` Skill）を次の対象に採用する。
  - 単位の順序: 公開インターフェースは `generate_translation_units`。
  - 出力の順序: 公開インターフェースは `render_document`。
  - 突き合わせが合わないworkspaceでの停止と案内: 公開インターフェースはCLIの `main`。
- TDDにしない対象: 該当リストを含まないworkspaceとPDFの回帰。既存テストの前後比較で確認する。
- 旧い分け方のworkspaceは、変更前のコードで人工EPUBから作る。保存物を手で書き換えて作らない。
- 自動テストは `CONTRIBUTING.md` の正式チェックに従い、`tests/test_units.py`、`tests/test_render.py`、`tests/test_translation.py`、`tests/test_preparation.py`、`tests/test_resume.py` から、差分が触る caller と fixture の利用先を選ぶ。新しいテスト基盤、恒久E2E、CIの追加はしない。
- 直接確認は verify-project Skill（`.pi/skills/verify-project/SKILL.md`）と Feature Map の「EPUB run／resumeと共通進捗（Issue #50）」「保存記録と詳細検査」を使う。不足している手順は、前後で異なる訳を与えた順序の観測と、旧workspaceでの停止案内である。これらは起動→観測→対照→後片付けの手順を、共通の `create-verification-skill` で既存資産へ最小限だけ補う。
- 分担:
  - Worker は focused test と手順の再実行を行う。
  - Root は新しい隔離workspaceで現行の製品入口を直接操作し、command・exit code・日英の出力順・送信件数・案内文を照合する。失敗も記録する。
  - 証拠に書籍の本文と認証情報を含めない。
  - 後片付けは、自分が作った一時ディレクトリ・人工credential・子プロセスだけを対象にする。
- Implementation Root は、承認された保証対象・対象外・期待結果を保ったまま、現在の手段を確認して手順を具体化してよい。追加installや共有環境の変更なしで安全に補える最小のhelperは Root の裁量で作成・保存できる。権限などで成立しない場合は、理由と再開条件を付けて BLOCKED とする。契約・保証・設計の変更が必要な場合は REPLAN_REQUIRED とする。
- Plan の承認と、Issue へ付けるコメントの公開の承認は別に得る。Implementation は別のセッションで行う。
