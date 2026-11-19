# #97 初回PDF補正：通常本文の語間を原本の描画位置で確かめる（第4改訂）

## 目的

初回補正で読めた原文を失わない。本文は実際の翻訳入力に、非翻訳のコード例は英語出力に、元の位置・役割・順序で届ける。今回は「通常本文で補正案だけに半角SPACEが1個あるとき、何を根拠に採用するか」を確定する。

対象は [Issue #97 の第3改訂コメント](https://github.com/minorunakamura/doc-translate/issues/97#issuecomment-6988485293)。[第3改訂 Plan](pdf-initial-source-preservation-97-r3.md)（SHA-256: `88aa28df3fdc00e9528b54fb05a14cb71e7c3d7ba6392a7b039a0203c5e8c38c`）の同位置SPACEの解釈を以下で置き換える。コードの空行を含む他の保証・制約は維持する。過去の Plan や固定対照は訂正しない。

## 停止理由と確認したこと

PDFの78枚目（印刷ページ62）では、Docling が落とした語間を補正案が戻し、現部分実装も警告なしで採用する。結果は紙面の読みと合う。しかし `pdf_adoption.py::_supported_space` と再生helperは PDFium の抽出文字列に空白を見つける。元の文字と、配置から PDFium が補った空白を区別しない。

私有証拠 `work/test-verification-97-r3-root-YfusFzEj/{review-decision,space78-layer,synthetic-generated-gap,review-counterexamples}.json`。該当SPACEは `IsGenerated=1`、境界に符号化された空白はなく、同一行の別々の文字描画間に位置移動がある。Planning の原本読取では、左右の内部はそのフォント・サイズ・横倍率での通常の字送りに合い、境界だけ約1.67 pt余分に進んだ。一方、自作PDFでは、一つの文字列の途中の字間調整でも PDFium が半角SPACEを返した。これらは判断根拠であり、製品用の閾値でも検証完了の記録でもない。

## 要件・決定事項

**合意した変更：** PDFium の生成SPACEだけでも、一律拒否でもなく、補正案の語間を元の描画位置が独立に裏付ける場合に採用する。「原本に符号化されたSPACE」ではなく「紙面の語間として採用した」と区別する。別ライブラリ、多数決、AIへの再質問は必要ない。

1. **発動条件**は、所有が確定した通常本文で、Docling と補正案の差が案側の半角SPACE1個だけのとき。原本の所有ページ・該当span・枠内で前後文脈も含む同一出現・同一行に対応させる。一致した本文の走査や一括スペース挿入はしない。
2. **その位置に符号化されたSPACE**が確認できれば案を採用する。**生成SPACE**なら、抽出文字列・選択可能であること・同じベースラインだけを証拠にしない。生成文字自体でなく、左右の実文字が別々に描画され、方向・フォント・サイズ・変換を考慮した通常の字送りに各側の実測位置が合い、内部の位置の揺れや計測誤差では説明できない追加余白が境界だけにあることを突き合わせる。描画オブジェクトが分かれただけ、小さな正の残差があるだけ、周囲と同じ一様な字間、単語内部の字間調整は不可。信頼できる字送りが取れない場合は推測しない。特定フォント、固定pt、文字列・ページ・資料hashで分岐しない。
3. 裏付けた場合はSPACEを入れた本文を実unitsに渡し、**この差については要確認を増やさない**。不一致・別出現・折返し・曖昧な場合はDocling原文を翻訳元に保持し、位置付きQA（対象ID、ページ、ref/spanまたは境界位置、抽出と案の差、採用内容・理由）を残す。処理失敗や再送にはしない。原本hash不正・入力破損・未知方式は不確定な読みではなく検証エラー。目的は紙面の読みであり、配置だけで全PDFの論理語境界や著者の意図まで保証しない。
4. **コードの空行数は別の契約。** 同じコード例の非空行境界は確認できるが追加空行数だけ不明なら、確認済みの両行・字下げ・Doclingの既存改行を保ち、余分な空行を足さない。前後両行の位置・差・採用内容・未確定理由をQAに残し、他の差がなければ #97 として受入可。固定対照との差を完全一致・復元成功と数えない。行境界・字下げ自体が不確かな場合は元のcodeと位置付きQA。空行差を他の不一致の免除にしない。

## 制約・対象外

既存の未コミット変更・未追跡資料・未知方式orphan拒否の部分実装を保持。commit／stash／破棄、元Workspace・attempt・固定期待／指紋・原本の変更、旧成功の修復／再翻訳なし。trial 15停止marker（累計15 trial／140予約）に触れず、実AI送信・追加retry・新OCR・モデル取得・認証変更・無断install／共有環境変更なし。書籍本文・画像・生回答・秘密をstdout／Git／Issueに出さない。固定正解は比較側だけに置く。辞書・コマンド名例外・新しい語分割器・原本の全面再抽出・永続索引を作らない。一般のコード整形、表再構築、新規跨ページ接続、EPUBの変更、全冊判定、#98〜#101の変更は対象外。#101には固定対照との差・未確定を明示し、統合期待と衝突すればそこで再確認する。原本の反復read／hashの性能指摘は延期し、今回の受入条件にしない。

## 実装方針

- `src/doc_translate/pdf_adoption.py` と `pdf_correction.py` の検証済み回答→採用chunkの境界に新規則を限定する。候補探索を採用や所有拡張の証明に使わない。draftのhashで検証したjob固有の同じ原本byte列を用い、ページ・座標原点・実寸を確認する。既存core依存 `pypdfium2==5.14.0` とlockを維持し、PDFium open・読取・closeだけをプロセス内で直列化する。
- **未解決のReview指摘6件。どれも今回の完了条件であり、指摘が記録されたことを修正済みとしない。**

  | 指摘 | 出力で確かめること |
  |---|---|
  | 同一itemのprovenance span間 | 既知の区切り（改行・空行を含む）を落としたり、一律SPACEに変えたりしない。 |
  | 混在する根拠と数値 | 部分文字列ではなく保護数値token全体を照合する。100→180／1000や小数への変化で、他の正しい内容は保持し、保護解除した実unitに元の数値＋位置QA。 |
  | code採用後のcallout | 最終codeの意図した行に対応させる。消えた行番号・範囲内でも別の行への参照を残さず、対応不能なら誤った対応付けを採用しない。 |
  | 画像のみの所有主張 | 同一文字列だけで異なる役割・別出現の本文を消費しない。図内外・入れ子、表／セル、除外と途中の非本文を越えず正しい位置・順序・回数を保つ。 |
  | 物理折返し | 引用符の外でも見た目の折返しだけを論理行の証明にしない。裏付け不能なら読めたcodeと位置QA。 |
  | codeの所有拡張 | 連続refの同一例は扱うが、隣接する独立した脚注・captionまで取り込まない。 |

- 現行作業ツリーの `owned-layout-v3`／chunk 4 は生成済みの未受入方式。**その識別子を新しい意味で再利用しない。** 新しい採否には別の方式値と対応chunk版を使い、方式なし／1、`owned-text-v1`／2、`owned-ranges-v2`／3、既生成のv3／4を従前の規則・byteで再読／再利用する。旧方式を受入済みと呼ぶことや旧成功の再送・書換えはしない。新規採用・成功再利用・該当orphan回収・別process loadで同じ記録から同じ結果。方式取り違え、入力・画像・chunk改変、再構成不一致を拒否。`_matches_request → _saved_correction → _make_chunk` の未知方式拒否を保持し、該当draft・scope・要求・configの検証済み成功orphanだけをpreflight／再送／追加attempt前に拒否。別要求は巻き込まない。`pdf_source`・`revise`・run/resumeのconsumerを維持する。
- `docs/reference/artifacts.md` に方式と互換・語間根拠と未確定差、`docs/reference/usage.md` に原文の採用と位置付き要確認の違い、`CONTRIBUTING.md` に関係する検査・依存／licenseを反映する。直接操作の更新先は既存 `.pi/skills/verify-project/` の該当節。

## 完了条件

1. 75ページ境界を含む76–80の欠落50項目すべてが、隣接所有chunkとの合計で正しい役割・位置・順序・回数で実units／英語出力に届く。退避・QAだけや全体失敗は不可。
2. 固定9対照を内容・役割・構造・出典・相対順・回数まで個別に照合する。76の行末hyphenはDocling＋位置QA。**78のSPACEは配置の裏付けを示して採用し、その差のQAなしで実unitに残す。** 77・79・80のコード3件は全文・行内空白・空行・字下げを照合。80の5行と第2行の字下げ、79のJSON・80の数値、周辺脚注・除外を維持。受入可能な空行差を他の欠落・不一致と相殺しない。
3. 同じ抽出・案でも原本の描画が違えば採否が変わる。今回の語間・コードの契約、上記6件、未知方式orphanの無送信拒否、既知旧版のbyte不変、新方式の保存再読を、最終出力／QAまたは送信記録で確かめる。

## 動作確認方法

- **自動回帰：採用と保存再読だけTDD。** 既存 `tdd` Skill、`tests/test_pdf_correction.py` 等の軽い公開経路 `correct_pdf → load_pdf_correction → assemble_pdf_source → generate_translation_units` と英語renderを使う。自作PDFで明示SPACE、別描画＋局所余白、同一描画内の字間調整、通常字送りでの描画分割／別出現・別行を判別する。PDFiumの抽出結果が同じ正例・反例を含め、実unitsと差に対応するQAの有無を照合する。判定関数をmockして済ませない。コードの非空行・空行数だけ不明な境界、1行を分断する案や上表の指摘は代表的な軽い反例で確認する。calloutは最終行との対応、数値はtoken復元後、画像・脚注は所属と出力で確認。未知方式は単体correctとrun/resumeの回収経路で該当・非該当を区別する。既に緑の挙動を壊してRedにしない。実書籍・通常CLI・配布・文書は非TDD。恒久の全経路E2E・新CIは追加しない。
- **実書籍と通常経路を分ける。** `.pi/skills/verify-project/SKILL.md` と同配下 `references/feature-map.md` の「所有本文の採用・保存再読（#97）」「PDF原文標本準備」「PDF run／resumeの工程・所有ページ進捗」「保存記録と詳細検査」を使う。新規0700親で許可済み2 chunkの保存回答だけを閉じたpipeで再生し、別processのload・組立→実units／英語renderとQAを確認する。固定比較元は `work/issue83-survey-xcP1KLJ7/root-final/{fixed-expectations,missing-content-gold,hashes}.json`、同親の `root-survey/seed-76/` と `work/design-and-build-great-web-apis/` の原本・draft・保存chunk。旧期待は変更しない。`scripts/pdf_adoption_replay.py`（Skill相対）の方式値固定と78のbounded text hit依存を最小更新し、期待文字列を読めた事実と、左右実文字の描画位置・採用結果を分離して照合する。別の隔離先で自作PDFの実Node/faux/no-network run→fresh resumeを実行し、実翻訳入力・再送0・旧immutable不変を確認する。異なる原本の並列照合を直列結果と限定比較する。
- **分担と証拠。** Workerはfocused回帰と更新した関連手順を再実行。Rootは別の隔離先で、元画像・同位置の描画・50項目・9対照と実出力・QA・送信数を直接比較し、全完了条件を受け入れる。版・dirty差分・入力hash・位置／行対応・根拠・固定との差・未確定理由・command/exitをprivateに残す。所有一時物とchildのみcleanupし、元資料hash／mtimeの不変と証拠の再読を確認。外部markerにはアクセスしない。`CONTRIBUTING.md` の正式順でfocused回帰、core全pytest／CLI help、正式PDF phase・PDF環境ty、Ruff check／format、wheel依存metadata・license／native条件とcore利用を確認する。現差分で独立Review・人間Code Reviewを受け直し、過去の結果・Approveを流用しない。
- Implementation Rootは現行手段を再確認し、保証・対象外・期待結果を変えず、既存Skill／Map／helperを最小補完して再実行できる。必要なら共通 `create-verification-skill` を使い、隔離下の標準機能と小さい接続・観測helperを保存／再利用してよい。無断install・共有環境変更・必須部品／認証の独自代替は不可。許可内で検証不能なら試行・不足・未確認・再開条件を示してBLOCKED。固定78を満たせないことをQAで合格に読み替えず、契約を変える必要がある場合はREPLAN_REQUIREDに戻る。
