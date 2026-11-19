# PDFの初回補正で脚注・captionを独立して残し、根拠のあるコードだけをページ間で接続する

## 目的
[Issue #99](https://github.com/minorunakamura/doc-translate/issues/99) の停止を解消する。作業は二つに分ける。**先行項目では「何がコードで何が脚注・captionか」を原本に基づいて保存する。#99では保存できたコード同士を接続する。** 一方の成功で他方の成功を代用しない。

今回の正式入力は本改訂と[全体Plan](pdf-initial-source-preservation.md)（SHA-256: `2ed173321bfa12e890499f48eea593863482eb65e502dede7a1e79ec9bb4f798`）。#99の前提・順序・検証は本改訂を優先する。[#97 第5版](pdf-initial-source-preservation-97-r5.md)の本文保持、[#98 第3版](pdf-initial-source-preservation-98-r3.md)の表示行・字下げ・空行・QAの契約を維持する。#97・#98は再開しない。本文の接続は#100、全体Planの8接続・全書籍で変わる組の無送信差分と原画像照合等の統合受入れは#101に残す。

## 停止理由と確認できた根拠
- 基点は `fix/pdf-source-preservation` の `ba0643b`。停止記録は `work/issue99-root-probe-pC0ufkUC/` の `probe.py`、`revision`、`probe/{summary.json,inventory.json,error-181.txt,error-186.txt}`。保存回答とは、以前の初回補正でAIが返し、attemptに保存された未編集の補正案。正解でも採用済み原文でもない。
- 同じ原本・draft・所有/参照ページ・投影・画像の下で、その回答を外部送信なしに公開 `correct_pdf` に通した記録。最初の所有ページが71・111・116・121の4 chunkは版10で保存された。181–185と186–190はどちらも `_make_chunk → adopt_owned_ranges` で `Overlapping ownership of extraction text`。二つのchunkが同じページを所有したのではなく、**一つの抽出項目内の同じ文字を複数ブロックが要求した**。この2件の接続はassembleに届いていない。
- `work/design-and-build-great-web-apis/` の該当attempt・Docling raw・原本PDFの範囲内の描画行を読取専用で照合した。185枚目は同一項目のコード7行のあとに書体の異なる番号付き1行があり、その行が脚注案の全文と一致する。187枚目は先頭1行がcaption案と一致し、案の参照先は直下6行のコード。187の局所分割は現行規則でも通るが、同chunkの186で先に止まる。181・182・186でも先頭1行がcaption、後続がコードの候補だが、現行のcaption分割はコード整形の照合にも依存して成立しない。183のコードと本文が原文の別範囲に一致する正常例も壊さない。これは**範囲を裏付ける材料の確認**であり、新規chunkの成功やコードの字下げの証明ではない。

## 要件・決定事項
1. AIの役割分類をやり直さない。案は候補として扱う。同じ所有ページの同じ抽出箇所で、検証した原本の対応する描画行・役割の根拠とDoclingの読順・文字範囲が一致し、各可読部分を正しい役割に一度だけ割り当てる分割が一通りのときだけ採用する。脚注は独立した脚注、captionは対応するコードを指す独立したcaptionとして元の位置に残す。
2. **所属と整形を別に採点する。** 所属が証明できても、案の文字差・未証明の字下げ・空行は通さない。Doclingの読める文字・意味のある空白・数値・記号・出現順と回数を守り、裏付けられない整形は確実な原文と既存の位置付きQAに戻す。逆に、所属そのものが不明なら元の拒否経路を維持する。全空白除去、近さ・書体・ref・回答順単独、先勝ち、片方を捨てる処理は禁止。
3. 先行項目は両chunkの採用・保存・再読と役割の検証まで。#99はそれを前提にコード接続の5組と負例を検証する。2組を外す、成功したchunkだけで判定する、警告や手作りの採用chunkで穴埋めすることはしない。

## 実装方針
- **先行：初回採用。** `src/doc_translate/pdf_correction.py::_make_chunk` から呼ぶ `pdf_adoption.py::adopt_owned_ranges` の共有refの競合判定に限定する。既存の読順・provenance検査と、検証した原本の局所描画行を読む処理を使う。明示spanがあれば尊重し、文字列の全出現・行の前後・区切り・対象参照を突き合わせる。対象範囲内に未配分の可読文字、競合する割当て、範囲外や異なる役割の混入があれば採用しない。分割できた場合は採用側のevidenceも非重複の範囲に限定し、原案や元のtargetを書き換えない。既に成立する分割を壊さず、整形は#98の規則で別に判断する。
- **後続：#99のassemble。** `pdf_source.py::assemble_pdf_source` のコード接続のみを対象に、採用前からある両端の提案と採用範囲の両方を照合する。候補は相互target、draft-local ref／異draftの同一性、明示span、newline、code・language、ページ/読順、介在ブロックで絞ってから**両側で一意**な場合だけ結ぶ。独立脚注は残す。captionや途中本文を飛び越える一般許可は加えない。脚注・captionの位置・対象、calloutの行参照、fragmentとQAを保つ。採用時のevidence追加で元の誤targetが見かけ上一致しても救済しない。73→74はその反例（元のoutgoingが右コードの根拠とは別の抽出項目）。不成立なら両断片と未解決記録を残す。
- **版と再現性。** 現行は方式なし＝chunk版1、名前付きの各旧方式、最新 `printed-ownership-v9`＝版10。保存requestの識別子とimmutableな入力に基づく既存の再構成を使い、旧版・先行の採用だけの版・後続の新方式を区別する。新しい接続を先行版の原文に遡及適用しない。混在時も無条件に新方式にしない。新規生成・該当orphan回収・`_saved_correction`・`load_pdf_correction`・run/resumeの旧原文照合・候補採用がその記録自身の規則を使う。旧成功のbyteを保ち、追加を理由に再送せず、未知方式・改変・再構成不一致を拒否する。
- 保存・再読契約は `docs/reference/artifacts.md`、英語出力とQAの読み方は `docs/reference/usage.md`。再実行方法は `.pi/skills/verify-project/` の関係節とFeature Mapに置く。開発チェックの入口が変わる場合のみ `CONTRIBUTING.md` を更新する。

## 完了条件
| 担当 | 実際の出力で確認すること |
|---|---|
| 先行項目 | 181–185・186–190の**回答全体**を同じ新しい採用規則で処理し、双方を保存・別processで再読。各chunkの断片から出力まで、コードは英語fenceに、脚注・caption・周辺本文は該当する実unitsと出力の元位置に、重複・漏れなく一度だけ残る。185の脚注がコードに入らず、187のcaptionが別ブロックとして正しいコードを指す。181・182・186の共有caption、183の本文、190のfilenameの不確実性も確認する。境界が決められない対照は拒否。接続済みであることは求めない。 |
| #99 | 停止記録の所有開始71・111・116・121・181・186の6 chunkを同じ後続方式で新規採用し、まとめてfresh load→assemble→units／英語出力・QAへ通す。115–116、117–118、119–121の既知未接続境界を含む系列、185–186、187–188の5組で、実際の出力の行順・全文・回数、両端の出典、周辺ブロックと独立した脚注・captionの位置/所属を原画像と照合する。119–121の接続と既知21行等の字下げ残差は別採点。115–116も41行完全復元とはしない。73→74、相互適合が片側で複数、誤target、途中本文は結合せず両断片とQAを残す。変更された他の組も見落とさず記録し、原画像で未確認のものを正当化しない。 |
| 共通 | 旧版と先行版の再読・再利用を新出力と区別し、誤った版や改変を拒否。実資料、固定期待、失敗記録が不変。最新差分で正式チェック・適用対象の独立Review・人間Code Reviewを実施。以前の成功・Approveは流用しない。 |

## 動作確認方法
- **小さい自作例だけTDD。** 既存の `tdd` Skill。先行は `tests/test_pdf_correction.py` の公開 `correct_pdf → load_pdf_correction` とassembleによる観測で、一意な共有項目でも現行が拒否する失敗から始め、同文が複数あり所属を区別できない例は引き続き拒否する。後続は `tests/test_pdf_source.py` の公開assembleで、相互適合が両側一意な組と片側複数・誤targetの拒否を検査。旧方式と先行版のbyte/新旧混在、改変・orphan・成功済み再送0は既存回帰を再利用し、変更する契約だけ補う。実書籍・文書・CLI・統合比較は非TDD。新CIや常設の全冊E2Eは作らない。
- **実資料は無送信の閉じた再生。** `.pi/skills/verify-project/SKILL.md` と同配下 `references/feature-map.md` の「所有本文の採用・保存再読（#97）」「コード断片・表の閉じた再生（#98）」「PDF原文標本準備」「保存記録と詳細検査」を参照。`scripts/pdf_adoption_replay.py` の単回pipe、入力/hash照合とfresh readerは再利用できる。既存モードは2件/4件限定・別々の採点であり、今回の2 chunkや6 chunkの結合検査ではない。承認範囲の別手順だけ最小補完する。元draft・原画像・投影・許可ページ・元回答を照合し、旧attempt/chunkを新規成功としてコピーしない。正解は出力後の独立比較だけ。比較資料は全体Plan記載の `work/issue83-survey-xcP1KLJ7/root-final/` と対応する `root-survey/seed-*/` の原画像、所有範囲と回答は `work/design-and-build-great-web-apis/`。候補の件数・hash・役割ラベルだけで合格にしない。
- **通常経路は別に検証。** Mapの人工PDFで実Node/faux/no-networkのrun→fresh resume。実翻訳入力、位置QA、旧immutable不変、成功済み再送0を確認する。正式チェックは `CONTRIBUTING.md` の影響選定（補正／source、run／resume／候補採用、units／render、対象helper、Ruff check/format、PDF環境ty。共通保存・広いconsumerに影響すればcore全pytest・正式PDF phase）。
- Workerはfocused回帰・手順更新と再実行。Rootは別の新規0700親で原画像と実出力を直接照合し、revision・dirty差分・入力指紋・command/exit・採否と未達を記録する。所有する一時Workspaceとchildだけをcleanupし、保持した証拠を再読、旧資料のhash/mtime不変を確認する。Implementation Rootは保証・禁止事項を変えずに既存手段を再確認し、必要時のみ共通 `create-verification-skill` で最小helper／手順を補完できる。

## 制約・対象外
旧Plan・原本・旧attempt・失敗を含む検証記録・固定期待・作業中の差分を変更、破棄、stashしない。書籍本文・画像・生回答はprivate限定。実AI送信、追加retry、認証・外部trial markerへのアクセス、旧workspaceの修復・移行・再翻訳、新OCR・install・共有環境変更はしない。ページ番号・特定ref・既知の文字列・書体名・座標を製品判定に使わない。一般の脚注／caption検出器、後処理での推測結合、EPUB・別件のrender警告は対象外。必須部品・安全な隔離が足りなければ不足と再開条件を示してBLOCKED。所属が証明できず必須条件を満たせないなら成功扱いせず、保証や設計を変更する前にREPLAN_REQUIREDへ戻る。
