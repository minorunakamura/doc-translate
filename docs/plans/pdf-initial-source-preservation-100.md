# #100 原本で読める本文記号を初回補正で直し、接続と図を別々に確かめる

## 目的
新しく PDF を処理した読者に、原本で確認できる本文を正しい記号のまま届ける。[#100](https://github.com/minorunakamura/doc-translate/issues/100) の成果は、**初回補正で何を採用したか、どの断片を接続したか、図がどこに何として出たか**の三つで判定する。どれか一つの合格で他を代用しない。

正式な元 Plan は [pdf-initial-source-preservation.md](pdf-initial-source-preservation.md)（SHA-256 `2ed173321bfa12e890499f48eea593863482eb65e502dede7a1e79ec9bb4f798`）。本版は #100 に限り、本文記号の採否と接続・図の受入方法を下記に改訂する。元 Plan、[#99 の第3版](pdf-initial-source-preservation-99-r3.md)、旧 Issue・固定期待は書き換えない。#99 は完了済み。全冊の変更組の列挙・統合受入れは [#101](https://github.com/minorunakamura/doc-translate/issues/101) に残す。

## 停止理由と根拠
`fix/pdf-source-preservation` の `074e642`、`drawn-code-v12`（schema 13）。私有記録 `work/issue100-root-output-yI42ksll/{result.json,compare.py,output.py,inputs.json,revision,exit}` は、所有61–65、121–125、211–215の保存回答を隔離再生したもの。旧提案順では3組とも「左本文→図→右本文」、採用 chunk では「左→右→図」、組立後は「結合本文→図」。**3組はすでに接続され、問題の記号は接続前に変わっている。**

| 境界 | schema 13 の本文・英語出力 | 接続の観測 |
|---|---|---|
| 64→65 | 固定期待の `–` 2か所・`—` 1か所がいずれも `-`。採用本文1回、固定全文0回 | 接続済み |
| 123→124 | 固定期待の `—` 1か所が欠ける。採用本文1回、固定全文0回 | 接続済み |
| 213→214 | 採用本文＝固定全文、英語に1回。変更不要の対照 | 接続済み |
| 61→62 | 脚注・見出し・独立本文3件が介在。図だけを挟む例ではない | 分離、両側に接続 QA |

旧採用断片の本文対は3組とも連結すると固定全文に一致する。schema 13 の組立本文はいずれも Docling 本文に一致する。`pdf_correction.py::_make_chunk` が呼ぶ `pdf_adoption.py::adopt_owned_ranges` は、通常本文について、この記号差を原本から採用する経路を持たず、抽出本文へ戻す。一方 `pdf_source.py::assemble_pdf_source` は採用済み断片を使う。Planning の読取専用照合では、指紋確認した原本 PDF の該当 provenance 領域で、前後の文字から4か所それぞれに一意に対応する非生成の塗り描画文字（64ページに U+2013×2・U+2014×1、124ページに U+2014×1）を確認した。これは根拠の所在であって、新方式の採用・保存や画像出力の合格実績ではない。

既存比較の `with_correction_QA` は block に QA があるかだけを示す。`units_match_source` は未展開の `source_text` 比較（64→65・213→214で偽）。図についても旧新の順序は分かるが、出力 crop・所属までは照合していない。この記録を最終採点に流用しない。

## 要件・決定事項
- **ユーザー決定：記号差を警告だけで済ませない。** 新規の通常本文では、検証済み補正案の局所的な記号置換・挿入が、同じ所有箇所の可視の原本と一意に合えば初回補正で採用する。前後の本文、語・数字・空白・URL等を巻き換えず、同じ箇所を一度だけ採用する。今回の4か所は必須正例で、Docling＋QA のままなら未達。
- 所有ページ・ref・元の有効範囲・前後文脈・原本の描画位置と対応ページ画像を根拠にする。別出現、図内文字・caption・別段落、不可視の文字層、回答の主張、ページ全体の文字列一致、正規化した記号の一致だけでは採用しない。記号のない原文 span は捏造せず、挿入は元の両隣と描画位置で追えるようにする。
- 原本で裏付けられない差は該当箇所の読めた Docling 本文＋位置付き補正 QA（対象・位置・違い・採用理由）とし、訂正成功には数えない。裏付けられた差に古い「抽出を採用」の警告を付けない。別の正当な不確実性は消さない。原本・画像の検証失敗や記録破損を「読めない」扱いで通さない。
- **接続規則を広げて記号や図を直さない。** 旧提案の図介在と採用後の実順を区別する。相互 target、同じ raw 項目の順序付き所有範囲・境界、mode・構造・ページ順、両端の一意性を確認する。採用上も本当に図や独立本文が間にあるものを3正例と同一視しない。未証明の飛越し、図を移動して隣接させる処理、誤 target や複数候補の救済はしない。

## 実装方針
1. 再開時に現行 branch・作業差分・未追跡物・停止記録を再確認し保持する。採用変更は `correct_pdf` の共通 chunk 作成箇所で、既存の所有判定・局所描画照合と artifact 検証を使う。出典の座標系と抽出側の charspan を訂正後本文の offset と混同しない。送信 projection、生回答、組立・units・renderer での事後置換は変更しない。
2. 新規採用に一つの後継方式を付ける。`request.context.adoption_rule`・chunk schema・保存済み成功の再利用・orphan 回収・別 process の load/再構成を同じ方式と元入力に結び付ける。旧方式（特に schema 13）は元の byte・挙動のまま再読し、既存成功を再送・昇格しない。新方式でも #99 の schema 12/13 用コード接続・元提案順の保護を失わず、混在時の旧挙動、改変・未知方式の拒否を維持する。
3. 図は下記の比較で最初に食い違う段階を特定する。既存の読順・所属・出力が契約を満たすなら製品変更不要。具体的な欠落・誤所属・順序違反があれば原因の段階で最小修正する。両本文間に図を戻すこと自体を目標にしない。
4. 採用条件・保存版は `docs/reference/artifacts.md`、利用者が訂正と未解決 QA を区別する方法は `docs/reference/usage.md`。実行手順は `.pi/skills/verify-project/SKILL.md` と同配下 `references/feature-map.md` の #100 関係節に置く。正式チェックの入口が変わるときだけ CONTRIBUTING.md を更新する。

## 完了条件
| 独立に採点するもの | 合格の証拠 |
|---|---|
| 本文の採否 | 64・124ページの4記号が**接続前の保存 chunk と fresh load**に正しい所有・位置であり、接続後の本文、対応する復号済み実 unit、英語の該当 anchor 区間まで届く。3正例とも固定全文（記号・語間を含む）が順番どおり1回。213→214や周辺本文を壊さず、全体の件数・QA・英語全体の部分一致だけでは不可。 |
| 接続／拒否 | 正例3組は最初の断片の位置で、その2断片だけが一つの本文になり、fragments・locations と両側出典が追える。61→62は強制結合せず、両断片、介在内容とその順、両端に対応する未解決 `pdf_connection` QA と失敗 check を保持。文字符号の訂正を新しい接続成功数に足さない。 |
| 図・周辺内容 | 各図は原本と同じページ領域・内容・所属で独立して1回。期待は「最初の断片位置に結合本文、図は次の非対象本文より前、非対象 block 同士の相対順はすべて保持」。対象組前後の独立本文、注記、caption があればその対応と unit を守り、図内文字を本文に重複させない。213→214 は特に図全体の切り抜きを検査。順番だけで所属・crop の合格にはしない。 |
| 互換性・保全 | 新旧記録の再読・再利用、通常 run／fresh resume、以前のコード接続・保護対象を維持する。今回の差分に対する正式チェック・独立 Review・人間 Code Review を経る。停止前の検査や Approve は流用しない。 |

## 動作確認方法
1. **材料を分離。** `work/design-and-build-great-web-apis/` の同一原本・draft/raw・ページ画像・検証済み request と未編集の最終回答を指紋で binding。所有開始 **61・121・211 の3 chunk**、元の所有／参照ページ・画像順・projection のまま新規の 0700 親で個別に閉じた pipe から一回ずつ再生し、正式な採用・保存経路へ通す。旧 chunk を新結果としてコピーしない。`work/issue83-survey-xcP1KLJ7/root-survey/seed-{61,111,211}/` の該当原画像・対応表と `root-final/`（root-survey の兄弟）の `fixed-expectations.json`・hash は**製品出力を得た後の採点用**。製品・補正案に正解を渡さない。
2. **照合順序。** 各組について次の表を埋める。読み順を同一の配列だと仮定せず、元 fragment と出典で各段階を対応付ける。

   | 順 | 何を見るか | 何と突き合わせるか |
   |---|---|---|
   | ① 原本／旧提案 | 紙面の本文・描画記号、図全領域と内外の注記・前後本文。旧回答の左右 fragment・図・周辺の順と所属 | 正解の `ordering_rule`・該当原画像と、旧回答の page/ref/span・図 region/members・caption 関係。物理的な上下、旧提案順、論理出力順を区別 |
   | ② 新規保存→別 process load | 左右**別々**の採用本文・記号・図・その前後すべての block | raw・提案との差を位置別に対応付け、図が間か後か、同じ図と member が保たれたか、無関係な内容の移動がないかを記録 |
   | ③ assemble→units | 正例は左位置の一本文と独立図、負例は別々の本文。本文の fragments/locations、図の region/members・参照、QA の target | 実 unit の block_id と保護 token の値・順を検査して復号し、本文全文・隣接注記と照合。図自体に unit がないことを欠落と数えない |
   | ④ render | `source-map.json` の対応を引いて **実際の `english.md` の anchor 順・区間内の本文・img と参照先の画像**、`qa.json` の stage/target/location/output_anchor | 該当原画像の crop・内部の全内容・非対象 block 全体の相対順と実出力を比較する。画像 fallback・map の存在・QA 総数は代用不可。残る QA は正しい対象／位置にあり、別段落・図に誤着していないこと |

3. **自動回帰と直接操作。** TDD は既存 `tdd` Skill で小さな自作 PDF の公開 `correct_pdf → load_pdf_correction` に限定して、原本が支持する記号を落とす失敗から始める。置換・挿入と、同じ案でも原本が支持しない／所属が曖昧なら元文字＋位置 QA になる対照を置く。保存再読分岐も公開境界で確認する。公開 assemble の一意／拒否は既存回帰を先に実行し、変更を要する具体的な失敗があればその境界だけ test-first。既に緑の3組を Red と称さない。実書籍・図の画像比較・CLI・文書は非TDD。全冊 E2E・新 CI は作らない。
4. **検証資産。** verify-project の Feature Map #97（別親の人工 PDF・実 Node/faux/no-network 通常 run→fresh resume）、#99（閉じた単回再生・fresh capture）、既存 `scripts/pdf_adoption_replay.py`・`pdf_{connection,role,listing}_comparison.py` の再読、token 展開、anchor 比較を使う。#99 の6 chunk／schema 13 固定の合格を転用しない。#100 の3 chunk と上表の位置別採点・図 crop 比較だけを共通 `create-verification-skill` に従って補完し、更新手順を再実行。人工 CLI では実翻訳入力・未証明差の位置 QA・無変更再開の成功済み再送0・immutable 不変を確認する。
5. Worker は focused 回帰と手順再実行。Implementation Root は別の 0700 親で原画像・採用前後・実出力を直接照合する。revision/dirty・入力指紋・command/exit・期待／実測／残差を private に保存。作成した一時 workspace/child だけ cleanup 後、証拠と元の hash/mtime を再読する。正式範囲は CONTRIBUTING.md「変更影響から正式実行範囲を選ぶ」に従い、補正・保存再利用／orphan・source・units/render・run/resume／候補採用・関係 helper、Ruff check/format・既存 PDF 環境の ty を確認。共通 schema／保存変更の影響を含め core 全 pytest・正式 PDF phase に戻す。

## 制約・対象外
既存の未コミット変更・部分実装・検証記録・旧 attempt・旧 Plan・固定期待・原本を保持する。無断の commit/stash/破棄、旧 workspace の移行・修復、実 AI 送信・retry・認証/marker 変更、OCR・install・共有環境変更は禁止。書籍本文・画像・生回答は private に限り、通常 stdout・Git・Issue へ出さない。ページ番号・特定 ref・文字列・固定位置で採否を決めない。コード・表・数値の読み替え、図を飛ばす新しい汎用接続、EPUB、翻訳品質、全冊統合はこの改訂の対象外。
Implementation Root は承認済みの保証を変えず、隔離済みの既存手段・最小 helper の作成保存と再利用を判断できる。許可範囲で手段が成立しなければ、試行済みと未確認を分け理由・再開条件付き BLOCKED。正式保証や公開契約のさらなる変更が必要なら REPLAN_REQUIRED。期待を弱めて完了としない。
