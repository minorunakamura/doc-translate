# #99 第2版：相互に一意なコード対を決めてから、間のブロックを検査する

## 目的
[Issue #99 の正式入力](https://github.com/minorunakamura/doc-translate/issues/99#issuecomment-7012575953)のコード接続を再開できるようにする。**「接続先が一つしかない」と「その相手までの間を越えてよい」は別の条件であり、この順に両方を満たすときだけ結ぶ。** 近い相手を選ばず、唯一の相手でも本文を飛び越えない。

## 入力と改訂箇所
- [第1版](pdf-initial-source-preservation-99.md)（SHA-256 `b6581be32905f0bfbbbdfc6b0646b4420df47bf62f5735f0881667509132666c`）の「後続：#99のassemble」にある「介在ブロックで絞ってから両側で一意」を、下記の判定順に置き換える。理由は、読順 `L, R1, R2` で両方が適合しても、介在を先に使うと R1 の存在で R2 が消え、L→R1 を誤って一意と判定するため。#99 の判定・期待結果は本版を優先し、原文保持・再読・検証・禁止事項は第1版と[全体Plan](pdf-initial-source-preservation.md)から引き継ぐ。旧文書は編集しない。
- [#108](https://github.com/minorunakamura/doc-translate/issues/108) は受入れ済み。`48da115` の `printed-roles-v10` は chunk schema 11。`work/test-verification-108-root-accept-XiruMIGt/direct.json` に両chunkの役割・出力確認がある。`work/test-verification-99-root-before-UQ8DK1tX/probe/summary.json` は6 chunkが版11で保存され、73→74 と 120→121 が接続済み、必須5組には未接続境界が残ることを示す。これは新方式の成功記録ではない。#108 を再実装・再開しない。

## 要件・決定事項
1. **相互適合する対を列挙する。** 組立前の所有コード断片について、左の元の outgoing.target が右を、右の元の incoming.target が左を指すことを、採用前の提案の出典と実際の採用範囲の双方で照合する。ページ、draft-local ref／異draftの実item・位置の同一性、明示span等のselector、双方の `newline`、code・language・構造、左が右より前という読順・ページ順を満たすこと。採用で追加されたevidenceだけで誤targetを救済しない。この段階では二端の順序を見るが、間に何があるか・距離・隣接性は使わない。
2. **両端の相手を数える。** 上の同一集合で、左の outgoing に適合する異なる右断片が1件、かつ右の incoming に適合する異なる左断片が1件の対だけを残す。数えるのはraw evidenceのhit数でなく相互適合した相手の断片数。同じ断片の incoming と outgoing は別の境界であり、正当な系列を禁じない。片側でも複数ならその端の候補は全部不採用。候補を一つずつ消して再集計したり、先着・近さ・他の結合結果で一意にしたりしない。
3. **一意な対にだけ介在の拒否条件を適用する。** 結合前の読順で、二端の間が空か、元の位置・所属で残せる独立脚注だけなら接続可。本文・別コード・captionなどが間にあれば接続不可。captionが周囲にあることと、captionを飛び越すことを区別する。拒否は別候補を採用する理由にならない。確定した境界だけを既存の `newline` 規則で結び、文字や字下げを推測しない。

### 公開 `assemble_pdf_source` の期待結果（後続の新方式）
| 入力（元の読順） | 返す `SourceSnapshot` |
|---|---|
| `L, R1, R2`。L→R1、L→R2 がともに相互適合 | 接続0。`[L, R1, R2]` の3コードをその順に一度ずつ、各1 fragmentと出典付きで返し、未解決の接続QAを残す。遠い方だけ落として近い方と結ばない。`L1, L2, R` で R 側が複数の場合も同じ。 |
| `L, 本文, R`。L↔R は両端で唯一 | 接続0。`[L, 本文, R]` の順・全文・所属を保ち、両コードは別fragmentのまま、接続QAを残す。 |
| `L, R`。両端で唯一。raw hitが複数でも他は相互適合しない | 接続1。コードは一つ、元の2 fragments・出典・行順を保持。`L, 独立脚注, R` でも一意ならコードだけがつながり、脚注は独立した元位置の内容として一度残る。解決した境界に失敗QAを付けない。他のQAは消さない。 |
| 相手の採用後evidenceにだけtargetが一致（実例73→74） | 接続0。元の提案が誤targetなので位置と両断片を保持し、未解決として返す。 |

拒否の公開観測は、ブロックの全文・出現回数・`structure.fragments`・`locations` に加え、当該断片を追える `uncertainties` と失敗した `pdf_connection` check。警告文言・件数だけを合格条件にしない。

## 実装方針
- `src/doc_translate/pdf_source.py::assemble_pdf_source` の新方式のコード境界だけを変える。現行 `candidates` は採用済みevidenceのhitから相手が1件かを先に調べる。新方式は上記の相互適合・一意性・介在の順に判定し、結合・fragmentの出典・alias・callout行・QAの対応は既存処理を使う。本文・リスト等の接続は変更しない。
- `pdf_correction.py` の保存requestの方式とchunk版を区別し、検証済みの元回答と採用断片を対応付ける。#108 の所属・整形の採用規則は不変。後続の新規処理と、方式なしの版1、名前付き旧版、#108版11の再現を分ける。新規生成・該当orphan回収・`_saved_correction`・`load_pdf_correction`・assemble・run/resumeの旧原文照合・候補採用は、保存記録自身の規則で再現する。混在を理由に旧断片を新方式へ昇格させない。旧成功のbyteを変えず、方式追加だけでは再送しない。未知方式・改変・再構成不一致は拒否する。
- 保存と組立の契約は `docs/reference/artifacts.md`、英語出力・接続と字下げのQAの読み分けは `docs/reference/usage.md`、再実行方法は `.pi/skills/verify-project/` の関連手順とFeature Mapに置く。開発チェックの入口が変わる場合だけ `CONTRIBUTING.md` を更新する。

## 完了条件
- 所有開始 **71・111・116・121・181・186** の6 chunkの未編集保存回答全体を、同じ後続方式で新規採用・保存する。別processの fresh load→公開assemble→実units／英語出力・位置QAを通して比較する。成功分だけの選別、旧採用chunkのコピー、手作りの実資料結果は禁止。
- **115–116、117–118、119–121の系列、185–186、187–188** の必須5組が同じ規則で接続される。全文・行順・出現回数・両端の出典、周辺本文・脚注・captionの位置と対象、callout行を原画像と照合する。185の脚注をfenceに入れず、187のcaptionを独立して対象コードに結び付ける。#108受入れ（181・182・186の共有caption、183本文、190 filenameの不確実性を含む）を維持する。73→74は接続せず、両断片と未解決QAが残る。この6 chunkで他に変わった組も記録し、原画像を見ずに正当化しない。
- **接続と字下げは別採点。** 119→120と既存120→121からなる系列の接続は、既知21行の字下げ残差の解消を意味しない。115–116の接続も41行完全復元とは数えない。未証明の行頭・空行は位置付きQAとともに報告する。役割や本文の欠落・二重収録を許さない。
- 公開assembleの表の結果、旧版・#108版のbyte再現と混在・不正拒否、通常run/resumeでの再送0、旧資料の不変をそれぞれ確認する。新差分に対する正式チェック、適用対象の独立Review、人間Code Reviewを完了する。以前のApproveやチェックは代用しない。

## 動作確認方法
- **TDDは小さい自作入力による公開assembleの接続・拒否に限定。** `tests/test_pdf_source.py` の公開入口と `tdd` Skillを使う。raw hitが複数でも相互適合が一対だけの正例と、上表の片側複数・唯一対の途中本文・誤targetを結果で区別する。複数の負例はR2も相互適合する入力を使い、単に遠い方が誤targetという例で代用しない。現行でも通る拒否例をRedと称さない。互換性は既存の保存再読・orphan・改変・混在・run/resume回帰に不足分だけ補う。実書籍・CLI・文書・統合比較は非TDD。新CI・常設全冊E2Eは作らない。
- **閉じた実資料再生。** `.pi/skills/verify-project/SKILL.md` と同配下 `references/feature-map.md` の #97・#98・#108、PDF原文標本準備、保存記録と詳細検査を再利用。`scripts/pdf_adoption_replay.py`（同Skill配下）の単回pipe・入力/hash照合・fresh reader、#108の役割比較は使えるが、既存の2・4 chunk検査は6 chunkをまとめた接続判定ではない。元入力は `work/design-and-build-great-web-apis/`、固定期待は `work/issue83-survey-xcP1KLJ7/root-final/`、原画像は対応する `root-survey/seed-*/`。元draft・投影・所有/参照範囲・画像・元回答を検証し、固定期待は出力後の独立採点にだけ使う。完成済み出力の流用、hashや候補件数だけの合格は禁止。
- **通常経路は別の人工入力で確認。** Map #97 の人工PDFを別の新規0700親で実Node/faux/no-networkのrun→fresh resumeに通し、実翻訳入力・位置QA・旧immutable不変・成功済み再送0を見る。正式チェックは `CONTRIBUTING.md` の影響選定に従い、補正/source、run/resume/候補採用、units/render、関連helper、Ruff check/format・PDF環境ty。共通保存や広いconsumerに及べばcore全pytest・正式PDF phaseを行う。
- Workerはfocused回帰、6 chunkの再生・採点に不足する手順だけの補完と再実行を担当。Implementation Rootは手段を再確認し、別の新規0700親で実出力・原画像・未達を直接照合する。revision・dirty差分・入力指紋・command/exit・期待と観測をprivateに記録し、自分の一時workspaceとchildだけをcleanup。保持証拠を再読し、旧資料のhash/mtime不変を確かめる。承認済みの保証・禁止を変えず、必要なら共通 `create-verification-skill` で既存手順・helperだけを最小補完できる。補完した手段の成立を受入れの代わりにしない。

## 制約・対象外
既存差分・部分実装・検証記録（失敗を含む）・旧Plan・旧attempt・原本・固定期待を変更、破棄、stashしない。実AI送信・追加retry、実認証・外部trial markerへのアクセス、旧workspace修復・移行・再翻訳、新OCR・追加install・共有環境変更は禁止。書籍本文・画像・生回答を外へ出さない。ページ番号・特定ref・既知の文字列・書体名・座標を製品判定にしない。#97・#98・#108の再実装、#100の本文接続、#101の全冊差分・統合受入れ、EPUB・別件のrender警告は対象外。安全な検証が許可範囲で成立しなければ不足と再開条件を示してBLOCKED。必須5組を含む保証・設計の変更が必要なら期待を緩めずREPLAN_REQUIREDへ戻る。
