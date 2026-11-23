# #111 ページをまたぐ PDF コード（73→74）を、紙面どおり1つのコードとして接続する

## 目的
[#111](https://github.com/minorunakamura/doc-translate/issues/111) を直す。73枚目の `p73-code`（`Home` → `-> Home`）と74枚目の `p74-code1`（`Home` → `-> Account` …）は、原画像では1つの Home ワークフローの例である。新しく PDF を処理する利用者の出力で、これを1つのコードとして、紙面の行順のまま各行1回だけ出す。

**前提：** 元の全体 Plan [pdf-initial-source-preservation.md](pdf-initial-source-preservation.md)（Docling の文字を基準、正解・ページ番号・特定 ref を判定に使わない、旧記録は旧規則で厳密に再読）と、[#99 第2版](pdf-initial-source-preservation-99-r2.md)・[第3版](pdf-initial-source-preservation-99-r3.md)の接続判定（相互適合 → 両端で一意 → 介在の検査）を引き継ぐ。対象 revision は `336f18d`（#110 まで）。旧 Plan・Issue は書き換えない。

## 入力設計からの変更（ユーザー確認済み）
#99 第2版の要件1「採用で追加された evidence だけで誤 target を救済しない」と、期待表の行「相手の採用後 evidence にだけ target が一致（実例73→74）→ 接続0」を、**新方式（schema 18）の記録に限り**、下の要件1の狭い条件で緩める。それ以外の #99 の判定（相互適合、相手の断片数による一意性、介在、ページ順・読順、newline・language）は変えない。schema 17 以前の記録は従来どおり接続しない。

## 確認した事実（Planning で直接確認、private）
- 最新の全70件再生 `work/preservation-all-tum3kOHp/proof/`（revision `1217835`＋`tested.diff`。現在の `336f18d` の src/tests 差分と一致、exit 0）。p73-code（chunk `f5a2dee1…`）と p74-code1 は別 block のまま、「Unresolved outgoing／incoming code continuation」の QA と失敗した `pdf_connection` check が付く。
- Docling（`work/design-and-build-great-web-apis/drafts/pdf/raw/c74ce852….json`）：p74 は `#/texts/817` section_header `Home`、`#/texts/818` code（`-> Account` から2つ目の `-> CreateActivity(...)` まで）、`#/texts/819`–`820` list_item（`#/groups/34`）。
- AI 案（保存回答）：p73-code の outgoing.target は `#/texts/817`。p74-code1 の案の evidence は `#/texts/818` だけで、案の本文は `Home` から始まる（先頭行 `Home` は 817、案の evidence の外側の前）。p74-code1 の incoming.target は `#/texts/810`（p73-code の案の evidence の先頭）で、案・採用の両方に一致する。採用後、p74-code1 の evidence に 817・819・820 が追加される。
- 全70件の linked code 57 件で、target が相手の案の evidence に一致せず採用後 evidence にだけ一致するのは、この1件（p73-code outgoing）だけ。
- 原画像（73–74枚目）：p73 末尾の `Home`〜`-> Home`（5行）に続き、p74 冒頭の `Home`〜`-> Home`（5行）、空行、`Home`〜`-> Home`（5行）。その後に本文。

## 要件・決定事項
1. **採用後にだけ一致する target を認める条件（新方式だけ）。** #99 の「相互適合」の判定で、一方の link（outgoing または incoming）の target が相手の**案の evidence に一致せず**、採用後の evidence にだけ一致する場合、次をすべて満たすときに限り、その方向を適合とみなす。
   - ① target が指す ref（draft-local ref／異 draft の実 item の同一性、selector は既存の `_matches` と同じ）は、組立対象のうち**相手の断片1つだけ**の採用後 evidence にある。
   - ② その ref の採用範囲の文字が、相手の**案の本文**に含まれ、接続する端（相手が右なら案の先頭行、左なら末尾行）にあり、相手の案の evidence に対応する行より外側（右なら前、左なら後）にある。ref の範囲の文字以外を根拠にしない。
   - ③ 逆方向の link は従来どおり、案と採用の両方の evidence で相手に一致する（両方向とも採用後にだけ一致する場合は認めない）。
   - 上を満たした組も、従来どおり両端で相手の断片が1つだけ、間が空か独立脚注・#109 の囲みだけ、ページ順・読順・language・newline が整う場合にだけ結ぶ。認めた結果を理由に別の候補を一意にしない。
2. **拒否の対照。** 次は従来どおり接続しない（両断片と未解決 QA を残す）。target の ref が相手の案の本文の端でない行、相手の案の本文にない行、相手以外の断片にも採用されている行、案の evidence 行より内側の行を指す場合。逆方向も採用後にだけ一致する場合。schema 17 以前の記録。
3. **記録（新しい採用方式）。** 新しい採用方式名（例：`adopted-edge-target-v17`）と chunk schema 18 を追加する。採用規則（`pdf_adoption.py`）は変えない。保存要求・再利用・orphan 回収・`load_pdf_correction` は既存の `_RULE_VERSIONS` で結び付け、schema 17 以前は旧規則で厳密に再読・組立する。`CorrectionAnswer` は変えない。
4. **表示は変えない。** 接続後の行順・空行・fragments・locations・callout・QA の対応は既存の接続処理を使う。新しい QA 種別は追加しない。接続した境界の未解決 QA は消え、他の QA（p74-code1 の空行数の QA など）は残る。

## 制約・対象外
- 過去の Workspace・訳文の修復、旧 chunk の書き換え・昇格・再送はしない。実 AI 送信、retry、実認証、外部 trial marker へのアクセス（読み取りを含む）、OCR、install、共有環境の変更はしない。
- 採用規則（行の回復・所有）と、介在・一意性の接続規則は変えない。ページ番号・ref・書籍固有の文字列・固定座標を規則に使わない。
- #101 の完了、#110、#109、75→76 など他の未接続は対象外。
- 書籍の本文・画像・生回答・エラーは private（`work/`）だけに置き、Issue・通常 stdout・Git に出さない。
- 既存の未コミット変更・検証記録・旧 Plan・固定見本は保持し、独断で commit・stash・破棄しない。

## 実装方針
- **接続（`src/doc_translate/pdf_source.py::assemble_pdf_source`）：** schema 18 の断片同士の組だけ、相互適合の判定（`points_to` を使う `forward`／`backward` の作成）で、要件1の条件を追加する。schema 17 以前を含む組は現行の判定のまま。mixed boundary 側の分岐は現行の `forward`／`backward` を使うので、それ以外は変えない。
- **方式と記録（`src/doc_translate/pdf_correction.py`）：** `ADOPTION_RULE` を新方式名に、`_RULE_VERSIONS` に旧 `drawn-table-listing-v16: 17` を残して新方式を 18 に、`PDFCorrectionChunk.schema_version` に 18 を加える。`pdf_source.py` の schema 比較（`>= 12` など）が 18 でも同じに働くことを確認する。
- **既存テストの期待の変更（ユーザー確認済み）：** `tests/test_pdf_source.py::test_added_listing_range_cannot_substitute_for_original_target` は同じ形なので、新方式では「接続する」に変わる。期待を改め、要件2の拒否の対照を加える。旧 schema で同じ入力が接続しないことは保存再読の形で押さえる。
- **検証資産：** `.pi/skills/verify-project/scripts/pdf_preservation_comparison.py` の方式・schema の受け付けを新方式に最小限更新する。
- **文書：** `docs/reference/artifacts.md` に schema 18 の節（採用後にだけ一致する target の条件、旧記録との関係）を追加し、既知の未解決にある 73→74 を解決済みの説明に置き換える。`.pi/skills/verify-project/references/feature-map.md` に #111 節を追加する。`docs/reference/usage.md`・CONTRIBUTING.md は利用者操作・正式チェックの入口が変わらないため更新しない。

## 完了条件
- **73→74 の接続：** 新方式の全70件再生（閉じた pipe、保存70・拒否0・未試行0）の source・実 units・英語出力で：
  - p73 の5行と p74 の11行（空行を含む紙面の行）が1つのコード block として、紙面順に各行1回、英語出力に1回だけある。
  - fragments が `p73-code`・`p74-code1` の2つで、locations に両ページの出典がある。
  - この組の「Unresolved outgoing／incoming code continuation」QA と失敗した `pdf_connection` check が消える。p74-code1 の既存の空行数の QA は残る。
- **変化の説明：** 基準 `work/preservation-all-tum3kOHp/proof/` と新の全70件で採用差分・接続差分を取る。schema・方式名だけの差以外の差がなく、接続差分は p73-code→p74-code1 の追加だけ。それ以外の差は原画像と照合して説明できなければ合格にしない。
- **拒否の対照・互換性：** 要件2の各場合が接続しない。schema 17 以前の記録の再読・再利用・旧規則での組立、新方式の保存・別 process の再読が成立する。人工 CLI の通常 run → fresh resume で成功済みの再送0、immutable 不変。
- 正式チェック、独立 Review、人間の Code Review を最終差分で受ける。

## 動作確認方法
- **TDD（`tdd` Skill）：** 要件1・2の接続判定に限定。`tests/test_pdf_source.py` の公開 `correct_pdf → load_pdf_correction → assemble_pdf_source` と既存の小さい自作入力（`competing_listing_reply` など）を使う。
  - 正例：右の案の先頭行（案の evidence の外側）が採用後に右へ所属し、左の target がその ref を指す組が接続する。左右反転（左の案の末尾行）も1例。
  - Red：現行コードで正例が接続0になることを確認する。
  - 拒否：要件2の各場合（端でない行、案の本文にない行、別断片にも採用、案 evidence の内側、両方向とも採用後のみ、schema 17）。既存の拒否テスト（相互複数、途中本文、順序）が通り続けること。現行で既に通る拒否例を Red と称さない。
- **非TDD：**
  - **全70件の再生：** verify-project の `pdf_preservation_comparison.py all` を新規 0700 親で最終差分に対して1回。拒否1件でもあれば比較に進まず停止。採用差分（`adoption-diff`、基準 `tum3kOHp`）と接続差分を #110 節と同じ方式で取る。
  - **原画像との照合（Root が直接）：** 73–74枚目の原画像と source・実 units・英語出力・QA を行ごとに照合する。変化したほかのブロック・組があれば同様に照合する。
  - **人工 CLI：** verify-project の #97 節の通常 run → fresh resume（実 Node／faux／no-network）を別の新規親で実行。
  - 実書籍の全翻訳、実 provider の起動、全組合せ E2E、新しい CI は行わない。
- **正式チェック：** CONTRIBUTING.md「変更影響から正式実行範囲を選ぶ」に従う。chunk schema を変えるため、Ruff check/format、PDF 環境の ty、core の全 pytest、正式 PDF phase を最終差分で実行する。
- **検証資産：** verify-project の #101・#110 節と `pdf_preservation_comparison.py`・`pdf_connection_comparison.py`・`pdf_adoption_replay.py` を再利用し、方式・schema の受け付けを更新する。#111 の照合手順と期待を Feature Map の #111 節として共通 `create-verification-skill` に従って最小限補う。Implementation Root は、同じ保証の範囲で、隔離した手段と小さい helper の作成・保存・再利用を判断してよい。
- **分担：** Worker は TDD・focused 回帰・全70件の再生・人工 CLI・Feature Map の更新と手順の再実行。Root は別の新規の親として、73–74 と全変化の原画像照合、正式チェックの結果を直接確認する。版・入力の hash・コマンドと exit・比較結果・失敗を private に記録し、作成した一時ファイルと child だけを cleanup する。
- 許可された範囲で安全に操作できなければ、理由と再開条件を示して BLOCKED とする。保証・期待・公開契約の変更や、要件1以外の新しい規則（採用規則の変更、介在・一意性の緩和など）が必要になったら REPLAN_REQUIRED とする。
